"""Falha em fonte auxiliar não pode descartar o que já foi baixado.

Caso real (12/09/2026): 6 das 7 etapas concluíram — CVM completa, COTAHIST de
dez anos, 25 minutos de download. O BCB respondeu HTTP 406, `run.py extrair`
devolveu 1, e o `set -e` do comecar.sh abortou o script antes do
`transformar`. Resultado: banco vazio por causa de uma fonte acessória.

Selic, IPCA e câmbio servem a deflacionamento e custo de oportunidade. A
ausência deles tira funcionalidade; não invalida fundamento nem preço.
"""

from __future__ import annotations

from datetime import date

import pytest

import run
from etl import bcb_sgs


# ---------------------------------------------------------------------------
# Classificação essencial x auxiliar
# ---------------------------------------------------------------------------
def test_fontes_essenciais_sao_as_da_cvm():
    assert run.ESSENCIAIS == {"cadastro CVM", "FCA (de-para ticker)", "DFP", "ITR"}


@pytest.mark.parametrize("auxiliar", ["BCB/SGS", "COTAHIST", "IPE (fatos relevantes)"])
def test_fontes_auxiliares_nao_sao_essenciais(auxiliar):
    assert auxiliar not in run.ESSENCIAIS


# ---------------------------------------------------------------------------
# Janelas de requisição ao SGS
# ---------------------------------------------------------------------------
def test_janelas_cobrem_o_intervalo_sem_buraco_nem_sobreposicao():
    ini, fim = date(2020, 1, 1), date(2026, 9, 12)
    js = list(bcb_sgs.janelas(ini, fim, anos=5))

    assert js[0][0] == ini
    assert js[-1][1] == fim
    for (_, fim_a), (ini_b, _) in zip(js, js[1:]):
        assert (ini_b - fim_a).days == 1, "janelas tem que ser contiguas"


def test_janela_respeita_o_limite_de_anos():
    js = list(bcb_sgs.janelas(date(2010, 1, 1), date(2026, 9, 12), anos=5))
    for ini, fim in js:
        assert (fim - ini).days <= 5 * 366


def test_intervalo_curto_vira_uma_janela_so():
    js = list(bcb_sgs.janelas(date(2026, 1, 1), date(2026, 3, 1), anos=5))
    assert js == [(date(2026, 1, 1), date(2026, 3, 1))]


def test_intervalo_invertido_nao_gera_janela():
    assert list(bcb_sgs.janelas(date(2026, 1, 1), date(2025, 1, 1))) == []


def test_inicio_em_29_de_fevereiro_nao_quebra():
    js = list(bcb_sgs.janelas(date(2024, 2, 29), date(2030, 1, 1), anos=5))
    assert js[0][0] == date(2024, 2, 29)
    assert js[-1][1] == date(2030, 1, 1)


# ---------------------------------------------------------------------------
# Cache: uma URL, um arquivo
# ---------------------------------------------------------------------------
def test_series_diferentes_do_sgs_nao_compartilham_arquivo():
    """As URLs do BCB terminam todas em `/dados`: o que distingue esta na query."""
    from etl.http_cache import _destino

    a = _destino(bcb_sgs._url(11, date(2020, 1, 1), date(2024, 12, 31)), "bcb")
    b = _destino(bcb_sgs._url(433, date(2020, 1, 1), date(2024, 12, 31)), "bcb")
    c = _destino(bcb_sgs._url(11, date(2025, 1, 1), date(2026, 9, 12)), "bcb")

    assert a != b, "series diferentes gravariam no mesmo arquivo"
    assert a != c, "janelas diferentes gravariam no mesmo arquivo"
    assert len({a, b, c}) == 3


def test_url_sem_query_mantem_o_nome_original():
    """Nao muda o cache ja existente de CVM e B3, que nao usam query."""
    from etl.http_cache import _destino

    d = _destino("https://exemplo/dados/dfp_cia_aberta_2023.zip", "cvm/dfp")
    assert d.name == "dfp_cia_aberta_2023.zip"


def test_cabecalhos_do_bcb_existem_para_o_406():
    """O gateway do BCB recusa User-Agent incomum; sem isto volta HTTP 406."""
    assert "User-Agent" in bcb_sgs.CABECALHOS
    assert "Accept" in bcb_sgs.CABECALHOS
