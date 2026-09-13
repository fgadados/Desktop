"""Fixtures SINTETICOS, construidos no layout documentado da CVM.

Aviso que importa: estes dados NAO sao um arquivo real da CVM. Eles reproduzem
o layout declarado em `etl/contracts.py` para exercitar as seis regras de
tratamento de forma deterministica. Confrontar o contrato com arquivo real e
trabalho de `tests/contract/`, que exige rede.

Cada gerador aqui embute de proposito a patologia que a regra correspondente
tem que resolver -- versao reenviada, linha duplicada por ORDEM_EXERC,
consolidado e individual convivendo, acumulado do ITR misturado ao trimestre.
"""

from __future__ import annotations

import pandas as pd

COLUNAS_BASE = [
    "CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP", "MOEDA",
    "ESCALA_MOEDA", "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC", "CD_CONTA",
    "DS_CONTA", "VL_CONTA", "ST_CONTA_FIXA",
]

CNPJ_A = "00.000.000/0001-91"
CNPJ_BANCO = "11.111.111/0001-11"


def fato(
    *,
    cnpj: str = CNPJ_A,
    dt_refer: str,
    versao: str = "1",
    ordem: str = "ÚLTIMO",
    doc: str = "DFP",
    demonstrativo: str = "DRE",
    base: str = "CON",
    cd_conta: str,
    ds_conta: str = "",
    vl: float,
    dt_ini: str | None = None,
    dt_fim: str | None = None,
    escala: str = "MILHAR",
    moeda: str = "REAL",
    denom: str = "COMPANHIA TESTE S.A.",
    coluna_df: str = "",
) -> dict:
    return {
        # Dimensao extra da DMPL: a mesma conta aparece uma vez por coluna do
        # patrimonio liquido. Vazia nos demais demonstrativos.
        "COLUNA_DF": coluna_df,
        "CNPJ_CIA": cnpj,
        "DT_REFER": dt_refer,
        "VERSAO": versao,
        "DENOM_CIA": denom,
        "CD_CVM": "99999",
        "GRUPO_DFP": f"DF Consolidado - {demonstrativo}",
        "MOEDA": moeda,
        "ESCALA_MOEDA": escala,
        "ORDEM_EXERC": ordem,
        "DT_INI_EXERC": dt_ini,
        "DT_FIM_EXERC": dt_fim or dt_refer,
        "CD_CONTA": cd_conta,
        "DS_CONTA": ds_conta,
        "VL_CONTA": str(vl),
        "ST_CONTA_FIXA": "S",
        "doc": doc,
        "demonstrativo": demonstrativo,
        "base": base,
        "ano_arquivo": int(dt_refer[:4]),
    }


def nome_csv(doc: str, demonstrativo: str, base: str, ano: int) -> str:
    """Nome do CSV dentro do ZIP, no padrao da CVM."""
    return f"{doc.lower()}_cia_aberta_{demonstrativo}_{base.lower()}_{ano}.csv"


def quadro(linhas: list[dict]) -> pd.DataFrame:
    """Monta o DataFrame com as colunas de proveniencia, como sai do ETL.

    Cada combinacao (documento, demonstrativo, base, ano) vira um arquivo
    proprio e e numerada a partir da linha 2, exatamente como a CVM entrega e
    como `etl.cvm_demonstracoes` le. Carimbar um nome so para tudo faria a
    linhagem do fixture mentir -- um saldo de BPP apontando para o CSV da DRE.
    """
    df = pd.DataFrame(linhas)
    for c in COLUNAS_BASE:
        if c not in df.columns:
            df[c] = None

    df["src_file"] = [
        nome_csv(d, dem, b, a)
        for d, dem, b, a in zip(df["doc"], df["demonstrativo"], df["base"], df["ano_arquivo"])
    ]
    df["src_archive"] = [
        f"{d.lower()}_cia_aberta_{a}.zip" for d, a in zip(df["doc"], df["ano_arquivo"])
    ]
    df["src_line"] = df.groupby("src_file", sort=False).cumcount() + 2
    df["src_sha256"] = "0" * 64
    return df


