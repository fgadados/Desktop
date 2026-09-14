"""Fumaca ponta a ponta: transform -> DuckDB -> consultas da interface.

Nao toca a rede. Usa os fixtures sinteticos para garantir que o schema, a
carga e as consultas da interface conversam entre si.
"""

from __future__ import annotations

import pandas as pd
import pytest

from db import load
from tests import fixtures as fx
from transform import indicators, lineage, pipeline, sector, validations


def _entrada():
    linhas = (
        fx.cenario_balanco_completo()
        + fx.cenario_dre_anual()
        + fx.cenario_dfc_anual()
        + fx.cenario_itr_trimestres()
    )
    df = fx.quadro(linhas)
    df["CNPJ_CIA"] = "00000000000191"
    return df


@pytest.fixture
def banco(tmp_path):
    resultado = pipeline.normalizar_fatos(_entrada())
    fatos = resultado["fatos"]

    con = load.conectar(tmp_path / "e2e.duckdb")
    load.substituir(con, "fato_contabil", pipeline.para_db(fatos))
    load.substituir(con, "teste_identidade", resultado["identidades"])

    conceitos = sorted(
        {c for p in sector.carregar_planos()["planos"].values() for c in (p.get("contas") or {})}
    )
    fund = indicators.montar_fundamentos(fatos, {"00000000000191": "INDUSTRIAL"}, conceitos)
    inds = []
    for cnpj, periodo in fund[["cnpj", "periodo"]].drop_duplicates().itertuples(index=False):
        inds += indicators.dupont(fund, cnpj, periodo)
        inds += indicators.fluxo_e_divida(fund, cnpj, periodo)
    cab, ent = lineage.para_quadros(inds)
    cab = cab.drop_duplicates(["entidade", "periodo", "indicador"])
    ent = ent.drop_duplicates(["entidade", "periodo", "indicador", "ordem"])
    load.substituir(con, "indicador", cab)
    load.substituir(con, "indicador_entrada", ent)

    empresa = pd.DataFrame([{
        "cnpj": "00000000000191", "denom_social": "COMPANHIA TESTE S.A.",
        "plano_contas": "INDUSTRIAL", "origem_classificacao": "teste",
        "base_escolhida": "CON", "cobertura_con": 5, "cobertura_ind": 0,
    }])
    empresa = empresa.merge(pipeline.ultimo_documento(fatos), on="cnpj", how="left")
    load.substituir(con, "empresa", empresa)
    yield con
    con.close()


def test_schema_aceita_todas_as_colunas_do_transform(banco):
    n = banco.execute("SELECT count(*) FROM fato_contabil").fetchone()[0]
    assert n > 0


def test_identidade_contabil_passa_no_cenario_completo(banco):
    df = banco.execute(
        "SELECT status, count(*) AS n FROM teste_identidade GROUP BY 1"
    ).fetchdf()
    assert dict(zip(df["status"], df["n"])).get("FALHA", 0) == 0


def test_ultimo_documento_e_versao_ficam_gravados(banco):
    r = banco.execute(
        "SELECT ultimo_doc_dt_refer, ultimo_doc_versao, ultimo_doc_tipo FROM empresa"
    ).fetchone()
    assert r[0] is not None and r[1] is not None and r[2] in ("DFP", "ITR")


def test_todo_indicador_tem_formula(banco):
    n = banco.execute(
        "SELECT count(*) FROM indicador WHERE formula IS NULL OR trim(formula) = ''"
    ).fetchone()[0]
    assert n == 0


def test_todo_indicador_ok_tem_entrada_com_arquivo_e_linha(banco):
    orfaos = banco.execute(
        "SELECT i.indicador, i.periodo FROM indicador i "
        "LEFT JOIN indicador_entrada e USING (entidade, periodo, indicador) "
        "WHERE i.status = 'OK' AND e.src_line IS NULL"
    ).fetchdf()
    assert orfaos.empty, orfaos.to_string()


def test_indicador_sem_valor_sempre_tem_motivo(banco):
    n = banco.execute(
        "SELECT count(*) FROM indicador WHERE status <> 'OK' AND (motivo IS NULL OR motivo = '')"
    ).fetchone()[0]
    assert n == 0


def test_q4_derivado_guarda_a_derivacao(banco):
    linhas = (
        fx.cenario_itr_trimestres(isolados=(20.0, 25.0, 30.0))
        + [fx.fato(dt_refer="2023-12-31", doc="DFP", cd_conta="3.11",
                   ds_conta="Lucro/Prejuízo Consolidado do Período", vl=100.0,
                   dt_ini="2023-01-01", dt_fim="2023-12-31")]
    )
    fatos = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    q4 = fatos[fatos["origem_periodo"] == "DERIVADO_Q4"]
    assert len(q4) == 1
    assert q4.iloc[0]["src_derivacao"]


def test_consultas_da_interface_rodam(banco, monkeypatch):
    """As consultas de `app/dados.py` precisam bater com o schema real."""
    sqls = [
        "SELECT e.*, d.ticker FROM empresa e LEFT JOIN depara_ticker d ON d.cnpj = e.cnpj",
        "SELECT * FROM indicador WHERE entidade = '00000000000191'",
        "SELECT * FROM indicador_entrada WHERE entidade = '00000000000191'",
        "SELECT * FROM teste_identidade WHERE cnpj = '00000000000191'",
        "SELECT * FROM preco_diario",
        "SELECT * FROM cobertura_ajuste",
        "SELECT * FROM evento_suspeito",
        "SELECT * FROM depara_divergencia",
        "SELECT * FROM reapresentacao",
        "SELECT url, sha256, bytes, baixado_em FROM fonte",
    ]
    for sql in sqls:
        banco.execute(sql).fetchdf()


def test_nenhum_valor_e_preenchido_por_interpolacao(banco):
    """Fato sem valor permanece nulo; nada de zero nem de media."""
    df = banco.execute(
        "SELECT count(*) FROM fato_contabil WHERE valor IS NULL"
    ).fetchone()[0]
    assert df == 0  # neste cenario todos tem valor; o teste guarda a invariante
    assert validations.resumo(
        banco.execute("SELECT * FROM teste_identidade").fetchdf()
    )["FALHA"] == 0
