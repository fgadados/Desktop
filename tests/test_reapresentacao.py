"""Politica de reapresentacao: restated + flag.

A serie usa o valor publicado mais recentemente; o valor original fica
preservado e a divergencia e sinalizada. Nenhuma escolha silenciosa.
"""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import dedup, money, periods, restatement


def _resolver(linhas):
    df = money.converter(fx.quadro(linhas))
    norm = periods.normalizar(dedup.filtrar_ordem_exerc(dedup.versao_maxima(df), manter=None))
    return restatement.resolver(norm)


def _cenario_reapresentado(original=100.0, reapresentado=92.0):
    return [
        # DFP de 2022, exercicio proprio
        fx.fato(dt_refer="2022-12-31", ordem="ÚLTIMO", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=original,
                dt_ini="2022-01-01", dt_fim="2022-12-31"),
        # DFP de 2023, comparativo do exercicio anterior -- reapresentado
        fx.fato(dt_refer="2023-12-31", ordem="PENÚLTIMO", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=reapresentado,
                dt_ini="2022-01-01", dt_fim="2022-12-31"),
        fx.fato(dt_refer="2023-12-31", ordem="ÚLTIMO", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=130.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]


def test_serie_usa_o_valor_mais_recente():
    r = _resolver(_cenario_reapresentado())
    linha2022 = r[r["periodo"] == "2022"].iloc[0]
    assert linha2022["VL_CONTA_NUM"] == pytest.approx(92_000.0)
    assert linha2022["valor_restated"] == pytest.approx(92_000.0)


def test_valor_original_e_preservado():
    r = _resolver(_cenario_reapresentado())
    linha2022 = r[r["periodo"] == "2022"].iloc[0]
    assert linha2022["valor_as_filed"] == pytest.approx(100_000.0)
    assert linha2022["dt_refer_as_filed"] == "2022-12-31"
    assert linha2022["dt_refer_usada"] == "2023-12-31"


def test_divergencia_e_sinalizada():
    r = _resolver(_cenario_reapresentado())
    linha2022 = r[r["periodo"] == "2022"].iloc[0]
    assert bool(linha2022["divergente"]) is True
    assert int(linha2022["n_publicacoes"]) == 2

    rel = restatement.relatorio_divergencias(r)
    assert len(rel) == 1
    assert rel.loc[0, "variacao_pct"] == pytest.approx(92 / 100 - 1)


def test_sem_reapresentacao_nao_ha_divergencia():
    r = _resolver(_cenario_reapresentado(original=100.0, reapresentado=100.0))
    assert not r["divergente"].any()
    assert restatement.relatorio_divergencias(r).empty


def test_periodo_publicado_uma_unica_vez_nao_e_marcado():
    linhas = [
        fx.fato(dt_refer="2023-12-31", ordem="ÚLTIMO", cd_conta="3.11", vl=130.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    r = _resolver(linhas)
    assert int(r.iloc[0]["n_publicacoes"]) == 1
    assert bool(r.iloc[0]["divergente"]) is False


def test_cada_fato_aparece_uma_unica_vez_apos_a_resolucao():
    r = _resolver(_cenario_reapresentado())
    chave = restatement.CHAVE_FATO
    assert not r.duplicated(chave).any()
