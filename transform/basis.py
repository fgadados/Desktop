"""Regra 4: convivencia de consolidado (CON) e individual (IND).

O mesmo pacote da CVM traz os dois. Somar sem escolher duplica; alternar entre
eles ao longo do historico quebra a comparabilidade -- que e justamente o que
uma serie de percentil de 10 anos precisa preservar.

Politica adotada (decisao do usuario): **uma base fixa por empresa**.

    Para cada companhia, dentro da janela de analise, conta-se quantos
    periodos tem dado em CON e quantos em IND. Vence a de maior cobertura;
    empate vai para CON, que e o padrao do projeto. Todo periodo que nao tem
    a base escolhida fica FALTANDO -- nao ha fallback periodo a periodo.

A base escolhida e a contagem das duas coberturas ficam gravadas e sao
exibidas na interface, junto do numero de periodos perdidos pela escolha.
"""

from __future__ import annotations

import pandas as pd

CON = "CON"
IND = "IND"


def cobertura(df: pd.DataFrame) -> pd.DataFrame:
    """Periodos distintos com dado, por (CNPJ, base)."""
    if df.empty:
        return pd.DataFrame(columns=["CNPJ_CIA", "base", "n_periodos"])
    return (
        df.groupby(["CNPJ_CIA", "base"], dropna=False)["periodo"]
        .nunique()
        .rename("n_periodos")
        .reset_index()
    )


def escolher_base(df: pd.DataFrame) -> pd.DataFrame:
    """Decide a base de cada companhia e registra o criterio."""
    cob = cobertura(df)
    if cob.empty:
        return pd.DataFrame(
            columns=["CNPJ_CIA", "base_escolhida", "cobertura_con", "cobertura_ind",
                     "criterio", "periodos_perdidos"]
        )

    largo = (
        cob.pivot(index="CNPJ_CIA", columns="base", values="n_periodos")
        .reindex(columns=[CON, IND])
        .fillna(0)
        .astype(int)
        .rename(columns={CON: "cobertura_con", IND: "cobertura_ind"})
        .reset_index()
    )

    escolha, criterio = [], []
    for _, r in largo.iterrows():
        c, i = int(r["cobertura_con"]), int(r["cobertura_ind"])
        if c >= i and c > 0:
            escolha.append(CON)
            criterio.append(
                "CON por maior cobertura" if c > i else "CON por empate (padrao do projeto)"
            )
        elif i > 0:
            escolha.append(IND)
            criterio.append("IND por maior cobertura (consolidado inexistente ou parcial)")
        else:
            escolha.append(None)
            criterio.append("sem dado em nenhuma base")
    largo["base_escolhida"] = escolha
    largo["criterio"] = criterio

    total = (
        df.groupby("CNPJ_CIA")["periodo"].nunique().rename("periodos_totais").reset_index()
    )
    largo = largo.merge(total, on="CNPJ_CIA", how="left")
    largo["periodos_perdidos"] = largo["periodos_totais"] - [
        int(r["cobertura_con"]) if r["base_escolhida"] == CON
        else int(r["cobertura_ind"]) if r["base_escolhida"] == IND
        else 0
        for _, r in largo.iterrows()
    ]
    return largo.drop(columns=["periodos_totais"])


def aplicar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filtra para a base escolhida de cada companhia.

    Devolve `(fatos, relatorio_base)`. Os fatos ganham `base_escolhida` e
    `criterio_base` para que a interface consiga responder "por que este
    numero e consolidado?" sem sair da tela.
    """
    rel = escolher_base(df)
    if df.empty or rel.empty:
        return df, rel

    out = df.merge(
        rel[["CNPJ_CIA", "base_escolhida", "criterio", "cobertura_con", "cobertura_ind"]],
        on="CNPJ_CIA",
        how="left",
    ).rename(columns={"criterio": "criterio_base"})
    out = out[out["base"] == out["base_escolhida"]].reset_index(drop=True)
    return out, rel
