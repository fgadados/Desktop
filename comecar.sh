#!/usr/bin/env bash
#
# Instala tudo e abre a interface. Um comando so.
#
#     ./comecar.sh          instala, roda o demo e abre a interface
#     ./comecar.sh pagina   gera empresas.html + empresas.csv e para
#     ./comecar.sh teste    instala e roda so a bateria de testes
#     ./comecar.sh real     instala e baixa os dados REAIS da CVM/B3/BCB
#
# Nao precisa saber Python para rodar isto. O script recusa seguir se algo
# estiver faltando e diz o que fazer -- nunca continua pela metade.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

# Grava a execucao inteira em b3dss-log.txt, na propria pasta do projeto.
# Assim nao e preciso copiar nada do terminal quando algo falha: basta
# arrastar o arquivo. Reexecuta o script uma vez com a saida duplicada.
LOG="$RAIZ/b3dss-log.txt"
if [ -z "${B3DSS_LOGGING:-}" ]; then
  export B3DSS_LOGGING=1
  { echo "### $(date '+%Y-%m-%d %H:%M:%S')  comecar.sh $*"; } > "$LOG"
  set +e
  "$0" "$@" 2>&1 | tee -a "$LOG"
  CODIGO="${PIPESTATUS[0]}"
  if [ "$CODIGO" -ne 0 ]; then
    echo
    echo "Falhou (codigo $CODIGO). A execucao inteira ficou registrada em:"
    echo "    $LOG"
    echo "Arraste esse arquivo para a conversa e eu acho a causa."
  fi
  exit "$CODIGO"
fi

# Cor so quando a saida e um terminal de verdade. Sob o `tee` do log ela
# seria gravada como lixo de escape dentro do arquivo.
if [ -t 1 ]; then
  VERDE=$'\033[0;32m'; VERM=$'\033[0;31m'; AMAR=$'\033[0;33m'; FIM=$'\033[0m'
else
  VERDE=""; VERM=""; AMAR=""; FIM=""
fi
ok()   { echo "${VERDE}✓${FIM} $*"; }
erro() { echo "${VERM}✗${FIM} $*" >&2; }
info() { echo "${AMAR}→${FIM} $*"; }

# ---------------------------------------------------------------------------
# 1. Python 3.11 ou mais novo
# ---------------------------------------------------------------------------
# Procura tambem nos caminhos do Homebrew: recem-instalado, o brew pode ainda
# nao estar no PATH da sessao (no Apple Silicon exige `brew shellenv`).
CANDIDATOS=(python3.13 python3.12 python3.11
            /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12
            /opt/homebrew/bin/python3.11
            /usr/local/bin/python3.13 /usr/local/bin/python3.12
            /usr/local/bin/python3.11
            python3)

versao_de() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }

achar_python() {
  for c in "${CANDIDATOS[@]}"; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$(versao_de "$c")" || continue
    [ -n "$v" ] || continue
    # 3.11 a 3.13: faixa com wheels publicados para pandas, pyarrow e duckdb.
    case "$v" in
      3.11|3.12|3.13) echo "$c"; return 0 ;;
    esac
  done
  # Nenhuma versao da faixa: aceita 3.14+ avisando, porque ali a instalacao
  # pode tentar compilar pyarrow/duckdb do zero e falhar de forma obscura.
  for c in "${CANDIDATOS[@]}"; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$(versao_de "$c")" || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

if ! PY="$(achar_python)"; then
  erro "Python 3.11 ou mais novo nao encontrado."
  echo
  echo "  No macOS, instale com Homebrew:"
  echo "      brew install python@3.12"
  echo
  echo "  Se nao tiver Homebrew, instale-o primeiro:"
  echo '      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo
  echo "  Ja instalou o Python pelo brew e mesmo assim apareceu esta mensagem?"
  echo "  O brew pode nao estar no PATH desta janela. Rode:"
  echo '      eval "$(/opt/homebrew/bin/brew shellenv)"'
  echo "  e chame este script de novo."
  exit 1
fi
PYVER="$(versao_de "$PY")"
ok "Python encontrado: $PY ($PYVER)"

