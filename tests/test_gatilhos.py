"""Gatilhos: descrevem condicao satisfeita, nunca recomendam acao."""

from __future__ import annotations

import pytest

from transform import triggers


GATILHO = {
    "nome": "pl_baixo",
    "descricao": "P/L no decil inferior de 10 anos",
    "escopo": "TICKER",
    "todas": [
        {"metrica": "pl_pct_10a", "operador": "lt", "valor": 0.10},
        {"metrica": "pl_n_obs_10a", "operador": "ge", "valor": 1250},
    ],
}


def test_dispara_e_mostra_os_numeros_que_satisfizeram():
    r = triggers.avaliar(GATILHO, "TEST3", {"pl_pct_10a": 0.04, "pl_n_obs_10a": 2400})
    assert r.status == triggers.DISPAROU
    assert "pl_pct_10a = 0.04 < 0.1" in r.explicacao


def test_nao_dispara_quando_a_condicao_falha():
    r = triggers.avaliar(GATILHO, "TEST3", {"pl_pct_10a": 0.55, "pl_n_obs_10a": 2400})
    assert r.status == triggers.NAO_DISPAROU
    assert "nao satisfaz" in r.explicacao


def test_metrica_ausente_vira_indeterminado_com_motivo_nao_falso():
    r = triggers.avaliar(GATILHO, "TEST3", {"pl_n_obs_10a": 2400})
    assert r.status == triggers.INDETERMINADO
    assert "nao calculada" in r.explicacao


def test_metrica_com_status_faltando_nao_dispara():
    metricas = {
        "pl_pct_10a": {"valor": None, "status": "FALTANDO", "motivo": "sem lucro no periodo"},
        "pl_n_obs_10a": 2400,
    }
    r = triggers.avaliar(GATILHO, "TEST3", metricas)
    assert r.status == triggers.INDETERMINADO
    assert "sem lucro no periodo" in r.explicacao


def test_explicacao_nao_contem_verbo_de_recomendacao():
    r = triggers.avaliar(GATILHO, "TEST3", {"pl_pct_10a": 0.04, "pl_n_obs_10a": 2400})
    proibidos = ["compr", "vend", "recomend", "alvo", "aloc", "oportunidade"]
    assert not any(p in r.explicacao.lower() for p in proibidos)


def test_qualquer_basta_uma_condicao():
    g = {
        "nome": "dd", "descricao": "drawdown", "escopo": "TICKER",
        "qualquer": [
            {"metrica": "drawdown_atual", "operador": "le", "valor": -0.40},
            {"metrica": "vol", "operador": "gt", "valor": 1.0},
        ],
    }
    assert triggers.avaliar(g, "T", {"drawdown_atual": -0.5, "vol": 0.2}).status == triggers.DISPAROU
    assert triggers.avaliar(g, "T", {"drawdown_atual": -0.1, "vol": 0.2}).status == triggers.NAO_DISPAROU


def test_operador_entre():
    g = {"nome": "faixa", "descricao": "x", "escopo": "TICKER",
         "todas": [{"metrica": "pvp", "operador": "entre", "valor": [0.5, 1.5]}]}
    assert triggers.avaliar(g, "T", {"pvp": 1.0}).status == triggers.DISPAROU
    assert triggers.avaliar(g, "T", {"pvp": 2.0}).status == triggers.NAO_DISPAROU


def test_comparacao_entre_duas_metricas():
    g = {"nome": "cmp", "descricao": "x", "escopo": "TICKER",
         "todas": [{"metrica": "pl", "operador": "lt", "valor": "pl_medio_10a"}]}
    assert triggers.avaliar(g, "T", {"pl": 8.0, "pl_medio_10a": 12.0}).status == triggers.DISPAROU


def test_operador_desconhecido_e_erro_de_configuracao(tmp_path):
    arq = tmp_path / "t.yml"
    arq.write_text(
        "gatilhos:\n  - nome: x\n    descricao: y\n    escopo: TICKER\n"
        "    todas:\n      - {metrica: a, operador: aproximadamente, valor: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(triggers.ConfiguracaoInvalida, match="operador"):
        triggers.carregar(arq)


def test_arquivo_do_projeto_e_valido():
    gatilhos = triggers.carregar()
    assert len(gatilhos) >= 1
    assert all(g["escopo"] in {"TICKER", "EMPRESA"} for g in gatilhos)
