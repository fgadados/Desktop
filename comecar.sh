#!/usr/bin/env bash
#
# Instala tudo e abre a interface. Um comando so.
#
#     ./comecar.sh          instala, roda o demo e abre a interface
#     ./comecar.sh pagina   gera empresas.html + empresas.csv e abre
#     ./comecar.sh teste    instala e roda so a bateria de testes
#     ./comecar.sh real     instala e baixa os dados REAIS da CVM/B3/BCB
#     ./comecar.sh atalho   cria atalhos clicaveis na Mesa (macOS)
#
# Nao precisa saber Python para rodar isto. O script recusa seguir se algo
# estiver faltando e diz o que fazer -- nunca continua pela metade.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$RAIZ"

# Sem isto o Python bufferiza a saida ao escrever num cano -- e ela escreve
# num cano, por causa do `tee` do log logo abaixo. O efeito na tela e um
# cursor parado por minutos durante o download, indistinguivel de travamento.
export PYTHONUNBUFFERED=1

# Grava a execucao inteira em b3dss-log.txt, na propria pasta do projeto.
# Assim nao e preciso copiar nada do terminal quando algo falha: basta
# arrastar o arquivo. Reexecuta o script uma vez com a saida duplicada.
LOG="$RAIZ/b3dss-log.txt"
if [ -z "${B3DSS_LOGGING:-}" ]; then
  export B3DSS_LOGGING=1
  # O log carimba a versao exata do codigo. Sem isso e impossivel distinguir
  # "a correcao nao funcionou" de "a correcao nao foi baixada" -- e ja se
  # gastou uma rodada nessa duvida.
  _commit="$(git -C "$RAIZ" rev-parse --short HEAD 2>/dev/null || echo 'sem git')"
  _data_commit="$(git -C "$RAIZ" log -1 --format=%cd --date=format:'%d/%m %H:%M' 2>/dev/null || echo '?')"
  _sujo=""
  git -C "$RAIZ" diff --quiet 2>/dev/null || _sujo=" (com alteracoes locais nao commitadas)"
  {
    echo "### $(date '+%Y-%m-%d %H:%M:%S')  comecar.sh $*"
    echo "### codigo: commit $_commit de $_data_commit$_sujo"
  } > "$LOG"
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
ok()    { echo "${VERDE}✓${FIM} $*"; }
erro()  { echo "${VERM}✗${FIM} $*" >&2; }
info()  { echo "${AMAR}→${FIM} $*"; }
aviso() { echo "${AMAR}!${FIM} $*"; }

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
info "Codigo: commit $(git -C "$RAIZ" rev-parse --short HEAD 2>/dev/null || echo '?')"

# Rodar codigo antigo ja custou rodadas inteiras: o usuario reporta um defeito
# que foi corrigido ha commits, e a conversa gasta um turno descobrindo que a
# correcao existe mas nao foi baixada. O carimbo do commit acima nao resolve
# sozinho -- ele so ajuda DEPOIS que alguem desconfia.
#
# Esta checagem e melhor-esforco: sem rede, sem remoto ou repositorio ausente,
# ela simplesmente nao diz nada. Ela nunca baixa nada por conta propria; quem
# decide atualizar e o usuario.
if command -v git >/dev/null 2>&1 && git -C "$RAIZ" rev-parse --git-dir >/dev/null 2>&1; then
  _ramo="$(git -C "$RAIZ" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [ -n "$_ramo" ] && [ "$_ramo" != "HEAD" ]; then
    # 10s de teto: a conferencia nao pode virar o proximo "travou".
    if timeout 10 git -C "$RAIZ" fetch --quiet origin "$_ramo" 2>/dev/null; then
      _atras="$(git -C "$RAIZ" rev-list --count "HEAD..origin/$_ramo" 2>/dev/null || echo 0)"
      if [ "${_atras:-0}" -gt 0 ] 2>/dev/null; then
        echo
        aviso "Ha $_atras commit(s) novo(s) que voce ainda nao baixou."
        echo "  Voce vai rodar codigo antigo, e um defeito ja corrigido pode"
        echo "  reaparecer. Para atualizar, interrompa (Ctrl+C) e rode:"
        echo
        echo "      cd $RAIZ && git pull && $0 $*"
        echo
      fi
    fi
  fi
