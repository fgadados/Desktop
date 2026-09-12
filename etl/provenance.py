"""Proveniencia: todo byte baixado e toda linha lida carregam origem.

O sistema inteiro depende disto. Um numero na interface so e aceitavel se for
possivel voltar ate `(arquivo, linha)` no arquivo original.

Duas estruturas:

* `Fonte` -- um arquivo baixado: URL, sha256, tamanho, timestamp e cabecalhos
  de validacao HTTP. Gravada em `data/raw/_manifest.json`.
* As colunas `src_*` -- injetadas em todo DataFrame bruto, identificando para
  cada linha o arquivo de origem e o numero da linha dentro dele.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etl.config import RAW

MANIFEST = RAW / "_manifest.json"

# Colunas de proveniencia injetadas em todo bruto. `src_line` e 1-based e conta
# a partir do inicio fisico do arquivo, cabecalho incluso -- de modo que
# `sed -n '{src_line}p' arquivo.csv` devolve exatamente a linha exibida.
SRC_COLS = ("src_archive", "src_file", "src_line", "src_sha256")


@dataclass
class Fonte:
    """Um arquivo baixado de uma fonte oficial."""

    url: str
    path: str
    sha256: str
    bytes: int
    baixado_em: str
    etag: str | None = None
    last_modified: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def nome(self) -> str:
        return Path(self.path).name


def sha256_arquivo(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloco in iter(lambda: fh.read(chunk), b""):
            h.update(bloco)
    return h.hexdigest()


def _carregar() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def registrar(fonte: Fonte) -> None:
    """Grava/atualiza a entrada do manifesto. Idempotente por URL."""
    man = _carregar()
    man[fonte.url] = asdict(fonte)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(man, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def consultar(url: str) -> Fonte | None:
    entrada = _carregar().get(url)
    if entrada is None:
        return None
    return Fonte(**entrada)


def manifesto() -> list[Fonte]:
    return [Fonte(**v) for v in _carregar().values()]


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def anotar_origem(df, *, archive: str, file: str, sha256: str, primeira_linha: int = 2):
    """Injeta as colunas `src_*` num DataFrame recem-lido.

    `primeira_linha` e o numero fisico da primeira linha de dados no arquivo:
    2 para um CSV com uma linha de cabecalho, 1 para um arquivo posicional sem
    cabecalho. A ordem original das linhas precisa estar preservada -- por isso
    a anotacao acontece imediatamente apos a leitura, antes de qualquer filtro.
    """
    df = df.copy()
    df["src_archive"] = archive
    df["src_file"] = file
    df["src_line"] = range(primeira_linha, primeira_linha + len(df))
    df["src_sha256"] = sha256
    return df
