"""Guardas sobre o que pode entrar no repositório.

Motivo: `pip install -e .` gera `b3_dss.egg-info/` na pasta de quem instala.
Esses arquivos foram commitados por engano e o `git pull` de quem já tinha
instalado passou a abortar — o git se recusa, corretamente, a sobrescrever
arquivo não rastreado.

O mesmo vale para dado baixado e para os arquivos gerados pelo pipeline:
nada disso é do repositório, é de cada máquina.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# Padrões que nunca podem estar sob controle de versão.
PROIBIDOS = (
    (".egg-info/", "artefato de `pip install -e .`, gerado em cada maquina"),
    ("/build/", "diretorio de build"),
    ("/dist/", "diretorio de distribuicao"),
    ("__pycache__/", "bytecode compilado"),
    ("data/raw/", "bruto baixado das fontes oficiais"),
    ("data/parquet/", "derivado do pipeline"),
    (".duckdb", "banco analitico, gerado localmente"),
    ("b3dss-log.txt", "log de execucao do comecar.sh"),
    ("empresas.html", "saida do `run.py pagina`"),
    ("empresas.csv", "saida do `run.py pagina`"),
    (".venv/", "ambiente virtual"),
)


def _rastreados() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, timeout=30
    )
    if r.returncode != 0:
        pytest.skip("git indisponivel ou diretorio nao e um repositorio")
    return r.stdout.splitlines()


@pytest.mark.parametrize("padrao,motivo", PROIBIDOS)
def test_artefato_gerado_nao_esta_versionado(padrao, motivo):
    alvo = padrao.strip("/") if padrao.startswith("/") else padrao
    ofensores = [f for f in _rastreados() if alvo in f]
    assert not ofensores, (
        f"arquivos versionados que nao deveriam estar ({motivo}): {ofensores[:8]}\n"
        f"Remova com: git rm -r --cached <caminho> e acrescente ao .gitignore"
    )


@pytest.mark.parametrize("padrao", ["*.egg-info/", "build/", "dist/", ".venv/"])
def test_gitignore_declara_o_padrao(padrao):
    texto = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert padrao in texto, f"'{padrao}' ausente do .gitignore"


def test_scripts_de_entrada_sao_executaveis():
    """Sem o bit de execucao no commit, `./comecar.sh` falha no clone."""
    r = subprocess.run(
        ["git", "ls-files", "-s", "comecar.sh"],
        cwd=RAIZ, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip("git indisponivel")
    modo = r.stdout.split()[0]
    assert modo == "100755", f"comecar.sh esta commitado com modo {modo}, esperado 100755"
