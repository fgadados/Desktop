"""Leitura dos pacotes ZIP da CVM com rastreabilidade linha a linha."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from etl import http_cache, provenance
from etl.config import CVM_DIRS, CVM_ENCODING, CVM_SEP, PARQUET
from etl.contracts import Contrato


class RastreabilidadeError(RuntimeError):
    """Nao foi possivel garantir a correspondencia 1 registro <-> 1 linha."""


def listar_pacotes(doc: str, *, extensao: str = ".zip") -> list[str]:
    """Lista as URLs reais do diretorio DADOS do documento (DFP, ITR, ...)."""
    dados_url, _ = CVM_DIRS[doc]
    return http_cache.listar_diretorio(dados_url, extensao=extensao)


def ano_do_pacote(url: str) -> int | None:
    m = re.search(r"(\d{4})\.zip$", url, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _conferir_linhas(bruto: bytes, n_registros: int, nome: str) -> None:
    """Falha se o CSV tiver quebra de linha dentro de campo.

    Sem essa garantia, `src_line` seria uma mentira e o requisito de
    rastreabilidade cairia. Preferimos parar a exibir linha errada.
    """
    texto_linhas = bruto.count(b"\n")
    if bruto and not bruto.endswith(b"\n"):
        texto_linhas += 1
    esperado = n_registros + 1  # cabecalho
    if texto_linhas != esperado:
        raise RastreabilidadeError(
            f"{nome}: {texto_linhas} linhas fisicas para {n_registros} registros "
            f"(esperado {esperado}). Ha quebra de linha dentro de campo; "
            "src_line nao pode ser garantido."
        )


def ler_csv_do_zip(
    zip_path: Path,
    nome_interno: str,
    contrato: Contrato,
    sha256: str,
    *,
    strict: bool = True,
) -> pd.DataFrame:
    """Le um CSV de dentro do ZIP, valida o contrato e anota proveniencia."""
    with zipfile.ZipFile(zip_path) as z:
        bruto = z.read(nome_interno)

    df = pd.read_csv(
        io.BytesIO(bruto),
        sep=CVM_SEP,
        encoding=CVM_ENCODING,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    contrato.validar(df.columns, strict=strict)
    _conferir_linhas(bruto, len(df), nome_interno)

    return provenance.anotar_origem(
        df,
        archive=zip_path.name,
        file=nome_interno,
        sha256=sha256,
        primeira_linha=2,
    )


def nomes_no_zip(zip_path: Path, padrao: str | None = None) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv")]
    if padrao:
        rx = re.compile(padrao, re.IGNORECASE)
        nomes = [n for n in nomes if rx.search(n)]
    return sorted(nomes)


def contrato_da_fonte(doc: str) -> dict[str, list[str]]:
    """Le o diretorio META da CVM e extrai a lista de campos publicada.

    Usado apenas por `tests/contract/` (marcados `live`): e a unica coisa que
    autoriza marcar um contrato como verificado. Devolve
    `{nome_do_arquivo_meta: [campos]}`.
    """
    _, meta_url = CVM_DIRS[doc]
    arquivos = http_cache.listar_diretorio(meta_url, extensao=".txt")
    campos: dict[str, list[str]] = {}
    for url in arquivos:
        fonte = http_cache.baixar(url, subdir=f"cvm/{doc.lower()}/meta")
        texto = Path(fonte.path).read_text(encoding=CVM_ENCODING, errors="replace")
        # O META da CVM descreve um campo por bloco, iniciado por "Campo: NOME"
        # ou por uma linha "NOME: descricao" em caixa alta.
        nomes = re.findall(r"^\s*(?:Campo\s*:\s*)?([A-Z][A-Z0-9_]{2,})\s*(?:[:\-]|$)", texto, re.MULTILINE)
        if nomes:
            campos[Path(url).name] = list(dict.fromkeys(nomes))
    return campos


def salvar_parquet(df: pd.DataFrame, caminho_relativo: str) -> Path:
    """Grava bruto tabulado. Sobrescreve: a saida e funcao pura da entrada."""
    destino = PARQUET / caminho_relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(destino, index=False)
    return destino
