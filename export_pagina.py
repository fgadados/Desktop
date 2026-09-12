"""Exporta o conteudo do banco para UM arquivo HTML e um CSV.

Motivacao: nem todo uso precisa de servidor. Aqui a saida e um arquivo que
abre com dois cliques no Finder, sem terminal, sem Streamlit, sem Python
rodando por tras. O CSV vai junto para quem prefere calcular no Excel ou no
pandas.

O HTML e autossuficiente: os dados vao embutidos como JSON dentro do proprio
arquivo. Da para mandar por e-mail, guardar, abrir offline.

Nenhum indicador e calculado aqui. Esta pagina entrega o dado contabil como
ele saiu da CVM depois das 6 regras de tratamento, com a origem de cada
numero. O calculo fica com quem le -- que era o pedido.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

LIMITE_LINHAS = 200_000

CONSULTA = """
SELECT
    f.cnpj,
    coalesce(e.denom_social, f.denom_cia)            AS empresa,
    coalesce(d.ticker, '')                           AS ticker,
    f.periodo,
    f.demonstrativo,
    f.base,
    f.cd_conta,
    f.ds_conta,
    f.valor,
    f.origem_periodo,
    f.versao,
    f.ordem_exerc,
    f.dt_refer,
    f.src_file || ':' || CAST(f.src_line AS VARCHAR) AS origem
FROM fato_contabil f
LEFT JOIN empresa e USING (cnpj)
LEFT JOIN (SELECT cnpj, min(ticker) AS ticker FROM depara_ticker GROUP BY cnpj) d
       USING (cnpj)
ORDER BY empresa, f.periodo DESC, f.demonstrativo, f.cd_conta
"""


def exportar(banco: Path, destino_html: Path, destino_csv: Path) -> dict:
    con = duckdb.connect(str(banco), read_only=True)
    df = con.execute(CONSULTA).fetchdf()
    meta = {
        "empresas": con.execute("SELECT count(*) FROM empresa").fetchone()[0],
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "banco": banco.name,
    }
    identidade = con.execute(
        "SELECT status, count(*) AS n FROM teste_identidade GROUP BY 1"
    ).fetchdf()
    con.close()

    df.to_csv(destino_csv, index=False, encoding="utf-8-sig")  # BOM: Excel/pt-BR

    truncado = len(df) > LIMITE_LINHAS
    df_html = df.head(LIMITE_LINHAS) if truncado else df
    destino_html.write_text(
        _montar_html(df_html, meta, identidade, total=len(df), truncado=truncado),
        encoding="utf-8",
    )
    return {"linhas": len(df), "truncado": truncado, **meta}


def _montar_html(df: pd.DataFrame, meta: dict, identidade: pd.DataFrame,
                 *, total: int, truncado: bool) -> str:
    registros = df.where(pd.notna(df), None).to_dict(orient="records")
    dados = json.dumps(registros, ensure_ascii=False, default=str)

    ident = " · ".join(
        f"{r['status']}: {int(r['n'])}" for _, r in identidade.iterrows()
    ) or "sem teste registrado"

    aviso = (
        f'<p class="aviso">Mostrando as primeiras {LIMITE_LINHAS:,} linhas de '
        f'{total:,}. O CSV ao lado tem todas.</p>'.replace(",", ".")
        if truncado else ""
    )

    return f"""<!doctype html>
