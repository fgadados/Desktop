"""`run.py` sozinho não é comando, e isso custou três rodadas.

O usuário digitou, em três momentos diferentes:

    run.py identidade
    zsh: command not found: run.py

Uma vez na janela do servidor Streamlit, duas numa aba de shell. Nas três eu
respondi com a linha correta em vez de tirar o atrito do caminho:

    .venv/bin/python run.py identidade

Instrução repetida três vezes é defeito de projeto, não distração de quem
digita. Duas saídas, e as duas passam a existir:

    ./b3 identidade           atalho curto, funciona de qualquer diretório
    ./run.py identidade       o próprio arquivo se reexecuta no .venv

A reexecução é o que cobre `python3 run.py`, que sem ela morre no primeiro
`import pandas` -- erro de interpretador disfarçado de erro de biblioteca.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
B3 = RAIZ / "b3"
RUN = RAIZ / "run.py"

# Sem .venv nesta instalacao nao ha o que reexecutar; o teste perderia sentido.
pytestmark = pytest.mark.skipif(
    not (RAIZ / ".venv" / "bin" / "python").exists(),
    reason="sem .venv: a reexecucao nao tem alvo",
)


def _rodar(comando: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        comando, cwd=RAIZ, capture_output=True, text=True, timeout=120, **kw
    )


def test_b3_existe_e_e_executavel():
    assert B3.exists(), "o atalho ./b3 sumiu"
    assert os.access(B3, os.X_OK), (
        "./b3 sem bit de execucao: o git preserva isso, entao perder o bit "
        "quebra o atalho para quem clonar"
    )


def test_run_py_e_executavel():
    assert os.access(RUN, os.X_OK), "sem o bit de execucao, ./run.py nao roda"


def test_atalho_b3_roda():
    p = _rodar(["./b3", "--help"])
    assert p.returncode == 0, p.stderr
    assert "identidade" in p.stdout


def test_run_py_direto_roda():
    """`./run.py` usa o shebang do sistema e se reexecuta no .venv."""
    p = _rodar(["./run.py", "--help"])
    assert p.returncode == 0, p.stderr
    assert "identidade" in p.stdout


def test_python_do_sistema_e_reexecutado():
    """O caso que morria com ModuleNotFoundError no primeiro import pesado."""
    sistema = "/usr/bin/python3"
    if not Path(sistema).exists():
        pytest.skip("sem python do sistema nesta maquina")
    p = _rodar([sistema, "run.py", "--help"])
    assert p.returncode == 0, (
        "o python do sistema nao tem as dependencias; run.py deveria ter se "
        f"reexecutado no .venv.\n{p.stderr}"
    )
    assert "identidade" in p.stdout


def test_reexecucao_nao_entra_em_laco():
    """O guarda de ambiente existe para isso. Sem ele, um .venv quebrado
    faria o processo se chamar para sempre."""
    p = _rodar([sys.executable, "run.py", "--help"],
               env={**os.environ, "B3DSS_SEM_REEXEC": "1"})
    assert p.returncode == 0, p.stderr


def test_b3_recusa_com_recado_util_sem_venv(tmp_path):
    """Quem clonar o repositorio e chamar ./b3 antes de instalar tem que ler
    o que fazer, nao um traceback."""
    copia = tmp_path / "b3"
    copia.write_text(B3.read_text(encoding="utf-8"), encoding="utf-8")
    copia.chmod(0o755)
    p = subprocess.run([str(copia), "identidade"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 1
    assert "comecar.sh" in p.stderr, p.stderr


def test_a_ajuda_documenta_o_atalho():
    """Se a ajuda ainda mandar digitar `.venv/bin/python run.py`, o atrito
    volta pela documentacao."""
    ajuda = _rodar(["./b3", "--help"]).stdout
    assert "./b3 identidade" in ajuda
