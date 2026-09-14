"""Plano de banco conferido contra o BPP real do Itaú.

Fonte: `python run.py contas ITUB4` em 13/09/2026, sobre o ITR CON de 2026T2
(`itr_cia_aberta_{BPA,BPP,DRE}_con_2026.csv`, 148 contas). Cópia do arquivo,
não memória.

O que o arquivo mostrou
-----------------------
O BPP de banco **não tem** a divisão circulante / não-circulante, e o
patrimônio líquido não está em 2.03:

    2.01  Passivos Financeiros ao Valor Justo            93.333
    2.02  Outros Passivos Financeiros Designados              0
    2.03  Passivos Financeiros ao Custo Amortizado    2.409.635
    2.04  Provisoes                                      18.662
    2.05  Passivos Fiscais                               12.019
    2.06  Outros Passivos                               440.450
    2.07  Passivos sobre Ativos Nao Correntes a Venda         0
    2.08  Patrimonio Liquido Consolidado                228.026   <- o PL
                                                     -----------
                                                      3.202.125  = conta 2

Dois defeitos, dos dois tipos que este projeto separa:

1. NÚMERO ERRADO. `validations.py` lia 2.03 como patrimônio líquido. Num
   banco isso rotula R$ 2,4 trilhões de captação como patrimônio — e o número
   ia para a tabela e para a tela. O PL de verdade é R$ 228 bilhões, dez vezes
   menor.

2. DIVERGÊNCIA INVENTADA. A decomposição somava só 2.01+2.02+2.03, deixando
   de fora tudo entre 2.04 e 2.08 — 22% do ativo. As 31 falhas de identidade
   do ITUB4 eram isso: o balanço fechava, a conta é que estava incompleta.

A correção não declara a lista por setor: soma os filhos DIRETOS de 2, que
vale para os três planos porque é o próprio arquivo dizendo quais são.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tests import fixtures as fx
from transform import sector, validations

CNPJ = "60872504000123"

# Copiado da saida de `run.py contas ITUB4`, ITR CON 2026T2. Valores em reais.
BPP_ITUB4 = [
    ("2",    "Passivo Total",                                3_202_125_000_000.0),
    ("2.01", "Passivos Financeiros ao Valor Justo através...",   93_333_000_000.0),
    ("2.02", "Outros Passivos Financeiros Designados ao Va...",            0.0),
    ("2.03", "Passivos Financeiros ao Custo Amortizado",     2_409_635_000_000.0),
    ("2.04", "Provisões",                                       18_662_000_000.0),
    ("2.05", "Passivos Fiscais",                                12_019_000_000.0),
    ("2.06", "Outros Passivos",                                440_450_000_000.0),
    ("2.07", "Passivos sobre Ativos Não Correntes a Venda",              0.0),
    ("2.08", "Patrimônio Líquido Consolidado",                 228_026_000_000.0),
]
ATIVO_ITUB4 = 3_202_125_000_000.0
PL_ITUB4 = 228_026_000_000.0

DRE_ITUB4 = {
    "3.03": ("Resultado Bruto Intermediação Financeira", 37_059_000_000.0),
    "3.05": ("Resultado Antes dos Tributos sobre o Lucro", 14_274_000_000.0),
    "3.07": ("Resultado Líquido das Operações Continuadas", 12_324_000_000.0),
    "3.09": ("Lucro/Prejuízo Consolidado do Período", 12_324_000_000.0),
}


def _fatos_do_banco():
    linhas = [
        fx.fato(cnpj=CNPJ, dt_refer="2026-06-30", doc="ITR", demonstrativo="BPA",
                cd_conta="1", ds_conta="Ativo Total",
                vl=ATIVO_ITUB4 / 1000, dt_fim="2026-06-30"),
    ]
    for cd, ds, valor in BPP_ITUB4:
        linhas.append(
            fx.fato(cnpj=CNPJ, dt_refer="2026-06-30", doc="ITR",
                    demonstrativo="BPP", cd_conta=cd, ds_conta=ds,
                    vl=valor / 1000, dt_fim="2026-06-30")
        )
    from transform import pipeline

    # Caminho completo, nao so a conversao de escala: a identidade e por
    # (empresa, base, PERIODO), e periodo so existe depois das regras 2 a 5.
    return pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]


@pytest.fixture(scope="module")
def identidade():
    ident = validations.identidade_contabil(_fatos_do_banco())
    assert not ident.empty, "o cenario do banco nao produziu linha de identidade"
    return ident.iloc[0]


# ---------------------------------------------------------------------------
# O balanco do banco fecha -- a conta e que estava errada
# ---------------------------------------------------------------------------
def test_a_soma_dos_filhos_de_2_fecha_com_o_passivo_total():
    """Aritmetica pura, antes de qualquer codigo: o arquivo e consistente."""
    soma = sum(v for cd, _ds, v in BPP_ITUB4 if cd != "2")
    assert soma == pytest.approx(ATIVO_ITUB4, abs=1.0)


def test_identidade_do_banco_passa(identidade):
    assert identidade["status"] == "OK", identidade["motivo"]
    assert identidade["residuo_decomposto"] == pytest.approx(0.0, abs=1.0)


def test_a_decomposicao_antiga_erraria_por_699_bilhoes():
    """Prova de que o defeito era real e do tamanho reportado."""
    valores = dict((cd, v) for cd, _ds, v in BPP_ITUB4)
    so_tres = valores["2.01"] + valores["2.02"] + valores["2.03"]
    assert ATIVO_ITUB4 - so_tres == pytest.approx(699_157_000_000.0, abs=1.0), (
        "e exatamente o residuo que `run.py identidade` reportava no ITUB4"
    )


def test_passivo_partes_registra_o_que_foi_somado(identidade):
    """Sem isso nao da para refazer a conta a partir da tela."""
    partes = identidade["passivo_partes"]
    for cd, _ds, _v in BPP_ITUB4:
        if cd != "2":
            assert cd in partes, f"{cd} ficou de fora de passivo_partes: {partes}"


# ---------------------------------------------------------------------------
# O patrimonio liquido de um banco nao esta em 2.03
# ---------------------------------------------------------------------------
def test_patrimonio_liquido_vem_de_2_08_e_nao_de_2_03(identidade):
    assert identidade["patrimonio_liquido"] == pytest.approx(PL_ITUB4, abs=1.0), (
        "2.03 num banco e 'Passivos Financeiros ao Custo Amortizado', R$ 2,4 "
        "trilhoes de captacao -- dez vezes o patrimonio liquido de verdade"
    )


def test_circulante_fica_nulo_onde_nao_existe(identidade):
    """Banco nao tem essa divisao. Nulo e honesto; preencher com a conta que
    caiu no codigo seria inventar."""
    assert pd.isna(identidade["passivo_circulante"])
    assert pd.isna(identidade["passivo_nao_circulante"])


# ---------------------------------------------------------------------------
# O plano declarado
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def plano():
    return sector.carregar_planos()["planos"]["FINANCEIRO"]["contas"]


def test_o_plano_aponta_2_08_para_patrimonio_liquido(plano):
    assert plano["patrimonio_liquido"]["codigos"] == ["2.08"]


def test_lucro_liquido_do_banco_e_3_09(plano):
    """O MESMO codigo significa coisas diferentes em cada plano, e e por isso
    que a regra 6 existe:

        banco       3.09  Lucro/Prejuizo Consolidado do Periodo
        seguradora  3.09  Resultado Antes dos Tributos sobre o Lucro
        industrial  3.11  Lucro/Prejuizo Consolidado do Periodo
    """
    import re

    from transform.sector import _sem_acento

    conceito = plano["lucro_liquido"]
    assert conceito["codigos"] == ["3.09"]
    assert re.search(conceito["padrao_ds_conta"], _sem_acento(DRE_ITUB4["3.09"][0]))
    # A defesa se a CVM renumerar: 3.07 nao e o consolidado do periodo.
    assert not re.search(conceito["padrao_ds_conta"],
                         _sem_acento(DRE_ITUB4["3.07"][0]))


def test_planos_nao_repetem_o_mesmo_codigo_para_lucro_liquido():
    """3.09 so pode estar no plano de banco. Nos outros dois ele e resultado
    antes de imposto, e aceita-lo ali entregaria lucro pre-imposto."""
    planos = sector.carregar_planos()["planos"]
    for nome in ("INDUSTRIAL", "SEGURADORA"):
        codigos = planos[nome]["contas"]["lucro_liquido"]["codigos"]
        assert "3.09" not in codigos, f"{nome} voltou a aceitar 3.09"


def test_o_arquivo_de_planos_nao_tem_chave_duplicada():
    """YAML com chave repetida faz a ultima vencer EM SILENCIO. Ja aconteceu
    ao declarar este plano: `passivo_total` ficou duas vezes no bloco."""
    import collections

    import yaml

    from transform.sector import ARQUIVO_PLANOS

    class Estrito(yaml.SafeLoader):
        pass

    def _sem_repetida(loader, node, deep=False):
        chaves = collections.Counter(
            loader.construct_object(k) for k, _ in node.value
        )
        repetidas = [k for k, n in chaves.items() if n > 1]
        assert not repetidas, f"chave duplicada no YAML: {repetidas}"
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Estrito.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _sem_repetida
    )
    yaml.load(ARQUIVO_PLANOS.read_text(encoding="utf-8"), Loader=Estrito)
