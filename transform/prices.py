"""Ajuste da serie de precos por eventos corporativos.

O COTAHIST nao e ajustado. Sem ajuste, toda serie de P/L e P/VP fica invalida:
um desdobramento 1:2 corta o preco pela metade sem alterar o lucro por acao
retroativamente, e o multiplo historico despenca por motivo contabil nenhum.

Duas series, conforme decisao do usuario
---------------------------------------
`fech_aj_split`
    Ajustado SOMENTE por evento de quantidade (desdobramento, grupamento,
    bonificacao). E a serie usada em P/L, P/VP e EV/EBITDA. Provento nao entra
    aqui: dividendo nao muda a quantidade de acoes, entao descontar dividendo
    do preco deprimiria artificialmente o multiplo historico de pagador de
    dividendo -- que e exatamente o erro que se quer evitar.

`fech_aj_total`
    Ajustado por quantidade E por provento reinvestido. E a serie usada em
    retorno, volatilidade, drawdown e correlacao.

Convencao de ajuste: retroativa. O ultimo preco da serie fica igual ao preco
negociado; os precos anteriores sao multiplicados pelo produto das razoes de
todos os eventos posteriores a eles.

    razao(D) = (1 - provento(D) / fech(D-1)) / fator_quantidade(D)

Cobertura
---------
Ticker sem evento cadastrado nao ganha serie "ajustada por padrao". Ele recebe
`cobertura_ajuste = 'SEM_EVENTOS'` ou `'SUSPEITA_NAO_RESOLVIDA'`, e todo
multiplo derivado dali herda a marca NAO_AJUSTADO.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl.b3_eventos import TIPOS_PROVENTO, TIPOS_QUANTIDADE

COBERTURA_OK = "AJUSTADO"
COBERTURA_SEM_EVENTOS = "SEM_EVENTOS"
COBERTURA_SUSPEITA = "SUSPEITA_NAO_RESOLVIDA"


def preco_unitario(cotahist: pd.DataFrame) -> pd.DataFrame:
    """Divide os precos por FATCOT para obter preco por 1 acao.

    FATCOT e o "fator de cotacao" do layout da B3: 1 quando o preco se refere
    a uma acao, 1000 quando se refere a mil. Ignorar isso mistura duas escalas
    na mesma serie.
    """
    out = cotahist.copy()
    fat = pd.to_numeric(out["FATCOT"], errors="raise").replace(0, np.nan)
    for c in ("PREABE", "PREMAX", "PREMIN", "PREMED", "PREULT"):
        out[c] = out[c] / fat
    out["fatcot_aplicado"] = fat
    return out


def serie_diaria(cotahist: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Serie de fechamento de um ticker, ordenada, sem duplicidade de data."""
    df = cotahist[cotahist["CODNEG"].astype("string").str.strip() == ticker.upper()].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["data", "fech", "volume", "quantidade", "src_file", "src_line"]
        )
    df = df.sort_values("data")
    dup = df["data"].duplicated(keep=False)
    if dup.any():
        raise ValueError(
            f"{ticker}: {int(dup.sum())} pregoes com data duplicada no COTAHIST filtrado. "
            "Confira CODBDI/TPMERC antes de montar a serie."
        )
    return pd.DataFrame(
        {
            "data": df["data"].to_numpy(),
            "fech": df["PREULT"].to_numpy(),
            "abertura": df["PREABE"].to_numpy(),
            "maxima": df["PREMAX"].to_numpy(),
            "minima": df["PREMIN"].to_numpy(),
            "volume": df["VOLTOT"].to_numpy(),
            "quantidade": df["QUATOT"].to_numpy(),
            "src_file": df["src_file"].to_numpy(),
            "src_line": df["src_line"].to_numpy(),
        }
    ).reset_index(drop=True)


