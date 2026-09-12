"""IPE -- fatos relevantes e comunicados ao mercado.

Entra no sistema como contexto datado, nunca como sinal. A interface mostra os
documentos entregues em volta de uma data; a leitura do conteudo e do usuario.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache, provenance
from etl.config import CVM_ENCODING, CVM_SEP
from etl.contracts import IPE


def extrair(anos: list[int] | None = None) -> Path:
    urls = cvm_common.listar_pacotes("IPE", extensao=".csv")
    if not urls:
        urls = cvm_common.listar_pacotes("IPE", extensao=".zip")
    if anos is not None:
        alvo = {str(a) for a in anos}
        urls = [u for u in urls if any(a in Path(u).name for a in alvo)]
    if not urls:
        raise FileNotFoundError(f"nenhum arquivo IPE encontrado para anos={anos}")

    quadros = []
    for url in urls:
        fonte = http_cache.baixar(url, subdir="cvm/ipe")
        p = Path(fonte.path)
        if p.suffix.lower() == ".zip":
            for nome in cvm_common.nomes_no_zip(p):
                quadros.append(
                    cvm_common.ler_csv_do_zip(p, nome, IPE, fonte.sha256, strict=False)
                )
        else:
            bruto = p.read_bytes()
            df = pd.read_csv(
                p, sep=CVM_SEP, encoding=CVM_ENCODING, dtype=str,
                keep_default_na=False, na_values=[""],
            )
            IPE.validar(df.columns, strict=False)
            linhas = cvm_common.numeros_de_linha(bruto, len(df), p.name)
            quadros.append(
                provenance.anotar_origem(
                    df, archive=p.name, file=p.name, sha256=fonte.sha256, linhas=linhas
                )
            )

    return cvm_common.salvar_parquet(
        pd.concat(quadros, ignore_index=True), "cvm/ipe.parquet"
    )
