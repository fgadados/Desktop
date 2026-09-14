"""FCA -- Formulario Cadastral. Tabela `valor_mobiliario`.

Esta e a fonte oficial do vinculo CNPJ -> codigo de negociacao. Os arquivos
da CVM nunca trazem ticker nas demonstracoes; a chave la e o CNPJ. O FCA e o
unico documento da propria CVM em que a companhia declara o ticker sob o qual
seus valores mobiliarios sao negociados (coluna `Codigo_Negociacao`).

Usar o FCA em vez de raspar um site de terceiro atende a restricao do projeto
e ainda deixa o vinculo datado (`Data_Inicio_Negociacao` / `Data_Fim_...`),
o que importa para ticker reaproveitado apos mudanca de razao social.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache
from etl.contracts import FCA_VALOR_MOBILIARIO

_PADRAO_VM = re.compile(r"fca_cia_aberta_valor_mobiliario_\d{4}\.csv$", re.IGNORECASE)


def extrair(anos: list[int] | None = None) -> Path:
    pacotes = cvm_common.listar_pacotes("FCA")
    if anos is not None:
        pacotes = [u for u in pacotes if cvm_common.ano_do_pacote(u) in set(anos)]
    if not pacotes:
        raise FileNotFoundError(f"nenhum pacote FCA encontrado para anos={anos}")

    quadros = []
    for url in pacotes:
        fonte = http_cache.baixar(url, subdir="cvm/fca")
        zip_path = Path(fonte.path)
        for nome in cvm_common.nomes_no_zip(zip_path):
            if not _PADRAO_VM.search(Path(nome).name):
                continue
            df = cvm_common.ler_csv_do_zip(
                zip_path, nome, FCA_VALOR_MOBILIARIO, fonte.sha256, strict=False
            )
            df["ano_arquivo"] = cvm_common.ano_do_pacote(url)
            quadros.append(df)

    if not quadros:
        raise FileNotFoundError(
            "fca_cia_aberta_valor_mobiliario_*.csv nao encontrado nos pacotes FCA"
        )
    return cvm_common.salvar_parquet(
        pd.concat(quadros, ignore_index=True), "cvm/fca_valor_mobiliario.parquet"
    )
