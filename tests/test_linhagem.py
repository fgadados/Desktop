"""Criterio de aceite: toda linha de indicador carrega contas de origem e formula."""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import indicators, lineage as ln, money, pipeline, sector


def _fundamentos(linhas, plano="INDUSTRIAL"):
    fatos = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    conceitos = list(sector.carregar_planos()["planos"]["INDUSTRIAL"]["contas"])
    return indicators.montar_fundamentos(fatos, {fx.CNPJ_A: plano}, conceitos)


def test_indicador_ok_sem_entradas_e_impossivel_por_construcao():
    with pytest.raises(ValueError, match="sem entradas"):
        ln.Indicador("x", "2023", "roe", 0.2, "roe = ll / pl", ln.OK, ())


def test_indicador_sem_formula_e_impossivel():
    with pytest.raises(ValueError, match="sem formula"):
        ln.Indicador("x", "2023", "roe", 0.2, "  ", ln.OK,
                     (ln.Entrada(rotulo="ll", valor=1.0),))


def test_status_diferente_de_ok_exige_motivo():
    with pytest.raises(ValueError, match="sem motivo"):
        ln.Indicador("x", "2023", "roe", None, "roe = ll / pl", ln.FALTANDO, ())


def test_dupont_reproduz_o_roe_direto():
    linhas = fx.cenario_balanco_completo(pl=400.0) + fx.cenario_dre_anual(
        receita=1000.0, lucro=80.0
    )
    fund = _fundamentos(linhas)
    inds = {i.nome: i for i in indicators.dupont(fund, fx.CNPJ_A, "2023")}

    assert inds["margem_liquida"].valor == pytest.approx(80 / 1000)
    assert inds["giro_ativo"].valor == pytest.approx(1000 / 1000)
    assert inds["alavancagem"].valor == pytest.approx(1000 / 400)
    assert inds["roe"].valor == pytest.approx(80 / 400)
    assert inds["roe_dupont"].valor == pytest.approx(inds["roe"].valor)
    assert inds["roe_dupont"].extra["diferenca_identidade"] == pytest.approx(0.0, abs=1e-12)


def test_cada_indicador_carrega_formula_contas_e_linha_de_origem():
    linhas = fx.cenario_balanco_completo() + fx.cenario_dre_anual()
    fund = _fundamentos(linhas)
    for ind in indicators.dupont(fund, fx.CNPJ_A, "2023"):
        assert ind.formula
        if ind.status == ln.OK:
            assert ind.entradas
            for e in ind.entradas:
                assert e.cd_conta and e.ds_conta
                assert e.src_file and e.src_line
                assert ":" in e.referencia
                # As tres colunas que respondem "qual documento gerou este
                # numero": versao (regra 2), ordem de exercicio (regra 3) e
                # base contabil (regra 4). Nenhuma pode chegar nula na tela.
                assert e.versao and e.ordem_exerc and e.base
                assert e.ordem_exerc.upper().startswith(("ÚLTIMO", "ULTIMO",
                                                         "PENÚLTIMO", "PENULTIMO"))
            linha = ind.para_linha()
            assert linha["contas_origem"]


def test_ebitda_exige_da_identificavel_e_nao_estima():
    """Decisao do usuario: EBITDA obrigatorio; sem D&A, fica FALTANDO."""
    com_da = fx.cenario_balanco_completo() + fx.cenario_dre_anual() + fx.cenario_dfc_anual(da=45.0)
    fund = _fundamentos(com_da)
    ebitda = [i for i in indicators.fluxo_e_divida(fund, fx.CNPJ_A, "2023") if i.nome == "ebitda"][0]
    assert ebitda.status == ln.OK
    assert ebitda.valor == pytest.approx((120 + 45) * 1000)

    sem_da = [l for l in com_da if "Deprecia" not in (l["DS_CONTA"] or "")]
    fund2 = _fundamentos(sem_da)
    ebitda2 = [i for i in indicators.fluxo_e_divida(fund2, fx.CNPJ_A, "2023") if i.nome == "ebitda"][0]
    assert ebitda2.status == ln.FALTANDO
    assert ebitda2.valor is None


def test_banco_recebe_nao_se_aplica_em_vez_de_numero():
    linhas = fx.cenario_balanco_completo(cnpj=fx.CNPJ_BANCO) + fx.cenario_dre_anual(
        cnpj=fx.CNPJ_BANCO
    )
    fatos = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    conceitos = ["receita_liquida", "lucro_liquido", "patrimonio_liquido", "ativo_total"]
    fund = indicators.montar_fundamentos(fatos, {fx.CNPJ_BANCO: "FINANCEIRO"}, conceitos)
    inds = {i.nome: i for i in indicators.dupont(fund, fx.CNPJ_BANCO, "2023")}
    assert inds["margem_liquida"].status == ln.NAO_SE_APLICA
    assert inds["margem_liquida"].motivo
    assert inds["roe"].status == ln.OK  # ROE continua valendo para banco


def test_fcl_e_soma_de_fco_e_fci():
    linhas = fx.cenario_balanco_completo() + fx.cenario_dre_anual() + fx.cenario_dfc_anual(
        fco=150.0, fci=-60.0
    )
    fund = _fundamentos(linhas)
    fcl = [i for i in indicators.fluxo_e_divida(fund, fx.CNPJ_A, "2023") if i.nome == "fcl"][0]
    assert fcl.valor == pytest.approx(90_000.0)
    assert "fco + fci" in fcl.formula


def test_divida_liquida_usa_caixa_e_registra_as_tres_contas():
    linhas = fx.cenario_balanco_completo() + fx.cenario_dre_anual()
    fund = _fundamentos(linhas)
    dl = [i for i in indicators.fluxo_e_divida(fund, fx.CNPJ_A, "2023")
          if i.nome == "divida_liquida"][0]
    assert dl.valor == pytest.approx((120 + 180 - 100) * 1000)
    assert {e.cd_conta for e in dl.entradas} == {"2.01.04", "2.02.01", "1.01.01"}


def test_quadros_para_carga_tem_uma_linha_por_entrada():
    linhas = fx.cenario_balanco_completo() + fx.cenario_dre_anual()
    fund = _fundamentos(linhas)
    inds = indicators.dupont(fund, fx.CNPJ_A, "2023")
    cab, ent = ln.para_quadros(inds)
    assert len(cab) == len(inds)
    assert set(ent.columns) >= {"referencia", "src_file", "src_line", "cd_conta"}


def test_ttm_exige_quatro_trimestres_consecutivos():
    fund = _fundamentos(fx.cenario_itr_trimestres())
    t = indicators.ttm(fund, "lucro_liquido")
    assert t.empty  # so ha 3 trimestres: nada de extrapolar
