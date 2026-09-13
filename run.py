#!/usr/bin/env python3
"""CLI do pipeline. Idempotente: rodar duas vezes nao altera o resultado.

    python run.py extrair            # baixa brutos das fontes oficiais
    python run.py transformar        # aplica as 6 regras e carrega o DuckDB
    python run.py tudo               # extrair + transformar
    python run.py diagnostico        # so imprime o estado, sem escrever nada
    python run.py demo               # roda tudo com dados SINTETICOS, sem rede
    python run.py pagina             # gera empresas.html + empresas.csv
    python run.py contas BBSE3       # plano de contas REAL de uma empresa

Cada etapa e separada de proposito: a extracao depende de rede e e a unica
parte nao reproduzivel offline. `transformar` roda inteiramente a partir dos
Parquet em `data/parquet/`.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from db import load
from etl import (
    avisos, b3_cotahist, b3_eventos, bcb_sgs, cvm_cadastro, cvm_demonstracoes,
    cvm_fca, cvm_ipe, provenance,
)
from etl.config import CONFIG_DIR, PARQUET
from transform import depara, indicators, lineage, pipeline, prices, risk, sector, triggers


# Fontes sem as quais nao ha analise nenhuma. As demais sao auxiliares: a
# ausencia delas tira funcionalidade, nao invalida o resto. Derrubar a
# transformacao inteira porque o BCB esta fora seria jogar fora meia hora de
# download bom da CVM e da B3.
ESSENCIAIS = {"cadastro CVM", "FCA (de-para ticker)", "DFP", "ITR"}


def _config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "tickers.yml").read_text(encoding="utf-8"))


def anos_da_config(cfg: dict, fonte: str, hoje: date | None = None) -> list[int]:
    """Janela RELATIVA: ano corrente mais N anos para tras.

    Uma lista fixa no arquivo de configuracao envelhece sem avisar -- o
    sistema passa a ignorar os anos recentes calado. Por isso a janela e
    calculada na execucao. Lista explicita, quando existir, vence.
    """
    explicita = cfg.get(f"anos_{fonte}")
    if explicita:
        return sorted(int(a) for a in explicita)

    para_tras = int(cfg.get(f"anos_para_tras_{fonte}", 6))
    if para_tras < 0:
        raise ValueError(f"anos_para_tras_{fonte} = {para_tras}: nao pode ser negativo")
    corrente = (hoje or date.today()).year
    return list(range(corrente - para_tras, corrente + 1))


# ---------------------------------------------------------------------------
# Extracao
# ---------------------------------------------------------------------------
def extrair(args) -> int:
    cfg = _config()
    anos_cvm = anos_da_config(cfg, "cvm")
    anos_cot = anos_da_config(cfg, "cotahist")

    etapas = [
        ("cadastro CVM", lambda: cvm_cadastro.extrair()),
        ("FCA (de-para ticker)", lambda: cvm_fca.extrair(anos_cvm)),
        ("DFP", lambda: cvm_demonstracoes.extrair("DFP", anos_cvm)),
        ("ITR", lambda: cvm_demonstracoes.extrair("ITR", anos_cvm)),
        ("IPE (fatos relevantes)", lambda: cvm_ipe.extrair(anos_cvm)),
        ("COTAHIST", lambda: b3_cotahist.extrair(anos_cot)),
        ("BCB/SGS", lambda: bcb_sgs.extrair(date(min(anos_cot), 1, 1), date.today())),
    ]

    print(f"{len(etapas)} etapas. Anos CVM: {min(anos_cvm)}-{max(anos_cvm)}; "
          f"COTAHIST: {min(anos_cot)}-{max(anos_cot)}.")
    print("Sao centenas de MB. O que ja foi baixado nao baixa de novo.\n")

    falhas = []
    inicio_total = time.monotonic()
    for i, (nome, fn) in enumerate(etapas, start=1):
        print(f"  [{i}/{len(etapas)}] {nome}...", flush=True)
        t0 = time.monotonic()
        try:
            saida = fn()
            print(f"  [{i}/{len(etapas)}] ok {nome} "
                  f"({time.monotonic() - t0:.0f}s): {saida}\n", flush=True)
        except Exception as exc:  # noqa: BLE001 -- o relatorio final e o produto
            falhas.append((nome, exc))
            print(f"  [{i}/{len(etapas)}] FALHA {nome}: {type(exc).__name__}: {exc}\n",
                  file=sys.stderr, flush=True)

    print(f"\n{len(provenance.manifesto())} arquivos no manifesto de proveniencia "
          f"({time.monotonic() - inicio_total:.0f}s no total).")
    print(avisos.resumo())
    if not falhas:
        return 0

    nomes_falhos = {n for n, _ in falhas}
    essenciais_falhos = nomes_falhos & ESSENCIAIS
    print(f"\n{len(falhas)} etapa(s) falharam. O pipeline NAO preenche o que faltou:")
    for nome, exc in falhas:
        marca = "ESSENCIAL" if nome in ESSENCIAIS else "auxiliar"
        print(f"  [{marca}] {nome}: {type(exc).__name__}: {exc}")

    if essenciais_falhos:
        print("\nFonte essencial faltando. Sem ela nao ha o que transformar.")
        return 1

    print("\nSo fontes auxiliares falharam. Os fundamentos e os precos estao "
          "completos, entao vale seguir para a transformacao -- os indicadores "
          "que dependiam do que faltou aparecerao como FALTANDO, com o motivo.")
    return 2  # parcial: quem chama decide se prossegue


# ---------------------------------------------------------------------------
# Transformacao e carga
# ---------------------------------------------------------------------------
def _ler(nome: str) -> pd.DataFrame | None:
    caminho = PARQUET / nome
    return pd.read_parquet(caminho) if caminho.exists() else None


def _mil(n: int) -> str:
    """Separador de milhar no padrao brasileiro, sem estragar o resto da frase."""
    return f"{n:,}".replace(",", ".")


def _etapa(numero: int, total: int, texto: str) -> float:
    """Anuncia a etapa ANTES de comecar. Silencio prolongado durante trabalho
    pesado e indistinguivel de travamento -- ja custou duas rodadas."""
    print(f"  [{numero}/{total}] {texto}...", flush=True)
    return time.monotonic()


def _fim(numero: int, total: int, texto: str, t0: float, detalhe: str = "") -> None:
    seg = time.monotonic() - t0
    extra = f" -- {detalhe}" if detalhe else ""
    print(f"  [{numero}/{total}] ok {texto} ({seg:.0f}s){extra}\n", flush=True)


def transformar(args) -> int:
    cfg = _config()
    tickers = [t.upper() for t in cfg["tickers"]]
    N = 6

    t0 = _etapa(1, N, "lendo os brutos de data/parquet")
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
    _fim(1, N, "brutos lidos", t0,
         f"{_mil(len(brutos))} fatos da CVM, "
         f"{_mil(0 if cot is None else len(cot))} cotacoes")

    # --- de-para (regra 1) -------------------------------------------------
    t0 = _etapa(2, N, "de-para CNPJ x ticker (regra 1)")
    cot_vista = b3_cotahist.somente_acoes_a_vista(cot) if cot is not None else None
    mapa = depara.construir(fca, cot_vista) if fca is not None else pd.DataFrame()
    if not mapa.empty:
        mapa = mapa[mapa["ticker"].isin(tickers)]
    cnpjs = sorted(set(mapa["cnpj"])) if not mapa.empty else []
    if not cnpjs:
        print("nenhum ticker do config resolveu para CNPJ via FCA. "
              "Confira o relatorio de divergencias.", file=sys.stderr)
    _fim(2, N, "de-para", t0,
         f"{len(cnpjs)} empresa(s) para {len(tickers)} ticker(s) pedidos")

    # --- fundamentos (regras 2 a 6) ---------------------------------------
    t0 = _etapa(3, N, "normalizando fatos: versao, ordem, periodo, base "
                      "(regras 2 a 5)")
    brutos["CNPJ_CIA"] = depara.normalizar_cnpj(brutos["CNPJ_CIA"])
    if cnpjs:
        brutos = brutos[brutos["CNPJ_CIA"].isin(cnpjs)]
    print(f"      {_mil(len(brutos))} fatos das empresas selecionadas", flush=True)
    resultado = pipeline.normalizar_fatos(brutos)
    fatos = resultado["fatos"]
    _fim(3, N, "normalizacao", t0, f"{_mil(len(fatos))} fatos")

    t0 = _etapa(4, N, "classificando setor (regra 6)")
    classificacao = (
        sector.classificar(cad) if cad is not None else pd.DataFrame(columns=["cnpj", "plano"])
    )
    plano_por_cnpj = dict(zip(classificacao["cnpj"], classificacao["plano"]))
    if cnpjs:
        planos = {c: plano_por_cnpj.get(c, "INDEFINIDO") for c in cnpjs}
        for c, p in sorted(planos.items()):
            print(f"      {c}: {p}", flush=True)
    _fim(4, N, "classificacao", t0)

    # --- carga --------------------------------------------------------------
    t0 = _etapa(5, N, "calculando indicadores e carregando o banco")
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
    _carregar_acoes(con, cnpjs)
    _carregar_fontes(con)
    load.substituir(con, "aviso", avisos.para_quadro())
    load.registrar_execucao(con, "transformar", n, observacao=f"{len(cnpjs)} empresas")
    _fim(5, N, "carga", t0, f"{_mil(n)} fatos no banco")

    t0 = _etapa(6, N, "resumo")
    identidades = resultado["identidades"]
    from transform.validations import resumo

    print(f"  fatos contabeis      : {n}")
    print(f"  identidade contabil  : {resumo(identidades)}")
    print(f"  reapresentacoes      : {len(resultado['reapresentacoes'])}")
    print(f"  soma T1..T3 vs 9M    : {len(resultado['soma_trimestres_divergente'])} divergencias")
    print(f"  fora de REAL         : {len(resultado['outra_moeda'])} linhas ignoradas")
    print(f"  avisos               : {len(avisos.registrados())}")
    con.close()
    print(f"\nBanco pronto. Para ver os dados:"
          f"\n    ./comecar.sh pagina      -> empresas.html + empresas.csv"
          f"\n    streamlit run app/main.py -> interface completa")
    return 0


def _para_reapresentacao(rea: pd.DataFrame) -> pd.DataFrame:
    if rea.empty:
        return pd.DataFrame(columns=["cnpj", "base", "demonstrativo", "cd_conta", "periodo"])
    # `COLUNA_DF` entra aqui porque `restatement.chave_fato` a inclui quando a
    # DMPL esta presente. Ela precisa do nome do schema, senao a carga recusa a
    # tabela inteira -- que foi exatamente o que aconteceu com dado real.
    return rea.rename(columns={"CNPJ_CIA": "cnpj", "CD_CONTA": "cd_conta",
                               "DS_CONTA": "ds_conta", "COLUNA_DF": "coluna_df"})


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


def _carregar_acoes(con, cnpjs: list[str]) -> None:
    """Acoes em circulacao, dos pacotes DFP e ITR."""
    from transform import shares

    partes = [
        _ler(f"cvm/{d}_composicao_capital.parquet") for d in ("dfp", "itr")
    ]
    partes = [p for p in partes if p is not None and not p.empty]
    if not partes:
        return

    acoes = shares.normalizar(pd.concat(partes, ignore_index=True))
    if cnpjs:
        acoes = acoes[acoes["cnpj"].isin(cnpjs)]
    if acoes.empty:
        return

    load.substituir(con, "acoes_em_circulacao", acoes)
    problemas = shares.inconsistencias(acoes)
    load.substituir(con, "acoes_inconsistencia", problemas)
    print(f"  acoes em circulacao  : {len(acoes)} registro(s), "
          f"{len(problemas)} inconsistencia(s)")


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
        linhas += fx.cenario_itr_completo(
            ano=ano, receita=1000.0 + 120.0 * i, lucro=lucro, ebit=120.0 + 18.0 * i,
            pl=400.0 + 40.0 * i, fco=150.0 + 12.0 * i, fci=-60.0 - 5.0 * i,
            da=45.0 + 3.0 * i)
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
    load.substituir(con, "reapresentacao", _para_reapresentacao(resultado["reapresentacoes"]))
    _carregar_indicadores(con, fatos, {cnpj: "INDUSTRIAL"})
    _demo_empresa(con, cnpj, fatos, resultado)
    _demo_precos(con, cnpj)

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
    print(f"Para VER isto na interface:  B3DSS_DB={caminho} streamlit run app/main.py")
    return 0


def _demo_empresa(con, cnpj: str, fatos: pd.DataFrame, resultado: dict) -> None:
    rel = resultado["relatorio_base"]
    linha = rel[rel["CNPJ_CIA"] == cnpj]
    emp = pd.DataFrame([{
        "cnpj": cnpj,
        "denom_social": "COMPANHIA SINTETICA S.A. -- DADOS INVENTADOS",
        "cd_cvm": "99999",
        "setor_ativ": "EMP. ADM. PART.",
        "plano_contas": "INDUSTRIAL",
        "origem_classificacao": "cenario de demonstracao (run.py demo)",
        "situacao": "ATIVO",
        "base_escolhida": linha["base_escolhida"].iloc[0] if not linha.empty else "CON",
        "cobertura_con": int(linha["cobertura_con"].iloc[0]) if not linha.empty else 0,
        "cobertura_ind": int(linha["cobertura_ind"].iloc[0]) if not linha.empty else 0,
        "criterio_base": linha["criterio"].iloc[0] if not linha.empty else "-",
    }]).merge(pipeline.ultimo_documento(fatos), on="cnpj", how="left")
    load.substituir(con, "empresa", emp)
    load.substituir(con, "depara_ticker", pd.DataFrame([{
        "cnpj": cnpj, "ticker": "DEMO3", "valor_mobiliario": "Acoes Ordinarias",
        "mercado": "Bolsa", "negociado_b3": True,
        "src_file": "cenario de demonstracao", "src_line": 2,
    }]))


def _demo_precos(con, cnpj: str) -> None:
    """Serie diaria com desdobramento 1:2 e uma queda de verdade.

    Caminho aleatorio com semente fixa: a serie tem volatilidade e drawdown
    observaveis, senao as medidas de risco saem todas zero e a tela de risco
    nao demonstra nada. Semente fixa mantem o demo idempotente.
    """
    import numpy as np

    rng = np.random.default_rng(42)
    n, meio = 1_000, 600
    datas = pd.bdate_range("2020-01-02", periods=n)
    retornos = rng.normal(0.0004, 0.021, n)
    retornos[180:260] -= 0.006  # janela de queda sustentada, para o drawdown
    fech = 40.0 * np.cumprod(1.0 + retornos)
    fech[meio:] /= 2.0  # desdobramento 1:2 a partir do pregao 601

    serie = pd.DataFrame({
        "data": datas, "fech": fech,
        "src_file": "COTAHIST_A2020.TXT", "src_line": range(2, 2 + n),
    })
    eventos = pd.DataFrame([{
        "ticker": "DEMO3", "data_ex": datas[meio], "tipo": "DESDOBRAMENTO",
        "fator": 2.0, "valor_por_acao": None,
        "fonte_url": "cenario de demonstracao -- nao e fonte real",
        "observacao": "sintetico",
    }])
    aj = prices.ajustar(serie, eventos)
    aj["ticker"] = "DEMO3"
    load.substituir(con, "preco_diario", aj)
    load.substituir(con, "evento_corporativo", eventos)
    status, motivo = prices.cobertura_ajuste("DEMO3", eventos, pd.DataFrame())
    load.substituir(con, "cobertura_ajuste",
                    pd.DataFrame([{"ticker": "DEMO3", "status": status, "motivo": motivo}]))


def pagina(args) -> int:
    """Gera um arquivo HTML autossuficiente + um CSV, sem servidor nenhum."""
    import export_pagina
    from etl.config import DATA, DUCKDB_PATH

    banco = Path(args.banco) if getattr(args, "banco", None) else DUCKDB_PATH
    if not banco.exists():
        alternativa = DATA / "demo.duckdb"
        if alternativa.exists():
            banco = alternativa
        else:
            print(f"nenhum banco encontrado em {banco}. Rode 'python run.py demo' "
                  "ou 'python run.py tudo' antes.", file=sys.stderr)
            return 1

    destino_html = Path(args.saida) if getattr(args, "saida", None) else Path("empresas.html")
    destino_csv = destino_html.with_suffix(".csv")
    r = export_pagina.exportar(banco, destino_html, destino_csv)

    print(f"  banco de origem : {banco}")
    print(f"  linhas          : {r['linhas']:,}".replace(",", "."))
    print(f"  empresas        : {r['empresas']}")
    print()
    print(f"  HTML : {destino_html.resolve()}")
    print(f"  CSV  : {destino_csv.resolve()}")
    print()
    print("  Abra o HTML com dois cliques no Finder. Nao precisa de terminal.")
    return 0


def schema(args) -> int:
    """Imprime as colunas REAIS de cada CSV dos pacotes ja baixados.

    Nao usa rede: le os ZIP em data/raw/. Serve para confrontar contrato
    declarado contra arquivo de verdade sem adivinhar nome de coluna.
    """
    import io
    import zipfile

    from etl.config import CVM_ENCODING, CVM_SEP, RAW

    zips = sorted(RAW.rglob("*.zip"))
    if not zips:
        print("nenhum pacote baixado em data/raw/. Rode a extracao antes.",
              file=sys.stderr)
        return 1

    vistos: dict[str, tuple[str, list[str]]] = {}
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                for nome in sorted(n for n in zf.namelist() if n.lower().endswith(".csv")):
                    # Assinatura do arquivo, sem o ano: basta um exemplar.
                    chave = re.sub(r"\d{4}", "AAAA", Path(nome).name)
                    if chave in vistos:
                        continue
                    with zf.open(nome) as fh:
                        cabecalho = io.TextIOWrapper(fh, encoding=CVM_ENCODING).readline()
                    colunas = [c.strip() for c in cabecalho.rstrip("\r\n").split(CVM_SEP)]
                    vistos[chave] = (z.name, colunas)
        except zipfile.BadZipFile:
            print(f"  (ignorado, nao e ZIP valido: {z.name})", file=sys.stderr)

    print(f"{len(vistos)} tipo(s) de arquivo encontrados em {len(zips)} pacote(s).\n")
    for chave in sorted(vistos):
        pacote, colunas = vistos[chave]
        print(f"{chave}   [{pacote}]")
        print(f"    {len(colunas)} colunas: {';'.join(colunas)}\n")
    print("Cole esta saida na conversa para que os contratos sejam ajustados "
          "contra o arquivo real, sem suposicao.")
    return 0


def contas(args) -> int:
    """Imprime o plano de contas REAL de uma empresa, do banco ja carregado.

    Mesma funcao que `schema` cumpre para as colunas: parar de declarar de
    memoria. O bloco SEGURADORA de config/sector_plans.yml avisa, no proprio
    arquivo, que seus codigos nunca foram confrontados com demonstracao de
    seguradora de verdade. Esta saida e o que permite confronta-los.
    """
    import duckdb

    from etl.config import DUCKDB_PATH

    if not Path(DUCKDB_PATH).exists():
        print(f"{DUCKDB_PATH} nao existe. Rode a transformacao antes.", file=sys.stderr)
        return 1

    alvo = args.empresa.upper().strip()
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)

    cnpj = con.execute(
        "SELECT cnpj FROM depara_ticker WHERE ticker = ?", [alvo]
    ).fetchone()
    cnpj = cnpj[0] if cnpj else re.sub(r"\D", "", alvo)

    cab = con.execute(
        "SELECT denom_social, setor_ativ, plano_contas, origem_classificacao "
        "FROM empresa WHERE cnpj = ?", [cnpj]
    ).fetchone()
    if cab is None:
        print(f"'{args.empresa}' nao esta no banco. Empresas carregadas:",
              file=sys.stderr)
        for r in con.execute(
            "SELECT e.cnpj, e.denom_social, string_agg(d.ticker, '/') "
            "FROM empresa e LEFT JOIN depara_ticker d ON d.cnpj = e.cnpj "
            "GROUP BY 1, 2 ORDER BY 2"
        ).fetchall():
            print(f"  {r[2] or '-':10s} {r[0]}  {r[1]}", file=sys.stderr)
        return 1

    print(f"{cab[0]}  ({cnpj})")
    print(f"SETOR_ATIV : {cab[1]}")
    print(f"plano      : {cab[2]}   [{cab[3]}]\n")

    periodo = con.execute(
        "SELECT periodo FROM fato_contabil WHERE cnpj = ? "
        "ORDER BY dt_fim_exerc DESC NULLS LAST LIMIT 1", [cnpj]
    ).fetchone()
    if periodo is None:
        print("empresa sem fato contabil carregado.", file=sys.stderr)
        return 1
    periodo = periodo[0]
    print(f"Contas presentes no periodo mais recente ({periodo}):\n")

    linhas = con.execute(
        "SELECT demonstrativo, cd_conta, coluna_df, ds_conta, valor, "
        "       src_file, src_line "
        "FROM fato_contabil WHERE cnpj = ? AND periodo = ? "
        "ORDER BY demonstrativo, cd_conta, coluna_df", [cnpj, periodo]
    ).fetchall()
    con.close()

    atual = None
    for dem, cd, coluna, ds, valor, arq, linha in linhas:
        if dem != atual:
            print(f"\n--- {dem} " + "-" * (72 - len(dem)))
            atual = dem
        v = "" if valor is None else f"{valor:>18,.2f}".replace(",", "_").replace(
            ".", ",").replace("_", ".")
        # Sem a coluna do PL, duas linhas da DMPL saem identicas na tela --
        # mesmo codigo, mesma descricao, valores diferentes e nada explicando.
        rotulo = f"{ds or ''} [{coluna}]" if coluna else (ds or "")
        print(f"  {cd:<14s} {rotulo[:44]:<44s} {v}   {arq}:{linha}")

    print(f"\n{len(linhas)} contas. Cole esta saida na conversa para que o plano "
          "do setor seja declarado a partir do arquivo real, sem suposicao.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("schema").set_defaults(fn=schema)
    ct = sub.add_parser("contas")
    ct.add_argument("empresa", help="ticker (BBSE3) ou CNPJ")
    ct.set_defaults(fn=contas)
    pag = sub.add_parser("pagina")
    pag.add_argument("--banco", help="caminho do .duckdb (padrao: data/b3dss.duckdb)")
    pag.add_argument("--saida", help="arquivo HTML de saida (padrao: empresas.html)")
    pag.set_defaults(fn=pagina)
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
