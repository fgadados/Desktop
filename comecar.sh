#!/usr/bin/env bash
#
# Instala tudo e abre a interface. Um comando so.
#
#     ./comecar.sh          instala, roda o demo e abre a interface
#     ./comecar.sh teste    instala e roda so a bateria de testes
#     ./comecar.sh real     instala e baixa os dados REAIS da CVM/B3/BCB
#
# Nao precisa saber Python para rodar isto. O script recusa seguir se algo
# estiver faltando e diz o que fazer -- nunca continua pela metade.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

VERDE=$'\033[0;32m'; VERM=$'\033[0;31m'; AMAR=$'\033[0;33m'; FIM=$'\033[0m'
ok()   { echo "${VERDE}✓${FIM} $*"; }
erro() { echo "${VERM}✗${FIM} $*" >&2; }
info() { echo "${AMAR}→${FIM} $*"; }

# ---------------------------------------------------------------------------
# 1. Python 3.11 ou mais novo
# ---------------------------------------------------------------------------
achar_python() {
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

if ! PY="$(achar_python)"; then
  erro "Python 3.11 ou mais novo nao encontrado."
  echo
  echo "  No macOS, instale com Homebrew:"
  echo "      brew install python@3.11"
  echo
  echo "  Se nao tiver Homebrew, instale-o primeiro:"
  echo '      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo
  echo "  Depois rode este script de novo."
  exit 1
fi
ok "Python encontrado: $PY ($("$PY" --version 2>&1))"

# ---------------------------------------------------------------------------
# 2. Ambiente virtual e dependencias
# ---------------------------------------------------------------------------
if [ ! -d .venv ]; then
  info "Criando ambiente virtual em .venv/ (so na primeira vez)..."
  "$PY" -m venv .venv
fi

VPY=".venv/bin/python"
info "Instalando dependencias..."
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -e ".[dev]"
ok "Dependencias instaladas"

# ---------------------------------------------------------------------------
# 3. O que fazer
# ---------------------------------------------------------------------------
MODO="${1:-demo}"

case "$MODO" in
  teste)
    info "Rodando a bateria de testes (sem rede)..."
    exec "$VPY" -m pytest
    ;;

  real)
    echo
    info "Baixando dados REAIS da CVM, B3 e BCB. Isso leva varios minutos."
    info "Antes, confere se os contratos de schema batem com os arquivos de hoje:"
    echo
    if ! "$VPY" -m pytest -m live tests/contract -q; then
      echo
      erro "Os contratos de schema divergem dos arquivos reais da CVM."
      echo "  Isto e esperado na primeira vez: os nomes de coluna foram declarados"
      echo "  a partir do layout publicado, nao confrontados com arquivo baixado."
      echo "  A saida acima diz exatamente qual coluna divergiu."
      echo "  Ajuste etl/contracts.py e rode de novo. Nao ignore este erro:"
      echo "  ele existe para o pipeline nao produzir numero errado em silencio."
      exit 1
    fi
    ok "Contratos conferem com os arquivos reais"
    "$VPY" run.py extrair
    "$VPY" run.py transformar
    BANCO="data/b3dss.duckdb"
    ;;

  demo|"")
    info "Montando o cenario de demonstracao (dados sinteticos, sem rede)..."
    "$VPY" run.py demo > /dev/null
    ok "Banco de demonstracao pronto"
    BANCO="data/demo.duckdb"
    ;;

  *)
    erro "Modo desconhecido: '$MODO'. Use: demo, teste ou real."
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# 4. Interface
# ---------------------------------------------------------------------------
echo
ok "Abrindo a interface no navegador."
echo "  Banco: $BANCO"
echo "  Para parar: Ctrl+C nesta janela."
echo
B3DSS_DB="$RAIZ/$BANCO" exec .venv/bin/streamlit run app/main.py
