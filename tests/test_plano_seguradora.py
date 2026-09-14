"""Plano de seguradora conferido contra a DRE real da BBSE3.

Fonte: `python run.py contas BBSE3` em 13/09/2026, sobre o ITR CON de 2026T2
(`itr_cia_aberta_{BPA,BPP,DRE}_con_2026.csv`, 214 contas). Os códigos e
descrições abaixo são cópia do arquivo, não memória.

O achado que este arquivo existe para travar
--------------------------------------------
A DRE de seguradora termina assim:

    3.09  Resultado Antes dos Tributos sobre o Lucro     2.883.137
    3.10  Imposto de Renda e Contribuição Social          -470.405
    3.11  Resultado Líquido das Operações Continuadas    2.412.732
    3.12  Resultado Líquido de Operações Descontinuadas          0
    3.13  Lucro/Prejuízo Consolidado do Período          2.412.732

Duas coisas, as duas erradas antes de 13/09/2026:

1. O resultado final é **3.13**, não 3.11. No plano industrial 3.11 é o
   consolidado do período; aqui é só a parte continuada.

2. `lucro_liquido` aceitava `["3.11", "3.09"]` com padrão `/LUCRO|PREJUIZO/`.
   A descrição de 3.09 é "Resultado Antes dos Tributos sobre o **Lucro**" --
   casa com o padrão. Bastava a companhia não publicar 3.11 para o sistema
   entregar resultado ANTES de imposto como lucro líquido, em silêncio: aqui,
   R$ 470 milhões a mais num trimestre, 16%.

Resultado antes de imposto não é aproximação de lucro líquido; é outro número.
"""

from __future__ import annotations

import pytest

from transform import sector

# Copiado da saída de `run.py contas BBSE3`, ITR CON 2026T2. Valores em reais.
DRE_BBSE3_2026T2 = {
    "3.01": ("Receitas das Atividades Seguradoras/Resseguradoras", 0.0),
    "3.02": ("Despesas da Atividade Seguradora/Resseguradora", 0.0),
    "3.03": ("Resultado Bruto", 0.0),
    "3.04": ("Despesas Administrativas", 0.0),
    "3.05": ("Outras Receitas e Despesas Operacionais", 1_113_768_000.0),
    "3.06": ("Resultado de Equivalência Patrimonial", 1_490_310_000.0),
    "3.07": ("Resultado Antes do Resultado Financeiro e dos Tributos",
             2_604_078_000.0),
    "3.08": ("Resultado Financeiro", 279_059_000.0),
    "3.09": ("Resultado Antes dos Tributos sobre o Lucro", 2_883_137_000.0),
    "3.10": ("Imposto de Renda e Contribuição Social sobre o Lucro",
             -470_405_000.0),
    "3.11": ("Resultado Líquido das Operações Continuadas", 2_412_732_000.0),
    "3.12": ("Resultado Líquido de Operações Descontinuadas", 0.0),
    "3.13": ("Lucro/Prejuízo Consolidado do Período", 2_412_732_000.0),
}

BPP_BBSE3_2026T2 = {
    "2": ("Passivo Total", 21_923_706_000.0),
    "2.01": ("Passivo Circulante", 7_160_130_000.0),
    "2.01.04": ("Capitalização", 0.0),
    "2.02": ("Passivo Não Circulante", 3_873_231_000.0),
    "2.03": ("Patrimônio Líquido Consolidado", 10_890_345_000.0),
}


@pytest.fixture(scope="module")
def planos() -> dict:
    return sector.carregar_planos()["planos"]


def _conceito(planos: dict, plano: str, nome: str) -> dict:
    contas = planos[plano]["contas"]
    assert nome in contas, f"{plano} nao declara o conceito '{nome}'"
    return contas[nome]


# ---------------------------------------------------------------------------
# O risco de trocar lucro por resultado antes de imposto
# ---------------------------------------------------------------------------
# FINANCEIRO ficou de fora, e a correcao de um erro meu. Esta lista dizia
# "nenhum plano", generalizando o arquivo da seguradora para todos os setores.
# O BPP real do ITUB4, lido um dia depois, mostrou que num BANCO 3.09 e
# "Lucro/Prejuizo Consolidado do Periodo" -- o resultado final, nao o
# pre-imposto. O mesmo codigo, tres significados:
#
#     banco       3.09  Lucro/Prejuizo Consolidado do Periodo
#     seguradora  3.09  Resultado Antes dos Tributos sobre o Lucro
#     industrial  3.11  Lucro/Prejuizo Consolidado do Periodo
#
# E exatamente o que a regra 6 existe para tratar, e a razao de
# `padrao_ds_conta` existir: o codigo sozinho nao basta, a descricao decide.
# Ver `tests/test_plano_financeiro.py`.
@pytest.mark.parametrize("plano", ["INDUSTRIAL", "SEGURADORA"])
def test_3_09_nao_e_lucro_liquido_nestes_planos(planos, plano):
    """Aqui 3.09 é resultado ANTES de imposto, e nunca é o lucro líquido."""
    codigos = _conceito(planos, plano, "lucro_liquido")["codigos"]
    assert "3.09" not in codigos, (
        f"{plano}: 3.09 de volta na lista de lucro_liquido. No arquivo real "
        "ele e 'Resultado Antes dos Tributos sobre o Lucro' -- casa com "
        "/LUCRO|PREJUIZO/ e entrega lucro pre-imposto sem nenhum sinal."
    )


