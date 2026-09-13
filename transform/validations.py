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

Montagem mista
--------------
A politica de reapresentacao do projeto e *restated*: cada fato usa a
publicacao mais recente dele. A resolucao e por CONTA, nao por documento --
entao um balanco pode acabar montado com pecas de datas diferentes. Se a
companhia reapresenta `2.03` na DFP do ano seguinte e nao republica `2`, o
sistema pega PL de um documento e Passivo Total de outro, e a decomposicao
deixa de fechar por construcao. Nao e erro de leitura: e consequencia direta
da politica escolhida.

Decisao do usuario (13/09/2026): manter a resolucao conta a conta e
SINALIZAR. Cada linha traz `montagem` (COERENTE ou MISTA) e `documentos`, com
a data de publicacao de onde veio cada conta. Assim um residuo tem como ser
lido: montagem mista explica a diferenca, montagem coerente nao explica nada
e aponta problema de verdade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl.config import TOL_IDENTIDADE_ABS, TOL_IDENTIDADE_REL

CHAVE = ["CNPJ_CIA", "base", "periodo"]

MISTA = "MISTA"
COERENTE = "COERENTE"

# Conta -> rotulo curto usado na coluna `documentos`.
PARTES = {
    "1": "ativo", "2": "passivo", "2.01": "pc", "2.02": "pnc", "2.03": "pl",
}


def _conta(df: pd.DataFrame, codigo: str, demonstrativo: str) -> pd.DataFrame:
    sel = df[(df["CD_CONTA"] == codigo) & (df["demonstrativo"] == demonstrativo)]
    agregados = {
        "valor": ("VL_CONTA_NUM", "sum"),
        "src_file": ("src_file", "first"),
        "src_line": ("src_line", "first"),
    }
    if "DT_REFER" in sel.columns:
        # Data do documento de onde o valor veio. Depois da resolucao de
        # reapresentacao ha uma linha por conta, entao `first` e o valor.
        agregados["dt_refer"] = ("DT_REFER", "first")
    return sel.groupby(CHAVE, dropna=False).agg(**agregados).reset_index()


def _marcar_montagem(out: pd.DataFrame, partes: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Acrescenta `montagem` e `documentos` a partir da data de cada conta.

    `documentos` sai como "ativo@2023-12-31 passivo@2023-12-31 pl@2024-12-31",
    que e o suficiente para ver de onde veio cada lado sem abrir o arquivo.
    """
    datas = out[CHAVE].copy()
    tem_data = False
    for codigo, parte in partes.items():
        rotulo = PARTES[codigo]
        if "dt_refer" not in parte.columns:
            datas[rotulo] = pd.NA
            continue
        tem_data = True
        datas = datas.merge(
            parte[CHAVE + ["dt_refer"]].rename(columns={"dt_refer": rotulo}),
            on=CHAVE, how="left",
        )

    rotulos = [PARTES[c] for c in partes]
    if not tem_data:
        # Sem DT_REFER nos fatos nao da para afirmar coerencia NEM mistura.
        out["montagem"] = None
        out["documentos"] = None
        return out

    def _texto(linha) -> str | None:
        itens = [f"{r}@{linha[r]}" for r in rotulos if pd.notna(linha[r])]
        return " ".join(itens) if itens else None

    def _classificar(linha) -> str | None:
        vistas = {str(linha[r]) for r in rotulos if pd.notna(linha[r])}
        if not vistas:
            return None
        return COERENTE if len(vistas) == 1 else MISTA

    out["montagem"] = datas.apply(_classificar, axis=1)
    out["documentos"] = datas.apply(_texto, axis=1)
    return out


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
        "montagem", "documentos", "src_ativo", "src_passivo",
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

    out = at.merge(pt, on=CHAVE, how="outer", suffixes=("_1", "_2"))
    for parte, col in ((pc, "passivo_circulante"), (pnc, "passivo_nao_circulante"),
                       (pl, "patrimonio_liquido")):
        out = out.merge(parte[CHAVE + [col]], on=CHAVE, how="outer")

    out = _marcar_montagem(out, {"1": at, "2": pt, "2.01": pc, "2.02": pnc, "2.03": pl})

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

    # Um residuo com montagem MISTA ja tem explicacao; com montagem COERENTE
    # nao tem nenhuma, e e o caso que merece investigacao. Dizer isso na
    # propria linha evita reabrir a mesma duvida a cada leitura da tela.
    mista = out["montagem"].eq(MISTA)
    out["motivo"] = np.where(
        faltando,
        "conta de ativo, passivo ou PL ausente no periodo",
        np.where(
            falha & mista,
            "residuo acima da tolerancia, com balanco montado de publicacoes "
            "de datas diferentes (reapresentacao resolvida conta a conta). "
            "Ver a coluna `documentos`.",
            np.where(
                falha,
                "residuo acima da tolerancia com todas as contas do MESMO "
                "documento -- a reapresentacao nao explica; confira versao e base",
                None,
            ),
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
