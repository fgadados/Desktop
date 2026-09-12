"""Carga no DuckDB. Idempotente por construcao.

Idempotencia (criterio de aceite 1): cada tabela e substituida inteira a
partir do DataFrame que a alimenta -- nunca acrescida. Rodar duas vezes com os
mesmos brutos produz byte a byte o mesmo banco, o que o teste de idempotencia
verifica pelo hash do conteudo de cada tabela.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd

from etl.config import DUCKDB_PATH

SCHEMA = Path(__file__).parent / "schema.sql"


def conectar(caminho: Path | None = None) -> duckdb.DuckDBPyConnection:
    p = Path(caminho or DUCKDB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    con.execute(SCHEMA.read_text(encoding="utf-8"))
    return con


def substituir(con: duckdb.DuckDBPyConnection, tabela: str, df: pd.DataFrame) -> int:
    """Troca o conteudo da tabela pelo DataFrame, respeitando o schema.

    Colunas do DataFrame que nao existem na tabela sao erro -- e sintoma de
    que a camada transform mudou e o schema nao acompanhou.
    """
    cols_tabela = [
        r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position", [tabela]
        ).fetchall()
    ]
    if not cols_tabela:
        raise KeyError(f"tabela '{tabela}' nao existe no schema")

    sobrando = [c for c in df.columns if c not in cols_tabela]
    if sobrando:
        raise KeyError(
            f"carga de '{tabela}': colunas nao previstas no schema {sobrando}. "
            "Atualize db/schema.sql em vez de descartar dado."
        )

    faltando = [c for c in cols_tabela if c not in df.columns]
    pronto = df.copy()
    for c in faltando:
        pronto[c] = None
    pronto = pronto[cols_tabela]

    con.register("_carga", pronto)
    con.execute(f"DELETE FROM {tabela}")
    con.execute(f"INSERT INTO {tabela} SELECT * FROM _carga")
    con.unregister("_carga")
    return len(pronto)


def hash_tabela(con: duckdb.DuckDBPyConnection, tabela: str) -> str:
    """Hash estavel do conteudo, independente da ordem das linhas."""
    df = con.execute(f"SELECT * FROM {tabela}").fetchdf()
    if df.empty:
        return hashlib.sha256(b"").hexdigest()
    ordenado = df.sort_values(list(df.columns), kind="mergesort").reset_index(drop=True)
    return hashlib.sha256(
        ordenado.to_csv(index=False).encode("utf-8")
    ).hexdigest()


def hash_banco(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    tabelas = [
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
    ]
    # `execucao` guarda timestamp: por definicao muda entre rodadas e nao
    # entra no hash de idempotencia.
    return {t: hash_tabela(con, t) for t in tabelas if t != "execucao"}


def registrar_execucao(
    con: duckdb.DuckDBPyConnection, etapa: str, linhas: int, hash_conteudo: str | None = None,
    observacao: str | None = None,
) -> None:
    con.execute(
        "INSERT INTO execucao VALUES (now(), ?, ?, ?, ?)",
        [etapa, linhas, hash_conteudo, observacao],
    )
