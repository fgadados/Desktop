"""Cadastro de companhias abertas (CVM/CAD). Traz CNPJ, CD_CVM e setor."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache, provenance
from etl.config import CVM_ENCODING, CVM_SEP
from etl.contracts import CAD_CIA


def extrair() -> Path:
    urls = [
        u
        for u in cvm_common.listar_pacotes("CAD", extensao=".csv")
        if "cad_cia_aberta" in u.lower()
    ]
    if not urls:
        raise FileNotFoundError("cad_cia_aberta.csv nao encontrado no diretorio CAD da CVM")

    quadros = []
    for url in urls:
        fonte = http_cache.baixar(url, subdir="cvm/cad")
        bruto = Path(fonte.path).read_bytes()
        df = pd.read_csv(
            Path(fonte.path),
            sep=CVM_SEP,
            encoding=CVM_ENCODING,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
        )
        # Nao-estrito: o cadastro carrega dezenas de campos de endereco que a
        # CVM altera com frequencia e que nao usamos.
        CAD_CIA.validar(df.columns, strict=False)
        cvm_common._conferir_linhas(bruto, len(df), Path(fonte.path).name)
        quadros.append(
            provenance.anotar_origem(
                df, archive=Path(fonte.path).name, file=Path(fonte.path).name,
                sha256=fonte.sha256, primeira_linha=2,
            )
        )

    return cvm_common.salvar_parquet(
        pd.concat(quadros, ignore_index=True), "cvm/cadastro.parquet"
    )