case "$PYVER" in
  3.11|3.12|3.13) ;;
  *)
    echo
    info "Python $PYVER e mais novo que a faixa testada (3.11 a 3.13)."
    info "Se a instalacao falhar tentando compilar pyarrow ou duckdb, e isto."
    info "Solucao: brew install python@3.12, apague a pasta .venv e rode de novo."
    echo
    ;;
esac

# ---------------------------------------------------------------------------
# 2. Ambiente virtual e dependencias
# ---------------------------------------------------------------------------
if [ ! -d .venv ]; then
  info "Criando ambiente virtual em .venv/ (so na primeira vez)..."
  "$PY" -m venv .venv
fi

VPY=".venv/bin/python"
if [ ! -x "$VPY" ]; then
  erro "O ambiente virtual em .venv/ esta incompleto (nao achei $VPY)."
  echo "  Apague a pasta e rode de novo:   rm -rf .venv && ./comecar.sh"
  exit 1
fi

info "Instalando dependencias..."
if ! "$VPY" -m pip install --quiet --upgrade pip 2>&1 \
   || ! "$VPY" -m pip install -e ".[dev]" > /tmp/b3dss-pip.log 2>&1; then
  erro "A instalacao das dependencias falhou."
  echo
  echo "  Ultimas linhas do erro:"
  tail -25 /tmp/b3dss-pip.log 2>/dev/null | sed 's/^/      /'
  echo
  echo "  Log completo em /tmp/b3dss-pip.log"
  echo "  Se aparecer 'building wheel' seguido de erro de compilador, o Python"
  echo "  desta maquina e mais novo que os pacotes suportam. Rode:"
  echo "      brew install python@3.12 && rm -rf .venv && ./comecar.sh"
  exit 1
fi
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

  pagina)
    # Um arquivo HTML e um CSV. Sem servidor, sem interface, sem terminal
    # depois: abre com dois cliques no Finder.
    if [ ! -f data/b3dss.duckdb ] && [ ! -f data/demo.duckdb ]; then
      info "Nenhum banco ainda. Montando o cenario de demonstracao..."
      "$VPY" run.py demo > /dev/null
    fi
    "$VPY" run.py pagina
    exit 0
    ;;

  demo|"")
    info "Montando o cenario de demonstracao (dados sinteticos, sem rede)..."
    "$VPY" run.py demo > /dev/null
    ok "Banco de demonstracao pronto"
    BANCO="data/demo.duckdb"
    ;;

  diagnostico)
    echo
    echo "===== DIAGNOSTICO -- cole esta saida inteira ao pedir ajuda ====="
    echo "sistema     : $(uname -srm)"
    [ "$(uname -s)" = "Darwin" ] && echo "macOS       : $(sw_vers -productVersion 2>/dev/null) ($(uname -m))"
    echo "shell       : ${BASH_VERSION:-desconhecido}"
    echo "diretorio   : $RAIZ"
    echo "git         : $(git --version 2>&1 | head -1)"
    echo "branch      : $(git rev-parse --abbrev-ref HEAD 2>&1)"
    echo "commit      : $(git rev-parse --short HEAD 2>&1)"
    echo "python usado: $PY ($PYVER)"
    echo "pythons no PATH:"
    for c in python3 python3.11 python3.12 python3.13 python3.14; do
      if command -v "$c" >/dev/null 2>&1; then
        echo "    $(command -v "$c")  ->  $("$c" --version 2>&1)"
      fi
    done
    echo "pacotes instalados:"
    "$VPY" -m pip list 2>/dev/null \
      | grep -iE '^(pandas|pyarrow|duckdb|streamlit|requests|PyYAML|altair|pytest) ' \
      | sed 's/^/    /' || echo "    (nenhum)"
    echo "bancos existentes:"
    ls -la data/*.duckdb 2>/dev/null | sed 's/^/    /' || echo "    (nenhum)"
    echo "espaco em disco:"
    df -h . 2>/dev/null | tail -1 | sed 's/^/    /'
    echo "================================================================"
    exit 0
    ;;

  *)
    erro "Modo desconhecido: '$MODO'. Use: demo, teste, real ou diagnostico."
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