# ---------------------------------------------------------------------------
# Cenarios
# ---------------------------------------------------------------------------
def cenario_balanco_completo(cnpj: str = CNPJ_A, ano: int = 2023, pl: float = 400.0):
    """Balanco que fecha: Ativo(1) = Passivo Total(2) = PC + PNC + PL."""
    fim = f"{ano}-12-31"
    pc, pnc = 300.0, 300.0
    total = pc + pnc + pl
    return [
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1",
             ds_conta="Ativo Total", vl=total, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1.01",
             ds_conta="Ativo Circulante", vl=total / 2, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1.01.01",
             ds_conta="Caixa e Equivalentes de Caixa", vl=100.0, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2",
             ds_conta="Passivo Total", vl=total, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.01",
             ds_conta="Passivo Circulante", vl=pc, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.01.04",
             ds_conta="Empréstimos e Financiamentos", vl=120.0, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.02",
             ds_conta="Passivo Não Circulante", vl=pnc, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.02.01",
             ds_conta="Empréstimos e Financiamentos", vl=180.0, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.03",
             ds_conta="Patrimônio Líquido Consolidado", vl=pl, dt_fim=fim),
    ]


def cenario_dre_anual(cnpj: str = CNPJ_A, ano: int = 2023, receita: float = 1000.0,
                      lucro: float = 80.0, ebit: float = 120.0):
    ini, fim = f"{ano}-01-01", f"{ano}-12-31"
    return [
        fato(cnpj=cnpj, dt_refer=fim, cd_conta="3.01",
             ds_conta="Receita de Venda de Bens e/ou Serviços", vl=receita,
             dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, cd_conta="3.02",
             ds_conta="Custo dos Bens e/ou Serviços Vendidos", vl=-600.0,
             dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, cd_conta="3.03", ds_conta="Resultado Bruto",
             vl=receita - 600.0, dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, cd_conta="3.05",
             ds_conta="Resultado Antes do Resultado Financeiro e dos Tributos",
             vl=ebit, dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, cd_conta="3.11",
             ds_conta="Lucro/Prejuízo Consolidado do Período", vl=lucro,
             dt_ini=ini, dt_fim=fim),
    ]


def cenario_dfc_anual(cnpj: str = CNPJ_A, ano: int = 2023, fco: float = 150.0,
                      fci: float = -60.0, da: float = 45.0):
    ini, fim = f"{ano}-01-01", f"{ano}-12-31"
    return [
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DFC_MI", cd_conta="6.01",
             ds_conta="Caixa Líquido Atividades Operacionais", vl=fco,
             dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DFC_MI", cd_conta="6.01.01.02",
             ds_conta="Depreciação e Amortização", vl=da, dt_ini=ini, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DFC_MI", cd_conta="6.02",
             ds_conta="Caixa Líquido Atividades de Investimento", vl=fci,
             dt_ini=ini, dt_fim=fim),
    ]


def cenario_itr_trimestres(cnpj: str = CNPJ_A, ano: int = 2023,
                           isolados=(20.0, 25.0, 30.0)):
    """ITR com trimestre isolado E acumulado convivendo -- a patologia da regra 5.

    Q1: so 3 meses (isolado == acumulado)
    Q2: 3 meses (abr-jun) + 6 meses (jan-jun)
    Q3: 3 meses (jul-set) + 9 meses (jan-set)
    """
    q1, q2, q3 = isolados
    linhas = []
    add = linhas.append

    add(fato(cnpj=cnpj, dt_refer=f"{ano}-03-31", doc="ITR", cd_conta="3.11",
             ds_conta="Lucro/Prejuízo do Período", vl=q1,
             dt_ini=f"{ano}-01-01", dt_fim=f"{ano}-03-31"))

    add(fato(cnpj=cnpj, dt_refer=f"{ano}-06-30", doc="ITR", cd_conta="3.11",
             ds_conta="Lucro/Prejuízo do Período", vl=q2,
             dt_ini=f"{ano}-04-01", dt_fim=f"{ano}-06-30"))
    add(fato(cnpj=cnpj, dt_refer=f"{ano}-06-30", doc="ITR", cd_conta="3.11",
             ds_conta="Lucro/Prejuízo do Período", vl=q1 + q2,
             dt_ini=f"{ano}-01-01", dt_fim=f"{ano}-06-30"))

    add(fato(cnpj=cnpj, dt_refer=f"{ano}-09-30", doc="ITR", cd_conta="3.11",
             ds_conta="Lucro/Prejuízo do Período", vl=q3,
             dt_ini=f"{ano}-07-01", dt_fim=f"{ano}-09-30"))
    add(fato(cnpj=cnpj, dt_refer=f"{ano}-09-30", doc="ITR", cd_conta="3.11",
             ds_conta="Lucro/Prejuízo do Período", vl=q1 + q2 + q3,
             dt_ini=f"{ano}-01-01", dt_fim=f"{ano}-09-30"))
    return linhas


