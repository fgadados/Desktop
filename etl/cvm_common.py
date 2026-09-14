"""Leitura dos pacotes ZIP da CVM com rastreabilidade linha a linha."""

from __future__ import annotations

import csv
import io
import re
import sys
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


def numeros_de_linha(bruto: bytes, n_registros: int, nome: str) -> list[int]:
    """Linha fisica onde cada registro comeca, 1-based, cabecalho incluso.

    Existe para que `src_line` nunca minta: `sed -n '{src_line}p' arquivo.csv`
    tem que devolver o registro exibido na interface.

    Caminho rapido -- o arquivo tem exatamente `n_registros + 1` linhas
    fisicas, logo nenhum campo contem quebra de linha e a numeracao e
    sequencial a partir da linha 2.

    Caminho lento -- ha quebra de linha dentro de campo. Isso acontece de
    verdade: `itr_cia_aberta_DFC_MI_ind_2019.csv` e os arquivos do IPE trazem
    texto multilinha entre aspas. Aqui o `csv.reader` da biblioteca padrao,
    que entende aspas, informa a posicao fisica de cada registro. Mais lento,
    mas so roda nos arquivos que precisam.

    Se nem assim a contagem casar com o que o pandas leu, levanta: melhor
    parar do que apontar para a linha errada.
    """
    fisicas = bruto.count(b"\n")
    if bruto and not bruto.endswith(b"\n"):
        fisicas += 1

    if fisicas == n_registros + 1:
        return list(range(2, 2 + n_registros))

    texto = io.StringIO(bruto.decode(CVM_ENCODING, errors="replace"), newline="")
    leitor = csv.reader(texto, delimiter=CVM_SEP)
    inicios: list[int] = []
    fim_anterior = 0
    for _ in leitor:
        inicios.append(fim_anterior + 1)
        fim_anterior = leitor.line_num
    inicios = inicios[1:]  # descarta o cabecalho

    if len(inicios) != n_registros:
        raise RastreabilidadeError(
            f"{nome}: o leitor de CSV encontrou {len(inicios)} registros e o "
            f"pandas {n_registros}. Os dois discordam sobre onde cada registro "
            "comeca, entao src_line nao pode ser garantido."
        )
    return inicios


def _conferir_linhas(bruto: bytes, n_registros: int, nome: str) -> list[int]:
    """Compatibilidade: devolve a numeracao em vez de so conferir."""
    return numeros_de_linha(bruto, n_registros, nome)


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
    linhas = numeros_de_linha(bruto, len(df), nome_interno)

    return provenance.anotar_origem(
        df,
        archive=zip_path.name,
        file=nome_interno,
        sha256=sha256,
        linhas=linhas,
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
