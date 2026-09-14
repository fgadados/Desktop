"""Acesso ao DuckDB para a interface. Somente leitura."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from etl.config import DUCKDB_PATH


@st.cache_resource
def conexao(caminho: str | None = None) -> duckdb.DuckDBPyConnection:
    p = Path(caminho or DUCKDB_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"banco nao encontrado em {p}. Rode 'python run.py tudo' antes de abrir a interface."
        )
    return duckdb.connect(str(p), read_only=True)


@st.cache_data(ttl=300)
def consultar(sql: str, params: tuple = ()) -> pd.DataFrame:
    return conexao().execute(sql, list(params)).fetchdf()


def empresas() -> pd.DataFrame:
    return consultar(
        "SELECT e.*, d.ticker FROM empresa e "
        "LEFT JOIN depara_ticker d ON d.cnpj = e.cnpj "
        "ORDER BY e.denom_social, d.ticker"
    )


def tickers() -> list[str]:
    df = consultar("SELECT DISTINCT ticker FROM preco_diario ORDER BY ticker")
    return df["ticker"].tolist()


def indicadores(cnpj: str) -> pd.DataFrame:
    return consultar(
        "SELECT * FROM indicador WHERE entidade = ? ORDER BY periodo DESC, indicador",
        (cnpj,),
    )


def entradas(cnpj: str, periodo: str, indicador: str) -> pd.DataFrame:
    return consultar(
        "SELECT ordem, rotulo, cd_conta, ds_conta, valor, demonstrativo, base, "
        "versao, ordem_exerc, dt_refer, referencia, src_sha256 "
        "FROM indicador_entrada "
        "WHERE entidade = ? AND periodo = ? AND indicador = ? ORDER BY ordem",
        (cnpj, periodo, indicador),
    )


def precos(ticker: str) -> pd.DataFrame:
    return consultar(
        "SELECT * FROM preco_diario WHERE ticker = ? ORDER BY data", (ticker,)
    )


def cobertura(ticker: str) -> pd.DataFrame:
    return consultar("SELECT * FROM cobertura_ajuste WHERE ticker = ?", (ticker,))


def identidade(cnpj: str) -> pd.DataFrame:
    return consultar(
        "SELECT * FROM teste_identidade WHERE cnpj = ? ORDER BY periodo DESC", (cnpj,)
    )


def fatos(cnpj: str, periodo: str) -> pd.DataFrame:
    return consultar(
        "SELECT demonstrativo, cd_conta, ds_conta, valor, origem_periodo, versao, "
        "ordem_exerc, dt_refer, src_file, src_line, src_derivacao "
        "FROM fato_contabil WHERE cnpj = ? AND periodo = ? "
        "ORDER BY demonstrativo, cd_conta",
        (cnpj, periodo),
    )
