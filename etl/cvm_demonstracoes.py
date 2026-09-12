"""Extracao de DFP (anual) e ITR (trimestral).

Saida: uma tabela longa de fatos contabeis, um registro por conta/periodo, com
as colunas cruas da CVM intactas mais `src_*` e mais os campos derivados do
proprio nome do arquivo (`demonstrativo`, `base`, `doc`, `ano_arquivo`).

Nada e filtrado, deduplicado ou convertido aqui. VERSAO, ORDEM_EXERC e a
convivencia CON/IND chegam inteiras na camada `transform`, que e onde as
regras 2, 3 e 4 sao aplicadas e testadas.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache
from etl.contracts import CABECALHO, CONTRATOS_DEMONSTRATIVO

# dfp_cia_aberta_BPA_con_2023.csv -> (dfp, BPA, con, 2023)
# itr_cia_aberta_2023.csv         -> (itr, None, None, 2023)  [cabecalho]
_PADRAO = re.compile(
    r"^(?P<doc>dfp|itr)_cia_aberta"
    r"(?:_(?P<dem>BPA|BPP|DRE|DRA|DFC_MD|DFC_MI|DVA|DMPL)_(?P<base>con|ind))?"
    r"_(?P<ano>\d{4})\.csv$",
    re.IGNORECASE,
)


def _decompor(nome_interno: str) -> dict | None:
    m = _PADRAO.match(Path(nome_interno).name)
    if not m:
        return None
    g = m.groupdict()
    return {
        "doc": g["doc"].upper(),
        "demonstrativo": (g["dem"] or "").upper() or None,
        "base": (g["base"] or "").upper() or None,  # CON | IND | None
        "ano_arquivo": int(g["ano"]),
    }


def extrair_ano(doc: str, url_pacote: str) -> dict[str, pd.DataFrame]:
    """Baixa um pacote anual e devolve {'fatos': df, 'cabecalho': df}."""
    fonte = http_cache.baixar(url_pacote, subdir=f"cvm/{doc.lower()}")
    zip_path = Path(fonte.path)

    fatos: list[pd.DataFrame] = []
    cabecalhos: list[pd.DataFrame] = []

    for nome in cvm_common.nomes_no_zip(zip_path):
        meta = _decompor(nome)
        if meta is None:
            # Arquivo novo no pacote: parar, nao ignorar em silencio.
            raise cvm_common.RastreabilidadeError(
                f"{zip_path.name}: arquivo '{nome}' nao reconhecido pelo padrao "
                "de nomes da CVM. Atualize etl/cvm_demonstracoes._PADRAO."
            )

        if meta["demonstrativo"] is None:
            df = cvm_common.ler_csv_do_zip(zip_path, nome, CABECALHO, fonte.sha256)
            df["doc"] = meta["doc"]
            cabecalhos.append(df)
            continue

        contrato = CONTRATOS_DEMONSTRATIVO[meta["demonstrativo"]]
        df = cvm_common.ler_csv_do_zip(zip_path, nome, contrato, fonte.sha256)
        for k, v in meta.items():
            df[k] = v
        fatos.append(df)

    return {
        "fatos": pd.concat(fatos, ignore_index=True) if fatos else _vazio_fatos(),
        "cabecalho": pd.concat(cabecalhos, ignore_index=True) if cabecalhos else pd.DataFrame(),
    }


def _vazio_fatos() -> pd.DataFrame:
    cols = list(CONTRATOS_DEMONSTRATIVO["DRE"].colunas) + [
        "src_archive", "src_file", "src_line", "src_sha256",
        "doc", "demonstrativo", "base", "ano_arquivo",
    ]
    return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})


def extrair(doc: str, anos: list[int] | None = None) -> dict[str, Path]:
    """Extrai todos os anos disponiveis (ou os pedidos) de DFP ou ITR."""
    pacotes = cvm_common.listar_pacotes(doc)
    if anos is not None:
        pacotes = [u for u in pacotes if cvm_common.ano_do_pacote(u) in set(anos)]
    if not pacotes:
        raise FileNotFoundError(
            f"nenhum pacote {doc} encontrado no diretorio da CVM para anos={anos}"
        )

    fatos, cabecalhos = [], []
    for url in pacotes:
        r = extrair_ano(doc, url)
        fatos.append(r["fatos"])
        if not r["cabecalho"].empty:
            cabecalhos.append(r["cabecalho"])

    saidas = {
        "fatos": cvm_common.salvar_parquet(
            pd.concat(fatos, ignore_index=True), f"cvm/{doc.lower()}_fatos.parquet"
        )
    }
    if cabecalhos:
        saidas["cabecalho"] = cvm_common.salvar_parquet(
            pd.concat(cabecalhos, ignore_index=True), f"cvm/{doc.lower()}_cabecalho.parquet"
        )
    return saidas
