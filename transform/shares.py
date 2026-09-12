"""Ações em circulação, a partir de `composicao_capital` da CVM.

Este era o insumo declarado como ausente no projeto: P/L e P/VP exigem
quantidade de ações, e as demonstrações não a trazem. Ela está no mesmo
pacote, em `{dfp,itr}_cia_aberta_composicao_capital_AAAA.csv`.

Definição usada
---------------
    ações_em_circulação = capital_integralizado − tesouraria

Ação em tesouraria não participa de lucro por ação nem de valor de mercado:
a companhia não paga dividendo a si mesma. Somar as duas infla a base e
deprime o LPA — erro silencioso, do tipo que este projeto existe para evitar.

A regra 2 (versão máxima por documento) vale aqui igual: a composição de
capital é reenviada junto com a demonstração.
"""

from __future__ import annotations

import pandas as pd

from transform.depara import normalizar_cnpj

COLUNAS_QUANTIDADE = (
    "QT_ACAO_ORDIN_CAP_INTEGR",
    "QT_ACAO_PREF_CAP_INTEGR",
    "QT_ACAO_TOTAL_CAP_INTEGR",
    "QT_ACAO_ORDIN_TESOURO",
    "QT_ACAO_PREF_TESOURO",
    "QT_ACAO_TOTAL_TESOURO",
)

SAIDA = [
    "cnpj", "dt_refer", "doc", "versao",
    "acoes_ordinarias", "acoes_preferenciais", "acoes_total",
    "tesouraria_ordinarias", "tesouraria_preferenciais", "tesouraria_total",
    "acoes_em_circulacao", "src_file", "src_line",
]


def normalizar(bruto: pd.DataFrame) -> pd.DataFrame:
    """Converte o arquivo cru em uma linha por (CNPJ, data de referência)."""
    if bruto.empty:
        return pd.DataFrame(columns=SAIDA)

    faltando = [c for c in COLUNAS_QUANTIDADE if c not in bruto.columns]
    if faltando:
        raise KeyError(
            f"composicao_capital sem as colunas de quantidade {faltando}. "
            "Rode `python run.py schema` e confira etl/contracts.py."
        )

    out = bruto.copy()
    out["cnpj"] = normalizar_cnpj(out["CNPJ_CIA"])
    out["versao"] = pd.to_numeric(out["VERSAO"], errors="raise").astype("int64")
    for c in COLUNAS_QUANTIDADE:
        # Quantidade de acoes e inteiro, mas a CVM as vezes grava com decimal.
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # Regra 2: so a versao maxima por documento entregue.
    chave = ["cnpj", "DT_REFER", "doc"]
    maximo = out.groupby(chave, dropna=False)["versao"].transform("max")
    out = out[out["versao"] == maximo]
    # Mesma empresa, mesma data, mesma versao: mantem um registro.
    out = out.drop_duplicates(chave + ["versao"], keep="last")

    resultado = pd.DataFrame({
        "cnpj": out["cnpj"],
        "dt_refer": pd.to_datetime(out["DT_REFER"], errors="coerce"),
        "doc": out["doc"],
        "versao": out["versao"],
        "acoes_ordinarias": out["QT_ACAO_ORDIN_CAP_INTEGR"],
        "acoes_preferenciais": out["QT_ACAO_PREF_CAP_INTEGR"],
        "acoes_total": out["QT_ACAO_TOTAL_CAP_INTEGR"],
        "tesouraria_ordinarias": out["QT_ACAO_ORDIN_TESOURO"],
        "tesouraria_preferenciais": out["QT_ACAO_PREF_TESOURO"],
        "tesouraria_total": out["QT_ACAO_TOTAL_TESOURO"],
        "src_file": out["src_file"],
        "src_line": out["src_line"],
    })
    resultado["acoes_em_circulacao"] = (
        resultado["acoes_total"] - resultado["tesouraria_total"].fillna(0)
    )
    return resultado[SAIDA].sort_values(["cnpj", "dt_refer"]).reset_index(drop=True)


def inconsistencias(df: pd.DataFrame) -> pd.DataFrame:
    """Sinaliza o que não fecha. Não corrige nada.

    * total declarado diferente de ordinárias + preferenciais;
    * tesouraria maior que o capital integralizado;
    * ações em circulação nula ou negativa.
    """
    if df.empty:
        return pd.DataFrame(columns=["cnpj", "dt_refer", "problema", "detalhe"])

    linhas = []
    for _, r in df.iterrows():
        soma = (r["acoes_ordinarias"] or 0) + (r["acoes_preferenciais"] or 0)
        if pd.notna(r["acoes_total"]) and abs(soma - r["acoes_total"]) > 1:
            linhas.append({
                "cnpj": r["cnpj"], "dt_refer": r["dt_refer"],
                "problema": "TOTAL_NAO_FECHA",
                "detalhe": f"ON+PN = {soma:,.0f} vs total declarado "
                           f"{r['acoes_total']:,.0f}",
            })
        if pd.notna(r["acoes_em_circulacao"]) and r["acoes_em_circulacao"] <= 0:
            linhas.append({
                "cnpj": r["cnpj"], "dt_refer": r["dt_refer"],
                "problema": "CIRCULACAO_NAO_POSITIVA",
                "detalhe": f"{r['acoes_em_circulacao']:,.0f} acoes em circulacao",
            })
    return pd.DataFrame(linhas, columns=["cnpj", "dt_refer", "problema", "detalhe"])


def vigente_em(df: pd.DataFrame, cnpj: str, data) -> pd.Series | None:
    """Composição de capital mais recente em vigor numa data.

    Sem interpolação: devolve o último documento com data de referência menor
    ou igual à data pedida, ou None se não houver nenhum.
    """
    data = pd.Timestamp(data)
    sel = df[(df["cnpj"] == cnpj) & (df["dt_refer"] <= data)]
    return None if sel.empty else sel.sort_values("dt_refer").iloc[-1]
