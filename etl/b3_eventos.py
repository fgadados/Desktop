"""Eventos corporativos: desdobramento, grupamento, bonificacao e proventos.

Sem isto toda serie historica de P/L e P/VP e invalida, entao o modulo e
deliberadamente conservador.

Por que nao ha um "provider automatico" ligado por padrao
--------------------------------------------------------
O COTAHIST nao traz fator de ajuste. A CVM publica o valor agregado de
dividendos e JCP na DMPL, mas sem data-ex e sem valor por acao -- insuficiente
para ajustar uma serie diaria. A restricao do projeto proibe raspar site de
terceiro. Restaria um endpoint da propria B3; nenhum e codificado aqui porque
nenhum foi confrontado com a documentacao oficial nesta instalacao, e o
projeto proibe inventar URL.

Desenho adotado:

1. `data/manual/corporate_events.csv` e a entrada autoritativa, mantida pelo
   usuario, com coluna de proveniencia obrigatoria (`fonte_url`).
2. `detectar_suspeitas()` varre o COTAHIST e aponta candidatos a evento nao
   registrado (salto de preco, mudanca de FATCOT, troca de ISIN). Ele NUNCA
   aplica nada: produz uma lista de pendencias para o usuario resolver.
3. `registrar_provider()` deixa o encaixe pronto para uma fonte oficial da B3
   quando o usuario verificar o endpoint na documentacao da propria B3.

Consequencia deliberada: ticker sem cobertura de evento tem sua serie marcada
`UNADJUSTED`, e todo multiplo derivado dela carrega essa marca. Nunca um
numero ajustado "por estimativa".
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from etl.config import MANUAL

ARQUIVO_MANUAL = MANUAL / "corporate_events.csv"

COLUNAS = [
    "ticker",
    "data_ex",  # YYYY-MM-DD, primeiro pregao SEM direito ao evento
    "tipo",  # DESDOBRAMENTO | GRUPAMENTO | BONIFICACAO | DIVIDENDO | JCP | RENDIMENTO
    "fator",  # razao acoes_depois/acoes_antes (2.0 = desdobra 1:2). Vazio p/ provento.
    "valor_por_acao",  # R$ por acao, bruto. Vazio para evento de quantidade.
    "fonte_url",  # obrigatorio: onde o usuario leu o evento
    "observacao",
]

TIPOS_QUANTIDADE = {"DESDOBRAMENTO", "GRUPAMENTO", "BONIFICACAO"}
TIPOS_PROVENTO = {"DIVIDENDO", "JCP", "RENDIMENTO"}


class EventoInvalidoError(ValueError):
    pass


def _garantir_arquivo() -> None:
    if ARQUIVO_MANUAL.exists():
        return
    ARQUIVO_MANUAL.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns=COLUNAS).to_csv(ARQUIVO_MANUAL, index=False)


def carregar_manual() -> pd.DataFrame:
    _garantir_arquivo()
    df = pd.read_csv(ARQUIVO_MANUAL, dtype=str, keep_default_na=False, na_values=[""])
    faltando = [c for c in COLUNAS if c not in df.columns]
    if faltando:
        raise EventoInvalidoError(f"{ARQUIVO_MANUAL}: colunas ausentes {faltando}")
    if df.empty:
        return _tipar(df)

    df = df.copy()
    df["src_file"] = ARQUIVO_MANUAL.name
    df["src_line"] = range(2, 2 + len(df))
    return validar(_tipar(df))


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "data_ex" in df:
        df["data_ex"] = pd.to_datetime(df["data_ex"], errors="coerce")
    for c in ("fator", "valor_por_acao"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "tipo" in df:
        df["tipo"] = df["tipo"].astype("string").str.upper().str.strip()
    if "ticker" in df:
        df["ticker"] = df["ticker"].astype("string").str.upper().str.strip()
    return df


def validar(df: pd.DataFrame) -> pd.DataFrame:
    """Recusa evento ambiguo. Nada de default silencioso."""
    if df.empty:
        return df
    erros: list[str] = []
    tipos_validos = TIPOS_QUANTIDADE | TIPOS_PROVENTO

    for _, r in df.iterrows():
        ref = f"{ARQUIVO_MANUAL.name}:{r.get('src_line', '?')}"
        if pd.isna(r["data_ex"]):
            erros.append(f"{ref}: data_ex ausente ou invalida")
        if r["tipo"] not in tipos_validos:
            erros.append(f"{ref}: tipo '{r['tipo']}' fora de {sorted(tipos_validos)}")
            continue
        if not str(r.get("fonte_url") or "").strip():
            erros.append(f"{ref}: fonte_url e obrigatorio (rastreabilidade)")
        if r["tipo"] in TIPOS_QUANTIDADE:
            if pd.isna(r["fator"]) or r["fator"] <= 0:
                erros.append(f"{ref}: tipo {r['tipo']} exige fator > 0")
            if not pd.isna(r["valor_por_acao"]):
                erros.append(f"{ref}: tipo {r['tipo']} nao aceita valor_por_acao")
        else:
            if pd.isna(r["valor_por_acao"]) or r["valor_por_acao"] < 0:
                erros.append(f"{ref}: tipo {r['tipo']} exige valor_por_acao >= 0")
            if not pd.isna(r["fator"]):
                erros.append(f"{ref}: tipo {r['tipo']} nao aceita fator")

    if erros:
        raise EventoInvalidoError("eventos corporativos invalidos:\n  " + "\n  ".join(erros))
    return df


# ---------------------------------------------------------------------------
# Encaixe para fonte oficial (desligado por padrao)
# ---------------------------------------------------------------------------
_PROVIDERS: dict[str, Callable[[list[str]], pd.DataFrame]] = {}


def registrar_provider(nome: str, fn: Callable[[list[str]], pd.DataFrame]) -> None:
    """Registra uma fonte oficial adicional de eventos.

    A funcao recebe a lista de tickers e devolve um DataFrame com `COLUNAS`.
    O resultado passa por `validar()` como qualquer entrada manual.
    """
    _PROVIDERS[nome] = fn


def carregar(tickers: list[str] | None = None) -> pd.DataFrame:
    quadros = [carregar_manual()]
    for nome, fn in _PROVIDERS.items():
        df = _tipar(fn(tickers or []))
        df["src_file"] = f"provider:{nome}"
        df["src_line"] = range(1, 1 + len(df))
        quadros.append(validar(df))
    todos = pd.concat([q for q in quadros if not q.empty], ignore_index=True) if any(
        not q.empty for q in quadros
    ) else quadros[0]
    if tickers is not None and not todos.empty:
        todos = todos[todos["ticker"].isin([t.upper() for t in tickers])]
    return todos.sort_values(["ticker", "data_ex"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Deteccao de evento nao registrado -- relatorio, nunca correcao
# ---------------------------------------------------------------------------
def detectar_suspeitas(
    cotacoes: pd.DataFrame,
    eventos: pd.DataFrame,
    *,
    limiar_queda: float = 0.30,
) -> pd.DataFrame:
    """Aponta pregoes cujo salto sugere evento ausente do cadastro.

    Sinais usados, todos observaveis no proprio COTAHIST:
      * variacao de fechamento abaixo de `-limiar_queda` (desdobramento) ou
        acima de `+limiar_queda/(1-limiar_queda)` (grupamento);
      * mudanca do campo FATCOT entre dois pregoes consecutivos;
      * mudanca de CODISI para o mesmo CODNEG.

    Saida: uma linha por suspeita, com a data, o sinal e se ha evento
    cadastrado naquela data. Nada e ajustado a partir disto.
    """
    cols = ["ticker", "data", "sinal", "detalhe", "tem_evento_cadastrado"]
    if cotacoes.empty:
        return pd.DataFrame(columns=cols)

    df = cotacoes.sort_values(["CODNEG", "data"]).copy()
    g = df.groupby("CODNEG", sort=False)
    df["_fech_ant"] = g["PREULT"].shift(1)
    df["_fatcot_ant"] = g["FATCOT"].shift(1)
    df["_isin_ant"] = g["CODISI"].shift(1)
    df["_var"] = df["PREULT"] / df["_fech_ant"] - 1.0

    limiar_alta = limiar_queda / (1.0 - limiar_queda)
    cadastrados = set()
    if not eventos.empty:
        cadastrados = {
            (t, pd.Timestamp(d).normalize())
            for t, d in zip(eventos["ticker"], eventos["data_ex"])
        }

    linhas = []

    def _add(r, sinal: str, detalhe: str) -> None:
        chave = (r["CODNEG"], pd.Timestamp(r["data"]).normalize())
        linhas.append(
            {
                "ticker": r["CODNEG"],
                "data": r["data"],
                "sinal": sinal,
                "detalhe": detalhe,
                "tem_evento_cadastrado": chave in cadastrados,
            }
        )

    for _, r in df.iterrows():
        if pd.notna(r["_var"]) and r["_var"] <= -limiar_queda:
            _add(r, "SALTO_BAIXA", f"fechamento {r['_var']:.2%} vs pregao anterior")
        elif pd.notna(r["_var"]) and r["_var"] >= limiar_alta:
            _add(r, "SALTO_ALTA", f"fechamento {r['_var']:+.2%} vs pregao anterior")
        if pd.notna(r["_fatcot_ant"]) and r["FATCOT"] != r["_fatcot_ant"]:
            _add(r, "FATCOT_MUDOU", f"{r['_fatcot_ant']:.0f} -> {r['FATCOT']:.0f}")
        if pd.notna(r["_isin_ant"]) and r["CODISI"] != r["_isin_ant"]:
            _add(r, "ISIN_MUDOU", f"{r['_isin_ant']} -> {r['CODISI']}")

    if not linhas:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(linhas)[cols].sort_values(["ticker", "data"]).reset_index(drop=True)
