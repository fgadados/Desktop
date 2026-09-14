"""O mesmo exercício chega por mais de um documento, e isso quebrava o Q4.

Caso real (12/09/2026), `07859971000130 CON DFC_MI 6.01.01.03 2020` duplicado:

    A DFP de 2020 traz o exercício 2020 como ORDEM_EXERC = ÚLTIMO.
    A DFP de 2021 traz o MESMO exercício 2020 como PENÚLTIMO, comparativo.

A derivação do Q4 roda antes da resolução de reapresentação, então os dois
convivem e a chave deixa de identificar uma linha só. O merge vira N-para-N.

É a segunda vez que a mesma classe de erro aparece — a primeira foi a
`COLUNA_DF` da DMPL. Por isso, além da correção, o erro passa a dizer qual
dimensão está faltando em vez de despejar o `MergeError` cru do pandas.

A regra adotada é a mesma política de reapresentação escolhida para o
projeto: vence a publicação mais recente.
"""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import periods, pipeline


def _exercicio(ano: int, doc: str, dt_refer: str, ordem: str, valor: float,
               dt_ini: str, dt_fim: str, conta: str = "6.01.01.03"):
    return fx.fato(
        dt_refer=dt_refer, doc=doc, demonstrativo="DFC_MI", ordem=ordem,
        cd_conta=conta, ds_conta="Depreciação e Amortização", vl=valor,
        dt_ini=dt_ini, dt_fim=dt_fim,
    )


def _cenario_dois_documentos(reapresentado: float = 92.0):
    """Exercicio 2020 publicado duas vezes, com valores diferentes."""
    return [
        # DFP de 2020: o exercicio proprio.
        _exercicio(2020, "DFP", "2020-12-31", "ÚLTIMO", 100.0,
                   "2020-01-01", "2020-12-31"),
        # DFP de 2021: o mesmo exercicio 2020, como comparativo reapresentado.
        _exercicio(2020, "DFP", "2021-12-31", "PENÚLTIMO", reapresentado,
                   "2020-01-01", "2020-12-31"),
        # ITR acumulado de 9 meses de 2020, tambem em dois documentos.
        _exercicio(2020, "ITR", "2020-09-30", "ÚLTIMO", 70.0,
                   "2020-01-01", "2020-09-30"),
        _exercicio(2020, "ITR", "2021-09-30", "PENÚLTIMO", 64.0,
                   "2020-01-01", "2020-09-30"),
    ]


def test_q4_nao_explode_com_exercicio_publicado_duas_vezes():
    """Era o MergeError: N linhas casando com N linhas."""
    out = pipeline.normalizar_fatos(fx.quadro(_cenario_dois_documentos()))["fatos"]
    q4 = out[out["origem_periodo"] == "DERIVADO_Q4"]
    assert len(q4) == 1, "esperada uma unica linha de Q4 para o exercicio"


def test_q4_usa_a_publicacao_mais_recente_dos_dois_lados():
    """Politica do projeto: restated. 92 - 64 = 28, nao 100 - 70 = 30."""
    out = pipeline.normalizar_fatos(fx.quadro(_cenario_dois_documentos()))["fatos"]
    q4 = out[out["origem_periodo"] == "DERIVADO_Q4"].iloc[0]
    assert q4["VL_CONTA_NUM"] == pytest.approx(28_000.0)


def test_sem_reapresentacao_o_resultado_e_o_mesmo():
    """Quando as duas publicacoes concordam, escolher nao muda nada."""
    linhas = [
        _exercicio(2020, "DFP", "2020-12-31", "ÚLTIMO", 100.0, "2020-01-01", "2020-12-31"),
        _exercicio(2020, "DFP", "2021-12-31", "PENÚLTIMO", 100.0, "2020-01-01", "2020-12-31"),
        _exercicio(2020, "ITR", "2020-09-30", "ÚLTIMO", 70.0, "2020-01-01", "2020-09-30"),
    ]
    out = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    q4 = out[out["origem_periodo"] == "DERIVADO_Q4"].iloc[0]
    assert q4["VL_CONTA_NUM"] == pytest.approx(30_000.0)


def test_erro_de_chave_duplicada_diz_qual_dimensao_falta():
    """O MergeError do pandas nao dizia; esta mensagem diz."""
    import pandas as pd

    df = pd.DataFrame([
        {"CNPJ_CIA": "07859971000130", "base": "CON", "demonstrativo": "DFC_MI",
         "CD_CONTA": "6.01.01.03", "ano_exercicio": 2020, "DT_REFER": "2020-12-31",
         "ORDEM_EXERC": "ÚLTIMO"},
        {"CNPJ_CIA": "07859971000130", "base": "CON", "demonstrativo": "DFC_MI",
         "CD_CONTA": "6.01.01.03", "ano_exercicio": 2020, "DT_REFER": "2021-12-31",
         "ORDEM_EXERC": "PENÚLTIMO"},
    ])
    chave = ["CNPJ_CIA", "base", "demonstrativo", "CD_CONTA", "ano_exercicio"]
    with pytest.raises(periods.ChaveDuplicadaError) as exc:
        periods._exigir_chave_unica(df, chave, "DFP anual")

    mensagem = str(exc.value)
    assert "DT_REFER" in mensagem, "a mensagem precisa mostrar o que distingue as linhas"
    assert "ORDEM_EXERC" in mensagem
    assert "6.01.01.03" in mensagem


def test_conferencia_de_trimestres_nao_conta_duas_vezes():
    """Trimestre publicado em dois documentos inflaria a soma e acusaria
    divergencia inexistente."""
    linhas = []
    for tri, (ini, fim, valor) in enumerate(
        [("2020-01-01", "2020-03-31", 20.0),
         ("2020-04-01", "2020-06-30", 25.0),
         ("2020-07-01", "2020-09-30", 30.0)], start=1
    ):
        # Cada trimestre isolado publicado duas vezes, com o mesmo valor.
        linhas.append(_exercicio(2020, "ITR", fim, "ÚLTIMO", valor, ini, fim))
        linhas.append(_exercicio(2021, "ITR", fim.replace("2020", "2021"),
                                 "PENÚLTIMO", valor, ini, fim))
    linhas.append(_exercicio(2020, "ITR", "2020-09-30", "ÚLTIMO", 75.0,
                             "2020-01-01", "2020-09-30"))

    from transform import dedup, money

    df = money.converter(pipeline.garantir_coluna_df(fx.quadro(linhas)))
    cls = periods.indice_trimestre(periods.classificar_janela(
        dedup.filtrar_ordem_exerc(dedup.versao_maxima(df), manter=None)
    ))
    div = periods.conferir_soma_trimestres(cls)
    assert div.empty, (
        "20+25+30 = 75 fecha com o acumulado; a duplicidade de publicacao "
        f"nao pode inventar divergencia:\n{div}"
    )
