"""Renderiza a interface de verdade e exige que nenhuma aba levante exceção.

Por que este teste existe
-------------------------
Defeito só de interface não aparece em teste de função. Já aconteceu duas
vezes, as duas achadas pelo usuário com o sistema rodando:

  1. `streamlit run app/main.py` falhava com `ModuleNotFoundError` -- o
     Streamlit põe `app/` no `sys.path`, não a raiz. O comando documentado no
     README não funcionava.
  2. Um ternário escrito como COMANDO:

         st.dataframe(rea, ...) if not rea.empty else st.write(
             "Nenhuma reapresentacao detectada."
         )

     O Streamlit reescreve toda expressão solta em `st.write(...)` -- é o
     "magic" que faz uma variável sozinha virar saída na tela. O valor aqui é
     um DeltaGenerator, então caía em `st.help()`, que tenta `ast.parse` da
     linha de origem; como o comando continuava na linha seguinte, o parse
     recebia código truncado e a aba "Qualidade dos dados" morria com
     `SyntaxError: '(' was never closed`. A mensagem apontava para a linha
     certa e para a causa errada.

Nenhum dos dois era pegável sem executar a interface. `AppTest` é o arnês
headless do próprio Streamlit: roda o script e expõe o que ele produziu,
incluindo exceção não tratada.

O banco vem do mesmo cenário sintético do teste de carga ponta a ponta, com
uma diferença deliberada: a empresa é classificada como SEGURADORA. Assim as
abas são renderizadas com indicador `NAO_SE_APLICA` e `FALTANDO` de verdade,
que é o estado em que a interface mais mexe com valor nulo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import fixtures as fx
from tests import test_carga_ponta_a_ponta as carga

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app" / "main.py"

pytest.importorskip("streamlit", reason="interface e opcional para a camada de dados")


@pytest.fixture(scope="module")
def banco(tmp_path_factory) -> Path:
    """Roda `run.py transformar` e devolve o caminho do DuckDB carregado."""
    dados = tmp_path_factory.mktemp("b3dss_ui")
    pq = dados / "parquet"
    for sub in ("cvm", "b3", "bcb"):
        (pq / sub).mkdir(parents=True)

    dfp, itr = carga._fatos_cvm()
    dfp.to_parquet(pq / "cvm" / "dfp_fatos.parquet")
    itr.to_parquet(pq / "cvm" / "itr_fatos.parquet")
    # SETOR_ATIV real, o mesmo que classificava BBSE3 errado ate 13/09/2026.
    fx.cadastro(setor="Emp. Adm. Part. - Seguradoras e Corretoras").to_parquet(
        pq / "cvm" / "cadastro.parquet"
    )
    carga._fca().to_parquet(pq / "cvm" / "fca_valor_mobiliario.parquet")
    carga._composicao_capital().to_parquet(pq / "cvm" / "dfp_composicao_capital.parquet")
    carga._cotahist().to_parquet(pq / "b3" / "cotahist.parquet")
    carga._sgs().to_parquet(pq / "bcb" / "sgs.parquet")

    proc = subprocess.run(
        [sys.executable, "run.py", "transformar"],
        cwd=RAIZ,
        env={**os.environ, "B3DSS_DATA": str(dados)},
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"transformar falhou:\n{proc.stdout}\n{proc.stderr}"
    return dados / "b3dss.duckdb"


# Em subprocesso, e nao no processo do teste. `etl.config` le `B3DSS_DB` no
# IMPORT: se outro teste ja tiver importado o modulo, por-la depois nao muda
# nada e a interface abre o banco errado. Na primeira versao deste arquivo o
# teste passava sozinho e falhava na suite inteira, exatamente por isso.
_RUNNER = """
import json, sys
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(sys.argv[1], default_timeout=120)
at.run()
print("@@JSON@@" + json.dumps({
    "excecoes": [str(e.value) for e in at.exception],
    "abas": [t.label for t in at.tabs],
    "textos": ([str(m.value) for m in at.markdown]
               + [str(c.value) for c in at.caption]),
}))
"""


@pytest.fixture(scope="module")
def render(banco, tmp_path_factory) -> dict:
    """Renderiza a interface uma vez e devolve o que ela produziu."""
    runner = tmp_path_factory.mktemp("runner") / "render.py"
    runner.write_text(_RUNNER, encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(runner), str(APP)],
        cwd=RAIZ,
        env={**os.environ, "B3DSS_DB": str(banco), "PYTHONPATH": str(RAIZ)},
        capture_output=True, text=True, timeout=300,
    )
    marca = "@@JSON@@"
    assert marca in proc.stdout, (
        "o arnes do Streamlit nao chegou a produzir resultado.\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    import json

    resultado = json.loads(proc.stdout.split(marca, 1)[1].splitlines()[0])
    # O que o usuario le no terminal, verbatim. Verificar o TEXTO em vez de
    # pendurar um handler no logger do Streamlit nao e atalho: e mais fiel.
    # A primeira versao deste arquivo capturava `logging.getLogger("streamlit")`
    # e nao pegava nada -- `import streamlit` reconfigura a hierarquia depois
    # que o handler foi posto. O teste passava com a API depreciada presente.
    resultado["terminal"] = proc.stderr + proc.stdout
    return resultado


def test_a_interface_sobe_sem_excecao(render):
    assert not render["excecoes"], (
        "a interface levantou excecao ao renderizar:\n"
        + "\n".join(render["excecoes"])
    )


def test_todas_as_abas_renderizam(render):
    """`st.tabs` executa o corpo de TODAS as abas numa mesma passada, entao
    uma execucao cobre as seis. Era na ultima delas que estava o defeito."""
    titulos = set(render["abas"])
    assert {"Fundamentos", "Qualidade dos dados"} <= titulos, titulos


def test_nenhuma_celula_escreve_a_palavra_None(render):
    """`None` na tela faz ausencia de motivo parecer um valor. Ja foi defeito."""
    suspeitas = [t for t in render["textos"] if t.strip() in {"None", "nan", "NaN"}]
    assert not suspeitas, suspeitas


def test_a_interface_nao_usa_api_depreciada(render):
    """Aviso repetido em cada `st.dataframe` inunda o terminal.

    Com a interface aberta em 13/09/2026 o terminal virou uma parede de

        `use_container_width` will be removed after 2025-12-31.
        For `use_container_width=True`, use `width='stretch'`. ...

    quatro linhas por chamada, a cada redesenho da tela. Nada ali quebrava um
    numero, mas era o mesmo defeito de sempre por outro lado: mensagem que
    importa deixa de ser vista porque esta afogada em mensagem que nao
    importa. O log do usuario e o canal de diagnostico deste projeto -- ele ja
    custou rodadas inteiras por ilegibilidade.
    """
    terminal = render["terminal"]
    culpadas = [
        linha for linha in terminal.splitlines()
        if "will be removed after" in linha or "Please replace `" in linha
    ]
    assert not culpadas, (
        "a interface usa API depreciada do Streamlit; isto sai no terminal do "
        "usuario a cada redesenho da tela:\n" + "\n".join(sorted(set(culpadas))[:5])
    )


def test_nenhuma_expressao_solta_em_app():
    """A causa-raiz do defeito, checada direto na árvore sintática.

    O Streamlit transforma toda expressão solta em `st.write(...)`. Uma
    chamada a `st.*` escrita como expressão-comando passa por esse caminho e
    vira `st.help()` de um DeltaGenerator. Não depende de renderizar: dá para
    exigir que não exista no arquivo.
    """
    import ast

    arvore = ast.parse(APP.read_text(encoding="utf-8"))
    faltas = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Expr) or not isinstance(no.value, ast.IfExp):
            continue
        faltas.append(f"{APP.name}:{no.lineno}")

    assert not faltas, (
        "ternario usado como comando -- o magic do Streamlit vai reescrever "
        f"isso em st.write() e quebrar a aba: {faltas}"
    )
