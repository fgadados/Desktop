"""Reapresentacao de exercicio anterior (restatement).

Um mesmo periodo aparece em mais de um documento: no arquivo do proprio ano
(ORDEM_EXERC = ULTIMO) e no arquivo do ano seguinte, como comparativo
(ORDEM_EXERC = PENULTIMO). Quando a companhia reapresenta, os dois valores
divergem.

Politica adotada (decisao do usuario): **restated + flag**.

    A serie usa sempre o valor publicado mais recentemente -- o do documento
    de maior DT_REFER, desempatado por VERSAO. O valor original, como
    publicado a epoca, e preservado em `valor_as_filed` e a divergencia e
    sinalizada. Nenhuma escolha silenciosa: a interface mostra os dois.

Efeito colateral aceito: o historico muda quando uma DFP nova reapresenta um
periodo antigo. Por isso `divergente` e `n_publicacoes` acompanham cada fato.
"""

from __future__ import annotations

import pandas as pd

from etl.config import TOL_IDENTIDADE_ABS, TOL_IDENTIDADE_REL

CHAVE_FATO = ["CNPJ_CIA", "base", "demonstrativo", "CD_CONTA", "periodo"]


def resolver(df: pd.DataFrame) -> pd.DataFrame:
    """Escolhe o valor mais recente por fato e marca divergencia.

    Entrada: fatos normalizados por periodo, com ULTIMO e PENULTIMO presentes
    (ou seja, `dedup.aplicar(..., manter_ordem=None)`).

    Colunas acrescentadas:
      valor_as_filed   -- valor do documento do proprio exercicio (ULTIMO)
      valor_restated   -- valor da publicacao mais recente (o que a serie usa)
      divergente       -- True quando os dois diferem alem da tolerancia
      n_publicacoes    -- quantas vezes o fato foi publicado
      dt_refer_usada   -- DT_REFER do documento de onde saiu o valor usado
    """
    if df.empty:
        return df.assign(
            valor_as_filed=pd.Series(dtype="float64"),
            valor_restated=pd.Series(dtype="float64"),
            divergente=pd.Series(dtype="bool"),
            n_publicacoes=pd.Series(dtype="int64"),
            dt_refer_usada=None,
        )

    out = df.copy()
    out["DT_REFER_DT"] = pd.to_datetime(out["DT_REFER"], errors="raise")
    if "versao_int" not in out.columns:
        out["versao_int"] = pd.to_numeric(out["VERSAO"], errors="raise")

    # Publicacao mais recente = maior DT_REFER, desempate por VERSAO.
    ordenado = out.sort_values(
        CHAVE_FATO + ["DT_REFER_DT", "versao_int"], ascending=True, kind="mergesort"
    )
    escolhido = ordenado.groupby(CHAVE_FATO, dropna=False, as_index=False).tail(1).copy()
    escolhido["valor_restated"] = escolhido["VL_CONTA_NUM"]
    escolhido["dt_refer_usada"] = escolhido["DT_REFER"]

    # As-filed: a linha ULTIMO, isto e, o documento do proprio exercicio.
    as_filed = (
        ordenado[ordenado["ordem_exerc_norm"] == "ULTIMO"]
        .groupby(CHAVE_FATO, dropna=False, as_index=False)
        .tail(1)[CHAVE_FATO + ["VL_CONTA_NUM", "DT_REFER"]]
        .rename(columns={"VL_CONTA_NUM": "valor_as_filed", "DT_REFER": "dt_refer_as_filed"})
    )

    n_pub = (
        ordenado.groupby(CHAVE_FATO, dropna=False)["DT_REFER"]
        .nunique()
        .rename("n_publicacoes")
        .reset_index()
    )

    res = escolhido.merge(as_filed, on=CHAVE_FATO, how="left").merge(
        n_pub, on=CHAVE_FATO, how="left"
    )

    dif = (res["valor_restated"] - res["valor_as_filed"]).abs()
    tol = (res["valor_as_filed"].abs() * TOL_IDENTIDADE_REL).clip(lower=TOL_IDENTIDADE_ABS)
    res["divergente"] = res["valor_as_filed"].notna() & (dif > tol)

    # A serie usa o restated.
    res["VL_CONTA_NUM"] = res["valor_restated"]
    return res.drop(columns=["DT_REFER_DT"]).reset_index(drop=True)


def relatorio_divergencias(df: pd.DataFrame) -> pd.DataFrame:
    """So os fatos reapresentados, prontos para a tela de sinalizacao."""
    if df.empty or "divergente" not in df.columns:
        return pd.DataFrame()
    cols = CHAVE_FATO + [
        "DS_CONTA", "valor_as_filed", "valor_restated", "dt_refer_as_filed",
        "dt_refer_usada", "n_publicacoes", "src_file", "src_line",
    ]
    existentes = [c for c in cols if c in df.columns]
    rel = df[df["divergente"]][existentes].copy()
    if not rel.empty:
        rel["variacao_pct"] = (
            rel["valor_restated"] / rel["valor_as_filed"].replace(0, pd.NA) - 1.0
        )
    return rel.reset_index(drop=True)
