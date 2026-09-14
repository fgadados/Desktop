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

import re

import numpy as np
import pandas as pd

from etl.config import TOL_IDENTIDADE_ABS, TOL_IDENTIDADE_REL
from transform.sector import _sem_acento

CHAVE = ["CNPJ_CIA", "base", "periodo"]

MISTA = "MISTA"
COERENTE = "COERENTE"

# Filho direto da conta 2: "2.01", "2.08". Nao "2.01.04", que e neto.
_FILHO_DE_2 = re.compile(r"^2\.\d+$")

# A conta do patrimonio liquido NAO tem codigo fixo entre setores.
#
#   industrial e seguradora : 2.03  Patrimonio Liquido Consolidado
#   banco                   : 2.08  Patrimonio Liquido Consolidado
#                             2.03  Passivos Financeiros ao Custo Amortizado
#
# Ate 13/09/2026 este modulo lia 2.03 como PL para todo mundo. Num banco isso
# rotulava R$ 2,4 trilhoes de captacao como patrimonio liquido -- numero
# errado gravado na tabela e exibido na tela, nao numero faltando. O codigo e
# achado pela DESCRICAO, que e o que o arquivo diz.
_PADRAO_PL = re.compile(r"PATRIMONIO LIQUIDO")


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


def filhos_do_passivo(fatos: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (empresa, periodo, conta filha direta de 2).

    A decomposicao do passivo NAO tem o mesmo formato em todo setor:

        industrial e seguradora : 2.01 + 2.02 + 2.03
        banco                   : 2.01 ... 2.08

    Somar tres codigos fixos deixava de fora, num banco, tudo entre 2.04 e
    2.08 -- 22% do ativo do Itau, e a falha aparecia como divergencia contabil
    quando na verdade o balanco fechava. Somar os filhos DIRETOS vale para os
    tres planos sem declarar nada: e o proprio arquivo dizendo quais sao.
    """
    sel = fatos[
        (fatos["demonstrativo"] == "BPP")
        & fatos["CD_CONTA"].astype(str).str.match(_FILHO_DE_2.pattern)
    ]
    if sel.empty:
        return pd.DataFrame(columns=CHAVE + ["CD_CONTA", "valor", "ds", "dt_refer"])

    agregados = {"valor": ("VL_CONTA_NUM", "sum")}
    agregados["ds"] = ("DS_CONTA", "first") if "DS_CONTA" in sel.columns else (
        "CD_CONTA", "first")
    if "DT_REFER" in sel.columns:
        agregados["dt_refer"] = ("DT_REFER", "first")
    out = sel.groupby(CHAVE + ["CD_CONTA"], dropna=False).agg(**agregados).reset_index()
    if "dt_refer" not in out.columns:
        out["dt_refer"] = pd.NA
    return out


def _por_descricao(filhos: pd.DataFrame, padrao: re.Pattern, coluna: str,
                   excluir: re.Pattern | None = None) -> pd.DataFrame:
    """Escolhe, entre os filhos do passivo, o que a DESCRICAO identificar."""
    if filhos.empty:
        return pd.DataFrame(columns=CHAVE + [coluna, "dt_refer"])
    ds = filhos["ds"].map(_sem_acento)
    casa = ds.str.contains(padrao.pattern, regex=True, na=False)
    if excluir is not None:
        casa &= ~ds.str.contains(excluir.pattern, regex=True, na=False)
    sel = filhos[casa]
    if sel.empty:
        return pd.DataFrame(columns=CHAVE + [coluna, "dt_refer"])
    # Mais de um casamento por periodo seria ambiguidade de layout: fica de
    # fora em vez de escolher no escuro, e a linha vira FALTANDO.
    unico = sel.groupby(CHAVE, dropna=False).filter(lambda g: len(g) == 1)
    if unico.empty:
        return pd.DataFrame(columns=CHAVE + [coluna, "dt_refer"])
    return unico.rename(columns={"valor": coluna})[CHAVE + [coluna, "dt_refer"]]


def _marcar_montagem(out: pd.DataFrame, partes: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Acrescenta `montagem` e `documentos` a partir da data de cada conta.

    `documentos` sai como "ativo@2023-12-31 passivo@2023-12-31 pl@2024-12-31",
    que e o suficiente para ver de onde veio cada lado sem abrir o arquivo.
    """
    datas = out[CHAVE].copy()
    tem_data = False
    for rotulo, parte in partes.items():
        if "dt_refer" not in parte.columns:
            datas[rotulo] = pd.NA
            continue
        tem_data = True
        datas = datas.merge(
            parte[CHAVE + ["dt_refer"]].drop_duplicates(CHAVE).rename(
                columns={"dt_refer": rotulo}),
            on=CHAVE, how="left",
        )

    rotulos = list(partes)
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
        "montagem", "documentos", "passivo_partes", "src_ativo", "src_passivo",
    ]
    if fatos.empty:
        return pd.DataFrame(columns=colunas)

    at = _conta(fatos, "1", "BPA").rename(
        columns={"valor": "ativo_total", "src_file": "f_at", "src_line": "l_at"})
    pt = _conta(fatos, "2", "BPP").rename(
        columns={"valor": "passivo_total", "src_file": "f_pt", "src_line": "l_pt"})

    filhos = filhos_do_passivo(fatos)
    # Circulante e nao-circulante existem no plano industrial e no de
    # seguradora, e NAO existem no de banco. Ficam nulos onde nao existem, em
    # vez de receber a conta que por acaso caiu naquele codigo.
    pc = _por_descricao(filhos, re.compile(r"PASSIVO CIRCULANTE"),
                        "passivo_circulante")
    pnc = _por_descricao(filhos, re.compile(r"PASSIVO NAO CIRCULANTE"),
                         "passivo_nao_circulante")
    pl = _por_descricao(filhos, _PADRAO_PL, "patrimonio_liquido")

    out = at.merge(pt, on=CHAVE, how="outer", suffixes=("_1", "_2"))
    for parte, col in ((pc, "passivo_circulante"), (pnc, "passivo_nao_circulante"),
                       (pl, "patrimonio_liquido")):
        out = out.merge(parte[CHAVE + [col]], on=CHAVE, how="left")

    out = _marcar_montagem(out, {"ativo": at, "passivo": pt, "pl": pl})

    out["residuo_principal"] = out["ativo_total"] - out["passivo_total"]

    # A decomposicao soma TODOS os filhos diretos de 2, nao tres codigos
    # fixos. `passivo_partes` registra quais foram somados, para a conta
    # poder ser refeita a mao a partir da tela.
    if filhos.empty:
        soma = pd.Series(np.nan, index=out.index)
        out["passivo_partes"] = None
    else:
        agregado = (
            filhos.groupby(CHAVE, dropna=False)
            .agg(_soma=("valor", "sum"),
                 _codigos=("CD_CONTA", lambda s: "+".join(sorted(set(s)))))
            .reset_index()
        )
        out = out.merge(agregado, on=CHAVE, how="left")
        soma = out["_soma"]
        out["passivo_partes"] = out["_codigos"]
        out = out.drop(columns=["_soma", "_codigos"])
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
