"""BCB / SGS -- Selic, IPCA e cambio.

Usados para custo de oportunidade (Selic) e deflacionamento (IPCA). O SGS
devolve JSON com data em dd/MM/yyyy e valor em texto com ponto decimal.

Nao ha interpolacao: dia sem publicacao fica sem linha. Quem consome
(`transform.risk`, `transform.indicators`) trata a ausencia explicitamente.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache, provenance
from etl.config import BCB_SERIES, BCB_SGS_URL


def _url(codigo: int, ini: date, fim: date) -> str:
    return BCB_SGS_URL.format(
        codigo=codigo, ini=ini.strftime("%d/%m/%Y"), fim=fim.strftime("%d/%m/%Y")
    )


def extrair(ini: date | None = None, fim: date | None = None) -> Path:
    ini = ini or date(2010, 1, 1)
    fim = fim or date.today()

    quadros = []
    for nome, codigo in BCB_SERIES.items():
        fonte = http_cache.baixar(_url(codigo, ini, fim), subdir="bcb")
        bruto = json.loads(Path(fonte.path).read_text(encoding="utf-8"))
        df = pd.DataFrame(bruto)
        if df.empty:
            continue
        esperadas = {"data", "valor"}
        if not esperadas.issubset(df.columns):
            raise ValueError(
                f"SGS {codigo} ({nome}): resposta com colunas {list(df.columns)}, "
                f"esperado ao menos {sorted(esperadas)}"
            )
        df = provenance.anotar_origem(
            df,
            archive=Path(fonte.path).name,
            file=Path(fonte.path).name,
            sha256=fonte.sha256,
            primeira_linha=1,  # JSON: a "linha" e o indice do elemento no array
        )
        df["serie"] = nome
        df["codigo_sgs"] = codigo
        df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="raise")
        df["valor"] = pd.to_numeric(df["valor"], errors="raise")
        quadros.append(df)

    if not quadros:
        raise RuntimeError("SGS nao devolveu nenhuma serie")
    return cvm_common.salvar_parquet(
        pd.concat(quadros, ignore_index=True), "bcb/sgs.parquet"
    )
