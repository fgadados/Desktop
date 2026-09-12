"""A DMPL tem uma dimensão a mais, e ela faz parte da chave do fato.

A Demonstração das Mutações do Patrimônio Líquido abre cada conta em colunas
— Capital Social, Reservas de Lucro, Lucros Acumulados, Ajustes de Avaliação
Patrimonial — de modo que o mesmo `CD_CONTA` aparece várias vezes no mesmo
período, uma por `COLUNA_DF`.

Encontrado no arquivo real (12/09/2026), rodando o pipeline sobre DFP e ITR
de 2020-2026: a derivação do 4º trimestre explodiu com `MergeError`, porque
tentava casar N linhas com N linhas.

O erro visível era o menor dos dois problemas. O grave era silencioso: a
resolução de reapresentação agrupava sem `COLUNA_DF`, mantinha UMA coluna do
patrimônio líquido e descartava as outras sem avisar.
"""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import dedup, money, periods, pipeline, restatement

COLUNAS_PL = [
    "Capital Social Integralizado",
    "Reservas de Lucro",
    "Lucros ou Prejuízos Acumulados",
]


def _dmpl_por_coluna(ano: int, doc: str, dt_ini: str, dt_fim: str, base_valor: float):
    """Uma linha de 5.01 por coluna do patrimônio líquido, como a CVM entrega."""
    return [
        fx.fato(dt_refer=dt_fim, doc=doc, demonstrativo="DMPL", cd_conta="5.01",
                ds_conta="Saldos Iniciais", vl=base_valor + i * 10,
                dt_ini=dt_ini, dt_fim=dt_fim, coluna_df=col)
        for i, col in enumerate(COLUNAS_PL)
    ]


def _cenario_completo(ano: int = 2023):
    """DFP anual + ITR acumulado de 9 meses, ambos abertos por coluna."""
    return (
        _dmpl_por_coluna(ano, "DFP", f"{ano}-01-01", f"{ano}-12-31", 100.0)
        + _dmpl_por_coluna(ano, "ITR", f"{ano}-01-01", f"{ano}-09-30", 70.0)
    )


def test_a_dimensao_entra_na_chave_do_fato():
    df = money.converter(fx.quadro(_cenario_completo()))
    assert "COLUNA_DF" in periods._chave_conta(df)
    assert "COLUNA_DF" in restatement.chave_fato(df)


def test_derivacao_do_q4_nao_explode_e_respeita_a_coluna():
    """Era o MergeError: N linhas casando com N linhas."""
    out = pipeline.normalizar_fatos(fx.quadro(_cenario_completo()))["fatos"]
    q4 = out[(out["periodo"] == "2023T4") & (out["origem_periodo"] == "DERIVADO_Q4")]

    assert len(q4) == len(COLUNAS_PL), "esperada uma linha de Q4 por coluna do PL"
    assert set(q4["COLUNA_DF"]) == set(COLUNAS_PL)
    # Cada coluna deriva da sua propria: (100+10i) - (70+10i) = 30, em milhar.
    for valor in q4["VL_CONTA_NUM"]:
        assert valor == pytest.approx(30_000.0)


def test_reapresentacao_preserva_todas_as_colunas():
    """O furo silencioso: antes sobrava uma coluna so, sem aviso."""
    linhas = _dmpl_por_coluna(2023, "DFP", "2023-01-01", "2023-12-31", 100.0)
    df = money.converter(fx.quadro(linhas))
    norm = periods.normalizar(
        dedup.filtrar_ordem_exerc(dedup.versao_maxima(df), manter=None)
    )
    res = restatement.resolver(norm)

    assert len(res) == len(COLUNAS_PL), "uma coluna do PL sobreviveu e as outras sumiram"
    assert set(res["COLUNA_DF"]) == set(COLUNAS_PL)
    assert res["VL_CONTA_NUM"].nunique() == len(COLUNAS_PL)


def test_demonstrativo_sem_a_dimensao_recebe_vazio():
    """DRE, BPA e afins nao tem COLUNA_DF; a chave precisa do mesmo formato."""
    df = pipeline.garantir_coluna_df(fx.quadro(fx.cenario_dre_anual()))
    assert (df["COLUNA_DF"] == "").all()


def test_chave_continua_unica_apos_o_pipeline():
    """A invariante que a chave primaria do banco depende."""
    linhas = _cenario_completo() + fx.cenario_dre_anual() + fx.cenario_balanco_completo()
    out = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    chave = ["CNPJ_CIA", "base", "demonstrativo", "periodo", "CD_CONTA", "COLUNA_DF"]
    duplicadas = out[out.duplicated(chave, keep=False)]
    assert duplicadas.empty, (
        "chave do fato nao e unica; a carga no DuckDB violaria a PRIMARY KEY:\n"
        f"{duplicadas[chave].head(10)}"
    )


def test_carga_no_banco_aceita_a_dmpl(tmp_path):
    from db import load

    out = pipeline.normalizar_fatos(fx.quadro(_cenario_completo()))["fatos"]
    con = load.conectar(tmp_path / "dmpl.duckdb")
    n = load.substituir(con, "fato_contabil", pipeline.para_db(out))
    colunas = con.execute(
        "SELECT DISTINCT coluna_df FROM fato_contabil ORDER BY 1"
    ).fetchdf()["coluna_df"].tolist()
    con.close()

    assert n == len(out)
    assert set(colunas) == set(COLUNAS_PL)
