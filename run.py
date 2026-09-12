#!/usr/bin/env python3
"""CLI do pipeline. Idempotente: rodar duas vezes nao altera o resultado.

    python run.py extrair            # baixa brutos das fontes oficiais
    python run.py transformar        # aplica as 6 regras e carrega o DuckDB
    python run.py tudo               # extrair + transformar
    python run.py diagnostico        # so imprime o estado, sem escrever nada
    python run.py demo               # roda tudo com dados SINTETICOS, sem rede

Cada etapa e separada de proposito: a extracao depende de rede e e a unica
parte nao reproduzivel offline. `transformar` roda inteiramente a partir dos
Parquet em `data/parquet/`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from db import load
from etl import (
    b3_cotahist, b3_eventos, bcb_sgs, cvm_cadastro, cvm_demonstracoes, cvm_fca,
    cvm_ipe, provenance,
)
from etl.config import CONFIG_DIR, PARQUET
from transform import depara, indicators, lineage, pipeline, prices, risk, sector, triggers


def _config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "tickers.yml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Extracao
# ---------------------------------------------------------------------------
def extrair(args) -> int:
    cfg = _config()
    anos_cvm = cfg["anos_cvm"]
    anos_cot = cfg["anos_cotahist"]

    etapas = [
        ("cadastro CVM", lambda: cvm_cadastro.extrair()),
        ("FCA (de-para ticker)", lambda: cvm_fca.extrair(anos_cvm)),
        ("DFP", lambda: cvm_demonstracoes.extrair("DFP", anos_cvm)),
        ("ITR", lambda: cvm_demonstracoes.extrair("ITR", anos_cvm)),
        ("IPE (fatos relevantes)", lambda: cvm_ipe.extrair(anos_cvm)),
        ("COTAHIST", lambda: b3_cotahist.extrair(anos_cot)),
        ("BCB/SGS", lambda: bcb_sgs.extrair(date(min(anos_cot), 1, 1), date.today())),
    ]

    falhas = []
    for nome, fn in etapas:
        try:
            saida = fn()
            print(f"  ok   {nome}: {saida}")
        except Exception as exc:  # noqa: BLE001 -- o relatorio final e o produto
            falhas.append((nome, exc))
            print(f"  FALHA {nome}: {type(exc).__name__}: {exc}", file=sys.stderr)

    print(f"\n{len(provenance.manifesto())} arquivos no manifesto de proveniencia.")
    if falhas:
        print(f"\n{len(falhas)} etapa(s) falharam. O pipeline NAO preenche o que faltou.")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Transformacao e carga
# ---------------------------------------------------------------------------
def _ler(nome: str) -> pd.DataFrame | None:
    caminho = PARQUET / nome
    return pd.read_parquet(caminho) if caminho.exists() else None


def transformar(args) -> int:
    cfg = _config()
    tickers = [t.upper() for t in cfg["tickers"]]

    dfp = _ler("cvm/dfp_fatos.parquet")
    itr = _ler("cvm/itr_fatos.parquet")
    if dfp is None and itr is None:
        print("nenhum bruto da CVM em data/parquet/. Rode 'python run.py extrair'.",
              file=sys.stderr)
        return 1
    brutos = pd.concat([d for d in (dfp, itr) if d is not None], ignore_index=True)

    cad = _ler("cvm/cadastro.parquet")
    fca = _ler("cvm/fca_valor_mobiliario.parquet")
    cot = _ler("b3/cotahist.parquet")
    sgs = _ler("bcb/sgs.parquet")

    # --- de-para (regra 1) -------------------------------------------------
    cot_vista = b3_cotahist.somente_acoes_a_vista(cot) if cot is not None else None
    mapa = depara.construir(fca, cot_vista) if fca is not None else pd.DataFrame()
    if not mapa.empty:
        mapa = mapa[mapa["ticker"].isin(tickers)]
    cnpjs = sorted(set(mapa["cnpj"])) if not mapa.empty else []
    if not cnpjs:
        print("nenhum ticker do config resolveu para CNPJ via FCA. "
              "Confira o relatorio de divergencias.", file=sys.stderr)

    # --- fundamentos (regras 2 a 6) ---------------------------------------
    brutos["CNPJ_CIA"] = depara.normalizar_cnpj(brutos["CNPJ_CIA"])
    if cnpjs:
        brutos = brutos[brutos["CNPJ_CIA"].isin(cnpjs)]
    resultado = pipeline.normalizar_fatos(brutos)
    fatos = resultado["fatos"]

    classificacao = (
        sector.classificar(cad) if cad is not None else pd.DataFrame(columns=["cnpj", "plano"])
    )
    plano_por_cnpj = dict(zip(classificacao["cnpj"], classificacao["plano"]))

    # --- carga --------------------------------------------------------------
    con = load.conectar()
    n = load.substituir(con, "fato_contabil", pipeline.para_db(fatos))
    load.substituir(con, "teste_identidade", resultado["identidades"])
    load.substituir(con, "reapresentacao", _para_reapresentacao(resultado["reapresentacoes"]))
    if not mapa.empty:
        load.substituir(con, "depara_ticker", mapa)
        if cot_vista is not None:
            load.substituir(con, "depara_divergencia",
                            _para_divergencias(depara.divergencias(mapa, cot_vista)))
    _carregar_empresa(con, cad, classificacao, resultado, fatos)
    _carregar_precos(con, cot_vista, tickers)
    if sgs is not None:
        load.substituir(con, "serie_macro", sgs[
            ["serie", "codigo_sgs", "data", "valor", "src_file", "src_line"]
        ])
    _carregar_indicadores(con, fatos, plano_por_cnpj)
    _carregar_fontes(con)
    load.registrar_execucao(con, "transformar", n, observacao=f"{len(cnpjs)} empresas")

    identidades = resultado["identidades"]
    from transform.validations import resumo

    print(f"  fatos contabeis      : {n}")
    print(f"  identidade contabil  : {resumo(identidades)}")
    print(f"  reapresentacoes      : {len(resultado['reapresentacoes'])}")
    print(f"  soma T1..T3 vs 9M    : {len(resultado['soma_trimestres_divergente'])} divergencias")
    print(f"  fora de REAL         : {len(resultado['outra_moeda'])} linhas ignoradas")
    con.close()
    return 0


def _para_reapresentacao(rea: pd.DataFrame) -> pd.DataFrame:
    if rea.empty:
        return pd.DataFrame(columns=["cnpj", "base", "demonstrativo", "cd_conta", "periodo"])
    return rea.rename(columns={"CNPJ_CIA": "cnpj", "CD_CONTA": "cd_conta",
                               "DS_CONTA": "ds_conta"})


def _para_divergencias(div: dict[str, pd.DataFrame]) -> pd.DataFrame:
    linhas = []
    for t, ticker, cnpj, detalhe in (
        ("fca_sem_pregao", "ticker", "cnpj",
         "declarado no FCA e ausente do COTAHIST (cancelado, nunca negociado ou erro de cadastro)"),
        ("pregao_sem_fca", "ticker", None,
         "negociado na B3 sem CNPJ associado no FCA: preco nao liga a fundamento"),
    ):
        df = div.get(t)
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            linhas.append({"tipo": t, "cnpj": r.get(cnpj) if cnpj else None,
                           "ticker": r[ticker], "detalhe": detalhe})
    return pd.DataFrame(linhas, columns=["tipo", "cnpj", "ticker", "detalhe"])


def _carregar_empresa(con, cad, classificacao, resultado, fatos) -> None:
    if cad is None or classificacao.empty:
        return
    emp = classificacao.rename(columns={"plano": "plano_contas"})
    base_cad = cad.copy()
    base_cad["cnpj"] = depara.normalizar_cnpj(base_cad["CNPJ_CIA"])
    emp = emp.merge(
        base_cad[["cnpj", "DENOM_SOCIAL", "CD_CVM", "SIT"]].drop_duplicates("cnpj"),
        on="cnpj", how="left",
    ).rename(columns={"DENOM_SOCIAL": "denom_social", "CD_CVM": "cd_cvm", "SIT": "situacao"})
    emp = emp.merge(pipeline.ultimo_documento(fatos), on="cnpj", how="left")
    rel = resultado["relatorio_base"].rename(columns={"CNPJ_CIA": "cnpj", "criterio": "criterio_base"})
    emp = emp.merge(
        rel[["cnpj", "base_escolhida", "cobertura_con", "cobertura_ind", "criterio_base"]],
        on="cnpj", how="left",
    )
    emp = emp[emp["cnpj"].isin(set(fatos["CNPJ_CIA"]))] if not fatos.empty else emp
    load.substituir(con, "empresa", emp)


def _carregar_precos(con, cot_vista, tickers) -> None:
    if cot_vista is None or cot_vista.empty:
        return
    eventos = b3_eventos.carregar(tickers)
    cot_vista = prices.preco_unitario(cot_vista)
    suspeitas = b3_eventos.detectar_suspeitas(
        cot_vista[cot_vista["CODNEG"].isin(tickers)], eventos
    )

    series, cobertura = [], []
    for t in tickers:
        s = prices.serie_diaria(cot_vista, t)
        if s.empty:
            continue
        aj = prices.ajustar(s, eventos[eventos["ticker"] == t])
        aj["ticker"] = t
        series.append(aj)
        status, motivo = prices.cobertura_ajuste(t, eventos, suspeitas)
        cobertura.append({"ticker": t, "status": status, "motivo": motivo})

    if series:
        load.substituir(con, "preco_diario", pd.concat(series, ignore_index=True))
    if cobertura:
        load.substituir(con, "cobertura_ajuste", pd.DataFrame(cobertura))
    if not eventos.empty:
        load.substituir(con, "evento_corporativo", eventos)
    if not suspeitas.empty:
        load.substituir(con, "evento_suspeito", suspeitas)


def _carregar_indicadores(con, fatos, plano_por_cnpj) -> None:
    if fatos.empty:
        return
    conceitos = sorted(
        {c for p in sector.carregar_planos()["planos"].values() for c in (p.get("contas") or {})}
    )
    fundamentos = indicators.montar_fundamentos(fatos, plano_por_cnpj, conceitos)

    todos = []
    for (cnpj, periodo) in fundamentos[["cnpj", "periodo"]].drop_duplicates().itertuples(index=False):
        todos += indicators.dupont(fundamentos, cnpj, periodo)
        todos += indicators.fluxo_e_divida(fundamentos, cnpj, periodo)
    if not todos:
        return
    cab, ent = lineage.para_quadros(todos)
    cab = cab.drop_duplicates(["entidade", "periodo", "indicador"], keep="first")
    ent = ent.drop_duplicates(["entidade", "periodo", "indicador", "ordem"], keep="first")
    load.substituir(con, "indicador", cab)
    load.substituir(con, "indicador_entrada", ent)


def _carregar_fontes(con) -> None:
    fontes = provenance.manifesto()
    if not fontes:
        return
    df = pd.DataFrame(
        [{"url": f.url, "caminho": f.path, "sha256": f.sha256, "bytes": f.bytes,
          "baixado_em": f.baixado_em, "etag": f.etag, "last_modified": f.last_modified}
         for f in fontes]
    )
    df["baixado_em"] = pd.to_datetime(df["baixado_em"])
    load.substituir(con, "fonte", df)


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------
def diagnostico(args) -> int:
    print("Brutos em data/parquet/:")
    for p in sorted(PARQUET.rglob("*.parquet")):
        print(f"  {p.relative_to(PARQUET)}  ({p.stat().st_size / 1e6:.1f} MB)")

    fontes = provenance.manifesto()
    print(f"\n{len(fontes)} arquivo(s) no manifesto.")
    for f in sorted(fontes, key=lambda x: x.baixado_em)[-5:]:
        print(f"  {f.baixado_em}  {f.nome}  sha256={f.sha256[:12]}...")

    print("\nContratos de schema (verificado_em = None significa nao confrontado "
          "com arquivo real nesta instalacao):")
    from etl.contracts import TODOS

    for nome, c in sorted(TODOS.items()):
        print(f"  {nome:28s} {len(c.colunas):2d} colunas  verificado_em={c.verificado_em}")

    print("\nGatilhos configurados:")
    for g in triggers.carregar():
        print(f"  {g['nome']:38s} escopo={g['escopo']}")
    return 0


# ---------------------------------------------------------------------------
# Demonstracao sem rede
# ---------------------------------------------------------------------------
def demo(args) -> int:
    """Roda o pipeline inteiro com dados sinteticos e imprime a saida.

    Existe para responder "o que este sistema mostra?" sem depender de rede e
    sem esperar o download da CVM. Os numeros sao INVENTADOS -- servem para
    exibir a forma do resultado e a cadeia de rastreabilidade, nada mais.
    O banco vai para `data/demo.duckdb`, separado do banco de producao.
    """
    from etl.config import DATA
    from tests import fixtures as fx

    cnpj = "00000000000191"
    anos = [2020, 2021, 2022, 2023]

    linhas = []
    for i, ano in enumerate(anos):
        lucro = 80.0 + 14.0 * i
        linhas += fx.cenario_balanco_completo(ano=ano, pl=400.0 + 40.0 * i)
        linhas += fx.cenario_dre_anual(ano=ano, receita=1000.0 + 120.0 * i,
                                       lucro=lucro, ebit=120.0 + 18.0 * i)
        linhas += fx.cenario_dfc_anual(ano=ano, fco=150.0 + 12.0 * i,
                                       fci=-60.0 - 5.0 * i, da=45.0 + 3.0 * i)
        linhas += fx.cenario_itr_trimestres(
            ano=ano, isolados=(lucro * 0.20, lucro * 0.25, lucro * 0.28))
    brutos = fx.quadro(linhas)
    brutos["CNPJ_CIA"] = cnpj

    print("AVISO: dados SINTETICOS. Nao sao da CVM. Servem so para mostrar a "
          "forma da saida.\n")
    resultado = pipeline.normalizar_fatos(brutos)
    fatos = resultado["fatos"]
    print(f"brutos no layout da CVM : {len(brutos)} linhas")
    print(f"apos as 6 regras        : {len(fatos)} fatos")

    caminho = DATA / "demo.duckdb"
    caminho.unlink(missing_ok=True)
    con = load.conectar(caminho)
    load.substituir(con, "fato_contabil", pipeline.para_db(fatos))
    load.substituir(con, "teste_identidade", resultado["identidades"])
    _carregar_indicadores(con, fatos, {cnpj: "INDUSTRIAL"})

    sep = "=" * 92
    print(f"\n{sep}\nIndicadores do exercicio 2023\n{sep}")
    print(con.execute(
        "SELECT indicador, round(valor, 6) AS valor, status, formula "
        "FROM indicador WHERE periodo = '2023' ORDER BY indicador"
    ).fetchdf().to_string(index=False))

    print(f"\n{sep}\nRastreabilidade: de onde saiu o ROE de 2023\n{sep}")
    print(con.execute(
        "SELECT rotulo, cd_conta, ds_conta, valor, demonstrativo, base, versao, "
        "ordem_exerc, dt_refer, referencia FROM indicador_entrada "
        "WHERE periodo = '2023' AND indicador = 'roe' ORDER BY ordem"
    ).fetchdf().to_string(index=False))

    print(f"\n{sep}\nDuPont ano a ano\n{sep}")
    print(con.execute(
        "SELECT periodo, "
        "max(CASE WHEN indicador='margem_liquida' THEN round(valor,4) END) AS margem_liq, "
        "max(CASE WHEN indicador='giro_ativo'     THEN round(valor,4) END) AS giro, "
        "max(CASE WHEN indicador='alavancagem'    THEN round(valor,4) END) AS alavancagem, "
        "max(CASE WHEN indicador='roe'            THEN round(valor,4) END) AS roe, "
        "max(CASE WHEN indicador='roe_dupont'     THEN round(valor,4) END) AS roe_dupont "
        "FROM indicador WHERE length(periodo) = 4 GROUP BY periodo ORDER BY periodo"
    ).fetchdf().to_string(index=False))

    print(f"\n{sep}\nRegra 5: Q4 derivado, com a linhagem dos dois insumos\n{sep}")
    for _, r in con.execute(
        "SELECT periodo, cd_conta, valor, src_derivacao FROM fato_contabil "
        "WHERE origem_periodo = 'DERIVADO_Q4' ORDER BY periodo"
    ).fetchdf().iterrows():
        print(f"  {r['periodo']}  conta {r['cd_conta']}  valor = {r['valor']:,.2f}")
        print(f"    {r['src_derivacao']}")

    print(f"\n{sep}\nIdentidade contabil\n{sep}")
    print(con.execute(
        "SELECT periodo, ativo_total, passivo_total, patrimonio_liquido, "
        "residuo_principal, residuo_decomposto, tolerancia, status, src_ativo "
        "FROM teste_identidade ORDER BY periodo"
    ).fetchdf().to_string(index=False))

    print(f"\n{sep}\nO que o sistema se recusa a calcular (trimestre sem DRE)\n{sep}")
    print(con.execute(
        "SELECT indicador, status, substr(motivo, 1, 70) AS motivo FROM indicador "
        "WHERE status <> 'OK' AND periodo = '2023T1' ORDER BY indicador"
    ).fetchdf().to_string(index=False))

    con.close()
    print(f"\nBanco de demonstracao: {caminho}")
    print("Para abrir a interface sobre ele:  B3DSS_DATA=data streamlit run app/main.py")
    print("(a interface le data/b3dss.duckdb; renomeie o demo se quiser visualizar)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("extrair").set_defaults(fn=extrair)
    sub.add_parser("transformar").set_defaults(fn=transformar)
    sub.add_parser("diagnostico").set_defaults(fn=diagnostico)
    sub.add_parser("demo").set_defaults(fn=demo)
    tudo = sub.add_parser("tudo")
    tudo.set_defaults(fn=lambda a: extrair(a) or transformar(a))
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
