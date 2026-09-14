"""BCB / SGS -- Selic, IPCA e cambio.

Usados para custo de oportunidade (Selic) e deflacionamento (IPCA). O SGS
devolve JSON com data em dd/MM/yyyy e valor em texto com ponto decimal.

Nao ha interpolacao: dia sem publicacao fica sem linha. Quem consome
(`transform.risk`, `transform.indicators`) trata a ausencia explicitamente.

Duas particularidades da API, encontradas ao rodar de verdade (12/09/2026)
-------------------------------------------------------------------------
1. O gateway responde **HTTP 406** a User-Agent que nao pareca navegador. E
   resposta do WAF, nao do servico -- a URL funciona no browser e falha no
   cliente. Por isso as requisicoes daqui mandam cabecalhos convencionais.

2. Janela longa em serie diaria estoura o limite de resposta. O intervalo e
   quebrado em pedacos de ate 5 anos e depois concatenado.

Esta fonte e AUXILIAR: sem ela o sistema perde deflacionamento e comparacao
com o CDI, mas os fundamentos e os precos seguem validos. `run.py extrair`
trata a falha dela como parcial, nao como fatal.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from etl import cvm_common, http_cache, provenance
from etl.config import BCB_SERIES, BCB_SGS_URL

# Janela maxima por requisicao: o SGS limita o tamanho da resposta, e uma
# serie diaria de dez anos passa do limite.
ANOS_POR_REQUISICAO = 5

# O WAF do BCB recusa User-Agent incomum com 406. Cabecalhos de navegador
# comum resolvem. Nao ha nada de evasivo aqui: e o mesmo pedido, com
# cabecalhos que o gateway aceita.
CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def _url(codigo: int, ini: date, fim: date) -> str:
    return BCB_SGS_URL.format(
        codigo=codigo, ini=ini.strftime("%d/%m/%Y"), fim=fim.strftime("%d/%m/%Y")
    )


def janelas(ini: date, fim: date, anos: int = ANOS_POR_REQUISICAO):
    """Quebra [ini, fim] em pedacos contiguos de no maximo `anos` anos."""
    if ini > fim:
        return
    atual = ini
    while atual <= fim:
        try:
            limite = date(atual.year + anos, atual.month, atual.day) - timedelta(days=1)
        except ValueError:  # 29/02 em ano nao bissexto
            limite = date(atual.year + anos, atual.month, 28)
        limite = min(limite, fim)
        yield atual, limite
        atual = limite + timedelta(days=1)


def extrair(ini: date | None = None, fim: date | None = None) -> Path:
    ini = ini or date(2010, 1, 1)
    fim = fim or date.today()

    quadros = []
    for nome, codigo in BCB_SERIES.items():
        registros: list[dict] = []
        primeira_fonte = None
        for j_ini, j_fim in janelas(ini, fim):
            fonte = http_cache.baixar(
                _url(codigo, j_ini, j_fim), subdir="bcb", headers=CABECALHOS
            )
            primeira_fonte = primeira_fonte or fonte
            texto = Path(fonte.path).read_text(encoding="utf-8").strip()
            if not texto:
                continue
            registros.extend(json.loads(texto))

        if not registros or primeira_fonte is None:
            print(f"      SGS {codigo} ({nome}): sem dados no periodo", flush=True)
            continue

        df = pd.DataFrame(registros)
        esperadas = {"data", "valor"}
        if not esperadas.issubset(df.columns):
            raise ValueError(
                f"SGS {codigo} ({nome}): resposta com colunas {list(df.columns)}, "
                f"esperado ao menos {sorted(esperadas)}"
            )

        df = provenance.anotar_origem(
            df,
            archive=Path(primeira_fonte.path).name,
            file=Path(primeira_fonte.path).name,
            sha256=primeira_fonte.sha256,
            primeira_linha=1,  # JSON: a "linha" e o indice do elemento no array
        )
        df["serie"] = nome
        df["codigo_sgs"] = codigo
        df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="raise")
        df["valor"] = pd.to_numeric(df["valor"], errors="raise")
        # Janelas contiguas nao deveriam se sobrepor, mas duplicata na
        # fronteira nao pode virar linha repetida na serie.
        df = df.drop_duplicates(subset=["serie", "data"], keep="first")
        quadros.append(df)
        print(f"      SGS {codigo} ({nome}): {len(df)} observacoes", flush=True)

    if not quadros:
        raise RuntimeError("SGS nao devolveu nenhuma serie")
    return cvm_common.salvar_parquet(
        pd.concat(quadros, ignore_index=True), "bcb/sgs.parquet"
    )
