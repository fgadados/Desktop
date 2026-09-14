"""Aviso da extração tem que chegar à tabela `aviso` do banco.

O caso real (13/09/2026). O log de uma execução completa dizia, com 3 minutos
de intervalo:

    2 aviso(s) -- nada aqui invalida numero:
      trailer_cotahist: 2
    ...
    avisos               : 0

E a tabela `aviso` do banco saía vazia. `_REGISTRO` é global por PROCESSO, e
`run.py extrair` e `run.py transformar` são dois processos: o segundo começa
com o registro zerado.

O efeito é o modo de falha que `etl/avisos.py` existe para impedir. O critério
do módulo diz que aviso não é silêncio porque fica gravado e consultável; o
código não cumpria a própria promessa do docstring, e ninguém olhando só o
banco teria como saber que dois arquivos do COTAHIST tinham rodapé divergente.
"""

from __future__ import annotations

import json

import pytest

from db import load
from etl import avisos


@pytest.fixture(autouse=True)
def registro_limpo():
    avisos.limpar()
    yield
    avisos.limpar()


def test_aviso_da_extracao_chega_ao_quadro_da_transformacao(tmp_path):
    arquivo = tmp_path / "avisos.json"

    # --- processo 1: a extracao ---
    avisos.avisar("COTAHIST_A2026.TXT", "trailer_cotahist",
                  "rodape declara 2776182 e foram lidos 2776184", imprimir=False)
    avisos.persistir(arquivo)

    # --- processo 2: a transformacao, comecando do zero ---
    avisos.limpar()
    assert avisos.para_quadro().empty, "o registro do novo processo nasce vazio"

    n = avisos.herdar(arquivo)
    quadro = avisos.para_quadro()

    assert n == 1
    assert len(quadro) == 1
    assert quadro.loc[0, "categoria"] == "trailer_cotahist"
    assert quadro.loc[0, "origem"] == "COTAHIST_A2026.TXT"


def test_aviso_herdado_vai_para_a_tabela_do_banco(tmp_path):
    """A promessa do docstring, verificada no banco e não no quadro."""
    arquivo = tmp_path / "avisos.json"
    avisos.avisar("COTAHIST_A2025.TXT", "trailer_cotahist", "diferenca de -2",
                  imprimir=False)
    avisos.persistir(arquivo)
    avisos.limpar()
    avisos.herdar(arquivo)

    con = load.conectar(tmp_path / "b.duckdb")
    load.substituir(con, "aviso", avisos.para_quadro())
    linhas = con.execute(
        "SELECT origem, categoria, mensagem FROM aviso"
    ).fetchall()
    con.close()

    assert linhas == [("COTAHIST_A2025.TXT", "trailer_cotahist", "diferenca de -2")]


def test_resumo_fala_so_do_processo_corrente(tmp_path):
    """`resumo()` é impresso ao fim da etapa que o usuário está vendo rodar.
    Contar aviso de outra etapa ali confundiria o que acabou de acontecer."""
    arquivo = tmp_path / "avisos.json"
    avisos.avisar("a.txt", "cat", "da extracao", imprimir=False)
    avisos.persistir(arquivo)
    avisos.limpar()
    avisos.herdar(arquivo)

    assert avisos.resumo() == "nenhum aviso."
    assert len(avisos.para_quadro()) == 1, "mas o banco recebe o herdado"


def test_mesmo_processo_nao_duplica(tmp_path):
    """`run.py tudo` roda extração e transformação no MESMO processo: o aviso
    está em `_REGISTRO` e no arquivo ao mesmo tempo."""
    arquivo = tmp_path / "avisos.json"
    avisos.avisar("a.txt", "cat", "uma vez so", imprimir=False)
    avisos.persistir(arquivo)
    avisos.herdar(arquivo)  # sem limpar: simula processo unico

    assert len(avisos.para_quadro()) == 1


def test_arquivo_ausente_nao_derruba_a_transformacao(tmp_path):
    """Rodar `transformar` sem extração antes é uso legítimo."""
    assert avisos.herdar(tmp_path / "nao_existe.json") == 0
    assert avisos.para_quadro().empty


def test_arquivo_corrompido_nao_derruba_a_transformacao(tmp_path):
    ruim = tmp_path / "avisos.json"
    ruim.write_text("{isto nao e json", encoding="utf-8")
    assert avisos.herdar(ruim) == 0


def test_registro_de_outra_versao_perde_a_linha_nao_a_rodada(tmp_path):
    arquivo = tmp_path / "avisos.json"
    arquivo.write_text(json.dumps([
        {"origem": "a", "categoria": "c", "mensagem": "m", "registrado_em": "2026-09-13"},
        {"campo_que_nao_existe": 1},
    ]), encoding="utf-8")

    assert avisos.herdar(arquivo) == 1


def test_persistir_sobrescreve_a_rodada_anterior(tmp_path):
    """O arquivo descreve os Parquet que estão em disco agora, não um
    histórico. Acumular faria o banco mostrar aviso de dado que não existe
    mais."""
    arquivo = tmp_path / "avisos.json"
    avisos.avisar("velho.txt", "cat", "rodada anterior", imprimir=False)
    avisos.persistir(arquivo)

    avisos.limpar()
    avisos.avisar("novo.txt", "cat", "rodada de agora", imprimir=False)
    avisos.persistir(arquivo)

    avisos.limpar()
    avisos.herdar(arquivo)
    quadro = avisos.para_quadro()
    assert list(quadro["origem"]) == ["novo.txt"]
