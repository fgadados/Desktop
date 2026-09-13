"""O balanço comparativo do ITR é 31/12, não o mesmo trimestre do ano anterior.

O caso real (13/09/2026, `run.py identidade` sobre ITUB4). Os quatro trimestres
de 2019 traziam o MESMO ativo total, idêntico ao do balanço anual:

    2019     1.637.481.000.000   ativo@2020-12-31   dfp_2020.csv:45721
    2019T1   1.637.481.000.000   ativo@2020-03-31   itr_2020.csv:113032
    2019T2   1.637.481.000.000   ativo@2020-06-30   itr_2020.csv:113104
    2019T3   1.637.481.000.000   ativo@2020-09-30   itr_2020.csv:113176

Balanço é saldo numa data: T1, T2 e T3 têm que diferir entre si. Os três eram
o mesmo saldo de 31 de dezembro de 2019.

A causa
-------
Num relatório intermediário as duas colunas usam convenções de comparativo
DIFERENTES:

    ORDEM_EXERC=ÚLTIMO      o período corrente, em ambas as demonstrações;
    ORDEM_EXERC=PENÚLTIMO   DRE compara com o mesmo período do ano anterior,
                            BALANÇO compara com o FECHAMENTO do exercício
                            anterior (31/12).

`rotular_saldos` dava ao balanço o trimestre do DOCUMENTO, com o argumento de
que "o balanço de um ITR do 2º trimestre fecha em T2". Verdade para a coluna
ÚLTIMO, falso para a PENÚLTIMO — e a regra era aplicada às duas.

A correção (decisão do usuário, 13/09/2026): o trimestre do saldo sai da data
do próprio saldo, o que vale para as duas colunas. Efeito colateral aceito: um
trimestre antigo cujo ITR próprio não foi baixado deixa de ter balanço, em vez
de exibir o de 31/12. Faltou, aparece como faltando.
"""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import pipeline

CNPJ = fx.CNPJ_A


def _itr_com_comparativo(ano: int, trimestre: int, ativo_corrente: float,
                         ativo_fechamento_anterior: float):
    """Um ITR como a CVM entrega: as duas colunas, com as duas convenções."""
    fim_mes = {1: "03-31", 2: "06-30", 3: "09-30"}[trimestre]
    dt_refer = f"{ano}-{fim_mes}"
    ini = f"{ano}-01-01"
    ini_anterior = f"{ano - 1}-01-01"
    return [
        # --- coluna ÚLTIMO: exercício corrente ---
        fx.fato(cnpj=CNPJ, dt_refer=dt_refer, doc="ITR", demonstrativo="BPA",
                ordem="ÚLTIMO", cd_conta="1", ds_conta="Ativo Total",
                vl=ativo_corrente, dt_fim=dt_refer),
        fx.fato(cnpj=CNPJ, dt_refer=dt_refer, doc="ITR", demonstrativo="DRE",
                ordem="ÚLTIMO", cd_conta="3.01", ds_conta="Receita",
                vl=100.0 * trimestre, dt_ini=ini, dt_fim=dt_refer),
        # --- coluna PENÚLTIMO: as duas convenções ---
        # DRE: mesmo período acumulado do ano anterior.
        fx.fato(cnpj=CNPJ, dt_refer=dt_refer, doc="ITR", demonstrativo="DRE",
                ordem="PENÚLTIMO", cd_conta="3.01", ds_conta="Receita",
                vl=90.0 * trimestre, dt_ini=ini_anterior,
                dt_fim=f"{ano - 1}-{fim_mes}"),
        # BALANÇO: fechamento do exercício anterior, sempre 31/12.
        fx.fato(cnpj=CNPJ, dt_refer=dt_refer, doc="ITR", demonstrativo="BPA",
                ordem="PENÚLTIMO", cd_conta="1", ds_conta="Ativo Total",
                vl=ativo_fechamento_anterior, dt_fim=f"{ano - 1}-12-31"),
    ]


@pytest.fixture(scope="module")
def fatos():
    linhas = []
    for tri, ativo in ((1, 1_100.0), (2, 1_200.0), (3, 1_300.0)):
        linhas += _itr_com_comparativo(2020, tri, ativo,
                                       ativo_fechamento_anterior=999.0)
    return pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]


def _ativo(fatos, periodo: str):
    sel = fatos[(fatos["CD_CONTA"] == "1") & (fatos["periodo"] == periodo)]
    return None if sel.empty else sel.iloc[0]["VL_CONTA_NUM"]


def test_o_saldo_comparativo_vai_para_o_fechamento_do_ano_anterior():
    """999 e o saldo de 31/12/2019: tem que aparecer em 2019T4, nao em T1."""
    linhas = []
    for tri, ativo in ((1, 1_100.0), (2, 1_200.0), (3, 1_300.0)):
        linhas += _itr_com_comparativo(2020, tri, ativo, 999.0)
    f = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]

    assert _ativo(f, "2019T4") == pytest.approx(999_000.0)
    for periodo in ("2019T1", "2019T2", "2019T3"):
        assert _ativo(f, periodo) is None, (
            f"{periodo} recebeu um saldo que e de 31/12/2019 -- era este o "
            "defeito encontrado no ITUB4"
        )


def test_os_trimestres_correntes_nao_se_repetem(fatos):
    """O contrario do sintoma: cada trimestre com seu proprio saldo."""
    valores = [_ativo(fatos, f"2020T{t}") for t in (1, 2, 3)]
    assert valores == [pytest.approx(v) for v in (1_100_000.0, 1_200_000.0,
                                                  1_300_000.0)]
    assert len(set(valores)) == 3, "tres trimestres, tres saldos distintos"


def test_a_dre_comparativa_continua_no_trimestre_do_ano_anterior(fatos):
    """A correcao mexeu SO no saldo patrimonial.

    A DRE comparativa segue casando com o mesmo periodo do ano anterior --
    2019T1, e nao 2019T4 como o balanco. Sao as duas convencoes convivendo no
    mesmo documento, que e o ponto todo deste arquivo.

    Só o T1 aparece: em T2 e T3 a janela comparativa e ACUMULADA (6M, 9M), e a
    regra 5 descarta acumulado do ITR da serie trimestral, por desenho.
    """
    dre = fatos[(fatos["CD_CONTA"] == "3.01")
                & (fatos["ordem_exerc_norm"] == "PENULTIMO")]
    assert list(dre["periodo"]) == ["2019T1"], dre[["periodo", "tipo_janela"]]
    assert dre.iloc[0]["VL_CONTA_NUM"] == pytest.approx(90_000.0)


def test_saldo_de_31_12_entra_no_ano_e_no_quarto_trimestre(fatos):
    """Fecha o exercicio e o 4o trimestre ao mesmo tempo, sob os dois rotulos."""
    assert _ativo(fatos, "2019") == pytest.approx(999_000.0)
    assert _ativo(fatos, "2019T4") == pytest.approx(999_000.0)


def test_saldo_do_terceiro_trimestre_nao_e_marcado_como_anual(fatos):
    sel = fatos[(fatos["CD_CONTA"] == "1") & (fatos["periodo"] == "2020T3")]
    assert not sel.iloc[0]["saldo_de_exercicio_anual"]
