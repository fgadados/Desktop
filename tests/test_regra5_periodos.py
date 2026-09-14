"""Regra 5: periodos no ITR e derivacao do 4o trimestre."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tests import fixtures as fx
from transform import dedup, money, periods


def _normalizado(linhas):
    df = money.converter(fx.quadro(linhas))
    return periods.normalizar(dedup.filtrar_ordem_exerc(dedup.versao_maxima(df)))


def test_classifica_janela_por_duracao():
    linhas = fx.cenario_itr_trimestres()
    cls = periods.classificar_janela(money.converter(fx.quadro(linhas)))
    tipos = sorted(cls["tipo_janela"].unique())
    assert tipos == ["ACUM_6M", "ACUM_9M", "TRIMESTRE_ISOLADO"]


def test_acumulado_do_itr_nao_entra_na_serie_trimestral():
    """Sem a regra, a soma dos trimestres de 2023 daria 150 em vez de 75."""
    out = _normalizado(fx.cenario_itr_trimestres(isolados=(20.0, 25.0, 30.0)))
    tri = out[out["tipo_janela"] == periods.TRIMESTRE_ISOLADO]
    assert sorted(tri["periodo"]) == ["2023T1", "2023T2", "2023T3"]
    assert tri["VL_CONTA_NUM"].sum() == pytest.approx(75_000.0)


def test_q4_derivado_por_diferenca_entre_dfp_e_acumulado_de_nove_meses():
    linhas = fx.cenario_itr_trimestres(isolados=(20.0, 25.0, 30.0))
    linhas += [
        fx.fato(dt_refer="2023-12-31", doc="DFP", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=100.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = _normalizado(linhas)
    q4 = out[(out["periodo"] == "2023T4") & (out["origem_periodo"] == "DERIVADO_Q4")]
    assert len(q4) == 1
    # 100 anual - 75 acumulado de 9 meses = 25
    assert q4.iloc[0]["VL_CONTA_NUM"] == pytest.approx(25_000.0)


def test_q4_derivado_carrega_a_linhagem_dos_dois_insumos():
    linhas = fx.cenario_itr_trimestres(isolados=(20.0, 25.0, 30.0))
    linhas += [
        fx.fato(dt_refer="2023-12-31", doc="DFP", cd_conta="3.11", vl=100.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = _normalizado(linhas)
    q4 = out[out["origem_periodo"] == "DERIVADO_Q4"].iloc[0]
    d = json.loads(q4["src_derivacao"])
    assert d["formula"] == "Q4 = DFP_anual(12M) - ITR_acumulado(9M)"
    assert d["anual"]["valor"] == pytest.approx(100_000.0)
    assert d["acum_9m"]["valor"] == pytest.approx(75_000.0)
    assert d["anual"]["linha"] > 0 and d["acum_9m"]["linha"] > 0


def test_sem_a_dfp_anual_o_q4_nao_e_inventado():
    out = _normalizado(fx.cenario_itr_trimestres())
    assert "2023T4" not in set(out["periodo"])


def test_sem_o_itr_de_nove_meses_o_q4_nao_e_inventado():
    linhas = [
        fx.fato(dt_refer="2023-12-31", doc="DFP", cd_conta="3.11", vl=100.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = _normalizado(linhas)
    assert not (out["origem_periodo"] == "DERIVADO_Q4").any()


def test_exercicio_social_nao_civil_e_lido_do_arquivo_nao_assumido():
    """Exercicio de abril a marco: T1 termina em junho, nao em marco."""
    linhas = [
        fx.fato(dt_refer="2023-06-30", doc="ITR", cd_conta="3.11", vl=10.0,
                dt_ini="2023-04-01", dt_fim="2023-06-30"),
        fx.fato(dt_refer="2023-09-30", doc="ITR", cd_conta="3.11", vl=12.0,
                dt_ini="2023-07-01", dt_fim="2023-09-30"),
        fx.fato(dt_refer="2023-09-30", doc="ITR", cd_conta="3.11", vl=22.0,
                dt_ini="2023-04-01", dt_fim="2023-09-30"),
    ]
    out = _normalizado(linhas)
    isolados = out[out["tipo_janela"] == periods.TRIMESTRE_ISOLADO]
    assert set(isolados["periodo"]) == {"2023T1", "2023T2"}


def test_saldo_patrimonial_herda_o_trimestre_do_documento():
    """BPA/BPP nao tem DT_INI; sem isso, PL nao casa com lucro do trimestre."""
    linhas = [
        # ITR do 2o trimestre como a CVM publica: o isolado E o acumulado.
        fx.fato(dt_refer="2023-06-30", doc="ITR", cd_conta="3.11", vl=10.0,
                dt_ini="2023-04-01", dt_fim="2023-06-30"),
        fx.fato(dt_refer="2023-06-30", doc="ITR", cd_conta="3.11", vl=18.0,
                dt_ini="2023-01-01", dt_fim="2023-06-30"),
        fx.fato(dt_refer="2023-06-30", doc="ITR", demonstrativo="BPP", cd_conta="2.03",
                ds_conta="Patrimônio Líquido Consolidado", vl=400.0, dt_fim="2023-06-30"),
    ]
    out = _normalizado(linhas)
    pl = out[out["CD_CONTA"] == "2.03"].iloc[0]
    assert pl["tipo_janela"] == periods.INSTANTE
    assert pl["periodo"] == "2023T2"


def test_conferencia_da_soma_de_trimestres_aponta_divergencia_sem_corrigir():
    """Q1+Q2+Q3 tem que reproduzir o acumulado de 9 meses."""
    linhas = fx.cenario_itr_trimestres(isolados=(20.0, 25.0, 30.0))
    # Adultera o acumulado de 9 meses para simular reapresentacao no meio do ano.
    for l in linhas:
        if l["DT_INI_EXERC"] == "2023-01-01" and l["DT_FIM_EXERC"] == "2023-09-30":
            l["VL_CONTA"] = "90.0"
    df = money.converter(fx.quadro(linhas))
    cls = periods.indice_trimestre(periods.classificar_janela(df))
    div = periods.conferir_soma_trimestres(cls)
    assert len(div) == 1
    assert div.loc[0, "diferenca"] == pytest.approx(-15_000.0)


def test_dfp_anual_permanece_como_periodo_proprio():
    linhas = fx.cenario_dre_anual()
    out = _normalizado(linhas)
    assert set(out[out["CD_CONTA"] == "3.01"]["periodo"]) == {"2023"}