_FIM_TRI = {1: "03-31", 2: "06-30", 3: "09-30"}
_INI_TRI = {1: "01-01", 2: "04-01", 3: "07-01"}


def cenario_itr_completo(cnpj: str = CNPJ_A, ano: int = 2023, receita: float = 1000.0,
                         lucro: float = 80.0, ebit: float = 120.0, pl: float = 400.0,
                         fco: float = 150.0, fci: float = -60.0, da: float = 45.0):
    """ITR de T1 a T3 como a CVM entrega: balanco + DRE + DFC, isolado E acumulado.

    `cenario_itr_trimestres` cobre so o lucro, porque os testes da regra 5 nao
    precisam de mais. Aqui o trimestre vem completo -- e o que a interface
    encontra num arquivo real, e sem isso toda tela trimestral aparece vazia.
    """
    # Fracao do ano acumulada ao fim de cada trimestre.
    peso = {1: 0.22, 2: 0.48, 3: 0.74}
    linhas = []
    for tri in (1, 2, 3):
        fim = f"{ano}-{_FIM_TRI[tri]}"
        ini_iso = f"{ano}-{_INI_TRI[tri]}"
        ini_ac = f"{ano}-01-01"
        ac, ac_ant = peso[tri], peso.get(tri - 1, 0.0)
        iso = ac - ac_ant

        # Saldo patrimonial no fechamento do trimestre.
        pl_tri = pl * (1 + 0.02 * tri)
        pc, pnc = 300.0, 300.0
        total = pc + pnc + pl_tri
        linhas += [
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPA", cd_conta="1",
                 ds_conta="Ativo Total", vl=total, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPA", cd_conta="1.01",
                 ds_conta="Ativo Circulante", vl=total / 2, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPA", cd_conta="1.01.01",
                 ds_conta="Caixa e Equivalentes de Caixa", vl=100.0, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2",
                 ds_conta="Passivo Total", vl=total, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2.01",
                 ds_conta="Passivo Circulante", vl=pc, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2.01.04",
                 ds_conta="Empréstimos e Financiamentos", vl=120.0, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2.02",
                 ds_conta="Passivo Não Circulante", vl=pnc, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2.02.01",
                 ds_conta="Empréstimos e Financiamentos", vl=180.0, dt_fim=fim),
            fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="BPP", cd_conta="2.03",
                 ds_conta="Patrimônio Líquido Consolidado", vl=pl_tri, dt_fim=fim),
        ]

        # Fluxos: o isolado e o acumulado do exercicio, lado a lado.
        for janela, fator, ini in (("iso", iso, ini_iso), ("ac", ac, ini_ac)):
            if tri == 1 and janela == "ac":
                continue  # no T1 o isolado JA e o acumulado; a CVM nao repete
            linhas += [
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", cd_conta="3.01",
                     ds_conta="Receita de Venda de Bens e/ou Serviços",
                     vl=receita * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", cd_conta="3.02",
                     ds_conta="Custo dos Bens e/ou Serviços Vendidos",
                     vl=-600.0 * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", cd_conta="3.03",
                     ds_conta="Resultado Bruto", vl=(receita - 600.0) * fator,
                     dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", cd_conta="3.05",
                     ds_conta="Resultado Antes do Resultado Financeiro e dos Tributos",
                     vl=ebit * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", cd_conta="3.11",
                     ds_conta="Lucro/Prejuízo Consolidado do Período",
                     vl=lucro * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="DFC_MI",
                     cd_conta="6.01", ds_conta="Caixa Líquido Atividades Operacionais",
                     vl=fco * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="DFC_MI",
                     cd_conta="6.01.01.02", ds_conta="Depreciação e Amortização",
                     vl=da * fator, dt_ini=ini, dt_fim=fim),
                fato(cnpj=cnpj, dt_refer=fim, doc="ITR", demonstrativo="DFC_MI",
                     cd_conta="6.02", ds_conta="Caixa Líquido Atividades de Investimento",
                     vl=fci * fator, dt_ini=ini, dt_fim=fim),
            ]
    return linhas


