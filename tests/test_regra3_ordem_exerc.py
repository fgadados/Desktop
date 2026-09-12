"""Regra 3: ORDEM_EXERC duplica cada linha (ULTIMO / PENULTIMO)."""

from __future__ import annotations

import pandas as pd
import pytest

from tests import fixtures as fx
from transform import dedup, money


def _duplicado() -> pd.DataFrame:
    linhas = [
        fx.fato(dt_refer="2023-12-31", ordem="ÚLTIMO", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=100.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(dt_refer="2023-12-31", ordem="PENÚLTIMO", cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=80.0,
                dt_ini="2022-01-01", dt_fim="2022-12-31"),
    ]
    return fx.quadro(linhas)


def test_sem_filtro_a_soma_dobra():
    """Documenta o erro que a regra evita: somar sem filtrar conta dois anos."""
    df = money.converter(_duplicado())
    assert df["VL_CONTA_NUM"].sum() == pytest.approx(180_000.0)


def test_filtro_padrao_mantem_apenas_ultimo():
    out = dedup.filtrar_ordem_exerc(_duplicado())
    assert len(out) == 1
    assert out.loc[0, "ordem_exerc_norm"] == "ULTIMO"
    assert out.loc[0, "VL_CONTA"] == "100.0"


def test_acento_e_caixa_nao_quebram_o_filtro():
    linhas = [
        fx.fato(dt_refer="2023-12-31", ordem="ULTIMO", cd_conta="3.11", vl=1.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(dt_refer="2023-12-31", ordem=" Último ", cd_conta="3.01", vl=2.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = dedup.filtrar_ordem_exerc(fx.quadro(linhas))
    assert len(out) == 2


def test_penultimo_pode_ser_retido_para_reconstruir_comparativo():
    out = dedup.filtrar_ordem_exerc(_duplicado(), manter=None)
    assert set(out["ordem_exerc_norm"]) == {"ULTIMO", "PENULTIMO"}


def test_valor_fora_do_dominio_levanta_em_vez_de_silenciar():
    linhas = [
        fx.fato(dt_refer="2023-12-31", ordem="ANTEPENÚLTIMO", cd_conta="3.11", vl=1.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    with pytest.raises(ValueError, match="fora do dominio"):
        dedup.filtrar_ordem_exerc(fx.quadro(linhas))


def test_ordem_das_regras_versao_antes_de_ordem_exerc():
    """Versao antiga com ULTIMO nao pode sobreviver a versao nova."""
    linhas = [
        fx.fato(dt_refer="2023-12-31", versao="1", ordem="ÚLTIMO", cd_conta="3.11",
                vl=100.0, dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(dt_refer="2023-12-31", versao="2", ordem="ÚLTIMO", cd_conta="3.11",
                vl=105.0, dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(dt_refer="2023-12-31", versao="2", ordem="PENÚLTIMO", cd_conta="3.11",
                vl=80.0, dt_ini="2022-01-01", dt_fim="2022-12-31"),
    ]
    out = dedup.aplicar(fx.quadro(linhas))
    assert len(out) == 1
    assert out.loc[0, "versao_int"] == 2
    assert out.loc[0, "VL_CONTA"] == "105.0"
