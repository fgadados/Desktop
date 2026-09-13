"""Dois silêncios encontrados na execução de 13/09/2026, às 17h.

1. Ano pedido que a CVM não lista
---------------------------------
O log da extração mostrou:

    [3/7] DFP: ... 2023, 2024, 2026      <- sem 2025
    [4/7] ITR: ... 2023, 2024, 2026      <- sem 2025
    [1/6] 26.429.192 fatos da CVM        <- eram 31.234.090

Os pacotes de 2025 sumiram do diretório da CVM entre duas execuções. A
extração filtrava os anos pedidos contra os listados e descartava o que não
encontrasse, **sem dizer nada** -- relatou sucesso, e 4,8 milhões de fatos, um
ano inteiro da carteira, deixaram de existir. O buraco só apareceu porque
alguém foi conferir trimestre a trimestre no `--periodos`.

Pelo critério de `etl/avisos.py` isso é AVISO e não falha dura: há dado
faltando, não número errado. Mas silêncio é o que o projeto não aceita.

2. Banco travado por outro processo
-----------------------------------
`transformar` morreu com um `IOException` cru do DuckDB falando de
"Conflicting lock ... PID 97930". A causa era a interface Streamlit aberta
noutra aba: o DuckDB aceita um escritor OU vários leitores, nunca os dois.

O usuário tinha acabado de esperar dois minutos de extração para receber um
traceback que não dizia o óbvio -- feche a interface.
"""

from __future__ import annotations

import pytest

import pandas as pd

from db import load
from etl import avisos


@pytest.fixture(autouse=True)
def registro_limpo():
    avisos.limpar()
    yield
    avisos.limpar()


# ---------------------------------------------------------------------------
# 1. Ano pedido que o diretorio nao lista
# ---------------------------------------------------------------------------
def _extrair_com_diretorio(monkeypatch, disponiveis: list[int], pedidos: list[int]):
    """Roda `extrair` com um diretorio da CVM simulado, sem baixar nada."""
    from etl import cvm_demonstracoes

    urls = [f"https://exemplo/dfp_cia_aberta_{a}.zip" for a in disponiveis]
    monkeypatch.setattr(cvm_demonstracoes.cvm_common, "listar_pacotes",
                        lambda doc: urls)
    def _sem_rede(doc, url):
        # `pytest.fail` nao serve aqui: ele levanta BaseException e escaparia
        # do `except Exception` logo abaixo, quebrando o teste em vez de
        # interromper o download.
        raise RuntimeError(f"download sabotado: {url}")

    monkeypatch.setattr(cvm_demonstracoes, "extrair_ano", _sem_rede)
    try:
        cvm_demonstracoes.extrair("DFP", pedidos)
    except RuntimeError:
        # O download e sabotado de proposito; o que interessa e o aviso, que
        # e emitido ANTES de qualquer download.
        pass


def test_ano_pedido_e_ausente_vira_aviso(monkeypatch):
    _extrair_com_diretorio(monkeypatch, disponiveis=[2023, 2024, 2026],
                           pedidos=[2023, 2024, 2025, 2026])
    registrados = avisos.registrados()
    assert registrados, "ano faltante passou em silencio -- era exatamente o defeito"
    aviso = registrados[0]
    assert aviso.categoria == "ano_sem_pacote"
    assert "2025" in aviso.mensagem
    assert "2026" in aviso.mensagem, "a mensagem precisa dizer o que EXISTE"


def test_todos_os_anos_presentes_nao_gera_aviso(monkeypatch):
    _extrair_com_diretorio(monkeypatch, disponiveis=[2024, 2025, 2026],
                           pedidos=[2024, 2025, 2026])
    assert not avisos.registrados(), "aviso a toa polui o log e treina a ignorar"


def test_varios_anos_faltantes_saem_em_um_aviso_so(monkeypatch):
    _extrair_com_diretorio(monkeypatch, disponiveis=[2026],
                           pedidos=[2023, 2024, 2025, 2026])
    registrados = avisos.registrados()
    assert len(registrados) == 1
    for ano in ("2023", "2024", "2025"):
        assert ano in registrados[0].mensagem


