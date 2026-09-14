"""Risco: volatilidade anualizada, drawdown maximo e correlacao.

Todas as medidas usam `fech_aj_total` -- a serie ajustada por quantidade E por
provento. Usar a serie de multiplos aqui subestimaria retorno de pagador de
dividendo.

Nenhuma serie e preenchida. Dia sem pregao nao existe; correlacao usa apenas
as datas em que TODOS os ativos do par negociaram, e o numero de observacoes
efetivas acompanha cada celula.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from transform import lineage as ln

PREGOES_POR_ANO = 252


def volatilidade_anualizada(
    retornos: pd.Series, ticker: str, *, min_obs: int = 60
) -> ln.Indicador:
    formula = (
        f"vol_anual = desvio_padrao(retorno_diario) x raiz({PREGOES_POR_ANO}); "
        "retorno sobre fech_aj_total"
    )
    r = retornos.dropna()
    if len(r) < min_obs:
        return ln.faltando(
            ticker, _rotulo(r), "volatilidade_anualizada", formula,
            f"{len(r)} retornos disponiveis, minimo {min_obs}",
        )
    valor = float(r.std(ddof=1) * np.sqrt(PREGOES_POR_ANO))
    return ln.Indicador(
        ticker, _rotulo(r), "volatilidade_anualizada", valor, formula, ln.OK,
        (ln.Entrada(rotulo="retornos_diarios", valor=float(len(r)),
                    ds_conta="serie fech_aj_total do COTAHIST"),),
        extra={"n_obs": int(len(r)), "inicio": str(r.index.min().date()),
               "fim": str(r.index.max().date())},
    )


def drawdown(precos: pd.Series) -> pd.DataFrame:
    """Serie de drawdown: queda em relacao ao maximo acumulado ate a data."""
    s = precos.dropna()
    pico = s.cummax()
    return pd.DataFrame({"preco": s, "pico": pico, "drawdown": s / pico - 1.0})


def drawdown_maximo(precos: pd.Series, ticker: str) -> ln.Indicador:
    formula = "drawdown_maximo = min(preco_t / max(preco_ate_t) - 1); sobre fech_aj_total"
    s = precos.dropna()
    if s.empty:
        return ln.faltando(ticker, "-", "drawdown_maximo", formula, "serie vazia")
    dd = drawdown(s)
    fundo = dd["drawdown"].idxmin()
    topo = dd.loc[:fundo, "preco"].idxmax()
    return ln.Indicador(
        ticker, _rotulo(s), "drawdown_maximo", float(dd.loc[fundo, "drawdown"]), formula,
        ln.OK,
        (ln.Entrada(rotulo="pico", valor=float(dd.loc[fundo, "pico"]),
                    ds_conta=f"fechamento ajustado em {topo.date()}"),
         ln.Entrada(rotulo="fundo", valor=float(dd.loc[fundo, "preco"]),
                    ds_conta=f"fechamento ajustado em {fundo.date()}")),
        extra={"data_pico": str(topo.date()), "data_fundo": str(fundo.date())},
    )


def matriz_correlacao(
    retornos_por_ticker: dict[str, pd.Series], *, min_obs: int = 60
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Correlacao de Pearson dos retornos diarios e a contagem de observacoes.

    Devolve `(correlacao, n_observacoes)`. Celula com menos de `min_obs` datas
    em comum vem como NaN: correlacao de 12 pregoes nao e correlacao.
    """
    if not retornos_por_ticker:
        return pd.DataFrame(), pd.DataFrame()
    painel = pd.DataFrame(retornos_por_ticker)
    corr = painel.corr(min_periods=min_obs)
    n = painel.notna().astype(int)
    contagem = n.T.dot(n)
    corr = corr.where(contagem >= min_obs)
    return corr, contagem


def _rotulo(s: pd.Series) -> str:
    if s.empty:
        return "-"
    return f"{s.index.min().date()}..{s.index.max().date()}"
