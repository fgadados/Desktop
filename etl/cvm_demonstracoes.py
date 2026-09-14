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

from etl import avisos, cvm_common, http_cache
from etl.contracts import CABECALHO, COMPOSICAO_CAPITAL, CONTRATOS_DEMONSTRATIVO

# dfp_cia_aberta_BPA_con_2023.csv -> (dfp, BPA, con, 2023)
# itr_cia_aberta_2023.csv         -> (itr, None, None, 2023)  [cabecalho]
_PADRAO = re.compile(
    r"^(?P<doc>dfp|itr)_cia_aberta"
    r"(?:_(?P<dem>BPA|BPP|DRE|DRA|DFC_MD|DFC_MI|DVA|DMPL)_(?P<base>con|ind)"
    r"|_(?P<aux>composicao_capital|parecer))?"
    r"_(?P<ano>\d{4})\.csv$",
    re.IGNORECASE,
)

# Tipos de arquivo dentro do pacote, confrontados com o ZIP real da CVM
# (DFP e ITR de 2023, em 12/09/2026):
#   demonstrativo       -- BPA, BPP, DRE, DFC_MI, ... os fatos contabeis
#   cabecalho           -- um registro por documento entregue
#   composicao_capital  -- quantidade de acoes em circulacao (ver abaixo)
#   parecer             -- parecer do auditor, texto livre
TIPO_DEMONSTRATIVO = "demonstrativo"
TIPO_CABECALHO = "cabecalho"
TIPO_COMPOSICAO_CAPITAL = "composicao_capital"
TIPO_PARECER = "parecer"


def _decompor(nome_interno: str) -> dict | None:
    m = _PADRAO.match(Path(nome_interno).name)
    if not m:
        return None
    g = m.groupdict()
    aux = (g["aux"] or "").lower()
    if g["dem"]:
        tipo = TIPO_DEMONSTRATIVO
    elif aux:
        tipo = aux  # composicao_capital | parecer
    else:
        tipo = TIPO_CABECALHO
    return {
        "tipo": tipo,
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
    capital: list[pd.DataFrame] = []

    for nome in cvm_common.nomes_no_zip(zip_path):
        meta = _decompor(nome)
        if meta is None:
            # Arquivo novo no pacote: registrado e pulado. Nao e silencio --
            # fica no relatorio de avisos e na tabela `aviso` do banco. Nenhum
            # numero ja lido fica errado por causa dele; o que ha e dado
            # potencialmente faltando, e derrubar o pacote inteiro por isso
            # custa mais do que protege. Ver `etl/avisos.py`.
            avisos.avisar(
                zip_path.name, "arquivo_desconhecido",
                f"'{nome}' nao casa com o padrao de nomes da CVM e foi pulado. "
                "Se for um demonstrativo novo, declare-o em "
                "etl/cvm_demonstracoes._PADRAO para que passe a ser lido.",
            )
            continue

        if meta["tipo"] == TIPO_PARECER:
            # Parecer do auditor: texto corrido, com quebra de linha dentro de
            # campo. Nao entra no pipeline de fatos -- `_conferir_linhas`
            # falharia, e com razao: ali src_line nao seria garantido.
            continue

        if meta["tipo"] == TIPO_COMPOSICAO_CAPITAL:
            df = cvm_common.ler_csv_do_zip(
                zip_path, nome, COMPOSICAO_CAPITAL, fonte.sha256, strict=False
            )
            df["doc"] = meta["doc"]
            df["ano_arquivo"] = meta["ano_arquivo"]
            capital.append(df)
            continue

        if meta["tipo"] == TIPO_CABECALHO:
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
        "composicao_capital": (
            pd.concat(capital, ignore_index=True) if capital else pd.DataFrame()
        ),
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
        achados = {cvm_common.ano_do_pacote(u) for u in pacotes}
        pacotes = [u for u in pacotes if cvm_common.ano_do_pacote(u) in set(anos)]

        # Ano pedido que o diretorio da CVM nao tem era descartado em silencio.
        # Aconteceu em 13/09/2026: DFP e ITR de 2025 sumiram do diretorio entre
        # duas execucoes, a extracao relatou sucesso, e 4,8 milhoes de fatos --
        # um ano inteiro da carteira -- simplesmente deixaram de existir. A
        # serie ficou com um buraco que so apareceu porque alguem foi conferir
        # trimestre a trimestre.
        #
        # Pelo criterio de `etl/avisos.py` isto e AVISO, nao falha dura: o que
        # ha e dado faltando, nao numero errado. Mas nao pode ser silencio.
        faltando = sorted(set(anos) - achados)
        if faltando:
            avisos.avisar(
                f"diretorio {doc} da CVM", "ano_sem_pacote",
                f"anos pedidos que o diretorio nao lista: {faltando}. "
                f"Anos disponiveis: {sorted(a for a in achados if a)}. "
                "A serie fica SEM esses anos -- nenhum numero fica errado, mas "
                "o periodo correspondente vai faltar na interface.",
            )

    if not pacotes:
        raise FileNotFoundError(
            f"nenhum pacote {doc} encontrado no diretorio da CVM para anos={anos}"
        )

    fatos, cabecalhos, capital = [], [], []
    for url in pacotes:
        r = extrair_ano(doc, url)
        fatos.append(r["fatos"])
        if not r["cabecalho"].empty:
            cabecalhos.append(r["cabecalho"])
        if not r["composicao_capital"].empty:
            capital.append(r["composicao_capital"])

    saidas = {
        "fatos": cvm_common.salvar_parquet(
            pd.concat(fatos, ignore_index=True), f"cvm/{doc.lower()}_fatos.parquet"
        )
    }
    if cabecalhos:
        saidas["cabecalho"] = cvm_common.salvar_parquet(
            pd.concat(cabecalhos, ignore_index=True), f"cvm/{doc.lower()}_cabecalho.parquet"
        )
    if capital:
        saidas["composicao_capital"] = cvm_common.salvar_parquet(
            pd.concat(capital, ignore_index=True),
            f"cvm/{doc.lower()}_composicao_capital.parquet",
        )
    return saidas
