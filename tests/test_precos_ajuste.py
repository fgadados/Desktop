"""Ajuste de preco por evento corporativo -- as duas series.

`fech_aj_split`  so eventos de quantidade -> usada em P/L, P/VP, EV/EBITDA
`fech_aj_total`  quantidade + provento     -> usada em retorno, vol, drawdown

Sem isso, um desdobramento 1:2 corta o P/L historico pela metade sem que nada
tenha acontecido com o lucro.
"""

from __future__ import annotations

import pandas as pd
import pytest

from etl import b3_eventos
from transform import prices


def serie(fechamentos: list[float], inicio="2024-01-01") -> pd.DataFrame:
    datas = pd.bdate_range(inicio, periods=len(fechamentos))
    return pd.DataFrame(
        {
            "data": datas,
            "fech": fechamentos,
            "src_file": ["COTAHIST_A2024.TXT"] * len(fechamentos),
            "src_line": range(2, 2 + len(fechamentos)),
        }
    )


def eventos(linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=b3_eventos.COLUNAS)
    df["data_ex"] = pd.to_datetime(df["data_ex"])
    return df


def test_sem_eventos_as_duas_series_sao_o_preco_negociado():
    p = prices.ajustar(serie([10.0, 11.0, 12.0]), eventos([]))
    assert list(p["fech_aj_split"]) == [10.0, 11.0, 12.0]
    assert list(p["fech_aj_total"]) == [10.0, 11.0, 12.0]


def test_desdobramento_ajusta_retroativamente_e_preserva_o_ultimo_preco():
    """1:2 na terceira data. Precos anteriores caem pela metade; o atual, nao."""
    p = serie([20.0, 20.0, 10.0, 10.0])
    ev = eventos([{"ticker": "TEST3", "data_ex": p.loc[2, "data"],
                   "tipo": "DESDOBRAMENTO", "fator": 2.0, "valor_por_acao": None}])
    aj = prices.ajustar(p, ev)
    assert list(aj["fech_aj_split"]) == pytest.approx([10.0, 10.0, 10.0, 10.0])
    assert aj["fech_aj_split"].iloc[-1] == aj["fech"].iloc[-1]


def test_grupamento_ajusta_no_sentido_oposto():
    """10:1 (fator 0.1): o preco decuplica; a serie anterior tem que subir."""
    p = serie([1.0, 1.0, 10.0])
    ev = eventos([{"ticker": "TEST3", "data_ex": p.loc[2, "data"],
                   "tipo": "GRUPAMENTO", "fator": 0.1, "valor_por_acao": None}])
    aj = prices.ajustar(p, ev)
    assert list(aj["fech_aj_split"]) == pytest.approx([10.0, 10.0, 10.0])


def test_provento_nao_entra_na_serie_de_multiplos():
    """O ponto da decisao: dividendo nao muda a quantidade de acoes."""
    p = serie([10.0, 10.0, 9.0])
    ev = eventos([{"ticker": "TEST3", "data_ex": p.loc[2, "data"],
                   "tipo": "DIVIDENDO", "fator": None, "valor_por_acao": 1.0}])
    aj = prices.ajustar(p, ev)
    assert list(aj["fech_aj_split"]) == pytest.approx([10.0, 10.0, 9.0])
    # Na serie de retorno total, o dividendo e reinvestido: 1 - 1/10 = 0.9
    assert list(aj["fech_aj_total"]) == pytest.approx([9.0, 9.0, 9.0])


def test_retorno_total_absorve_o_provento_e_fica_nulo_no_dia_ex():
    p = serie([10.0, 10.0, 9.0])
    ev = eventos([{"ticker": "TEST3", "data_ex": p.loc[2, "data"],
                   "tipo": "DIVIDENDO", "fator": None, "valor_por_acao": 1.0}])
    aj = prices.ajustar(p, ev)
    r = prices.retorno_diario(aj, "fech_aj_total")
    assert r.iloc[-1] == pytest.approx(0.0)
    # Na serie sem ajuste de provento o mesmo dia aparece como -10%.
    assert prices.retorno_diario(aj, "fech_aj_split").iloc[-1] == pytest.approx(-0.10)


def test_eventos_compostos_multiplicam_na_ordem_certa():
    p = serie([40.0, 20.0, 10.0])
    ev = eventos([
        {"ticker": "T", "data_ex": p.loc[1, "data"], "tipo": "DESDOBRAMENTO",
         "fator": 2.0, "valor_por_acao": None},
        {"ticker": "T", "data_ex": p.loc[2, "data"], "tipo": "DESDOBRAMENTO",
         "fator": 2.0, "valor_por_acao": None},
    ])
    aj = prices.ajustar(p, ev)
    assert list(aj["fech_aj_split"]) == pytest.approx([10.0, 10.0, 10.0])


