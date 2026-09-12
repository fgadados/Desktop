"""Validacoes de integridade. Sinalizam, nunca corrigem.

Identidade contabil
-------------------
Um detalhe do plano da CVM que muda o teste: a conta `2` nao e "passivo
exigivel", e **Passivo Total**, e ela ja inclui o patrimonio liquido (`2.03`).
Escrever `1 == 2 + 2.03` produziria erro sistematico do tamanho do PL em toda
empresa do mercado.

O teste correto tem duas partes:

    (a) principal:    Ativo Total (1) == Passivo Total (2)
    (b) decomposta:   Passivo Total (2) == PC (2.01) + PNC (2.02) + PL (2.03)

(b) e a leitura de "Ativo = Passivo + Patrimonio Liquido" nos codigos reais do
arquivo. As duas rodam, com a mesma tolerancia declarada em `etl.config`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl.config import TOL_IDENTIDADE_ABS, TOL_IDENTIDADE_REL

CHAVE = ["CNPJ_CIA", "base", "periodo"]


def _conta(df: pd.DataFrame, codigo: str, demonstrativo: str) -> pd.DataFrame:
    sel = df[(df["CD_CONTA"] == codigo) & (df["demonstrativo"] == demonstrativo)]
    return (
        sel.groupby(CHAVE, dropna=False)
        .agg(valor=("VL_CONTA_NUM", "sum"),
             src_file=("src_file", "first"),
             src_line=("src_line", "first"))
        .reset_index()
    )


def identidade_contabil(fatos: pd.DataFrame) -> pd.DataFrame:
    """Roda as duas identidades por empresa e periodo.

    Devolve uma linha por (empresa, base, periodo) com os dois residuos, a
    tolerancia aplicada e o veredito. Empresa sem uma das contas aparece com
    `status='FALTANDO'` -- ausencia nao vira aprovacao.
    """
    colunas = CHAVE + [
        "ativo_total", "passivo_total", "passivo_circulante",
        "passivo_nao_circulante", "patrimonio_liquido",
        "residuo_principal", "residuo_decomposto", "tolerancia", "status", "motivo",
        "src_ativo", "src_passivo",
    ]
    if fatos.empty:
        return pd.DataFrame(columns=colunas)

    at = _conta(fatos, "1", "BPA").rename(
        columns={"valor": "ativo_total", "src_file": "f_at", "src_line": "l_at"})
    pt = _conta(fatos, "2", "BPP").rename(
        columns={"valor": "passivo_total", "src_file": "f_pt", "src_line": "l_pt"})
    pc = _conta(fatos, "2.01", "BPP").rename(columns={"valor": "passivo_circulante"})
    pnc = _conta(fatos, "2.02", "BPP").rename(columns={"valor": "passivo_nao_circulante"})
    pl = _conta(fatos, "2.03", "BPP").rename(columns={"valor": "patrimonio_liquido"})

    out = at.merge(pt, on=CHAVE, how="outer")
    for parte, col in ((pc, "passivo_circulante"), (pnc, "passivo_nao_circulante"),
                       (pl, "patrimonio_liquido")):
        out = out.merge(parte[CHAVE + [col]], on=CHAVE, how="outer")

    out["residuo_principal"] = out["ativo_total"] - out["passivo_total"]
    soma = (
        out["passivo_circulante"].fillna(0)
        + out["passivo_nao_circulante"].fillna(0)
        + out["patrimonio_liquido"].fillna(0)
    )
    out["residuo_decomposto"] = out["passivo_total"] - soma
    out["tolerancia"] = np.maximum(
        TOL_IDENTIDADE_ABS, out["ativo_total"].abs().fillna(0) * TOL_IDENTIDADE_REL
    )

    faltando = out[["ativo_total", "passivo_total", "patrimonio_liquido"]].isna().any(axis=1)
    falha = (out["residuo_principal"].abs() > out["tolerancia"]) | (
        out["residuo_decomposto"].abs() > out["tolerancia"]
    )

    out["status"] = np.where(faltando, "FALTANDO", np.where(falha, "FALHA", "OK"))
    out["motivo"] = np.where(
        faltando,
        "conta de ativo, passivo ou PL ausente no periodo",
        np.where(
            falha,
            "residuo acima da tolerancia -- confira versao e base do documento",
            None,
        ),
    )
    out["src_ativo"] = [
        f"{f}:{int(l)}" if pd.notna(l) else None for f, l in zip(out["f_at"], out["l_at"])
    ]
    out["src_passivo"] = [
        f"{f}:{int(l)}" if pd.notna(l) else None for f, l in zip(out["f_pt"], out["l_pt"])
    ]
    # `cnpj` e o nome usado em todo o schema analitico; `CNPJ_CIA` so existe
    # enquanto o dado ainda tem a cara do arquivo da CVM.
    return out[colunas].rename(columns={"CNPJ_CIA": "cnpj"}).reset_index(drop=True)


def resumo(identidades: pd.DataFrame) -> dict[str, int]:
    if identidades.empty:
        return {"OK": 0, "FALHA": 0, "FALTANDO": 0}
    c = identidades["status"].value_counts().to_dict()
    return {k: int(c.get(k, 0)) for k in ("OK", "FALHA", "FALTANDO")}
