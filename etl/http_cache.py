"""Download idempotente com cache local e revalidacao condicional.

Idempotencia (criterio de aceite 1): rodar o pipeline duas vezes nao muda
resultado. Aqui isso significa que `baixar()` so escreve em disco quando o
servidor devolve 200 com corpo novo; 304 mantem o arquivo e o sha256 antigos.
O arquivo so e substituido apos gravacao completa em `.part` e rename atomico,
para que uma queda no meio nao deixe um bruto truncado no cache.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from etl import provenance
from etl.config import HTTP_RETRIES, HTTP_TIMEOUT, RAW, USER_AGENT


class DownloadError(RuntimeError):
    pass


def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _destino(url: str, subdir: str) -> Path:
    nome = Path(urlparse(url).path).name or "index.html"
    d = RAW / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d / nome


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def baixar(url: str, subdir: str, *, forcar: bool = False,
           silencioso: bool = False) -> provenance.Fonte:
    """Baixa `url` para `data/raw/<subdir>/`, revalidando pelo manifesto.

    Devolve a `Fonte` com sha256 e cabecalhos de validacao. Nao levanta em
    caso de 304: devolve a Fonte ja registrada.

    Imprime o progresso: um download de dez anos de COTAHIST leva minutos, e
    silencio nesse intervalo e indistinguivel de travamento.
    """
    destino = _destino(url, subdir)
    anterior = provenance.consultar(url)
    nome = destino.name
    inicio = time.monotonic()

    headers: dict[str, str] = {}
    if anterior and destino.exists() and not forcar:
        if anterior.etag:
            headers["If-None-Match"] = anterior.etag
        if anterior.last_modified:
            headers["If-Modified-Since"] = anterior.last_modified

    ultimo_erro: Exception | None = None
    for tentativa in range(HTTP_RETRIES):
        try:
            with _sessao() as s:
                r = s.get(url, headers=headers, timeout=HTTP_TIMEOUT, stream=True)
                if r.status_code == 304:
                    assert anterior is not None
                    if not silencioso:
                        print(f"      {nome}: em cache, nao mudou", flush=True)
                    return anterior
                if r.status_code != 200:
                    raise DownloadError(f"HTTP {r.status_code} em {url}")

                total = int(r.headers.get("Content-Length") or 0)
                if not silencioso:
                    tamanho = f" ({_mb(total)})" if total else ""
                    print(f"      baixando {nome}{tamanho}...", flush=True)

                parcial = destino.with_suffix(destino.suffix + ".part")
                recebido = 0
                proximo_aviso = 8 << 20  # avisa a cada 8 MB em arquivo grande
                with open(parcial, "wb") as fh:
                    for bloco in r.iter_content(chunk_size=1 << 20):
                        if not bloco:
                            continue
                        fh.write(bloco)
                        recebido += len(bloco)
                        if not silencioso and recebido >= proximo_aviso:
                            pct = f" ({recebido * 100 // total}%)" if total else ""
                            print(f"        {_mb(recebido)}{pct}", flush=True)
                            proximo_aviso += 8 << 20
                parcial.replace(destino)
                if not silencioso:
                    seg = time.monotonic() - inicio
                    print(f"      {nome}: {_mb(recebido)} em {seg:.0f}s", flush=True)

                fonte = provenance.Fonte(
                    url=url,
                    path=str(destino),
                    sha256=provenance.sha256_arquivo(destino),
                    bytes=destino.stat().st_size,
                    baixado_em=provenance.agora(),
                    etag=r.headers.get("ETag"),
                    last_modified=r.headers.get("Last-Modified"),
                )
                provenance.registrar(fonte)
                return fonte
        except (requests.RequestException, DownloadError) as exc:
            ultimo_erro = exc
            if tentativa < HTTP_RETRIES - 1:
                time.sleep(2 ** (tentativa + 1))

    raise DownloadError(f"falha ao baixar {url}: {ultimo_erro}")


_HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def listar_diretorio(url: str, *, extensao: str | None = None) -> list[str]:
    """Lista os arquivos de um diretorio de indice (Apache autoindex da CVM).

    O projeto nunca monta nome de arquivo por conta propria: o diretorio e
    listado e os nomes reais sao usados. Devolve URLs absolutas ordenadas.
    """
    with _sessao() as s:
        r = s.get(url, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise DownloadError(f"HTTP {r.status_code} ao listar {url}")

    achados: list[str] = []
    for href in _HREF.findall(r.text):
        if href.startswith("?") or href.startswith("#") or href in ("../", "/"):
            continue
        absoluta = urljoin(url, href)
        if not absoluta.startswith(url):
            continue  # link para fora do diretorio (rodape, pagina-pai)
        if extensao and not absoluta.lower().endswith(extensao.lower()):
            continue
        achados.append(absoluta)
    return sorted(set(achados))
