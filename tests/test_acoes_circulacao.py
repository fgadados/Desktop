"""Ações em circulação a partir de `composicao_capital` da CVM.

Colunas confrontadas com o arquivo real em 12/09/2026.
"""

from __future__ import annotations

import pandas as pd
import pytest

from transform import shares


def _bruto(linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    df["src_file"] = "dfp_cia_aberta_composicao_capital_2023.csv"
    df["src_line"] = range(2, 2 + len(df))
    if "doc" not in df:
        df["doc"] = "DFP"
    return df


def _linha(**kw) -> dict:
    base = {
        "CNPJ_CIA": "00.000.000/0001-91",
        "DT_REFER": "2023-12-31",
        "VERSAO": "1",
        "DENOM_CIA": "TESTE S.A.",
        "QT_ACAO_ORDIN_CAP_INTEGR": "1000000",
        "QT_ACAO_PREF_CAP_INTEGR": "500000",
        "QT_ACAO_TOTAL_CAP_INTEGR": "1500000",
        "QT_ACAO_ORDIN_TESOURO": "10000",
        "QT_ACAO_PREF_TESOURO": "0",
        "QT_ACAO_TOTAL_TESOURO": "10000",
    }
    base.update(kw)
    return base


def test_circulacao_desconta_tesouraria():
    """Somar tesouraria infla a base e deprime o lucro por acao."""
    out = shares.normalizar(_bruto([_linha()]))
    assert out.loc[0, "acoes_total"] == 1_500_000
    assert out.loc[0, "tesouraria_total"] == 10_000
    assert out.loc[0, "acoes_em_circulacao"] == 1_490_000


def test_regra_da_versao_maxima_vale_aqui_tambem():
    out = shares.normalizar(_bruto([
        _linha(VERSAO="1", QT_ACAO_TOTAL_CAP_INTEGR="1000000"),
        _linha(VERSAO="2", QT_ACAO_TOTAL_CAP_INTEGR="1500000"),
    ]))
    assert len(out) == 1
    assert out.loc[0, "versao"] == 2
    assert out.loc[0, "acoes_total"] == 1_500_000


def test_datas_diferentes_convivem():
    out = shares.normalizar(_bruto([
        _linha(DT_REFER="2022-12-31"),
        _linha(DT_REFER="2023-12-31"),
    ]))
    assert len(out) == 2


def test_linhagem_preservada():
    out = shares.normalizar(_bruto([_linha()]))
    assert out.loc[0, "src_file"].startswith("dfp_cia_aberta_composicao_capital")
    assert int(out.loc[0, "src_line"]) == 2


def test_coluna_de_quantidade_ausente_levanta():
    df = _bruto([_linha()]).drop(columns=["QT_ACAO_TOTAL_TESOURO"])
    with pytest.raises(KeyError, match="QT_ACAO_TOTAL_TESOURO"):
        shares.normalizar(df)


def test_total_que_nao_fecha_e_sinalizado_nao_corrigido():
    out = shares.normalizar(_bruto([
        _linha(QT_ACAO_TOTAL_CAP_INTEGR="9999999"),  # != ON + PN
    ]))
    problemas = shares.inconsistencias(out)
    assert list(problemas["problema"]) == ["TOTAL_NAO_FECHA"]
    # O valor publicado permanece intacto.
    assert out.loc[0, "acoes_total"] == 9_999_999


def test_circulacao_nao_positiva_e_sinalizada():
    out = shares.normalizar(_bruto([
        _linha(QT_ACAO_TOTAL_TESOURO="1500000"),  # toda a base em tesouraria
    ]))
    problemas = shares.inconsistencias(out)
    assert "CIRCULACAO_NAO_POSITIVA" in set(problemas["problema"])


def test_vigente_em_nao_extrapola_para_tras():
    out = shares.normalizar(_bruto([
        _linha(DT_REFER="2022-12-31", QT_ACAO_TOTAL_CAP_INTEGR="1000000"),
        _linha(DT_REFER="2023-12-31", QT_ACAO_TOTAL_CAP_INTEGR="1500000"),
    ]))
    cnpj = "00000000000191"
    assert shares.vigente_em(out, cnpj, "2021-06-30") is None
    assert shares.vigente_em(out, cnpj, "2023-06-30")["acoes_total"] == 1_000_000
    assert shares.vigente_em(out, cnpj, "2024-06-30")["acoes_total"] == 1_500_000


def test_entrada_vazia_devolve_quadro_vazio():
    out = shares.normalizar(pd.DataFrame())
    assert out.empty
    assert list(out.columns) == shares.SAIDA
