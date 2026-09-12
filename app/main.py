"""Interface do sistema de suporte a decisao.

Regra que governa toda esta tela: o sistema EXPOE, nao aconselha. Nao ha card
de "barato/caro", nao ha score, nao ha preco-alvo. Todo numero exibido tem um
botao que abre a formula, as contas de origem e a linha do arquivo da CVM de
onde ele saiu.

    streamlit run app/main.py
"""

from __future__ import annotations

import json

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from app import dados
from transform import indicators, risk, triggers

st.set_page_config(page_title="B3 -- suporte a decisao", layout="wide")

AVISO = (
    "Sistema **descritivo**. Ele mostra dados, series historicas e as condicoes "
    "que voce configurou. Ele nao recomenda compra, venda ou alocacao, e nao "
    "calcula preco-alvo. A conclusao e sua."
)


def main() -> None:
    st.title("Renda variavel B3 -- suporte a decisao")
    st.caption(AVISO)

    try:
        empresas = dados.empresas()
    except FileNotFoundError as exc:
        st.error(str(exc))
        return

    if empresas.empty:
        st.warning("Banco vazio. Rode `python run.py tudo`.")
        return

    with st.sidebar:
        st.header("Selecao")
        rotulos = {
            f"{r.ticker or '(sem ticker)'} -- {r.denom_social}": r.cnpj
            for r in empresas.itertuples()
        }
        escolha = st.selectbox("Empresa", sorted(rotulos))
        cnpj = rotulos[escolha]
        ticker = escolha.split(" -- ")[0]
        st.divider()
        _ficha_da_fonte(empresas, cnpj)

    abas = st.tabs(
        ["Fundamentos", "Multiplos em percentil", "DuPont", "Risco", "Gatilhos",
         "Qualidade dos dados"]
    )
    with abas[0]:
        _fundamentos(cnpj)
    with abas[1]:
        _multiplos(cnpj, ticker)
    with abas[2]:
        _dupont(cnpj)
    with abas[3]:
        _risco(ticker)
    with abas[4]:
        _gatilhos(cnpj, ticker)
    with abas[5]:
        _qualidade(cnpj, ticker)


# ---------------------------------------------------------------------------
def _ficha_da_fonte(empresas: pd.DataFrame, cnpj: str) -> None:
    """Criterio de aceite: data e versao do ultimo documento CVM processado."""
    linha = empresas[empresas["cnpj"] == cnpj].iloc[0]
    st.subheader("Ultimo documento CVM")
    st.metric("Data de referencia", str(linha.get("ultimo_doc_dt_refer") or "-"))
    st.write(
        f"**Tipo:** {linha.get('ultimo_doc_tipo') or '-'} &nbsp;&nbsp; "
        f"**Versao:** {linha.get('ultimo_doc_versao') or '-'}"
    )
    st.write(f"**Plano de contas:** `{linha['plano_contas']}`")
    st.caption(f"Classificacao: {linha['origem_classificacao']}")
    st.write(f"**Base contabil fixada:** `{linha.get('base_escolhida') or '-'}`")
    st.caption(
        f"{linha.get('criterio_base') or '-'} "
        f"(CON: {linha.get('cobertura_con')} periodos, IND: {linha.get('cobertura_ind')})"
    )


