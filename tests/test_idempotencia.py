"""Criterio de aceite 1: rodar o pipeline duas vezes nao altera o resultado."""

from __future__ import annotations

import pandas as pd

from db import load
from tests import fixtures as fx
from transform import pipeline


def _entrada():
    linhas = (
        fx.cenario_balanco_completo()
        + fx.cenario_dre_anual()
        + fx.cenario_dfc_anual()
        + fx.cenario_itr_trimestres()
    )
    return fx.quadro(linhas)


def test_transform_e_funcao_pura_da_entrada():
    a = pipeline.normalizar_fatos(_entrada())["fatos"]
    b = pipeline.normalizar_fatos(_entrada())["fatos"]
    cols = sorted(a.columns)
    pd.testing.assert_frame_equal(
        a[cols].sort_values(cols, kind="mergesort").reset_index(drop=True),
        b[cols].sort_values(cols, kind="mergesort").reset_index(drop=True),
    )


def test_carga_duas_vezes_produz_o_mesmo_banco(tmp_path):
    caminho = tmp_path / "teste.duckdb"
    fatos = pipeline.para_db(pipeline.normalizar_fatos(_entrada())["fatos"])

    con = load.conectar(caminho)
    load.substituir(con, "fato_contabil", fatos)
    primeiro = load.hash_banco(con)
    con.close()

    con = load.conectar(caminho)
    load.substituir(con, "fato_contabil", fatos)
    segundo = load.hash_banco(con)
    con.close()

    assert primeiro == segundo


def test_carga_substitui_em_vez_de_acrescentar(tmp_path):
    caminho = tmp_path / "teste.duckdb"
    fatos = pipeline.para_db(pipeline.normalizar_fatos(_entrada())["fatos"])
    con = load.conectar(caminho)
    n1 = load.substituir(con, "fato_contabil", fatos)
    load.substituir(con, "fato_contabil", fatos)
    total = con.execute("SELECT count(*) FROM fato_contabil").fetchone()[0]
    con.close()
    assert total == n1


def test_coluna_nao_prevista_no_schema_e_erro_nao_descarte(tmp_path):
    con = load.conectar(tmp_path / "teste.duckdb")
    df = pd.DataFrame({"cnpj": ["x"], "coluna_nova_inesperada": [1]})
    try:
        load.substituir(con, "fato_contabil", df)
        assert False, "deveria ter levantado"
    except KeyError as exc:
        assert "coluna_nova_inesperada" in str(exc)
    finally:
        con.close()


def test_hash_e_estavel_a_ordem_das_linhas(tmp_path):
    caminho = tmp_path / "teste.duckdb"
    fatos = pipeline.para_db(pipeline.normalizar_fatos(_entrada())["fatos"])

    con = load.conectar(caminho)
    load.substituir(con, "fato_contabil", fatos)
    h1 = load.hash_tabela(con, "fato_contabil")
    load.substituir(con, "fato_contabil", fatos.iloc[::-1].reset_index(drop=True))
    h2 = load.hash_tabela(con, "fato_contabil")
    con.close()
    assert h1 == h2
