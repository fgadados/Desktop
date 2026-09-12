"""Camada analitica: indicadores com linhagem obrigatoria.

Nenhum card de multiplo absoluto. O que se constroi aqui:

* multiplo em percentil e z-score do proprio historico da empresa (5 e 10 anos);
* decomposicao DuPont do ROE (margem liquida x giro do ativo x alavancagem);
* cobertura do dividendo por fluxo de caixa livre -- nao por lucro contabil;
* insumos de risco (a agregacao fica em `transform/risk.py`).

Toda funcao devolve `lineage.Indicador`, que so aceita status OK acompanhado de
formula e de pelo menos uma `Entrada` rastreada ate (arquivo, linha).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from transform import lineage as ln
from transform.sector import Resolvedor

TRIMESTRES_TTM = 4


# ---------------------------------------------------------------------------
# Conceitos -> valores, periodo a periodo
# ---------------------------------------------------------------------------
def montar_fundamentos(
    fatos: pd.DataFrame, plano_por_cnpj: dict[str, str], conceitos: list[str]
) -> pd.DataFrame:
    """Resolve cada conceito para cada (CNPJ, periodo). Formato longo.

    Colunas: cnpj, periodo, conceito, valor, status, motivo e a linhagem
    (cd_conta, ds_conta, src_file, src_line, ...).
    """
    linhas = []
    if fatos.empty:
        return pd.DataFrame(
            columns=["cnpj", "periodo", "conceito", "valor", "status", "motivo",
                     "cd_conta", "ds_conta", "src_file", "src_line", "src_archive"]
        )

    for (cnpj, periodo), grupo in fatos.groupby(["CNPJ_CIA", "periodo"], dropna=False):
        plano = plano_por_cnpj.get(cnpj, "INDEFINIDO")
        r = Resolvedor(plano)
        for conceito in conceitos:
            res = r.resolver(grupo, conceito)
            e = res.entrada
            linhas.append(
                {
                    "cnpj": cnpj,
                    "periodo": periodo,
                    "plano": plano,
                    "conceito": conceito,
                    "valor": res.valor,
                    "status": res.status,
                    "motivo": res.motivo,
                    "cd_conta": e.cd_conta if e else None,
                    "ds_conta": e.ds_conta if e else None,
                    "demonstrativo": e.demonstrativo if e else None,
                    "base": e.base if e else None,
                    "versao": e.versao if e else None,
                    "ordem_exerc": e.ordem_exerc if e else None,
                    "dt_refer": e.dt_refer if e else None,
                    "src_archive": e.src_archive if e else None,
                    "src_file": e.src_file if e else None,
                    "src_line": e.src_line if e else None,
                    "src_sha256": e.src_sha256 if e else None,
                }
            )
    return pd.DataFrame(linhas)


def _entrada_de(linha: pd.Series) -> ln.Entrada:
    return ln.Entrada(
        rotulo=linha["conceito"],
        valor=None if pd.isna(linha["valor"]) else float(linha["valor"]),
        cd_conta=linha.get("cd_conta"),
        ds_conta=linha.get("ds_conta"),
        demonstrativo=linha.get("demonstrativo"),
        base=linha.get("base"),
        versao=linha.get("versao"),
        ordem_exerc=linha.get("ordem_exerc"),
        dt_refer=linha.get("dt_refer"),
        src_archive=linha.get("src_archive"),
        src_file=linha.get("src_file"),
        src_line=None if pd.isna(linha.get("src_line")) else int(linha["src_line"]),
        src_sha256=linha.get("src_sha256"),
    )


class Cesta:
    """Acesso indexado aos fundamentos de uma empresa/periodo."""

    def __init__(self, fundamentos: pd.DataFrame, cnpj: str, periodo: str):
        self.cnpj, self.periodo = cnpj, periodo
        sel = fundamentos[
            (fundamentos["cnpj"] == cnpj) & (fundamentos["periodo"] == periodo)
        ]
        self._por_conceito = {r["conceito"]: r for _, r in sel.iterrows()}

    def pegar(self, conceito: str):
        """Devolve (valor, Entrada, status, motivo)."""
        r = self._por_conceito.get(conceito)
        if r is None:
            return None, None, ln.FALTANDO, f"conceito '{conceito}' nao resolvido"
        status = "OK" if r["status"] == "OK" else (
            ln.NAO_SE_APLICA if r["status"] == "NAO_SE_APLICA" else ln.FALTANDO
        )
        valor = None if pd.isna(r["valor"]) else float(r["valor"])
        return valor, _entrada_de(r), status, r["motivo"]


def _combinar(cesta: Cesta, conceitos: list[str]):
    """Junta varios conceitos; devolve (valores, entradas, status, motivo)."""
    valores, entradas = {}, []
    for c in conceitos:
        v, e, st, mt = cesta.pegar(c)
        if st == ln.NAO_SE_APLICA:
            return None, entradas, ln.NAO_SE_APLICA, mt
        if st != "OK" or v is None:
            return None, entradas, ln.FALTANDO, f"{c}: {mt or 'ausente'}"
        valores[c] = v
        if e:
            entradas.append(e)
    return valores, entradas, "OK", None


def _razao(cesta, nome, num, den, formula, *, escala=1.0):
    vals, ents, st, mt = _combinar(cesta, [num, den])
    if st != "OK":
        cons = ln.nao_se_aplica if st == ln.NAO_SE_APLICA else None
        if cons:
            return cons(cesta.cnpj, cesta.periodo, nome, formula, mt)
        return ln.faltando(cesta.cnpj, cesta.periodo, nome, formula, mt, ents)
    if vals[den] == 0:
        return ln.Indicador(
            cesta.cnpj, cesta.periodo, nome, None, formula, ln.DIVIDIR_POR_ZERO,
            tuple(ents), f"{den} = 0",
        )
    return ln.Indicador(
        cesta.cnpj, cesta.periodo, nome, vals[num] / vals[den] * escala, formula,
        ln.OK, tuple(ents),
    )


# ---------------------------------------------------------------------------
# DuPont
# ---------------------------------------------------------------------------
def dupont(fundamentos: pd.DataFrame, cnpj: str, periodo: str) -> list[ln.Indicador]:
    """ROE decomposto: margem liquida x giro do ativo x alavancagem.

        ROE = (LL / Receita) x (Receita / Ativo) x (Ativo / PL)

    O produto dos tres reproduz LL/PL por construcao. O sistema calcula os
    quatro e verifica a identidade; divergencia vira motivo, nao correcao.
    """
    c = Cesta(fundamentos, cnpj, periodo)
    out = [
        _razao(c, "margem_liquida", "lucro_liquido", "receita_liquida",
               "margem_liquida = lucro_liquido / receita_liquida"),
        _razao(c, "giro_ativo", "receita_liquida", "ativo_total",
               "giro_ativo = receita_liquida / ativo_total"),
        _razao(c, "alavancagem", "ativo_total", "patrimonio_liquido",
               "alavancagem = ativo_total / patrimonio_liquido"),
        _razao(c, "roe", "lucro_liquido", "patrimonio_liquido",
               "roe = lucro_liquido / patrimonio_liquido"),
    ]

    partes = {i.nome: i for i in out}
    tres = ["margem_liquida", "giro_ativo", "alavancagem"]
    if all(partes[n].status == ln.OK for n in tres):
        produto = float(np.prod([partes[n].valor for n in tres]))
        entradas = tuple(e for n in tres for e in partes[n].entradas)
        extra = {}
        if partes["roe"].status == ln.OK:
            extra = {
                "roe_direto": partes["roe"].valor,
                "diferenca_identidade": produto - partes["roe"].valor,
            }
        out.append(
            ln.Indicador(
                cnpj, periodo, "roe_dupont", produto,
                "roe_dupont = margem_liquida x giro_ativo x alavancagem",
                ln.OK, entradas, extra=extra,
            )
        )
    else:
        motivo = "; ".join(
            f"{n}: {partes[n].motivo}" for n in tres if partes[n].status != ln.OK
        )
        status = (
            ln.NAO_SE_APLICA
            if any(partes[n].status == ln.NAO_SE_APLICA for n in tres)
            else ln.FALTANDO
        )
        out.append(
            ln.Indicador(
                cnpj, periodo, "roe_dupont", None,
                "roe_dupont = margem_liquida x giro_ativo x alavancagem",
                status, (), motivo,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Caixa, EBITDA e divida
# ---------------------------------------------------------------------------
def fluxo_e_divida(fundamentos: pd.DataFrame, cnpj: str, periodo: str) -> list[ln.Indicador]:
    c = Cesta(fundamentos, cnpj, periodo)
    out: list[ln.Indicador] = []

    # Fluxo de caixa livre = FCO + FCI. O FCI ja vem negativo quando ha
    # investimento, entao a soma e a definicao correta -- nao a subtracao.
    vals, ents, st, mt = _combinar(c, ["fco", "fci"])
    formula_fcl = "fcl = fco + fci  (FCI da CVM ja vem com sinal de saida)"
    if st == "OK":
        out.append(
            ln.Indicador(cnpj, periodo, "fcl", vals["fco"] + vals["fci"], formula_fcl,
                         ln.OK, tuple(ents))
        )
    elif st == ln.NAO_SE_APLICA:
        out.append(ln.nao_se_aplica(cnpj, periodo, "fcl", formula_fcl, mt))
    else:
        out.append(ln.faltando(cnpj, periodo, "fcl", formula_fcl, mt, ents))

    # Divida bruta = curto + longo prazo.
    formula_db = "divida_bruta = divida_curto_prazo + divida_longo_prazo"
    r = Resolvedor(_plano_de(fundamentos, cnpj))
    aplicavel, motivo_na = r.conceito_aplicavel("divida_bruta")
    if not aplicavel:
        out.append(ln.nao_se_aplica(cnpj, periodo, "divida_bruta", formula_db, motivo_na))
        out.append(
            ln.nao_se_aplica(
                cnpj, periodo, "divida_liquida",
                "divida_liquida = divida_bruta - caixa_equivalentes", motivo_na,
            )
        )
    else:
        vals, ents, st, mt = _combinar(c, ["divida_curto_prazo", "divida_longo_prazo"])
        if st == "OK":
            db = vals["divida_curto_prazo"] + vals["divida_longo_prazo"]
            out.append(ln.Indicador(cnpj, periodo, "divida_bruta", db, formula_db,
                                    ln.OK, tuple(ents)))
            cx, ecx, st2, mt2 = c.pegar("caixa_equivalentes")
            formula_dl = "divida_liquida = divida_bruta - caixa_equivalentes"
            if st2 == "OK" and cx is not None:
                out.append(
                    ln.Indicador(cnpj, periodo, "divida_liquida", db - cx, formula_dl,
                                 ln.OK, tuple(ents) + (ecx,))
                )
            else:
                out.append(ln.faltando(cnpj, periodo, "divida_liquida", formula_dl,
                                       f"caixa_equivalentes: {mt2}", ents))
        else:
            out.append(ln.faltando(cnpj, periodo, "divida_bruta", formula_db, mt, ents))
            out.append(
                ln.faltando(
                    cnpj, periodo, "divida_liquida",
                    "divida_liquida = divida_bruta - caixa_equivalentes",
                    f"divida_bruta: {mt}", ents,
                )
            )

    # EBITDA. Decisao do usuario: EBITDA e obrigatorio; sem D&A identificavel
    # na DFC, o indicador fica FALTANDO. EV/EBIT nao e calculado.
    formula_ebitda = "ebitda = ebit + depreciacao_amortizacao"
    vals, ents, st, mt = _combinar(c, ["ebit", "depreciacao_amortizacao"])
    if st == "OK":
        # A D&A entra na DFC indireta como ajuste positivo ao lucro; usamos o
        # valor absoluto para nao depender do sinal de cada companhia.
        da = abs(vals["depreciacao_amortizacao"])
        out.append(
            ln.Indicador(cnpj, periodo, "ebitda", vals["ebit"] + da, formula_ebitda,
                         ln.OK, tuple(ents),
                         extra={"da_sinal_original": vals["depreciacao_amortizacao"]})
        )
    elif st == ln.NAO_SE_APLICA:
        out.append(ln.nao_se_aplica(cnpj, periodo, "ebitda", formula_ebitda, mt))
    else:
        out.append(ln.faltando(cnpj, periodo, "ebitda", formula_ebitda, mt, ents))

    return out


def _plano_de(fundamentos: pd.DataFrame, cnpj: str) -> str:
    sel = fundamentos[fundamentos["cnpj"] == cnpj]
    return str(sel["plano"].iloc[0]) if not sel.empty else "INDEFINIDO"


# ---------------------------------------------------------------------------
# Cobertura do dividendo por fluxo de caixa livre
# ---------------------------------------------------------------------------
def cobertura_dividendo(
    fundamentos: pd.DataFrame, dividendos_pagos: pd.DataFrame, cnpj: str, periodo: str
) -> list[ln.Indicador]:
    """Cobertura por FCL, nao por lucro contabil.

    `dividendos_pagos` vem do fluxo de financiamento da DFC (saida de caixa
    efetiva no periodo), com as colunas cnpj, periodo, valor e linhagem. Lucro
    contabil nao cobre dividendo: caixa cobre.
    """
    c = Cesta(fundamentos, cnpj, periodo)
    fcl_ind = [i for i in fluxo_e_divida(fundamentos, cnpj, periodo) if i.nome == "fcl"][0]
    formula = "cobertura_dividendo_fcl = fcl / dividendos_pagos_caixa"

    sel = dividendos_pagos[
        (dividendos_pagos["cnpj"] == cnpj) & (dividendos_pagos["periodo"] == periodo)
    ]
    if sel.empty:
        return [ln.faltando(cnpj, periodo, "cobertura_dividendo_fcl", formula,
                            "dividendos pagos nao identificados na DFC do periodo")]
    if fcl_ind.status != ln.OK:
        return [ln.Indicador(cnpj, periodo, "cobertura_dividendo_fcl", None, formula,
                             fcl_ind.status, (), f"fcl: {fcl_ind.motivo}")]

    div = abs(float(sel["valor"].sum()))
    ent_div = ln.Entrada(
        rotulo="dividendos_pagos_caixa", valor=div,
        cd_conta=str(sel["cd_conta"].iloc[0]), ds_conta=str(sel["ds_conta"].iloc[0]),
        demonstrativo="DFC_MI", src_file=sel["src_file"].iloc[0],
        src_line=int(sel["src_line"].iloc[0]) if pd.notna(sel["src_line"].iloc[0]) else None,
    )
    if div == 0:
        return [ln.Indicador(cnpj, periodo, "cobertura_dividendo_fcl", None, formula,
                             ln.DIVIDIR_POR_ZERO, fcl_ind.entradas + (ent_div,),
                             "dividendos pagos = 0 no periodo")]

    lucro, ent_l, st_l, _ = c.pegar("lucro_liquido")
    extra = {}
    if st_l == "OK" and lucro not in (None, 0):
        extra["payout_sobre_lucro"] = div / lucro
    return [
        ln.Indicador(
            cnpj, periodo, "cobertura_dividendo_fcl", fcl_ind.valor / div, formula,
            ln.OK, fcl_ind.entradas + (ent_div,), extra=extra,
        )
    ]


# ---------------------------------------------------------------------------
# TTM: 12 meses moveis a partir de trimestres isolados
# ---------------------------------------------------------------------------
def ttm(fundamentos: pd.DataFrame, conceito: str) -> pd.DataFrame:
    """Soma movel de 4 trimestres isolados por empresa.

    Exige os 4 trimestres presentes: janela incompleta nao vira estimativa,
    vira ausencia. `periodos_usados` guarda quais entraram na soma.
    """
    sel = fundamentos[
        (fundamentos["conceito"] == conceito)
        & (fundamentos["status"] == "OK")
        & (fundamentos["periodo"].str.contains("T", na=False))
    ].copy()
    if sel.empty:
        return pd.DataFrame(columns=["cnpj", "periodo", "valor_ttm", "periodos_usados",
                                     "origens"])

    sel["_ano"] = sel["periodo"].str.slice(0, 4).astype(int)
    sel["_tri"] = sel["periodo"].str.slice(5).astype(int)
    sel = sel.sort_values(["cnpj", "_ano", "_tri"])

    linhas = []
    for cnpj, g in sel.groupby("cnpj", sort=False):
        g = g.reset_index(drop=True)
        for i in range(TRIMESTRES_TTM - 1, len(g)):
            janela = g.iloc[i - TRIMESTRES_TTM + 1 : i + 1]
            esperado = _sequencia_ok(janela)
            if not esperado:
                continue
            linhas.append(
                {
                    "cnpj": cnpj,
                    "periodo": g.loc[i, "periodo"],
                    "valor_ttm": float(janela["valor"].sum()),
                    "periodos_usados": ",".join(janela["periodo"]),
                    "origens": " | ".join(
                        f"{f}:{int(l)}" for f, l in zip(janela["src_file"], janela["src_line"])
                        if pd.notna(l)
                    ),
                }
            )
    return pd.DataFrame(linhas)


def _sequencia_ok(janela: pd.DataFrame) -> bool:
    """Os 4 trimestres precisam ser consecutivos, sem buraco."""
    idx = [a * 4 + t for a, t in zip(janela["_ano"], janela["_tri"])]
    return len(idx) == TRIMESTRES_TTM and idx == list(range(idx[0], idx[0] + TRIMESTRES_TTM))


# ---------------------------------------------------------------------------
# Percentil e z-score contra o proprio historico
# ---------------------------------------------------------------------------
def percentil_historico(
    serie: pd.Series, janelas_anos: tuple[int, ...] = (5, 10), min_obs: int = 250
) -> pd.DataFrame:
    """Percentil e z-score de cada ponto contra a janela movel anterior.

    A comparacao e sempre contra a propria empresa -- e o ponto do projeto:
    "P/L 12" nao diz nada; "P/L no percentil 8 dos ultimos 10 anos" diz.

    Janela com menos de `min_obs` observacoes nao produz numero. Sem
    extrapolacao para historico curto.
    """
    s = serie.dropna().sort_index()
    out = {"data": s.index}
    for anos in janelas_anos:
        dias = f"{int(anos * 365.25)}D"
        movel = s.rolling(dias, min_periods=min_obs)
        media, desvio = movel.mean(), movel.std(ddof=1)
        out[f"z_{anos}a"] = (s - media) / desvio.replace(0, np.nan)
        out[f"pct_{anos}a"] = movel.apply(
            lambda w: float((w[:-1] <= w[-1]).mean()) if len(w) > 1 else np.nan, raw=True
        )
        out[f"n_obs_{anos}a"] = movel.count()
        out[f"media_{anos}a"] = media
        out[f"desvio_{anos}a"] = desvio
    return pd.DataFrame(out).set_index("data")