<html lang="pt-BR">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dados das empresas — B3</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px 16px 64px;
         font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         color: #16202c; background: #fff; }}
  .wrap {{ max-width: 1400px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #5a6775; font-size: 13px; margin: 0 0 20px; }}
  .barra {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
            margin-bottom: 14px; position: sticky; top: 0; background: #fff;
            padding: 10px 0; border-bottom: 1px solid #e3e8ee; z-index: 5; }}
  input, select {{ font: inherit; padding: 7px 10px; border: 1px solid #c7d0da;
                   border-radius: 6px; background: #fff; }}
  input[type=search] {{ min-width: 260px; flex: 1 1 260px; }}
  .contagem {{ color: #5a6775; font-size: 13px; white-space: nowrap; }}
  .tabela-area {{ overflow-x: auto; border: 1px solid #e3e8ee; border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid #eef2f6;
            white-space: nowrap; }}
  th {{ background: #f6f8fa; font-weight: 600; position: sticky; top: 0;
        cursor: pointer; user-select: none; }}
  th:hover {{ background: #eef2f6; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.origem {{ color: #6b7885; font-size: 12px; font-family: ui-monospace, Menlo, monospace; }}
  tr:hover td {{ background: #f9fbfd; }}
  .aviso {{ background: #fff8e6; border: 1px solid #f0dca0; padding: 10px 12px;
            border-radius: 6px; font-size: 13px; }}
  .rodape {{ margin-top: 18px; color: #5a6775; font-size: 12.5px; }}
  code {{ background: #f2f5f8; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
</style>
<div class="wrap">
  <h1>Dados contábeis das empresas</h1>
  <p class="sub">
    Gerado em {meta['gerado_em']} a partir de <code>{html.escape(meta['banco'])}</code> ·
    {meta['empresas']} empresa(s) · identidade contábil — {html.escape(ident)}
  </p>
  {aviso}

  <div class="barra">
    <input type="search" id="busca" placeholder="Buscar empresa, conta, descrição...">
    <select id="fEmpresa"><option value="">Todas as empresas</option></select>
    <select id="fPeriodo"><option value="">Todos os períodos</option></select>
    <select id="fDem"><option value="">Todos os demonstrativos</option></select>
    <span class="contagem" id="contagem"></span>
  </div>

  <div class="tabela-area">
    <table id="tabela">
      <thead><tr>
        <th data-c="empresa">Empresa</th>
        <th data-c="ticker">Ticker</th>
        <th data-c="periodo">Período</th>
        <th data-c="demonstrativo">Dem.</th>
        <th data-c="base">Base</th>
        <th data-c="cd_conta">Conta</th>
        <th data-c="ds_conta">Descrição</th>
        <th data-c="valor">Valor (R$)</th>
        <th data-c="origem_periodo">Origem</th>
        <th data-c="origem">Arquivo:linha</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <p class="rodape">
    Valores em reais, já convertidos de UNIDADE/MILHAR. A coluna
    <strong>Arquivo:linha</strong> aponta a linha física do arquivo da CVM de onde
    o número veio. <strong>Origem</strong> diz <code>DIRETO</code> quando foi lido do
    arquivo e <code>DERIVADO_Q4</code> quando o 4º trimestre foi obtido pela diferença
    entre a DFP anual e o ITR acumulado de 9 meses.
    Nenhum valor é estimado ou interpolado: o que falta, falta.
  </p>
</div>

<script>
const DADOS = {dados};
const COLS = ["empresa","ticker","periodo","demonstrativo","base","cd_conta",
              "ds_conta","valor","origem_periodo","origem"];
const NUM = new Intl.NumberFormat("pt-BR", {{minimumFractionDigits: 2,
                                             maximumFractionDigits: 2}});
let ordem = {{coluna: null, asc: true}};

function unicos(campo) {{
  return [...new Set(DADOS.map(r => r[campo]).filter(v => v !== null && v !== ""))].sort();
}}
for (const [id, campo] of [["fEmpresa","empresa"],["fPeriodo","periodo"],
                           ["fDem","demonstrativo"]]) {{
  const sel = document.getElementById(id);
  for (const v of unicos(campo)) {{
    const o = document.createElement("option"); o.value = o.textContent = v; sel.append(o);
  }}
  sel.addEventListener("change", render);
}}
document.getElementById("busca").addEventListener("input", render);

document.querySelectorAll("#tabela th").forEach(th => th.addEventListener("click", () => {{
  const c = th.dataset.c;
  ordem = {{coluna: c, asc: ordem.coluna === c ? !ordem.asc : true}};
  render();
}}));

function filtrar() {{
  const q = document.getElementById("busca").value.trim().toLowerCase();
  const fe = document.getElementById("fEmpresa").value;
  const fp = document.getElementById("fPeriodo").value;
  const fd = document.getElementById("fDem").value;
  return DADOS.filter(r =>
    (!fe || r.empresa === fe) && (!fp || r.periodo === fp) && (!fd || r.demonstrativo === fd)
    && (!q || COLS.some(c => String(r[c] ?? "").toLowerCase().includes(q))));
}}

function render() {{
  let linhas = filtrar();
  if (ordem.coluna) {{
    const c = ordem.coluna, s = ordem.asc ? 1 : -1;
    linhas = [...linhas].sort((a, b) => {{
      const x = a[c], y = b[c];
      if (x === null) return 1; if (y === null) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * s;
      return String(x).localeCompare(String(y), "pt-BR") * s;
    }});
  }}
  const tbody = document.querySelector("#tabela tbody");
  tbody.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const r of linhas.slice(0, 5000)) {{
    const tr = document.createElement("tr");
    for (const c of COLS) {{
      const td = document.createElement("td");
      if (c === "valor") {{
        td.className = "num";
        td.textContent = r.valor === null ? "—" : NUM.format(r.valor);
      }} else if (c === "origem") {{
        td.className = "origem"; td.textContent = r[c] ?? "";
      }} else {{
        td.textContent = r[c] ?? "";
      }}
      tr.append(td);
    }}
    frag.append(tr);
  }}
  tbody.append(frag);
  const cont = document.getElementById("contagem");
  cont.textContent = linhas.length.toLocaleString("pt-BR") + " linha(s)"
    + (linhas.length > 5000 ? " — exibindo as 5.000 primeiras" : "");
}}
render();
</script>
</html>"""