def test_nenhum_ano_disponivel_continua_sendo_falha_dura(monkeypatch):
    """Sem pacote nenhum nao ha o que transformar: aviso nao serve."""
    from etl import cvm_demonstracoes

    monkeypatch.setattr(cvm_demonstracoes.cvm_common, "listar_pacotes",
                        lambda doc: ["https://exemplo/dfp_cia_aberta_2019.zip"])
    with pytest.raises(FileNotFoundError):
        cvm_demonstracoes.extrair("DFP", [2025, 2026])


# ---------------------------------------------------------------------------
# 2. Banco travado por outro processo
# ---------------------------------------------------------------------------
# A trava do DuckDB e por PROCESSO: duas conexoes no mesmo processo
# compartilham a instancia e nao conflitam. Por isso o dono da trava precisa
# ser outro processo -- que e exatamente a situacao real, com o Streamlit
# rodando noutra aba do Terminal.
_SEGURA_O_BANCO = """
import sys, time, duckdb
con = duckdb.connect(sys.argv[1])
print("pronto", flush=True)
time.sleep(60)
"""


def test_banco_aberto_por_outro_processo_diz_o_que_fazer(tmp_path):
    import subprocess
    import sys

    caminho = tmp_path / "b3dss.duckdb"
    load.conectar(caminho).close()  # cria o arquivo com o schema em dia

    dono = subprocess.Popen(
        [sys.executable, "-c", _SEGURA_O_BANCO, str(caminho)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert dono.stdout.readline().strip() == "pronto", "o dono nao subiu"
        with pytest.raises(load.BancoEmUsoError) as exc:
            load.conectar(caminho)
        mensagem = str(exc.value)
        assert "interface" in mensagem.lower(), (
            "a mensagem precisa nomear a causa provavel, nao so o sintoma"
        )
        assert "Ctrl+C" in mensagem, "e precisa dizer o que fazer"
    finally:
        dono.kill()
        dono.wait(timeout=10)


def test_banco_travado_nao_e_apagado(tmp_path):
    """A ordem errada destruia o banco de quem estivesse com a interface
    aberta: conferia a impressao lendo o arquivo (falhava pela trava),
    concluia "schema mudou" e apagava ANTES de tentar abrir."""
    import subprocess
    import sys

    caminho = tmp_path / "b3dss.duckdb"
    con = load.conectar(caminho)
    load.substituir(con, "aviso", pd.DataFrame([{
        "origem": "teste", "categoria": "marca", "mensagem": "tem que sobreviver",
        "registrado_em": "2026-09-13",
    }]))
    con.close()

    dono = subprocess.Popen(
        [sys.executable, "-c", _SEGURA_O_BANCO, str(caminho)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert dono.stdout.readline().strip() == "pronto"
        with pytest.raises(load.BancoEmUsoError):
            load.conectar(caminho)
        assert caminho.exists(), "o banco do usuario foi apagado enquanto em uso"
    finally:
        dono.kill()
        dono.wait(timeout=10)

    con = load.conectar(caminho)
    n = con.execute("SELECT count(*) FROM aviso").fetchone()[0]
    con.close()
    assert n == 1, "o conteudo tinha que continuar la"


def test_depois_de_fechar_a_conexao_volta_a_abrir(tmp_path):
    caminho = tmp_path / "b3dss.duckdb"
    load.conectar(caminho).close()
    con = load.conectar(caminho)
    con.close()


def test_erro_de_io_que_nao_e_trava_continua_subindo(tmp_path):
    """Nao mascarar outros IOException com o recado da interface."""
    import duckdb

    ruim = tmp_path / "sem_permissao"
    ruim.mkdir()
    with pytest.raises((duckdb.IOException, OSError, PermissionError)):
        load.conectar(ruim)  # diretorio, nao arquivo
