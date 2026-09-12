"""COTAHIST da B3 -- series historicas de cotacoes, layout posicional.

Registro de 245 bytes. O parser e por posicao de campo, conforme o layout
publicado pela B3 ("Series Historicas -- Layout do arquivo"). Nenhuma coluna e
inferida por separador.

Validacoes obrigatorias na leitura (qualquer uma falha -> erro, nao aviso):

* todo registro tem exatamente 245 bytes;
* o arquivo comeca com header (TIPREG=00) e termina com trailer (TIPREG=99);
* a contagem de registros do trailer bate com o numero de registros lidos.

Precos vem como inteiro com 2 casas implicitas (formato (11)V99) e sao
divididos por 100. FATCOT ("fator de cotacao") diz se o preco se refere a 1
ou a 1000 acoes; e preservado cru e tratado na camada de ajuste, nunca aqui.

O arquivo NAO e ajustado por evento corporativo. O ajuste vive em
`transform/prices.py` e depende de `etl/b3_eventos.py`.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache, provenance
from etl.config import COTAHIST_ANO_INICIAL, COTAHIST_URL

TAMANHO_REGISTRO = 245


@dataclass(frozen=True)
class Campo:
    nome: str
    inicio: int  # 1-based, inclusivo (como no layout da B3)
    fim: int  # 1-based, inclusivo
    tipo: str  # "N" inteiro, "X" texto, "V99"/"V06" decimal implicito

    @property
    def fatia(self) -> slice:
        return slice(self.inicio - 1, self.fim)


# Layout oficial do registro tipo 01 (cotacoes), 245 bytes.
LAYOUT: tuple[Campo, ...] = (
    Campo("TIPREG", 1, 2, "N"),
    Campo("DATA", 3, 10, "X"),
    Campo("CODBDI", 11, 12, "X"),
    Campo("CODNEG", 13, 24, "X"),
    Campo("TPMERC", 25, 27, "X"),
    Campo("NOMRES", 28, 39, "X"),
    Campo("ESPECI", 40, 49, "X"),
    Campo("PRAZOT", 50, 52, "X"),
    Campo("MODREF", 53, 56, "X"),
    Campo("PREABE", 57, 69, "V99"),
    Campo("PREMAX", 70, 82, "V99"),
    Campo("PREMIN", 83, 95, "V99"),
    Campo("PREMED", 96, 108, "V99"),
    Campo("PREULT", 109, 121, "V99"),
    Campo("PREOFC", 122, 134, "V99"),
    Campo("PREOFV", 135, 147, "V99"),
    Campo("TOTNEG", 148, 152, "N"),
    Campo("QUATOT", 153, 170, "N"),
    Campo("VOLTOT", 171, 188, "V99"),
    Campo("PREEXE", 189, 201, "V99"),
    Campo("INDOPC", 202, 202, "N"),
    Campo("DATVEN", 203, 210, "X"),
    Campo("FATCOT", 211, 217, "N"),
    Campo("PTOEXE", 218, 230, "V06"),
    Campo("CODISI", 231, 242, "X"),
    Campo("DISMES", 243, 245, "N"),
)

assert LAYOUT[-1].fim == TAMANHO_REGISTRO, "layout COTAHIST nao soma 245 bytes"

# CODBDI 02 = lote padrao; TPMERC 010 = mercado a vista. E o subconjunto usado
# para serie de preco de acao. Opcoes, termo e fracionario ficam de fora.
CODBDI_LOTE_PADRAO = "02"
TPMERC_VISTA = "010"

_DIVISOR = {"V99": 100.0, "V06": 1_000_000.0}


class CotahistError(RuntimeError):
    pass


def _parse_linhas(linhas: list[bytes], origem: str) -> pd.DataFrame:
    registros = []
    for i, linha in enumerate(linhas, start=1):
        if len(linha) != TAMANHO_REGISTRO:
            raise CotahistError(
                f"{origem}: linha {i} tem {len(linha)} bytes, esperado {TAMANHO_REGISTRO}"
            )
        registros.append(
            {c.nome: linha[c.fatia].decode("latin-1").strip() for c in LAYOUT}
        )
    return pd.DataFrame(registros, dtype="object")


def parse_bytes(conteudo: bytes, origem: str, sha256: str) -> pd.DataFrame:
    linhas = [l for l in conteudo.split(b"\n") if l.strip(b"\r\x00")]
    linhas = [l.rstrip(b"\r") for l in linhas]
    if not linhas:
        raise CotahistError(f"{origem}: arquivo vazio")

    header, *corpo = linhas
    if header[:2] != b"00":
        raise CotahistError(f"{origem}: primeiro registro nao e header (TIPREG=00)")
    if not corpo or corpo[-1][:2] != b"99":
        raise CotahistError(f"{origem}: ultimo registro nao e trailer (TIPREG=99)")
    trailer, corpo = corpo[-1], corpo[:-1]

    # Trailer: posicoes 32-42 trazem o total de registros do arquivo.
    declarado = trailer[31:42].decode("latin-1").strip()
    if declarado.isdigit():
        # O total do trailer inclui header e trailer.
        esperado = int(declarado)
        obtido = len(corpo) + 2
        if esperado != obtido:
            raise CotahistError(
                f"{origem}: trailer declara {esperado} registros, lidos {obtido}"
            )

    df = _parse_linhas(corpo, origem)

    # `src_line` = 2 porque a linha 1 do arquivo e o header.
    df = provenance.anotar_origem(
        df, archive=origem, file=origem, sha256=sha256, primeira_linha=2
    )

    for campo in LAYOUT:
        if campo.tipo in _DIVISOR:
            df[campo.nome] = (
                pd.to_numeric(df[campo.nome], errors="raise") / _DIVISOR[campo.tipo]
            )
        elif campo.tipo == "N":
            df[campo.nome] = pd.to_numeric(df[campo.nome], errors="raise")

    df["data"] = pd.to_datetime(df["DATA"], format="%Y%m%d", errors="raise")
    return df


def parse_arquivo(path: Path, sha256: str) -> pd.DataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            nomes = [n for n in z.namelist() if not n.endswith("/")]
            if len(nomes) != 1:
                raise CotahistError(f"{path.name}: esperado 1 arquivo no ZIP, achei {nomes}")
            return parse_bytes(z.read(nomes[0]), nomes[0], sha256)
    return parse_bytes(path.read_bytes(), path.name, sha256)


def extrair(anos: list[int] | None = None) -> Path:
    """Baixa e parseia os arquivos anuais.

    Ano ausente na B3 -- tipicamente o corrente, antes do primeiro pregao do
    ano -- e reportado e pulado, nao derruba a extracao inteira. Se nenhum
    ano vier, levanta: aqui nao ha o que reportar como parcial.
    """
    from datetime import date

    if anos is None:
        anos = list(range(COTAHIST_ANO_INICIAL, date.today().year + 1))

    quadros, ausentes = [], []
    for ano in anos:
        try:
            fonte = http_cache.baixar(COTAHIST_URL.format(ano=ano), subdir="b3/cotahist")
        except http_cache.DownloadError as exc:
            ausentes.append((ano, str(exc)))
            print(f"      COTAHIST {ano}: indisponivel na B3 ({exc}). "
                  "Os pregoes deste ano ficam FALTANDO.", flush=True)
            continue
        df = parse_arquivo(Path(fonte.path), fonte.sha256)
        df["ano_arquivo"] = ano
        quadros.append(df)

    if not quadros:
        raise FileNotFoundError(
            f"nenhum arquivo COTAHIST obtido para {anos}. Ausentes: {ausentes}"
        )
    if ausentes:
        print(f"      {len(ausentes)} ano(s) sem COTAHIST: "
              f"{[a for a, _ in ausentes]}", flush=True)

    todos = pd.concat(quadros, ignore_index=True)
    return cvm_common.salvar_parquet(todos, "b3/cotahist.parquet")


def somente_acoes_a_vista(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra lote padrao + mercado a vista. Nao altera valores."""
    return df[
        (df["CODBDI"] == CODBDI_LOTE_PADRAO) & (df["TPMERC"] == TPMERC_VISTA)
    ].copy()
