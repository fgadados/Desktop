"""Regra 4: consolidado e individual convivem no mesmo arquivo.

Politica do projeto: uma base fixa por empresa, escolhida por cobertura, com
empate em favor do consolidado. Periodo sem a base escolhida vira ausencia --
nao ha fallback periodo a periodo, porque alternar base dentro da serie e
exatamente o que quebra a comparabilidade de percentil de 10 anos.
"""

from __future__ import annotations

import pandas as pd

from transform import basis


def _fatos(registros: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    """(cnpj, base, periodo, valor)"""
    return pd.DataFrame(
        [{"CNPJ_CIA": c, "base": b, "periodo": p, "VL_CONTA_NUM": v}
         for c, b, p, v in registros]
    )


def test_consolidado_vence_quando_tem_mais_cobertura():
    df = _fatos([
        ("A", "CON", "2021", 1), ("A", "CON", "2022", 2), ("A", "CON", "2023", 3),
        ("A", "IND", "2023", 30),
    ])
    rel = basis.escolher_base(df)
    assert rel.loc[0, "base_escolhida"] == "CON"
    assert rel.loc[0, "cobertura_con"] == 3
    assert rel.loc[0, "cobertura_ind"] == 1


def test_individual_vence_quando_nao_ha_consolidado():
    """Empresa sem controlada nao publica consolidado. IND e a unica base."""
    df = _fatos([("B", "IND", "2022", 1), ("B", "IND", "2023", 2)])
    rel = basis.escolher_base(df)
    assert rel.loc[0, "base_escolhida"] == "IND"
    assert "consolidado inexistente" in rel.loc[0, "criterio"]


def test_empate_vai_para_consolidado_e_o_criterio_fica_explicito():
    df = _fatos([
        ("C", "CON", "2022", 1), ("C", "CON", "2023", 2),
        ("C", "IND", "2022", 10), ("C", "IND", "2023", 20),
    ])
    rel = basis.escolher_base(df)
    assert rel.loc[0, "base_escolhida"] == "CON"
    assert "empate" in rel.loc[0, "criterio"]


def test_periodo_sem_a_base_escolhida_vira_ausencia_nao_fallback():
    """O ano so com IND some da serie quando a base fixada e CON."""
    df = _fatos([
        ("D", "CON", "2021", 1), ("D", "CON", "2022", 2), ("D", "CON", "2023", 3),
        ("D", "IND", "2020", 99),
    ])
    fatos, rel = basis.aplicar(df)
    assert set(fatos["periodo"]) == {"2021", "2022", "2023"}
    assert 99 not in set(fatos["VL_CONTA_NUM"])
    assert int(rel.loc[0, "periodos_perdidos"]) == 1


def test_a_base_usada_e_o_criterio_acompanham_cada_fato():
    df = _fatos([("E", "CON", "2023", 1)])
    fatos, _ = basis.aplicar(df)
    assert fatos.loc[0, "base_escolhida"] == "CON"
    assert isinstance(fatos.loc[0, "criterio_base"], str)


def test_empresas_diferentes_podem_ter_bases_diferentes():
    df = _fatos([
        ("F", "CON", "2023", 1),
        ("G", "IND", "2023", 2),
    ])
    fatos, rel = basis.aplicar(df)
    escolhas = dict(zip(rel["CNPJ_CIA"], rel["base_escolhida"]))
    assert escolhas == {"F": "CON", "G": "IND"}
    assert len(fatos) == 2


def test_soma_nao_mistura_as_duas_bases():
    """Sem a regra, somar CON + IND dobraria o resultado da mesma empresa."""
    df = _fatos([("H", "CON", "2023", 100.0), ("H", "IND", "2023", 95.0)])
    fatos, _ = basis.aplicar(df)
    assert fatos["VL_CONTA_NUM"].sum() == 100.0
