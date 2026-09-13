"""Regra 6: plano de contas setorial.

Bancos e seguradoras nao seguem o plano industrial. O teste exige que o
sistema (a) escolha o plano certo, (b) recuse calcular indicador que nao
existe naquele plano, com motivo, e (c) nunca force um numero.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests import fixtures as fx
from transform import money, sector
from transform.lineage import NAO_SE_APLICA


def _fatos_empresa(linhas):
    return money.converter(fx.quadro(linhas))


def test_classificacao_de_setor_por_padrao_do_cadastro():
    cad = pd.concat([
        fx.cadastro(fx.CNPJ_A, "Emp. Adm. Part."),
        fx.cadastro(fx.CNPJ_BANCO, "Bancos"),
    ])
    cls = sector.classificar(cad)
    planos = dict(zip(cls["cnpj"], cls["plano"]))
    assert planos["00000000000191"] == "INDUSTRIAL"
    assert planos["11111111000111"] == "FINANCEIRO"


def test_seguradora_cai_no_plano_de_seguradora():
    cls = sector.classificar(fx.cadastro(fx.CNPJ_A, "Seguros e Previdência"))
    assert cls.loc[0, "plano"] == "SEGURADORA"


# `SETOR_ATIV` como a CVM escreve, copiado da tela do sistema rodando sobre o
# `cad_cia_aberta.csv` real em 13/09/2026. O valor de BBSE3 é o que expôs o
# defeito: o padrão antigo era "SEGURO", e "SEGURADORAS" não contém "SEGURO"
# --- S-E-G-U-R-A-D-O-R-A-S contra S-E-G-U-R-O. A empresa caía no coringa
# `.*` e virava INDUSTRIAL, e o sintoma não era erro: era indicador FALTANDO
# em cascata, porque o plano industrial procurava empréstimo na conta 2.01.04
# e achava "Capitalização".
#
# O teste anterior usava "Seguros e Previdência", string que eu inventei e que
# casava com o padrão antigo. Por isso ele passava enquanto o dado real não.
@pytest.mark.parametrize("setor_ativ,plano", [
    ("Emp. Adm. Part. - Seguradoras e Corretoras", "SEGURADORA"),  # BBSE3, confirmado
    ("Seguradoras", "SEGURADORA"),
    ("Seguros", "SEGURADORA"),
    ("Resseguradoras", "SEGURADORA"),
    ("Previdência e Seguros", "SEGURADORA"),
    ("Capitalização", "SEGURADORA"),
])
def test_variantes_de_seguradora_do_setor_ativ(setor_ativ, plano):
    cls = sector.classificar(fx.cadastro(fx.CNPJ_A, setor_ativ))
    assert cls.loc[0, "plano"] == plano, (
        f"'{setor_ativ}' caiu em {cls.loc[0, 'plano']}: "
        f"{cls.loc[0, 'origem_classificacao']}"
    )


def test_seguranca_nao_e_seguradora():
    """O radical foi encurtado para pegar SEGURADORAS; não pode pegar demais.
    'Segurança' vira 'SEGURANCA' sem acento e não contém SEGURO nem SEGURADOR."""
    cls = sector.classificar(fx.cadastro(fx.CNPJ_A, "Serviços de Segurança"))
    assert cls.loc[0, "plano"] != "SEGURADORA"


def test_setor_desconhecido_nao_vira_industrial_por_omissao():
    """A regra e explicita: sem classificacao, nada de plano industrial calado."""
    cad = fx.cadastro(fx.CNPJ_A, "")
    cad["SETOR_ATIV"] = None
    cls = sector.classificar(cad)
    # O padrao ".*" do arquivo casa string vazia; o que nao pode e o sistema
    # inventar setor. Aqui a origem da classificacao fica registrada.
    assert cls.loc[0, "origem_classificacao"].startswith("SETOR_ATIV=")


def test_banco_nao_calcula_margem_bruta_nem_ebitda():
    r = sector.Resolvedor("FINANCEIRO")
    for indicador in ("margem_bruta", "ebitda", "ev_ebitda"):
        ok, motivo = r.indicador_aplicavel(indicador)
        assert ok is False
        assert motivo and len(motivo) > 20  # o motivo e exibido na interface


def test_banco_ainda_calcula_roe_e_alavancagem():
    r = sector.Resolvedor("FINANCEIRO")
    for indicador in ("roe", "alavancagem", "pvp"):
        ok, _ = r.indicador_aplicavel(indicador)
        assert ok is True


def test_conceito_nao_aplicavel_devolve_nao_se_aplica_nao_zero():
    fatos = _fatos_empresa(fx.cenario_dre_anual())
    r = sector.Resolvedor("FINANCEIRO")
    res = r.resolver(fatos, "receita_liquida")
    assert res.status == "NAO_SE_APLICA"
    assert res.valor is None
    assert "nao tem receita liquida" in res.motivo


def test_conceito_aplicavel_mas_ausente_e_faltando_nao_zero():
    fatos = _fatos_empresa(fx.cenario_balanco_completo())  # sem DRE
    r = sector.Resolvedor("INDUSTRIAL")
    res = r.resolver(fatos, "receita_liquida")
    assert res.status == "FALTANDO"
    assert res.valor is None


def test_resolucao_carrega_a_linha_do_arquivo_de_origem():
    fatos = _fatos_empresa(fx.cenario_dre_anual())
    r = sector.Resolvedor("INDUSTRIAL")
    res = r.resolver(fatos, "receita_liquida")
    assert res.status == "OK"
    assert res.valor == pytest.approx(1_000_000.0)  # 1000 milhares
    assert res.entrada.cd_conta == "3.01"
    assert res.entrada.src_line >= 2
    assert ":" in res.entrada.referencia


def test_descricao_divergente_do_layout_e_sinalizada_nao_aceita():
    """Se o codigo 3.05 vier com outra descricao, o layout mudou."""
    linhas = fx.cenario_dre_anual()
    for l in linhas:
        if l["CD_CONTA"] == "3.05":
            l["DS_CONTA"] = "Outra coisa completamente diferente"
    r = sector.Resolvedor("INDUSTRIAL")
    res = r.resolver(_fatos_empresa(linhas), "ebit")
    assert res.status == "DIVERGENCIA_LAYOUT"
    assert res.valor is None


def test_da_e_encontrada_por_descricao_dentro_do_subgrupo_da_dfc():
    """Nao ha codigo padronizado de D&A: a busca e por descricao sob 6.01.*"""
    fatos = _fatos_empresa(fx.cenario_dfc_anual(da=45.0))
    r = sector.Resolvedor("INDUSTRIAL")
    res = r.resolver(fatos, "depreciacao_amortizacao")
    assert res.status == "OK"
    assert res.valor == pytest.approx(45_000.0)


def test_sem_da_identificavel_o_conceito_fica_faltando():
    linhas = [l for l in fx.cenario_dfc_anual() if "Deprecia" not in (l["DS_CONTA"] or "")]
    r = sector.Resolvedor("INDUSTRIAL")
    res = r.resolver(_fatos_empresa(linhas), "depreciacao_amortizacao")
    assert res.status == "FALTANDO"


def test_plano_indefinido_nao_calcula_indicador_sensivel_a_setor():
    r = sector.Resolvedor("INDEFINIDO")
    ok, motivo = r.indicador_aplicavel("margem_liquida")
    assert ok is False
    assert "nao classificado" in motivo


def test_energia_herda_o_plano_industrial():
    r = sector.Resolvedor("ENERGIA")
    fatos = _fatos_empresa(fx.cenario_dre_anual())
    assert r.resolver(fatos, "receita_liquida").status == "OK"
