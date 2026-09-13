"""Roda `run.py transformar` inteiro e confere que TODA tabela carrega.

Por que este teste existe
-------------------------
Três vezes seguidas a mesma classe de erro passou pelos testes e só apareceu
na máquina do usuário, depois de 25 minutos de download:

  1. `COLUNA_DF` fora da chave do fato -- a DMPL quebrava o merge;
  2. exercício publicado em dois documentos -- o Q4 virava merge N-para-N;
  3. `COLUNA_DF` na tabela `reapresentacao` -- a carga recusava a tabela.

Os três tinham a mesma causa metodológica: os testes exercitavam as funções
isoladamente, com dado sintético escolhido para a regra sob teste. Ninguém
exercitava o CAMINHO COMPLETO -- ler parquet bruto, aplicar as seis regras,
calcular indicador e gravar CADA tabela do schema.

O terceiro erro é o mais instrutivo. `relatorio_divergencias` só devolve linha
quando existe reapresentação de fato. Com dado sintético sem divergência, o
relatório vinha vazio, `_para_reapresentacao` devolvia o quadro-placeholder e
a carga passava. Com sete anos de CVM real, divergência é rotina -- e a coluna
que faltava no schema derrubou tudo.

Daí a exigência deste cenário: ele precisa produzir linha em toda tabela que o
`transformar` grava. Tabela que fica vazia não prova nada.

O teste roda em subprocesso de propósito. `etl.config` lê `B3DSS_DATA` no
import; trocar isso dentro do processo de teste testaria outra coisa. E assim
o que roda aqui é a mesma linha de comando que roda no Mac.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from tests import fixtures as fx

RAIZ = Path(__file__).resolve().parent.parent
TICKER = "WEGE3"  # precisa estar em config/tickers.yml para o de-para resolver


# ---------------------------------------------------------------------------
# Cenário: uma empresa, com todas as patologias que o pipeline trata
# ---------------------------------------------------------------------------
def _dmpl(ano: int, dt_refer: str, ordem: str, valor_lucros: float):
    """DMPL: a mesma conta em duas colunas do PL.

    É a dimensão `COLUNA_DF`. Sem ela na chave, as duas linhas colidem.
    """
    fim = f"{ano}-12-31"
    return [
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="DMPL", ordem=ordem,
                cd_conta="5.01", ds_conta="Saldo Inicial", vl=1000.0,
                dt_ini=f"{ano}-01-01", dt_fim=fim, coluna_df="Capital Social Integralizado"),
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="DMPL", ordem=ordem,
                cd_conta="5.01", ds_conta="Saldo Inicial", vl=valor_lucros,
                dt_ini=f"{ano}-01-01", dt_fim=fim, coluna_df="Lucros ou Prejuizos Acumulados"),
    ]


def _fatos_cvm() -> tuple[pd.DataFrame, pd.DataFrame]:
    """DFP e ITR sintéticos com reapresentação, DMPL e Q4 derivável."""
    dfp: list[dict] = []

    # Exercícios 2022 e 2023 completos, publicados no próprio ano (ÚLTIMO).
    for ano, pl in ((2022, 380.0), (2023, 400.0)):
        dfp += fx.cenario_balanco_completo(ano=ano, pl=pl)
        dfp += fx.cenario_dre_anual(ano=ano)
        dfp += fx.cenario_dfc_anual(ano=ano)
        dfp += _dmpl(ano, f"{ano}-12-31", "ÚLTIMO", 500.0)

    # A DFP de 2023 traz 2022 como comparativo e REAPRESENTA: PL 380 -> 372,
    # lucro da DMPL 500 -> 488. É o que popula a tabela `reapresentacao` --
    # inclusive com a dimensão COLUNA_DF, que era a coluna que faltava.
    dfp += [
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="BPP", ordem="PENÚLTIMO",
                cd_conta="2.03", ds_conta="Patrimônio Líquido Consolidado", vl=372.0,
                dt_fim="2022-12-31"),
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="DRE", ordem="PENÚLTIMO",
                cd_conta="3.11", ds_conta="Lucro/Prejuízo do Período", vl=88.0,
                dt_ini="2022-01-01", dt_fim="2022-12-31"),
    ]
    dfp += _dmpl(2022, "2023-12-31", "PENÚLTIMO", 488.0)

    # ITR de 2023: acumulados que permitem derivar o Q4 por diferença (regra 5).
    itr = fx.cenario_itr_completo(ano=2023)

    return fx.quadro(dfp), fx.quadro(itr)


def _cotahist() -> pd.DataFrame:
    """COTAHIST no layout já parseado, com um salto que vira evento suspeito."""
    pregoes = pd.bdate_range("2023-01-02", "2023-12-29")
    fech = [20.0 + i * 0.01 for i in range(len(pregoes))]
    fech[-30:] = [v / 2 for v in fech[-30:]]  # desdobramento não cadastrado

    return pd.DataFrame({
        "TIPREG": 1, "DATA": [d.strftime("%Y%m%d") for d in pregoes],
        "CODBDI": "02", "CODNEG": TICKER, "TPMERC": "010",
        "NOMRES": "TESTE", "ESPECI": "ON", "PRAZOT": "", "MODREF": "R$",
        "PREABE": fech, "PREMAX": fech, "PREMIN": fech, "PREMED": fech,
        "PREULT": fech, "PREOFC": fech, "PREOFV": fech,
        "TOTNEG": 100, "QUATOT": 10_000, "VOLTOT": [f * 10_000 for f in fech],
        "PREEXE": 0.0, "INDOPC": 0, "DATVEN": "99991231", "FATCOT": 1,
        "PTOEXE": 0.0, "CODISI": "BRTESTACNOR0", "DISMES": 0,
        "data": pregoes,
        "src_archive": "COTAHIST_A2023.ZIP", "src_file": "COTAHIST_A2023.TXT",
        "src_line": range(2, 2 + len(pregoes)), "src_sha256": "0" * 64,
    })


def _fca() -> pd.DataFrame:
    return pd.DataFrame([{
        "CNPJ_Companhia": fx.CNPJ_A, "Data_Referencia": "2023-12-31", "Versao": "2",
        "ID_Documento": "1", "Valor_Mobiliario": "Ações Ordinárias",
        "Sigla_Classe_Acao_Preferencial": None, "Codigo_Negociacao": TICKER,
        "Mercado": "Bolsa", "Sigla_Entidade_Administradora": "B3",
        "Data_Inicio_Negociacao": "2010-01-04", "Data_Fim_Negociacao": None,
        "src_file": "fca_cia_aberta_valor_mobiliario_2023.csv", "src_line": 2,
    }])


def _composicao_capital() -> pd.DataFrame:
    return pd.DataFrame([{
        "CNPJ_CIA": fx.CNPJ_A, "DT_REFER": "2023-12-31", "VERSAO": "1",
        "DENOM_CIA": "COMPANHIA TESTE S.A.",
        "QT_ACAO_ORDIN_CAP_INTEGR": 100_000_000.0, "QT_ACAO_PREF_CAP_INTEGR": 0.0,
        "QT_ACAO_TOTAL_CAP_INTEGR": 100_000_000.0,
        "QT_ACAO_ORDIN_TESOURO": 1_000_000.0, "QT_ACAO_PREF_TESOURO": 0.0,
        "QT_ACAO_TOTAL_TESOURO": 1_000_000.0,
        "doc": "DFP",
        "src_file": "dfp_cia_aberta_2023.csv", "src_line": 2,
    }])


def _sgs() -> pd.DataFrame:
    datas = pd.bdate_range("2023-01-02", "2023-03-31")
    return pd.DataFrame({
        "serie": "selic_diaria", "codigo_sgs": 11, "data": datas, "valor": 0.05,
        "src_file": "dados-teste.json", "src_line": range(1, 1 + len(datas)),
    })


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def banco(tmp_path_factory):
    dados = tmp_path_factory.mktemp("b3dss_dados")
    pq = dados / "parquet"
    (pq / "cvm").mkdir(parents=True)
    (pq / "b3").mkdir(parents=True)
    (pq / "bcb").mkdir(parents=True)

    dfp, itr = _fatos_cvm()
    dfp.to_parquet(pq / "cvm" / "dfp_fatos.parquet")
    itr.to_parquet(pq / "cvm" / "itr_fatos.parquet")
    fx.cadastro().to_parquet(pq / "cvm" / "cadastro.parquet")
    _fca().to_parquet(pq / "cvm" / "fca_valor_mobiliario.parquet")
    _composicao_capital().to_parquet(pq / "cvm" / "dfp_composicao_capital.parquet")
    _cotahist().to_parquet(pq / "b3" / "cotahist.parquet")
    _sgs().to_parquet(pq / "bcb" / "sgs.parquet")

    env = {**os.environ, "B3DSS_DATA": str(dados), "PYTHONUNBUFFERED": "1"}

    def rodar(rotulo: str) -> str:
        proc = subprocess.run(
            [sys.executable, "run.py", "transformar"],
            cwd=RAIZ, env=env, capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, (
            f"`run.py transformar` falhou na {rotulo} -- e exatamente o comando "
            f"que roda no Mac.\n--- stdout ---\n{proc.stdout}"
            f"\n--- stderr ---\n{proc.stderr}"
        )
        return proc.stdout

    # Duas execuções seguidas no MESMO diretório. A segunda é a que vale: ela
    # encontra um banco já criado, que é a situação em que `CREATE TABLE IF NOT
    # EXISTS` deixa de proteger e a carga passa a falhar por uma coluna que
    # existe no `.sql`. Testar só com banco novo esconde essa classe inteira.
    primeira = rodar("primeira execucao")
    segunda = rodar("segunda execucao, sobre banco ja existente")

    con = duckdb.connect(str(dados / "b3dss.duckdb"), read_only=True)
    yield con, primeira, segunda
    con.close()


def _n(con, tabela: str) -> int:
    return con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


# Toda tabela que `transformar` grava neste cenário. Tabela que sai vazia não
# exercita a carga -- por isso a lista é de tabelas que TÊM que ter linha.
TABELAS_OBRIGATORIAS = [
    "fato_contabil",
    "teste_identidade",
    "reapresentacao",     # o erro de 13/09/2026 estava aqui
    "empresa",
    "depara_ticker",
    "preco_diario",
    "cobertura_ajuste",
    "evento_suspeito",    # o salto de 50% no cenário de preço
    "acoes_em_circulacao",
    "serie_macro",
    "indicador",
    "indicador_entrada",
    "execucao",
]


@pytest.mark.parametrize("tabela", TABELAS_OBRIGATORIAS)
def test_tabela_carregada(banco, tabela):
    con, *_ = banco
    assert _n(con, tabela) > 0, (
        f"'{tabela}' ficou vazia. Ou o cenario deixou de exercitar esse caminho, "
        "ou a carga silenciou -- os dois casos precisam de correcao, nao de "
        "remover a tabela desta lista."
    )


def test_reapresentacao_preserva_a_coluna_da_dmpl(banco):
    """A regressão concreta: `COLUNA_DF` não existia no schema e a carga
    recusava a tabela inteira com KeyError."""
    con, *_ = banco
    dmpl = con.execute(
        "SELECT coluna_df, valor_as_filed, valor_restated FROM reapresentacao "
        "WHERE demonstrativo = 'DMPL' ORDER BY coluna_df"
    ).fetchdf()

    assert not dmpl.empty, "a reapresentacao da DMPL sumiu do relatorio"
    assert dmpl["coluna_df"].str.strip().ne("").all(), (
        "sem `coluna_df` nao da para saber QUAL coluna do PL foi reapresentada"
    )
    # 500 -> 488 na coluna de lucros acumulados (valores em MILHAR).
    lucros = dmpl[dmpl["coluna_df"].str.contains("Lucros")].iloc[0]
    assert lucros["valor_as_filed"] == pytest.approx(500_000.0)
    assert lucros["valor_restated"] == pytest.approx(488_000.0)


def test_reapresentacao_do_balanco_tambem_entra(banco):
    """PL 380 -> 372: a divergência fica visível, não é escolhida em silêncio."""
    con, *_ = banco
    linha = con.execute(
        "SELECT valor_as_filed, valor_restated, n_publicacoes FROM reapresentacao "
        "WHERE cd_conta = '2.03' AND periodo = '2022'"
    ).fetchdf()
    assert len(linha) == 1
    assert linha["valor_as_filed"].iloc[0] == pytest.approx(380_000.0)
    assert linha["valor_restated"].iloc[0] == pytest.approx(372_000.0)
    assert linha["n_publicacoes"].iloc[0] == 2


def test_nenhuma_coluna_do_transform_fica_fora_do_schema(banco):
    """`db.load.substituir` levanta KeyError quando a camada transform ganha
    coluna que o schema não tem. Este teste é o que faz esse erro aparecer
    aqui, e não depois de meia hora de download na máquina do usuário."""
    _, primeira, segunda = banco
    for rotulo, saida in (("1a", primeira), ("2a", segunda)):
        assert "nao previstas no schema" not in saida
        assert "[5/6] ok carga" in saida, (
            f"a carga precisa ter concluido na {rotulo} execucao; saida:\n{saida}"
        )


def test_segunda_execucao_nao_recria_o_banco(banco):
    """Com o schema em dia, reconectar não pode apagar e refazer o arquivo.
    A recriação existe para schema que mudou, não para toda rodada."""
    _, _, segunda = banco
    assert "schema mudou" not in segunda


def test_q4_derivado_existe(banco):
    """Regra 5: o quarto trimestre não é publicado, é derivado por diferença."""
    con, *_ = banco
    n = con.execute(
        "SELECT count(*) FROM fato_contabil WHERE origem_periodo = 'DERIVADO_Q4'"
    ).fetchone()[0]
    assert n > 0


def test_acoes_em_circulacao_desconta_tesouraria(banco):
    con, *_ = banco
    linha = con.execute(
        "SELECT acoes_total, tesouraria_total, acoes_em_circulacao "
        "FROM acoes_em_circulacao"
    ).fetchdf().iloc[0]
    assert linha["acoes_em_circulacao"] == pytest.approx(99_000_000.0)


def test_preco_sem_evento_cadastrado_nao_finge_estar_ajustado(banco):
    """O cenário tem um salto de 50% sem evento em corporate_events.csv.
    A série não pode sair marcada como ajustada."""
    con, *_ = banco
    status = con.execute(
        "SELECT status FROM cobertura_ajuste WHERE ticker = ?", [TICKER]
    ).fetchone()[0]
    assert status in ("SEM_EVENTOS", "SUSPEITA_NAO_RESOLVIDA")