def test_data_ex_sem_pregao_cai_no_pregao_seguinte():
    p = serie([20.0, 20.0, 10.0])
    sabado = p.loc[1, "data"] + pd.Timedelta(days=1)
    while sabado.weekday() < 5:
        sabado += pd.Timedelta(days=1)
    ev = eventos([{"ticker": "T", "data_ex": sabado, "tipo": "DESDOBRAMENTO",
                   "fator": 2.0, "valor_por_acao": None}])
    aj = prices.ajustar(p, ev)
    assert aj["fech_aj_split"].iloc[-1] == 10.0


def test_fatcot_normaliza_preco_para_uma_acao():
    bruto = pd.DataFrame({
        "PREABE": [1000.0], "PREMAX": [1000.0], "PREMIN": [1000.0],
        "PREMED": [1000.0], "PREULT": [1000.0], "FATCOT": [1000],
    })
    out = prices.preco_unitario(bruto)
    assert out.loc[0, "PREULT"] == pytest.approx(1.0)


def test_evento_invalido_e_recusado_na_leitura():
    with pytest.raises(b3_eventos.EventoInvalidoError):
        b3_eventos.validar(
            _com_origem([{"ticker": "T", "data_ex": pd.Timestamp("2024-01-02"),
                          "tipo": "DESDOBRAMENTO", "fator": None,
                          "valor_por_acao": None, "fonte_url": "https://b3.com.br/x"}])
        )


def test_provento_sem_fonte_e_recusado():
    with pytest.raises(b3_eventos.EventoInvalidoError, match="fonte_url"):
        b3_eventos.validar(
            _com_origem([{"ticker": "T", "data_ex": pd.Timestamp("2024-01-02"),
                          "tipo": "DIVIDENDO", "fator": None,
                          "valor_por_acao": 1.0, "fonte_url": ""}])
        )


def _com_origem(linhas):
    df = pd.DataFrame(linhas)
    df["src_line"] = range(2, 2 + len(df))
    return df


# ---------------------------------------------------------------------------
# Cobertura: ausencia de evento nao vira "ajustado"
# ---------------------------------------------------------------------------
def test_salto_sem_evento_cadastrado_marca_a_serie_como_suspeita():
    cot = pd.DataFrame({
        "CODNEG": ["TEST3"] * 3,
        "data": pd.bdate_range("2024-01-01", periods=3),
        "PREULT": [20.0, 20.0, 10.0],
        "FATCOT": [1, 1, 1],
        "CODISI": ["BRTESTACNOR0"] * 3,
    })
    susp = b3_eventos.detectar_suspeitas(cot, pd.DataFrame(columns=b3_eventos.COLUNAS))
    assert (susp["sinal"] == "SALTO_BAIXA").any()
    assert not susp["tem_evento_cadastrado"].any()

    status, motivo = prices.cobertura_ajuste("TEST3", pd.DataFrame(columns=b3_eventos.COLUNAS), susp)
    assert status == prices.COBERTURA_SUSPEITA
    assert "corporate_events.csv" in motivo


def test_serie_sem_eventos_e_marcada_como_nao_ajustada():
    status, _ = prices.cobertura_ajuste(
        "TEST3", pd.DataFrame(columns=b3_eventos.COLUNAS), pd.DataFrame()
    )
    assert status == prices.COBERTURA_SEM_EVENTOS


def test_suspeita_resolvida_por_evento_cadastrado_libera_a_serie():
    cot = pd.DataFrame({
        "CODNEG": ["TEST3"] * 3,
        "data": pd.bdate_range("2024-01-01", periods=3),
        "PREULT": [20.0, 20.0, 10.0],
        "FATCOT": [1, 1, 1],
        "CODISI": ["BRTESTACNOR0"] * 3,
    })
    ev = eventos([{"ticker": "TEST3", "data_ex": cot["data"].iloc[2],
                   "tipo": "DESDOBRAMENTO", "fator": 2.0, "valor_por_acao": None}])
    susp = b3_eventos.detectar_suspeitas(cot, ev)
    assert susp["tem_evento_cadastrado"].all()
    status, _ = prices.cobertura_ajuste("TEST3", ev, susp)
    assert status == prices.COBERTURA_OK
