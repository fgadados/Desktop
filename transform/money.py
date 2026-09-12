"""Escala e moeda. Outro ponto onde o numero sai errado em silencio.

`VL_CONTA` vem como texto com ponto decimal e `ESCALA_MOEDA` diz se o valor
esta em UNIDADE ou em MILHAR. Companhias diferentes -- e a mesma companhia em
anos diferentes -- alternam entre as duas. Somar sem converter mistura ordens
de grandeza de 1000x.

`MOEDA` e REAL em praticamente tudo, mas nao e assumido: valor em outra moeda
nao e convertido (converter exigiria escolher uma taxa e uma data, o que e
decisao de modelagem) -- ele e marcado e fica de fora dos indicadores.
"""

from __future__ import annotations

import unicodedata

import pandas as pd

ESCALAS = {"UNIDADE": 1.0, "MILHAR": 1_000.0, "MILHAO": 1_000_000.0}
MOEDA_ESPERADA = "REAL"


def _norm(s: pd.Series) -> pd.Series:
    return (
        s.astype("string")
        .fillna("")
        .map(lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode())
        .str.upper()
        .str.strip()
    )


def converter(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta `VL_CONTA_NUM` em reais e `moeda_ok`.

    Escala desconhecida levanta erro: preferimos parar a multiplicar por um
    fator chutado.
    """
    if df.empty:
        return df.assign(VL_CONTA_NUM=pd.Series(dtype="float64"), moeda_ok=pd.Series(dtype="bool"))

    out = df.copy()
    escala_norm = _norm(out["ESCALA_MOEDA"])
    desconhecidas = sorted(set(escala_norm.unique()) - set(ESCALAS) - {""})
    if desconhecidas:
        raise ValueError(
            f"ESCALA_MOEDA desconhecida: {desconhecidas}. Valores aceitos: "
            f"{sorted(ESCALAS)}. Confira o layout da CVM antes de prosseguir."
        )

    fator = escala_norm.map(ESCALAS).astype("float64")
    if fator.isna().any():
        n = int(fator.isna().sum())
        raise ValueError(f"{n} linhas com ESCALA_MOEDA vazia; escala nao pode ser assumida.")

    out["VL_CONTA_NUM"] = pd.to_numeric(out["VL_CONTA"], errors="raise") * fator
    out["escala_fator"] = fator
    out["moeda_ok"] = _norm(out["MOEDA"]) == MOEDA_ESPERADA
    return out


def somente_moeda_esperada(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separa o que esta em real do que nao esta. O resto vira relatorio."""
    if "moeda_ok" not in df.columns:
        raise KeyError("chame transform.money.converter antes de filtrar por moeda")
    return df[df["moeda_ok"]].reset_index(drop=True), df[~df["moeda_ok"]].reset_index(drop=True)