def test_a_descricao_de_3_09_realmente_casaria_com_o_padrao(planos):
    """Prova de que o risco era real, e não teórico."""
    import re

    from transform.sector import _sem_acento

    padrao = _conceito(planos, "INDUSTRIAL", "lucro_liquido")["padrao_ds_conta"]
    ds = _sem_acento(DRE_BBSE3_2026T2["3.09"][0])
    assert re.search(padrao, ds), (
        "se este teste falhar, o padrao mudou e o comentario do YAML precisa "
        "ser corrigido junto"
    )


# ---------------------------------------------------------------------------
# O plano de seguradora contra o arquivo
# ---------------------------------------------------------------------------
def test_lucro_liquido_de_seguradora_prefere_3_13(planos):
    """3.13 e o consolidado do periodo; 3.11 e so a parte continuada."""
    codigos = _conceito(planos, "SEGURADORA", "lucro_liquido")["codigos"]
    assert codigos[0] == "3.13", codigos


def test_o_padrao_de_lucro_casa_com_a_descricao_real_de_3_13(planos):
    import re

    from transform.sector import _sem_acento

    conceito = _conceito(planos, "SEGURADORA", "lucro_liquido")
    ds = _sem_acento(DRE_BBSE3_2026T2["3.13"][0])
    assert re.search(conceito["padrao_ds_conta"], ds)


def test_resultado_financeiro_de_seguradora_e_3_08(planos):
    """No plano industrial e 3.06 -- que aqui e equivalencia patrimonial.
    Usar 3.06 daria equivalencia rotulada como resultado financeiro."""
    import re

    from transform.sector import _sem_acento

    conceito = _conceito(planos, "SEGURADORA", "resultado_financeiro")
    assert conceito["codigos"] == ["3.08"]
    assert re.search(conceito["padrao_ds_conta"],
                     _sem_acento(DRE_BBSE3_2026T2["3.08"][0]))
    assert not re.search(conceito["padrao_ds_conta"],
                         _sem_acento(DRE_BBSE3_2026T2["3.06"][0])), (
        "o padrao casaria tambem com a conta de equivalencia patrimonial"
    )


@pytest.mark.parametrize("conceito,codigo", [
    ("ativo_total", "1"),
    ("passivo_total", "2"),
    ("passivo_circulante", "2.01"),
    ("passivo_nao_circulante", "2.02"),
    ("patrimonio_liquido", "2.03"),
    ("receita_seguros", "3.01"),
    ("equivalencia_patrimonial", "3.06"),
])
def test_codigos_do_plano_existem_na_empresa_real(planos, conceito, codigo):
    assert _conceito(planos, "SEGURADORA", conceito)["codigos"][0] == codigo


@pytest.mark.parametrize("conceito,codigo,fonte", [
    ("passivo_total", "2", BPP_BBSE3_2026T2),
    ("passivo_circulante", "2.01", BPP_BBSE3_2026T2),
    ("passivo_nao_circulante", "2.02", BPP_BBSE3_2026T2),
    ("patrimonio_liquido", "2.03", BPP_BBSE3_2026T2),
    ("receita_seguros", "3.01", DRE_BBSE3_2026T2),
    ("equivalencia_patrimonial", "3.06", DRE_BBSE3_2026T2),
])
def test_padrao_casa_com_a_descricao_real(planos, conceito, codigo, fonte):
    import re

    from transform.sector import _sem_acento

    padrao = _conceito(planos, "SEGURADORA", conceito)["padrao_ds_conta"]
    ds = _sem_acento(fonte[codigo][0])
    assert re.search(padrao, ds), f"/{padrao}/ nao casa com '{ds}'"


def test_divida_nao_se_aplica_a_seguradora(planos):
    """2.01.04 na BBSE3 e "Capitalizacao", nao emprestimo. Se o conceito
    estivesse ativo, o plano leria provisao tecnica como divida."""
    nao_aplica = {d["conceito"] for d in planos["SEGURADORA"]["nao_se_aplica"]}
    assert "divida_bruta" in nao_aplica
    assert "Capitaliza" in BPP_BBSE3_2026T2["2.01.04"][0]


def test_identidade_contabil_fecha_na_empresa_real():
    """Confere a aritmetica do balanco que o teste do pipeline aplica:
    Passivo Total (2) == PC (2.01) + PNC (2.02) + PL (2.03)."""
    soma = sum(BPP_BBSE3_2026T2[c][1] for c in ("2.01", "2.02", "2.03"))
    assert soma == pytest.approx(BPP_BBSE3_2026T2["2"][1], abs=1.0)