def _fundamentos(cnpj: str) -> None:
    ind = dados.indicadores(cnpj)
    if ind.empty:
        st.info("Sem indicadores calculados para esta empresa.")
        return

    periodos = sorted(ind["periodo"].unique(), reverse=True)
    periodo = st.selectbox("Periodo", periodos, key="fund_periodo")
    sel = ind[ind["periodo"] == periodo].sort_values("indicador")

    st.dataframe(
        sel[["indicador", "valor", "status", "motivo", "formula", "contas_origem"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Rastreabilidade")
    st.caption(
        "Escolha um indicador para ver a formula, as contas usadas e a linha exata "
        "do arquivo da CVM de onde cada numero veio."
    )
    alvo = st.selectbox("Indicador", sel["indicador"].tolist(), key="fund_indicador")
    linha = sel[sel["indicador"] == alvo].iloc[0]

    c1, c2 = st.columns([2, 1])
    with c1:
        st.code(linha["formula"], language="text")
    with c2:
        st.metric(alvo, "-" if pd.isna(linha["valor"]) else f"{linha['valor']:,.6g}")
        st.write(f"status: `{linha['status']}`")
    if linha["motivo"]:
        st.warning(linha["motivo"])

    ent = dados.entradas(cnpj, periodo, alvo)
    if ent.empty:
        st.info("Indicador sem entradas: nao foi calculado.")
    else:
        st.dataframe(ent, width="stretch", hide_index=True)
        st.caption(
            "A coluna `referencia` aponta arquivo e linha fisica: "
            "`sed -n '<linha>p' <arquivo>` devolve o registro original."
        )

    with st.expander("Todas as contas do periodo, como vieram do arquivo"):
        st.dataframe(dados.fatos(cnpj, periodo), width="stretch", hide_index=True)


def _multiplos(cnpj: str, ticker: str) -> None:
    st.caption(
        "Multiplo absoluto nao e exibido isolado: o que importa aqui e onde o "
        "multiplo de hoje esta dentro da propria historia da empresa."
    )
    cob = dados.cobertura(ticker)
    if not cob.empty and cob.iloc[0]["status"] != "AJUSTADO":
        st.error(
            f"Serie de precos de {ticker}: **{cob.iloc[0]['status']}** -- "
            f"{cob.iloc[0]['motivo']} Enquanto isso nao for resolvido, qualquer "
            "multiplo historico derivado desta serie e invalido."
        )

    precos = dados.precos(ticker)
    if precos.empty:
        st.info(f"Sem serie de precos para {ticker}.")
        return

    ind = dados.indicadores(cnpj)
    lucro = ind[(ind["indicador"] == "roe") & (ind["status"] == "OK")]
    if lucro.empty:
        st.info(
            "P/L e P/VP exigem quantidade de acoes em circulacao, que nao vem nos "
            "arquivos de demonstracoes da CVM. Preencha "
            "`data/manual/shares_outstanding.csv` para habilitar os multiplos. "
            "Ate la, eles aparecem como faltando -- nunca estimados."
        )

    serie = pd.Series(precos["fech_aj_split"].to_numpy(),
                      index=pd.DatetimeIndex(precos["data"]))
    pct = indicators.percentil_historico(serie.dropna())
    if pct.empty or pct[[c for c in pct.columns if c.startswith("pct_")]].isna().all().all():
        st.info("Historico insuficiente para percentil de 5 ou 10 anos.")
    else:
        st.altair_chart(
            alt.Chart(pct.reset_index()).mark_line().encode(
                x=alt.X("data:T", title=""),
                y=alt.Y("pct_10a:Q", title="percentil do proprio historico (10a)",
                        scale=alt.Scale(domain=[0, 1])),
            ).properties(height=220),
            use_container_width=True,
        )
        st.caption(
            "Leitura: 0,05 significa que o valor de hoje e menor que 95% das "
            "observacoes da propria empresa na janela. Nao significa barato."
        )
        st.dataframe(pct.tail(5), width="stretch")


def _dupont(cnpj: str) -> None:
    ind = dados.indicadores(cnpj)
    partes = ["margem_liquida", "giro_ativo", "alavancagem", "roe", "roe_dupont"]
    sel = ind[ind["indicador"].isin(partes)]
    if sel.empty:
        st.info("DuPont nao disponivel para esta empresa.")
        return

    na = sel[sel["status"] == "NAO_SE_APLICA"]
    if not na.empty:
        st.warning(
            "Indicadores marcados como nao aplicaveis ao plano de contas do setor: "
            + "; ".join(sorted(set(na["indicador"])))
        )
        st.caption(na.iloc[0]["motivo"])

    largo = sel[sel["status"] == "OK"].pivot_table(
        index="periodo", columns="indicador", values="valor"
    ).sort_index()
    if largo.empty:
        st.info("Sem periodo com DuPont completo.")
        return

    st.dataframe(largo, width="stretch")
    longo = largo.reset_index().melt("periodo", var_name="componente", value_name="valor")
    st.altair_chart(
        alt.Chart(longo[longo["componente"] != "roe_dupont"]).mark_line(point=True).encode(
            x=alt.X("periodo:O", title=""),
            y=alt.Y("valor:Q", title=""),
            color="componente:N",
        ).properties(height=260),
        use_container_width=True,
    )
    st.code("ROE = margem_liquida x giro_ativo x alavancagem", language="text")
    if "roe" in largo and "roe_dupont" in largo:
        dif = (largo["roe_dupont"] - largo["roe"]).abs().max()
        st.caption(f"Maior diferenca entre o ROE direto e o produto DuPont: {dif:.2e}")


def _risco(ticker: str) -> None:
    precos = dados.precos(ticker)
    if precos.empty:
        st.info(f"Sem serie de precos para {ticker}.")
        return
    st.caption("Risco usa `fech_aj_total` (ajustado por quantidade E provento).")

    s = pd.Series(precos["fech_aj_total"].to_numpy(), index=pd.DatetimeIndex(precos["data"]))
    retornos = s.pct_change().dropna()

    vol = risk.volatilidade_anualizada(retornos, ticker)
    dd = risk.drawdown_maximo(s, ticker)

    c1, c2 = st.columns(2)
    for col, ind in ((c1, vol), (c2, dd)):
        with col:
            col.metric(ind.nome, "-" if ind.valor is None else f"{ind.valor:.2%}")
            col.code(ind.formula, language="text")
            if ind.motivo:
                col.warning(ind.motivo)
            if ind.extra:
                col.caption(json.dumps(ind.extra, ensure_ascii=False))

    curva = risk.drawdown(s).reset_index(names="data")
    st.altair_chart(
        alt.Chart(curva).mark_area().encode(
            x=alt.X("data:T", title=""),
            y=alt.Y("drawdown:Q", title="drawdown", axis=alt.Axis(format="%")),
        ).properties(height=220),
        use_container_width=True,
    )

    st.subheader("Correlacao da carteira")
    escolhidos = st.multiselect("Ativos", dados.tickers(), default=[ticker])
    if len(escolhidos) >= 2:
        series = {}
        for t in escolhidos:
            p = dados.precos(t)
            if p.empty:
                continue
            st_ = pd.Series(p["fech_aj_total"].to_numpy(), index=pd.DatetimeIndex(p["data"]))
            series[t] = st_.pct_change().dropna()
        corr, n = risk.matriz_correlacao(series)
        st.dataframe(corr.style.format("{:.2f}"), width="stretch")
        st.caption("Observacoes em comum por par (celula vazia = menos de 60 pregoes):")
        st.dataframe(n, width="stretch")


def _gatilhos(cnpj: str, ticker: str) -> None:
    st.caption(
        "Gatilhos sao **seus**, definidos em `config/triggers.yml`. Quando um "
        "dispara, a tela mostra a condicao satisfeita e os numeros. Nunca uma acao."
    )
    ind = dados.indicadores(cnpj)
    if ind.empty:
        st.info("Sem indicadores para avaliar.")
        return

    periodo = st.selectbox("Periodo", sorted(ind["periodo"].unique(), reverse=True),
                           key="gat_periodo")
    sel = ind[ind["periodo"] == periodo]
    metricas = {
        r["indicador"]: {"valor": None if pd.isna(r["valor"]) else float(r["valor"]),
                         "status": r["status"], "motivo": r["motivo"],
                         "origem": r["contas_origem"]}
        for _, r in sel.iterrows()
    }

    precos = dados.precos(ticker)
    if not precos.empty:
        s = precos["fech_aj_total"].to_numpy()
        metricas["drawdown_atual"] = {
            "valor": float(s[-1] / np.maximum.accumulate(s)[-1] - 1.0), "status": "OK",
            "motivo": None, "origem": "serie fech_aj_total",
        }

    for escopo, entidade in (("EMPRESA", cnpj), ("TICKER", ticker)):
        for r in triggers.avaliar_todos(entidade, escopo, metricas):
            caixa = {
                triggers.DISPAROU: st.success,
                triggers.NAO_DISPAROU: st.info,
                triggers.INDETERMINADO: st.warning,
            }[r.status]
            with st.container(border=True):
                caixa(f"**{r.nome}** ({r.status})")
                st.caption(r.descricao)
                st.write(r.explicacao)
                detalhe = pd.DataFrame(
                    [{"metrica": c.metrica, "observado": c.valor_observado,
                      "operador": c.operador, "alvo": str(c.alvo),
                      "satisfeita": c.satisfeita, "motivo": c.motivo,
                      "contas_origem": c.origem}
                     for c in r.condicoes]
                )
                st.dataframe(detalhe, width="stretch", hide_index=True)


def _qualidade(cnpj: str, ticker: str) -> None:
    st.subheader("Identidade contabil")
    st.code(
        "principal:  Ativo Total (1) == Passivo Total (2)\n"
        "decomposta: Passivo Total (2) == PC (2.01) + PNC (2.02) + PL (2.03)\n"
        "Obs.: na CVM a conta 2 JA INCLUI o patrimonio liquido.",
        language="text",
    )
    ident = dados.identidade(cnpj)
    if ident.empty:
        st.info("Sem teste de identidade para esta empresa.")
    else:
        cont = ident["status"].value_counts()
        c = st.columns(3)
        for col, k in zip(c, ("OK", "FALHA", "FALTANDO")):
            col.metric(k, int(cont.get(k, 0)))
        st.dataframe(ident, width="stretch", hide_index=True)

    st.subheader("Reapresentacao de exercicio anterior")
    st.caption(
        "Politica: a serie usa o valor publicado mais recentemente (restated). "
        "O valor original e preservado e a divergencia aparece aqui."
    )
    rea = dados.consultar("SELECT * FROM reapresentacao WHERE cnpj = ?", (cnpj,))
    st.dataframe(rea, width="stretch", hide_index=True) if not rea.empty else st.write(
        "Nenhuma reapresentacao detectada."
    )

    st.subheader("Cobertura de ajuste de precos")
    cob = dados.cobertura(ticker)
    st.dataframe(cob, width="stretch", hide_index=True) if not cob.empty else st.write("-")
    susp = dados.consultar("SELECT * FROM evento_suspeito WHERE ticker = ?", (ticker,))
    if not susp.empty:
        st.warning(
            f"{len(susp)} pregao(s) com salto compativel com evento corporativo. "
            "Linhas com `tem_evento_cadastrado = false` precisam ser resolvidas em "
            "`data/manual/corporate_events.csv`."
        )
        st.dataframe(susp, width="stretch", hide_index=True)

    st.subheader("De-para CNPJ x ticker")
    div = dados.consultar("SELECT * FROM depara_divergencia")
    st.dataframe(div, width="stretch", hide_index=True) if not div.empty else st.write(
        "Sem divergencia entre FCA e COTAHIST."
    )

    st.subheader("Proveniencia dos arquivos")
    st.dataframe(
        dados.consultar("SELECT url, sha256, bytes, baixado_em FROM fonte ORDER BY baixado_em DESC"),
        width="stretch", hide_index=True,
    )


if __name__ == "__main__":
    main()