fi

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
    # `extrair` devolve 2 quando so fontes AUXILIARES falharam. Nesse caso a
    # transformacao segue: descartar o download bom da CVM e da B3 porque o
    # BCB esta fora seria perder meia hora de trabalho por um dado acessorio.
    set +e
    "$VPY" run.py extrair
    CODIGO_EXTRAIR=$?
    set -e
    case "$CODIGO_EXTRAIR" in
      0) ok "Extracao completa" ;;
      2) info "Extracao PARCIAL (ver acima). Seguindo com o que foi obtido." ;;
      *) erro "Extracao falhou numa fonte essencial. Nada a transformar."
         exit "$CODIGO_EXTRAIR" ;;
    esac
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
    # No macOS, abre o arquivo no navegador direto -- o objetivo aqui e nao
    # precisar de terminal depois.
    if command -v open >/dev/null 2>&1 && [ -f empresas.html ]; then
      open empresas.html
    fi
    exit 0
    ;;

  atalho)
    MESA="$HOME/Desktop"
    [ -d "$MESA" ] || MESA="$HOME"
    criar_atalho() {  # $1 = nome do arquivo, $2 = modo do comecar.sh
      local alvo="$MESA/$1"
      cat > "$alvo" <<ATALHO
#!/bin/bash
# Atalho gerado por comecar.sh. Clique duas vezes para rodar.
cd "$RAIZ" || { echo "Pasta do projeto nao encontrada: $RAIZ"; read -r; exit 1; }
./comecar.sh $2
echo
echo "Terminado. Pode fechar esta janela."
ATALHO
      chmod +x "$alvo"
      ok "$alvo"
    }
    criar_atalho "B3 - gerar pagina.command" "pagina"
    criar_atalho "B3 - abrir interface.command" "demo"
    criar_atalho "B3 - baixar dados reais.command" "real"
    echo
    info "Clique duas vezes em qualquer um deles na Mesa. Sem terminal, sem cd."
    info "Na primeira vez o macOS pode pedir confirmacao: clique em Abrir."
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
echo

# O `streamlit run` nao volta: ele SERVE a pagina e segura a janela enquanto
# roda. Isso ja foi reportado como travamento.
#
# A mensagem "Ctrl+C para parar" era impressa aqui, ANTES de subir o servidor,
# e o banner do proprio Streamlit a empurrava para fora da tela. Quem olhava
# depois via so a parede de texto e um cursor que nao responde.
#
# Por isso o servidor sobe em segundo plano e a explicacao e impressa DEPOIS
# do banner dele, que e quando ela tem chance de ser a ultima coisa na tela.
# O `wait` mantem o script em primeiro plano, entao Ctrl+C continua chegando
# ao Streamlit exatamente como antes.
B3DSS_DB="$RAIZ/$BANCO" .venv/bin/streamlit run app/main.py &
_streamlit=$!

_caixa() {
  # Largura pela linha mais longa, com piso de 60. Fixar a largura quebraria
  # a caixa quando o caminho do projeto for maior que o esperado -- e o
  # caminho entra nos comandos prontos abaixo.
  local largura=60 linha
  for linha in "$@"; do
    [ "${#linha}" -gt "$largura" ] && largura="${#linha}"
  done
  local borda; borda="$(printf '─%.0s' $(seq 1 $largura))"
  echo
  echo "${VERDE}┌${borda}┐${FIM}"
  for linha in "$@"; do
    printf "${VERDE}│${FIM}%-${largura}s${VERDE}│${FIM}\n" "$linha"
  done
  echo "${VERDE}└${borda}┘${FIM}"
  echo
}

( sleep 4
  # So anuncia o servidor se ele ainda estiver de pe. Se o Streamlit morreu
  # nesses 4 segundos, imprimir "nao esta travada" seria mentir sobre um
  # processo que nao existe mais, e esconder o erro dele.
  kill -0 "$_streamlit" 2>/dev/null || exit 0
  # Os comandos vao PRONTOS para colar. Dizer "abra outra aba" sem dizer o
  # que rodar la deixa o passo seguinte por conta do usuario -- e ele ja
  # digitou `run.py identidade` nesta janela, que e o servidor e nao um
  # prompt, e sem o `.venv/bin/python` na frente.
  _caixa \
    "  Esta janela agora E o servidor. Nao esta travada." \
    "  Ela fica assim enquanto a interface estiver no ar." \
    "" \
    "  Ver os dados : http://localhost:8501 no navegador" \
    "  Encerrar     : Ctrl+C aqui" \
    "" \
    "  Para rodar comando, abra OUTRA aba (Cmd+T) e cole:" \
    "" \
    "      cd $RAIZ && ./b3 identidade" \
    "      cd $RAIZ && ./b3 contas ITUB4" ) &

# `set -e` esta ligado e Ctrl+C faz o `wait` sair com 130. Encerrar assim e o
# jeito normal de fechar a interface, nao falha: o `|| true` evita que o
# script termine com erro so por o usuario ter apertado Ctrl+C.
wait "$_streamlit" || true
