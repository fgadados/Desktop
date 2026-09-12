"""A janela de anos é RELATIVA ao ano corrente, sempre.

Bug real: `config/tickers.yml` trazia a lista fixa [2015..2024]. Em setembro
de 2026 o sistema baixava até 2024 e ignorava 2025 e 2026 — os anos mais
relevantes — sem emitir aviso nenhum. Lista de anos escrita à mão envelhece
em silêncio, e silêncio é o modo de falha que este projeto tenta eliminar.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml

import run
from etl.config import CONFIG_DIR


def test_janela_e_o_ano_corrente_mais_n_para_tras():
    cfg = {"anos_para_tras_cvm": 6}
    anos = run.anos_da_config(cfg, "cvm", hoje=date(2026, 9, 12))
    assert anos == [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    assert len(anos) == 7  # 6 para tras + o corrente


def test_a_janela_acompanha_a_virada_do_ano():
    """O ponto do bug: nada de lista que envelhece."""
    cfg = {"anos_para_tras_cvm": 6}
    assert run.anos_da_config(cfg, "cvm", hoje=date(2026, 12, 31))[-1] == 2026
    assert run.anos_da_config(cfg, "cvm", hoje=date(2027, 1, 1)) == [
        2021, 2022, 2023, 2024, 2025, 2026, 2027
    ]


def test_zero_para_tras_traz_so_o_ano_corrente():
    cfg = {"anos_para_tras_cvm": 0}
    assert run.anos_da_config(cfg, "cvm", hoje=date(2026, 9, 12)) == [2026]


def test_lista_explicita_vence_o_calculo():
    """Para reproduzir uma analise antiga com janela fechada."""
    cfg = {"anos_para_tras_cvm": 6, "anos_cvm": [2023, 2024]}
    assert run.anos_da_config(cfg, "cvm", hoje=date(2026, 9, 12)) == [2023, 2024]


def test_fontes_tem_janelas_independentes():
    cfg = {"anos_para_tras_cvm": 6, "anos_para_tras_cotahist": 2}
    hoje = date(2026, 9, 12)
    assert len(run.anos_da_config(cfg, "cvm", hoje=hoje)) == 7
    assert run.anos_da_config(cfg, "cotahist", hoje=hoje) == [2024, 2025, 2026]


def test_valor_negativo_levanta():
    with pytest.raises(ValueError, match="nao pode ser negativo"):
        run.anos_da_config({"anos_para_tras_cvm": -1}, "cvm", hoje=date(2026, 9, 12))


def test_config_do_projeto_nao_tem_lista_fixa_de_anos():
    """A guarda contra a recaida: nada de lista de anos ativa no YAML."""
    cfg = yaml.safe_load((CONFIG_DIR / "tickers.yml").read_text(encoding="utf-8"))
    assert "anos_cvm" not in cfg, (
        "lista fixa de anos no config: ela envelhece e o sistema passa a "
        "ignorar os anos recentes em silencio"
    )
    assert "anos_cotahist" not in cfg
    assert "anos_para_tras_cvm" in cfg
    assert "anos_para_tras_cotahist" in cfg


def test_config_do_projeto_alcanca_o_ano_corrente():
    cfg = yaml.safe_load((CONFIG_DIR / "tickers.yml").read_text(encoding="utf-8"))
    for fonte in ("cvm", "cotahist"):
        anos = run.anos_da_config(cfg, fonte)
        assert anos[-1] == date.today().year, f"{fonte} nao chega ao ano corrente"
        assert len(anos) == 7, f"{fonte}: esperado 7 anos (corrente + 6)"
