"""Orquestracao da camada transform, na ordem em que as regras dependem umas
das outras.

    money.converter      escala (UNIDADE/MILHAR) -> reais
    dedup.versao_maxima  regra 2
    dedup.filtrar_ordem  regra 3  (mantendo PENULTIMO para restatement)
    periods.normalizar   regra 5  (trimestre isolado + Q4 derivado)
    restatement.resolver politica restated + flag
    basis.aplicar        regra 4  (base fixa por empresa)
    validations          identidade contabil

A ordem nao e negociavel. Resolver reapresentacao antes de normalizar periodo
compararia janelas diferentes; escolher base antes de normalizar periodo
contaria cobertura sobre periodos que ainda se sobrepoem.
"""

from __future__ import annotations

import pandas as pd

from transform import basis, dedup, money, periods, restatement, validations


def garantir_coluna_df(df: pd.DataFrame) -> pd.DataFrame:
    """`COLUNA_DF` faz parte da granularidade do fato, e so a DMPL a tem.

    A DMPL (mutacoes do patrimonio liquido) abre cada conta em colunas --
    Capital Social, Reservas de Lucro, Lucros Acumulados, etc. -- entao o
    mesmo CD_CONTA aparece varias vezes no mesmo periodo, uma por coluna.

    Sem esta dimensao na chave, duas coisas quebram: a derivacao do Q4 tenta
    casar N linhas com N linhas, e -- pior, porque e silenciosa -- a
    resolucao de reapresentacao mantem uma coluna e descarta as outras.

    Demonstrativos sem essa dimensao recebem string vazia, para que a chave
    tenha o mesmo formato em todos.
    """
    out = df.copy()
    if "COLUNA_DF" not in out.columns:
        out["COLUNA_DF"] = ""
    else:
        out["COLUNA_DF"] = out["COLUNA_DF"].fillna("").astype(str).str.strip()
    return out


def normalizar_fatos(fatos_brutos: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Aplica as seis regras e devolve os quadros prontos para carga."""
    em_reais = money.converter(garantir_coluna_df(fatos_brutos))
    em_reais, outra_moeda = money.somente_moeda_esperada(em_reais)

    # Regras 2 e 3. PENULTIMO e mantido porque `restatement` precisa dele.
    dedup_ok = dedup.filtrar_ordem_exerc(dedup.versao_maxima(em_reais), manter=None)

    # Regra 5.
    por_periodo = periods.normalizar(dedup_ok)
    soma_divergente = periods.conferir_soma_trimestres(por_periodo)

    # Politica de reapresentacao (restated + flag).
    resolvido = restatement.resolver(por_periodo)
    reapresentacoes = restatement.relatorio_divergencias(resolvido)

    # Regra 4, por ultimo: a cobertura e contada sobre periodos ja normalizados.
    com_base, relatorio_base = basis.aplicar(resolvido)

    identidades = validations.identidade_contabil(com_base)

    return {
        "fatos": com_base,
        "relatorio_base": relatorio_base,
        "reapresentacoes": reapresentacoes,
        "soma_trimestres_divergente": soma_divergente,
        "identidades": identidades,
        "outra_moeda": outra_moeda,
    }


def ultimo_documento(fatos: pd.DataFrame) -> pd.DataFrame:
    """Criterio de aceite: data e versao do ultimo documento CVM por empresa."""
    if fatos.empty:
        return pd.DataFrame(
            columns=["cnpj", "ultimo_doc_dt_refer", "ultimo_doc_versao", "ultimo_doc_tipo"]
        )
    df = fatos.copy()
    df["_dt"] = pd.to_datetime(df["DT_REFER"], errors="coerce")
    idx = df.groupby("CNPJ_CIA")["_dt"].idxmax()
    ultimo = df.loc[idx, ["CNPJ_CIA", "DT_REFER", "versao_int", "doc"]]
    return ultimo.rename(
        columns={
            "CNPJ_CIA": "cnpj",
            "DT_REFER": "ultimo_doc_dt_refer",
            "versao_int": "ultimo_doc_versao",
            "doc": "ultimo_doc_tipo",
        }
    ).reset_index(drop=True)


COLUNAS_FATO_DB = {
    "CNPJ_CIA": "cnpj",
    "DENOM_CIA": "denom_cia",
    "doc": "doc",
    "demonstrativo": "demonstrativo",
    "base": "base",
    "periodo": "periodo",
    "tipo_janela": "tipo_janela",
    "origem_periodo": "origem_periodo",
    "COLUNA_DF": "coluna_df",
    "CD_CONTA": "cd_conta",
    "DS_CONTA": "ds_conta",
    "VL_CONTA_NUM": "valor",
    "escala_fator": "escala_fator",
    "MOEDA": "moeda",
    "versao_int": "versao",
    "versoes_descartadas": "versoes_descartadas",
    "ORDEM_EXERC": "ordem_exerc",
    "DT_REFER": "dt_refer",
    "DT_INI_EXERC": "dt_ini_exerc",
    "DT_FIM_EXERC": "dt_fim_exerc",
    "valor_as_filed": "valor_as_filed",
    "valor_restated": "valor_restated",
    "divergente": "divergente",
    "n_publicacoes": "n_publicacoes",
    "base_escolhida": "base_escolhida",
    "criterio_base": "criterio_base",
    "src_archive": "src_archive",
    "src_file": "src_file",
    "src_line": "src_line",
    "src_sha256": "src_sha256",
    "src_derivacao": "src_derivacao",
}


def para_db(fatos: pd.DataFrame) -> pd.DataFrame:
    presentes = {k: v for k, v in COLUNAS_FATO_DB.items() if k in fatos.columns}
    return fatos[list(presentes)].rename(columns=presentes)
