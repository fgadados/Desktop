"""Regra 1: ticker nao existe na CVM; a chave e o CNPJ.

O de-para tem que sair do FCA (CVM) cruzado com o universo negociado na B3, e
tem que RECLAMAR quando os dois lados nao batem, em vez de escolher um.
"""

from __future__ import annotations

import pandas as pd
import pytest

from transform import depara


def _fca(linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    df["src_file"] = "fca_cia_aberta_valor_mobiliario_2024.csv"
    df["src_line"] = range(2, 2 + len(df))
    return df


def _cotahist(tickers: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"CODNEG": tickers})


def test_cnpj_normalizado_para_14_digitos():
    s = pd.Series(["00.000.000/0001-91", "11111111000111", "123"])
    assert list(depara.normalizar_cnpj(s)) == [
        "00000000000191", "11111111000111", "00000000000123"
    ]


def test_depara_liga_cnpj_a_ticker_pelo_fca():
    fca = _fca([
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "Ações Ordinárias",
         "Codigo_Negociacao": "TEST3", "Mercado": "Bolsa"},
    ])
    d = depara.construir(fca, _cotahist(["TEST3", "OUTR4"]))
    assert len(d) == 1
    assert d.loc[0, "cnpj"] == "00000000000191"
    assert d.loc[0, "ticker"] == "TEST3"
    assert bool(d.loc[0, "negociado_b3"]) is True
    # Linhagem: a linha do FCA de onde o vinculo saiu.
    assert d.loc[0, "src_file"].startswith("fca_cia_aberta_valor_mobiliario")
    assert int(d.loc[0, "src_line"]) == 2


def test_fca_respeita_ultima_versao_do_formulario():
    fca = _fca([
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "VELHO3"},
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "2", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "NOVO3"},
    ])
    d = depara.construir(fca, _cotahist(["NOVO3"]))
    assert set(d["ticker"]) == {"NOVO3"}


def test_divergencias_dos_dois_lados_sao_reportadas_nao_resolvidas():
    fca = _fca([
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "TEST3"},
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "MORTO3"},
    ])
    cot = _cotahist(["TEST3", "ORFA4"])
    d = depara.construir(fca, cot)
    div = depara.divergencias(d, cot)

    assert list(div["fca_sem_pregao"]["ticker"]) == ["MORTO3"]
    assert list(div["pregao_sem_fca"]["ticker"]) == ["ORFA4"]
    # Nenhum dos dois foi descartado nem "corrigido": ambos continuam visiveis.
    assert set(d["ticker"]) == {"TEST3", "MORTO3"}


def test_ticker_apontando_para_dois_cnpjs_levanta_em_vez_de_escolher():
    fca = _fca([
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "DUPL3"},
        {"CNPJ_Companhia": "11.111.111/0001-11", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "DUPL3"},
    ])
    d = depara.construir(fca, _cotahist(["DUPL3"]))
    with pytest.raises(ValueError, match="mais de um CNPJ"):
        depara.cnpj_de(d, "DUPL3")


def test_sem_cotahist_o_depara_segue_valido_mas_sem_confirmacao():
    fca = _fca([
        {"CNPJ_Companhia": "00.000.000/0001-91", "Data_Referencia": "2024-01-01",
         "Versao": "1", "Valor_Mobiliario": "ON", "Codigo_Negociacao": "TEST3"},
    ])
    d = depara.construir(fca, None)
    assert pd.isna(d.loc[0, "negociado_b3"])
