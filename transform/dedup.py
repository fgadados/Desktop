"""Regras 2 e 3: coluna VERSAO e coluna ORDEM_EXERC.

Regra 2 -- VERSAO
    O mesmo exercicio e reenviado em versoes. Reter apenas a versao maxima por
    (CNPJ, periodo de referencia, documento, demonstrativo, base). Versao vem
    como texto no arquivo ("1", "2", "10"): comparar como inteiro, senao "10"
    perde para "2".

Regra 3 -- ORDEM_EXERC
    Cada linha aparece duas vezes, uma com ORDEM_EXERC = ULTIMO (o exercicio
    de referencia) e outra com PENULTIMO (o comparativo do exercicio
    anterior). Somar sem filtrar dobra tudo. O padrao e ULTIMO; PENULTIMO so e
    retido quando o objetivo e reconstruir o comparativo -- que e exatamente o
    que `transform.restatement` faz para detectar reapresentacao.
"""

from __future__ import annotations

import unicodedata

import pandas as pd

ULTIMO = "ULTIMO"
PENULTIMO = "PENULTIMO"

# Chave de versionamento: um documento entregue pela companhia para um periodo.
CHAVE_VERSAO = ("CNPJ_CIA", "DT_REFER", "doc", "demonstrativo", "base")


def normalizar_ordem_exerc(serie: pd.Series) -> pd.Series:
    """ULTIMO / PENULTIMO sem acento e em caixa alta, preservando o original.

    A CVM grava em latin-1 com acento. Comparar contra literal acentuado e
    fragil a mudanca de encoding entre anos; comparar contra a forma sem
    acento nao perde informacao porque os dois unicos valores do dominio
    continuam distintos.
    """
    s = serie.astype("string").fillna("")
    sem_acento = s.map(
        lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
    )
    return sem_acento.str.upper().str.strip()


def versao_maxima(df: pd.DataFrame, chave: tuple[str, ...] = CHAVE_VERSAO) -> pd.DataFrame:
    """Regra 2. Mantem so a versao maxima por chave e registra qual foi.

    Acrescenta `versao_int` e `versoes_descartadas` (lista das versoes que
    existiam e foram deixadas de lado), para que a interface consiga dizer
    "usei a versao 3; existiam 1 e 2".
    """
    if df.empty:
        return df.assign(versao_int=pd.Series(dtype="int64"), versoes_descartadas=None)

    faltando = [c for c in (*chave, "VERSAO") if c not in df.columns]
    if faltando:
        raise KeyError(f"versao_maxima: colunas ausentes {faltando}")

    out = df.copy()
    out["versao_int"] = pd.to_numeric(out["VERSAO"], errors="raise").astype("int64")

    chave = list(chave)
    maximos = out.groupby(chave, dropna=False)["versao_int"].transform("max")
    todas = (
        out.groupby(chave, dropna=False)["versao_int"]
        .agg(lambda s: sorted(set(s.tolist())))
        .rename("_todas")
    )

    out = out.merge(todas, left_on=chave, right_index=True, how="left")
    out["versoes_descartadas"] = [
        ",".join(str(v) for v in todas_v if v != vmax) or None
        for todas_v, vmax in zip(out["_todas"], maximos)
    ]
    out = out[out["versao_int"] == maximos].drop(columns=["_todas"])
    return out.reset_index(drop=True)


def filtrar_ordem_exerc(df: pd.DataFrame, manter: str = ULTIMO) -> pd.DataFrame:
    """Regra 3. Mantem uma ordem de exercicio; `manter=None` mantem as duas."""
    if df.empty or "ORDEM_EXERC" not in df.columns:
        return df
    out = df.copy()
    out["ordem_exerc_norm"] = normalizar_ordem_exerc(out["ORDEM_EXERC"])

    dominio = set(out["ordem_exerc_norm"].unique()) - {""}
    inesperados = dominio - {ULTIMO, PENULTIMO}
    if inesperados:
        raise ValueError(
            f"ORDEM_EXERC com valores fora do dominio conhecido: {sorted(inesperados)}. "
            "Confira o layout da CVM antes de prosseguir."
        )

    if manter is None:
        return out.reset_index(drop=True)
    return out[out["ordem_exerc_norm"] == manter].reset_index(drop=True)


def aplicar(df: pd.DataFrame, manter_ordem: str | None = ULTIMO) -> pd.DataFrame:
    """Regras 2 e 3 na ordem correta: versao primeiro, ordem depois.

    A ordem importa. Filtrar ORDEM_EXERC antes de resolver VERSAO pode deixar
    um comparativo de versao antiga convivendo com um exercicio de versao
    nova para o mesmo periodo.
    """
    return filtrar_ordem_exerc(versao_maxima(df), manter=manter_ordem)
