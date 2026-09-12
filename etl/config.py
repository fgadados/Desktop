"""Caminhos, URLs oficiais e parametros globais do pipeline.

Regra do projeto: nenhuma URL de terceiro. Somente CVM Dados Abertos, B3 e
BCB/SGS. Os diretorios da CVM sao listados antes do download (ver
`etl.cvm_common.listar_diretorio`); os nomes de arquivo nunca sao inventados.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Raiz do projeto e layout de dados
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("B3DSS_DATA", ROOT / "data"))

RAW = DATA / "raw"  # bytes exatamente como baixados
PARQUET = DATA / "parquet"  # brutos tabulados, sem normalizacao
WAREHOUSE = DATA / "warehouse"  # saida da camada transform
MANUAL = DATA / "manual"  # entradas mantidas a mao pelo usuario
DUCKDB_PATH = DATA / "b3dss.duckdb"

CONFIG_DIR = ROOT / "config"

for _d in (RAW, PARQUET, WAREHOUSE, MANUAL):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Fontes oficiais
# --------------------------------------------------------------------------
CVM_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA"

CVM_DIRS = {
    # documento -> (diretorio de DADOS, diretorio de METADADOS)
    "DFP": (f"{CVM_BASE}/DOC/DFP/DADOS/", f"{CVM_BASE}/DOC/DFP/META/"),
    "ITR": (f"{CVM_BASE}/DOC/ITR/DADOS/", f"{CVM_BASE}/DOC/ITR/META/"),
    "IPE": (f"{CVM_BASE}/DOC/IPE/DADOS/", f"{CVM_BASE}/DOC/IPE/META/"),
    "FCA": (f"{CVM_BASE}/DOC/FCA/DADOS/", f"{CVM_BASE}/DOC/FCA/META/"),
    "CAD": (f"{CVM_BASE}/CAD/DADOS/", f"{CVM_BASE}/CAD/META/"),
}

# B3 COTAHIST anual (layout fixo de 245 bytes).
COTAHIST_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ano}.ZIP"
COTAHIST_ANO_INICIAL = 2010

# BCB / SGS. Codigos conferidos no proprio catalogo do SGS.
BCB_SGS_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
    "?formato=json&dataInicial={ini}&dataFinal={fim}"
)
BCB_SERIES = {
    "selic_diaria": 11,  # Taxa Selic, % a.d.
    "ipca_mensal": 433,  # IPCA, variacao mensal %
    "usd_venda": 1,  # Dolar comercial venda, R$/US$
}

# --------------------------------------------------------------------------
# Parametros de leitura dos arquivos da CVM
# --------------------------------------------------------------------------
CVM_ENCODING = "latin-1"
CVM_SEP = ";"

# --------------------------------------------------------------------------
# Tolerancias explicitas (criterio de aceite: nada de tolerancia implicita)
# --------------------------------------------------------------------------
# Identidade contabil Ativo = Passivo + PL. A CVM publica em reais com
# ESCALA_MOEDA em unidade ou milhar; a tolerancia e relativa ao ativo total
# para nao punir arredondamento de escala, com piso absoluto em R$ 1,00.
TOL_IDENTIDADE_REL = 1e-6
TOL_IDENTIDADE_ABS = 1.0

# Derivacao do 4o trimestre (DFP anual menos acumulado de 9 meses do ITR).
# Divergencia acima disso marca o trimestre como suspeito, nunca o corrige.
TOL_Q4_REL = 1e-6
TOL_Q4_ABS = 1.0

HTTP_TIMEOUT = 120
HTTP_RETRIES = 4
USER_AGENT = "b3-dss/0.1 (uso pessoal, nao comercial)"
