"""`CREATE TABLE IF NOT EXISTS` num banco que já existe é no-op SILENCIOSO.

O caso real (13/09/2026). A tabela `reapresentacao` ganhou a coluna
`coluna_df` em `db/schema.sql`, os 216 testes passaram, e a execução na
máquina do usuário falhou assim:

    KeyError: carga de 'reapresentacao': colunas nao previstas no schema
              ['coluna_df']

Olhando o `.sql`, a coluna estava lá. O banco `data/b3dss.duckdb` tinha sido
criado na rodada anterior, com o schema antigo, e `IF NOT EXISTS` não recria
tabela nenhuma. Todo teste até então usava `tmp_path` — banco novo a cada
execução —, então a divergência entre arquivo e banco não tinha como aparecer.

A resposta escolhida é recriar, não migrar: este banco é derivado por inteiro
dos Parquet, nenhuma linha é digitada nele, e `substituir` troca cada tabela
por completo a cada rodada. Refazer custa uma transformação e não perde dado.
Migração incremental (`ALTER TABLE`) traria o risco oposto: um banco que
ninguém sabe dizer de que versão é.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from db import load


def _banco_com_schema_antigo(caminho: Path) -> None:
    """Cria um banco como estava ANTES de `coluna_df` entrar no schema."""
    con = duckdb.connect(str(caminho))
    con.execute(
        "CREATE TABLE reapresentacao ("
        "  cnpj VARCHAR NOT NULL, base VARCHAR NOT NULL,"
        "  demonstrativo VARCHAR NOT NULL, cd_conta VARCHAR NOT NULL,"
        "  periodo VARCHAR NOT NULL, ds_conta VARCHAR)"
    )
    con.execute("INSERT INTO reapresentacao VALUES ('1', 'CON', 'BPP', '2.03', '2022', 'PL')")
    con.close()


def test_banco_antigo_e_recriado_quando_o_schema_muda(tmp_path, capsys):
    caminho = tmp_path / "b3dss.duckdb"
    _banco_com_schema_antigo(caminho)

    con = load.conectar(caminho)
    colunas = [
        r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'reapresentacao'"
        ).fetchall()
    ]
    con.close()

    assert "coluna_df" in colunas, (
        "o banco continuou com o schema antigo; era exatamente este o defeito"
    )


def test_a_recriacao_nao_e_silenciosa(tmp_path, capsys):
    """Apagar o banco do usuário sem dizer nada seria a pior forma de acertar."""
    caminho = tmp_path / "b3dss.duckdb"
    _banco_com_schema_antigo(caminho)

    load.conectar(caminho).close()
    saida = capsys.readouterr().out
    assert "schema mudou" in saida
    assert "derivado" in saida, "o aviso precisa dizer por que nada se perde"


def test_banco_em_dia_nao_e_recriado(tmp_path, capsys):
    """Rodar duas vezes seguidas não pode apagar e refazer o banco à toa."""
    caminho = tmp_path / "b3dss.duckdb"

    con = load.conectar(caminho)
    load.substituir(con, "aviso", pd.DataFrame([{
        "origem": "teste", "categoria": "marca",
        "mensagem": "sobrevive a reconexao", "registrado_em": "2026-09-13",
    }]))
    con.close()

    con = load.conectar(caminho)
    n = con.execute("SELECT count(*) FROM aviso").fetchone()[0]
    con.close()

    assert n == 1, "o banco foi recriado sem o schema ter mudado"
    assert "schema mudou" not in capsys.readouterr().out


def test_banco_novo_grava_a_impressao(tmp_path):
    caminho = tmp_path / "b3dss.duckdb"
    con = load.conectar(caminho)
    gravada = con.execute("SELECT impressao FROM schema_versao").fetchall()
    con.close()

    assert len(gravada) == 1, "uma linha, sempre: a impressao vigente"
    assert gravada[0][0] == load.impressao_schema()


def test_arquivo_ilegivel_nao_derruba_a_execucao(tmp_path, capsys):
    """Banco corrompido ou de outra versao do DuckDB: recria, não trava."""
    caminho = tmp_path / "b3dss.duckdb"
    caminho.write_bytes(b"isto nao e um banco duckdb")

    con = load.conectar(caminho)
    assert con.execute("SELECT count(*) FROM fato_contabil").fetchone()[0] == 0
    con.close()


def test_schema_versao_fora_do_hash_de_idempotencia(tmp_path):
    """Ela guarda `aplicado_em`; entraria no hash e quebraria a idempotência."""
    con = load.conectar(tmp_path / "b3dss.duckdb")
    tabelas = set(load.hash_banco(con))
    con.close()

    assert "schema_versao" not in tabelas
    assert "execucao" not in tabelas
    assert "fato_contabil" in tabelas