def cenario_banco(cnpj: str = CNPJ_BANCO, ano: int = 2023, pl: float = 200.0,
                  lucro: float = 30.0):
    """Balanco e DRE no plano de BANCO, que nao e o industrial renumerado.

    Confrontado com ITUB4 (ITR CON 2026T2) em 13/09/2026. As diferencas que
    importam, e que ate entao os testes deste projeto nao reproduziam:

        nao ha circulante / nao-circulante no passivo;
        o patrimonio liquido e 2.08, e 2.03 e captacao ao custo amortizado;
        o resultado final da DRE e 3.09, nao 3.11.

    Usar o cenario industrial com o plano FINANCEIRO -- o que este arquivo
    fazia antes -- so "funcionava" enquanto o plano de banco apontava, errado,
    para os codigos industriais.
    """
    fim = f"{ano}-12-31"
    captacao = 700.0
    outros = 100.0
    total = captacao + outros + pl
    return [
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1",
             ds_conta="Ativo Total", vl=total, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1.01",
             ds_conta="Caixa e Equivalentes de Caixa", vl=50.0, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPA", cd_conta="1.02",
             ds_conta="Ativos Financeiros", vl=total - 50.0, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2",
             ds_conta="Passivo Total", vl=total, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.03",
             ds_conta="Passivos Financeiros ao Custo Amortizado", vl=captacao,
             dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.06",
             ds_conta="Outros Passivos", vl=outros, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="BPP", cd_conta="2.08",
             ds_conta="Patrimônio Líquido Consolidado", vl=pl, dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DRE", cd_conta="3.01",
             ds_conta="Receitas da Intermediação Financeira", vl=120.0,
             dt_ini=f"{ano}-01-01", dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DRE", cd_conta="3.02",
             ds_conta="Despesas da Intermediação Financeira", vl=-70.0,
             dt_ini=f"{ano}-01-01", dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DRE", cd_conta="3.03",
             ds_conta="Resultado Bruto Intermediação Financeira", vl=50.0,
             dt_ini=f"{ano}-01-01", dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DRE", cd_conta="3.05",
             ds_conta="Resultado Antes dos Tributos sobre o Lucro", vl=lucro * 1.2,
             dt_ini=f"{ano}-01-01", dt_fim=fim),
        fato(cnpj=cnpj, dt_refer=fim, demonstrativo="DRE", cd_conta="3.09",
             ds_conta="Lucro/Prejuízo Consolidado do Período", vl=lucro,
             dt_ini=f"{ano}-01-01", dt_fim=fim),
    ]


def cadastro(cnpj: str = CNPJ_A, setor: str = "Emp. Adm. Part.") -> pd.DataFrame:
    return pd.DataFrame(
        [{"CNPJ_CIA": cnpj, "DENOM_SOCIAL": "COMPANHIA TESTE S.A.", "CD_CVM": "99999",
          "SETOR_ATIV": setor, "SIT": "ATIVO"}]
    )
