"""Criterio de aceite: identidade contabil com tolerancia explicita.

Atencao ao plano da CVM: a conta `2` e Passivo Total e JA INCLUI o patrimonio
liquido (`2.03`). Testar `1 == 2 + 2.03` erraria por exatamente o PL em toda
empresa. O teste correto e duplo -- ver `transform/validations.py`.
"""

from __future__ import annotations

import pytest

from etl.config import TOL_IDENTIDADE_ABS
from tests import fixtures as fx
from transform import dedup, money, periods, validations


def _validar(linhas):
    df = money.converter(fx.quadro(linhas))
    norm = periods.normalizar(dedup.aplicar(df))
    return validations.identidade_contabil(norm)


def test_balanco_que_fecha_passa_nas_duas_identidades():
    ident = _validar(fx.cenario_balanco_completo())
    # Duas linhas, nao uma: o saldo de 31/12 fecha ao mesmo tempo o exercicio
    # e o 4o trimestre, e entra na serie sob os dois rotulos -- mesma linha do
    # mesmo arquivo, mesma linhagem. Antes de 13/09/2026 um documento so de
    # balanco nao ganhava o rotulo trimestral, porque o trimestre era herdado
    # das linhas de fluxo do documento e ali nao havia nenhuma.
    assert set(ident["periodo"]) == {"2023", "2023T4"}
    for _, linha in ident.iterrows():
        assert linha["status"] == "OK"
        assert linha["residuo_principal"] == pytest.approx(0.0)
        assert linha["residuo_decomposto"] == pytest.approx(0.0)


def test_identidade_principal_e_ativo_igual_a_passivo_total():
    """Passivo Total ja contem o PL: somar de novo produziria residuo = PL."""
    ident = _validar(fx.cenario_balanco_completo(pl=400.0))
    at = ident.loc[0, "ativo_total"]
    pt = ident.loc[0, "passivo_total"]
    pl = ident.loc[0, "patrimonio_liquido"]
    assert at == pytest.approx(pt)
    assert at != pytest.approx(pt + pl)


def test_balanco_que_nao_fecha_e_reprovado():
    linhas = fx.cenario_balanco_completo()
    for l in linhas:
        if l["CD_CONTA"] == "1" and l["demonstrativo"] == "BPA":
            l["VL_CONTA"] = str(float(l["VL_CONTA"]) + 50.0)
    ident = _validar(linhas)
    assert ident.loc[0, "status"] == "FALHA"
    assert abs(ident.loc[0, "residuo_principal"]) == pytest.approx(50_000.0)


def test_diferenca_dentro_da_tolerancia_absoluta_nao_reprova():
    linhas = fx.cenario_balanco_completo()
    for l in linhas:
        if l["CD_CONTA"] == "1" and l["demonstrativo"] == "BPA":
            # Em UNIDADE, para que a diferenca fique no piso absoluto de R$ 1.
            l["ESCALA_MOEDA"] = "UNIDADE"
            l["VL_CONTA"] = str(float(l["VL_CONTA"]) * 1000 + TOL_IDENTIDADE_ABS * 0.5)
    ident = _validar(linhas)
    assert ident.loc[0, "status"] == "OK"


def test_conta_ausente_vira_faltando_nao_aprovacao():
    linhas = [l for l in fx.cenario_balanco_completo() if l["CD_CONTA"] != "2.03"]
    ident = _validar(linhas)
    assert ident.loc[0, "status"] == "FALTANDO"


def test_identidade_carrega_a_origem_dos_numeros():
    ident = _validar(fx.cenario_balanco_completo())
    assert ":" in ident.loc[0, "src_ativo"]
    assert ":" in ident.loc[0, "src_passivo"]


def test_escala_milhar_e_convertida_antes_da_comparacao():
    """MILHAR vs UNIDADE no mesmo balanco quebraria a identidade se ignorado."""
    linhas = fx.cenario_balanco_completo()
    for l in linhas:
        if l["CD_CONTA"] in ("2", "2.01", "2.02", "2.03"):
            l["ESCALA_MOEDA"] = "UNIDADE"
            l["VL_CONTA"] = str(float(l["VL_CONTA"]) * 1000)
    ident = _validar(linhas)
    assert ident.loc[0, "status"] == "OK"


def test_resumo_conta_os_tres_estados():
    # 2 = o mesmo balanco sob os rotulos "2023" e "2023T4". Ver o teste do
    # topo deste arquivo.
    ident = _validar(fx.cenario_balanco_completo())
    assert validations.resumo(ident) == {"OK": 2, "FALHA": 0, "FALTANDO": 0}
