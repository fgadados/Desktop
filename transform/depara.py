"""Regra 1: ticker nao existe nos arquivos da CVM. A chave e o CNPJ.

O de-para e construido a partir de duas fontes oficiais, sem raspagem:

* CVM/FCA, tabela `valor_mobiliario` -- e o unico documento da propria CVM em
  que a companhia declara o `Codigo_Negociacao` de cada valor mobiliario, com
  data de inicio e fim de negociacao;
* B3/COTAHIST -- o universo de codigos efetivamente negociados no mercado a
  vista, lote padrao.

O cruzamento e uma intersecao explicita. Divergencia dos dois lados nao e
resolvida em silencio: vira relatorio.
"""

from __future__ import annotations

import re

import pandas as pd

_SO_DIGITOS = re.compile(r"\D")


def normalizar_cnpj(serie: pd.Series) -> pd.Series:
    """CNPJ como 14 digitos. A CVM alterna entre formatado e cru entre anos."""
    s = serie.astype("string").fillna("")
    return s.map(lambda x: _SO_DIGITOS.sub("", x).zfill(14) if x else pd.NA).astype("string")


def _ultima_versao_fca(fca: pd.DataFrame) -> pd.DataFrame:
    """Mesma logica da regra 2 aplicada ao FCA: so a ultima versao do ano."""
    out = fca.copy()
    out["_versao"] = pd.to_numeric(out["Versao"], errors="coerce")
    chave = ["CNPJ_Companhia", "Data_Referencia"]
    maximo = out.groupby(chave, dropna=False)["_versao"].transform("max")
    return out[out["_versao"] == maximo].drop(columns=["_versao"])


def construir(fca_vm: pd.DataFrame, cotahist: pd.DataFrame | None = None) -> pd.DataFrame:
    """Monta a tabela CNPJ -> ticker.

    `cotahist` deve ser o COTAHIST ja filtrado para acoes a vista
    (`etl.b3_cotahist.somente_acoes_a_vista`). Se vier None, a coluna
    `negociado_b3` fica nula e nada e descartado -- o de-para segue valido,
    apenas sem a confirmacao da B3.
    """
    if fca_vm.empty:
        return pd.DataFrame(
            columns=["cnpj", "ticker", "valor_mobiliario", "classe", "mercado",
                     "data_inicio", "data_fim", "negociado_b3", "src_file", "src_line"]
        )

    fca = _ultima_versao_fca(fca_vm)
    out = pd.DataFrame(
        {
            "cnpj": normalizar_cnpj(fca["CNPJ_Companhia"]),
            "ticker": fca["Codigo_Negociacao"].astype("string").str.upper().str.strip(),
            "valor_mobiliario": fca["Valor_Mobiliario"],
            "classe": fca.get("Sigla_Classe_Acao_Preferencial"),
            "mercado": fca.get("Mercado"),
            "data_inicio": pd.to_datetime(fca.get("Data_Inicio_Negociacao"), errors="coerce"),
            "data_fim": pd.to_datetime(fca.get("Data_Fim_Negociacao"), errors="coerce"),
            "src_file": fca["src_file"],
            "src_line": fca["src_line"],
        }
    )
    out = out[out["ticker"].notna() & (out["ticker"] != "")]

    if cotahist is None or cotahist.empty:
        out["negociado_b3"] = pd.NA
    else:
        negociados = set(
            cotahist["CODNEG"].astype("string").str.upper().str.strip().dropna().unique()
        )
        out["negociado_b3"] = out["ticker"].isin(negociados)

    # Um mesmo ticker pode aparecer em varios anos do FCA. Mantem a linha mais
    # recente por (cnpj, ticker), preservando a origem.
    out = out.sort_values(["cnpj", "ticker", "src_line"]).drop_duplicates(
        ["cnpj", "ticker"], keep="last"
    )
    return out.reset_index(drop=True)


def divergencias(depara: pd.DataFrame, cotahist: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Os dois lados do desencontro, para exibicao. Nada e corrigido.

    * `fca_sem_pregao`: ticker declarado no FCA que nao aparece no COTAHIST
      (papel cancelado, nunca negociado, ou erro de digitacao no formulario).
    * `pregao_sem_fca`: ticker negociado que nao tem CNPJ associado. Enquanto
      estiver nesta lista, nao ha como ligar preco a fundamento -- e o sistema
      diz isso, em vez de chutar o emissor pelo prefixo de 4 letras.
    """
    negociados = pd.DataFrame(
        {
            "ticker": sorted(
                set(cotahist["CODNEG"].astype("string").str.upper().str.strip().dropna())
            )
        }
    )
    fca_sem_pregao = depara[depara["negociado_b3"] == False][  # noqa: E712
        ["cnpj", "ticker", "valor_mobiliario", "data_fim", "src_file", "src_line"]
    ].reset_index(drop=True)
    pregao_sem_fca = negociados[~negociados["ticker"].isin(set(depara["ticker"]))].reset_index(
        drop=True
    )
    return {"fca_sem_pregao": fca_sem_pregao, "pregao_sem_fca": pregao_sem_fca}


def cnpj_de(depara: pd.DataFrame, ticker: str) -> str | None:
    m = depara[depara["ticker"] == ticker.upper()]
    if m.empty:
        return None
    valores = sorted(set(m["cnpj"].dropna()))
    if len(valores) > 1:
        raise ValueError(
            f"ticker {ticker} aponta para mais de um CNPJ no FCA: {valores}. "
            "Divergencia de fonte -- resolva no relatorio antes de usar."
        )
    return valores[0]