def _razoes(precos: pd.DataFrame, eventos: pd.DataFrame, *, com_proventos: bool) -> pd.Series:
    """Razao de ajuste por data-ex. Indexada por data, 1.0 onde nao ha evento."""
    razao = pd.Series(1.0, index=pd.DatetimeIndex(precos["data"]), name="razao")
    if eventos.empty:
        return razao

    fech = pd.Series(precos["fech"].to_numpy(), index=pd.DatetimeIndex(precos["data"]))
    fech_ant = fech.shift(1)

    for _, e in eventos.iterrows():
        d = pd.Timestamp(e["data_ex"]).normalize()
        if d not in razao.index:
            # Data-ex em dia sem pregao para o papel: aplica no primeiro
            # pregao seguinte, que e onde o preco efetivamente abre "ex".
            posteriores = razao.index[razao.index >= d]
            if len(posteriores) == 0:
                continue
            d = posteriores[0]

        if e["tipo"] in TIPOS_QUANTIDADE:
            fator = float(e["fator"])
            if fator <= 0:
                raise ValueError(f"fator invalido em {e['ticker']} {d.date()}: {fator}")
            razao.loc[d] = razao.loc[d] / fator
        elif com_proventos and e["tipo"] in TIPOS_PROVENTO:
            p_ant = fech_ant.get(d)
            if p_ant is None or not np.isfinite(p_ant) or p_ant <= 0:
                raise ValueError(
                    f"{e['ticker']} {d.date()}: provento sem fechamento anterior valido; "
                    "nao e possivel calcular a razao de ajuste sem estimar."
                )
            razao.loc[d] = razao.loc[d] * (1.0 - float(e["valor_por_acao"]) / float(p_ant))
    return razao


def ajustar(precos: pd.DataFrame, eventos: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta `fech_aj_split` e `fech_aj_total` a serie diaria.

    Ajuste retroativo: o produto acumulado das razoes de eventos POSTERIORES a
    cada data multiplica o preco daquela data.
    """
    out = precos.copy()
    if out.empty:
        out["fech_aj_split"] = pd.Series(dtype="float64")
        out["fech_aj_total"] = pd.Series(dtype="float64")
        return out

    for coluna, com_prov in (("fech_aj_split", False), ("fech_aj_total", True)):
        razao = _razoes(out, eventos, com_proventos=com_prov).to_numpy()
        # Produto dos eventos estritamente posteriores a cada data.
        posterior = np.concatenate([razao[1:], [1.0]])
        acumulado = np.cumprod(posterior[::-1])[::-1]
        out[coluna] = out["fech"].to_numpy() * acumulado
    return out


def cobertura_ajuste(
    ticker: str, eventos: pd.DataFrame, suspeitas: pd.DataFrame
) -> tuple[str, str]:
    """Diz se a serie ajustada daquele ticker pode ser usada em multiplo."""
    tem_evento = not eventos.empty and (eventos["ticker"] == ticker.upper()).any()
    abertas = pd.DataFrame()
    if not suspeitas.empty:
        abertas = suspeitas[
            (suspeitas["ticker"] == ticker.upper())
            & (~suspeitas["tem_evento_cadastrado"])
            & (suspeitas["sinal"].isin(["SALTO_BAIXA", "SALTO_ALTA", "FATCOT_MUDOU"]))
        ]
    if not abertas.empty:
        datas = ", ".join(str(pd.Timestamp(d).date()) for d in abertas["data"].head(5))
        return (
            COBERTURA_SUSPEITA,
            f"{len(abertas)} pregao(s) com salto compativel com evento nao cadastrado "
            f"({datas}). Registre em data/manual/corporate_events.csv.",
        )
    if not tem_evento:
        return (
            COBERTURA_SEM_EVENTOS,
            "nenhum evento corporativo cadastrado para o ticker; a serie e o preco "
            "negociado, sem ajuste.",
        )
    return COBERTURA_OK, "eventos cadastrados aplicados retroativamente."


def retorno_diario(precos: pd.DataFrame, coluna: str = "fech_aj_total") -> pd.Series:
    """Retorno simples diario. Sem preenchimento de dia faltante."""
    s = pd.Series(precos[coluna].to_numpy(), index=pd.DatetimeIndex(precos["data"]))
    return s.pct_change().dropna()
