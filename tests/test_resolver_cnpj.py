"""Ticker termina em dígito. `ITUB4` não é o CNPJ número 4.

O caso real (13/09/2026). `run.py bruto ITUB4` respondeu:

    CNPJ 4 nao aparece em nenhum arquivo bruto da CVM baixado.

O código resolvia assim:

    cnpj = re.sub(r"\\D", "", alvo)     # "ITUB4" -> "4"
    if not cnpj:                        # "4" e nao-vazio
        ...consultar o de-para...       # nunca chegava aqui

Quase todo ticker da B3 termina em dígito — ON é 3, PN é 4, unit é 11. Então
o caminho errado não era um caso de borda, era o caso comum: `PETR4`, `WEGE3`,
`TAEE11` e `BBSE3` dariam "4", "3", "11" e "3".

A falha não foi o regex, foi o teste de validade: `if cnpj` pergunta "veio
alguma coisa?", quando a pergunta certa é "isso tem cara de CNPJ?". Um CNPJ
tem 14 dígitos, e é isso que decide agora.

`contas` já fazia na ordem certa — ticker primeiro, dígitos como recuo. Os
dois comandos passaram a usar a mesma função justamente para não divergirem
de novo.
"""

from __future__ import annotations

import pytest

import run
from tests import fixtures as fx


@pytest.fixture(autouse=True)
def sem_banco(tmp_path, monkeypatch):
    """Sem de-para disponível: só o formato decide."""
    monkeypatch.setattr("etl.config.DUCKDB_PATH", tmp_path / "nao_existe.duckdb")
    yield


@pytest.mark.parametrize("ticker", ["ITUB4", "PETR4", "WEGE3", "TAEE11", "BBSE3"])
def test_ticker_nao_vira_cnpj_pelos_digitos(ticker):
    """Era o defeito: os digitos do ticker viravam um "CNPJ" qualquer."""
    assert run._resolver_cnpj(ticker) == "", (
        f"'{ticker}' resolveu para algo sem o de-para; os digitos do ticker "
        "nao sao um CNPJ"
    )


@pytest.mark.parametrize("entrada,esperado", [
    ("60.872.504/0001-23", "60872504000123"),
    ("60872504000123", "60872504000123"),
    (" 60.872.504/0001-23 ", "60872504000123"),
])
def test_cnpj_com_14_digitos_e_aceito_em_qualquer_formato(entrada, esperado):
    assert run._resolver_cnpj(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "  ", "4", "123", "60872504000", "abc"])
def test_o_que_nao_tem_14_digitos_nao_e_cnpj(entrada):
    assert run._resolver_cnpj(entrada) == ""


def test_o_de_para_vence_o_formato(tmp_path, monkeypatch):
    """Com banco carregado, o ticker resolve pelo de-para -- que e a regra 1
    do projeto: a chave e o CNPJ, e quem faz a ponte e o FCA."""
    import pandas as pd

    from db import load

    caminho = tmp_path / "b.duckdb"
    con = load.conectar(caminho)
    load.substituir(con, "depara_ticker", pd.DataFrame([{
        "cnpj": "60872504000123", "ticker": "ITUB4",
        "valor_mobiliario": "Ações Preferenciais", "negociado_b3": True,
    }]))
    con.close()
    monkeypatch.setattr("etl.config.DUCKDB_PATH", caminho)

    assert run._resolver_cnpj("ITUB4") == "60872504000123"
    assert run._resolver_cnpj("itub4") == "60872504000123", "caixa nao importa"
    assert run._resolver_cnpj("XXXX9") == "", "ticker desconhecido nao inventa CNPJ"
