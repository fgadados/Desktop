"""Regra 2: coluna VERSAO -- reter apenas a versao maxima por documento."""

from __future__ import annotations

import pandas as pd

from tests import fixtures as fx
from transform import dedup


def _com_versoes(versoes_e_valores: list[tuple[str, float]]) -> pd.DataFrame:
    linhas = [
        fx.fato(dt_refer="2023-12-31", versao=v, cd_conta="3.11",
                ds_conta="Lucro/Prejuízo Consolidado do Período", vl=val,
                dt_ini="2023-01-01", dt_fim="2023-12-31")
        for v, val in versoes_e_valores
    ]
    return fx.quadro(linhas)


def test_mantem_apenas_a_versao_maxima():
    df = _com_versoes([("1", 100.0), ("2", 111.0)])
    out = dedup.versao_maxima(df)
    assert len(out) == 1
    assert out.loc[0, "VL_CONTA"] == "111.0"
    assert out.loc[0, "versao_int"] == 2


def test_versao_comparada_como_inteiro_nao_como_texto():
    """'10' tem que vencer '9'. Comparacao lexicografica erraria aqui."""
    df = _com_versoes([("9", 900.0), ("10", 1000.0)])
    out = dedup.versao_maxima(df)
    assert len(out) == 1
    assert out.loc[0, "versao_int"] == 10
    assert out.loc[0, "VL_CONTA"] == "1000.0"


def test_registra_quais_versoes_foram_descartadas():
    df = _com_versoes([("1", 100.0), ("2", 110.0), ("3", 120.0)])
    out = dedup.versao_maxima(df)
    assert out.loc[0, "versoes_descartadas"] == "1,2"


def test_versao_e_resolvida_por_documento_nao_globalmente():
    """Duas empresas, versoes diferentes: cada uma mantem a sua maxima."""
    linhas = [
        fx.fato(cnpj=fx.CNPJ_A, dt_refer="2023-12-31", versao="3", cd_conta="3.11", vl=10.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(cnpj=fx.CNPJ_A, dt_refer="2023-12-31", versao="1", cd_conta="3.11", vl=99.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(cnpj=fx.CNPJ_BANCO, dt_refer="2023-12-31", versao="1", cd_conta="3.11", vl=20.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = dedup.versao_maxima(fx.quadro(linhas))
    assert len(out) == 2
    assert set(zip(out["CNPJ_CIA"], out["versao_int"])) == {(fx.CNPJ_A, 3), (fx.CNPJ_BANCO, 1)}


def test_periodos_diferentes_da_mesma_empresa_nao_competem():
    linhas = [
        fx.fato(dt_refer="2022-12-31", versao="1", cd_conta="3.11", vl=10.0,
                dt_ini="2022-01-01", dt_fim="2022-12-31"),
        fx.fato(dt_refer="2023-12-31", versao="2", cd_conta="3.11", vl=20.0,
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = dedup.versao_maxima(fx.quadro(linhas))
    assert len(out) == 2


def test_linhagem_sobrevive_a_deduplicacao():
    df = _com_versoes([("1", 100.0), ("2", 111.0)])
    out = dedup.versao_maxima(df)
    assert out.loc[0, "src_line"] == 3  # segunda linha de dados do CSV
    assert out.loc[0, "src_file"].endswith(".csv")
