"""Carga no DuckDB. Idempotente por construcao.

Idempotencia (criterio de aceite 1): cada tabela e substituida inteira a
partir do DataFrame que a alimenta -- nunca acrescida. Rodar duas vezes com os
mesmos brutos produz byte a byte o mesmo banco, o que o teste de idempotencia
verifica pelo hash do conteudo de cada tabela.

Schema que muda
---------------
`schema.sql` e todo `CREATE TABLE IF NOT EXISTS`. Num banco que ja existe,
isso nao e no-op inofensivo: e no-op SILENCIOSO. Acrescentar uma coluna ao
arquivo nao acrescenta coluna nenhuma ao banco ja criado, e a carga passa a
falhar por uma coluna que, olhando o `.sql`, esta la.

Este banco e derivado por inteiro dos Parquet em `data/parquet/` -- nenhuma
linha e digitada nele, nenhuma sobrevive a `substituir`. Entao a resposta
correta a uma mudanca de schema e refazer o arquivo, nao migra-lo: custa uma
transformacao e nao perde dado. A impressao digital do `schema.sql` fica
gravada no proprio banco; quando difere, o arquivo e recriado, com aviso.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd

from etl.config import DUCKDB_PATH

SCHEMA = Path(__file__).parent / "schema.sql"

# Tabelas que nao entram no hash de idempotencia: guardam quando algo
# aconteceu, entao mudam entre rodadas por definicao.
NAO_DETERMINISTICAS = {"execucao", "schema_versao"}


def impressao_schema() -> str:
    """sha256 do `schema.sql` vigente."""
    return hashlib.sha256(SCHEMA.read_bytes()).hexdigest()


def _impressao_gravada(p: Path) -> str | None:
    """Le a impressao do banco existente. `None` quando nao da para saber."""
    try:
        con = duckdb.connect(str(p), read_only=True)
    except duckdb.Error:
        return None
    try:
        existe = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'schema_versao'"
        ).fetchone()[0]
        if not existe:
            return None  # banco anterior a este controle
        linha = con.execute("SELECT impressao FROM schema_versao").fetchone()
        return linha[0] if linha else None
    except duckdb.Error:
        return None
    finally:
        con.close()


def _apagar(p: Path) -> None:
    p.unlink(missing_ok=True)
    Path(str(p) + ".wal").unlink(missing_ok=True)


def conectar(caminho: Path | None = None, *, avisar=print) -> duckdb.DuckDBPyConnection:
    p = Path(caminho or DUCKDB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    sql = SCHEMA.read_text(encoding="utf-8")
    impressao = impressao_schema()

    if p.exists():
        gravada = _impressao_gravada(p)
        if gravada != impressao:
            avisar(
                f"      schema mudou desde que {p.name} foi criado "
                f"({'sem registro' if gravada is None else gravada[:12]} -> "
                f"{impressao[:12]}). Recriando o banco a partir dos Parquet -- "
                "nenhum dado se perde, o banco e derivado."
            )
            _apagar(p)

    con = duckdb.connect(str(p))
    con.execute(sql)
    con.execute("DELETE FROM schema_versao")
    con.execute("INSERT INTO schema_versao VALUES (?, now())", [impressao])
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
    return {t: hash_tabela(con, t) for t in tabelas if t not in NAO_DETERMINISTICAS}


def registrar_execucao(
    con: duckdb.DuckDBPyConnection, etapa: str, linhas: int, hash_conteudo: str | None = None,
    observacao: str | None = None,
) -> None:
    con.execute(
        "INSERT INTO execucao VALUES (now(), ?, ?, ?, ?)",
        [etapa, linhas, hash_conteudo, observacao],
    )
