"""Balanço montado de publicações com datas diferentes fica marcado.

O problema
----------
A política de reapresentação do projeto é *restated*: cada fato usa a
publicação mais recente dele. A resolução é por CONTA, não por documento.

Se a companhia reapresenta o patrimônio líquido (2.03) na DFP do ano seguinte
e não republica o Passivo Total (2), o sistema pega PL de um documento e
Passivo Total de outro. A decomposição `2 == 2.01 + 2.02 + 2.03` deixa de
fechar **por construção** — não é erro de leitura.

A decisão (13/09/2026, do usuário)
----------------------------------
Manter a resolução conta a conta e sinalizar. A alternativa era montar cada
balanço de uma publicação só, o que faria a identidade sempre fechar ao custo
de ignorar reapresentação parcial recente.

O que isso exige do teste: que a marca distinga os dois casos. Resíduo com
montagem MISTA já tem explicação; resíduo com montagem COERENTE não tem
nenhuma, e é o que merece investigação. Se a marca não separasse os dois, a
contagem de falhas continuaria tão pouco informativa quanto o "FALHA: 37" que
motivou tudo isto.
"""

from __future__ import annotations

import pytest

from tests import fixtures as fx
from transform import pipeline, validations


def _balanco(ano: int, dt_refer: str, ordem: str, *, pl: float,
             pc: float = 300.0, pnc: float = 300.0, total: float | None = None):
    """Balanço completo publicado num documento específico."""
    fim = f"{ano}-12-31"
    total = pc + pnc + pl if total is None else total
    return [
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="BPA", ordem=ordem,
                cd_conta="1", ds_conta="Ativo Total", vl=total, dt_fim=fim),
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="BPP", ordem=ordem,
                cd_conta="2", ds_conta="Passivo Total", vl=total, dt_fim=fim),
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="BPP", ordem=ordem,
                cd_conta="2.01", ds_conta="Passivo Circulante", vl=pc, dt_fim=fim),
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="BPP", ordem=ordem,
                cd_conta="2.02", ds_conta="Passivo Não Circulante", vl=pnc, dt_fim=fim),
        fx.fato(dt_refer=dt_refer, doc="DFP", demonstrativo="BPP", ordem=ordem,
                cd_conta="2.03", ds_conta="Patrimônio Líquido Consolidado",
                vl=pl, dt_fim=fim),
    ]


def _identidade(linhas, periodo: str = "2022"):
    fatos = pipeline.normalizar_fatos(fx.quadro(linhas))["fatos"]
    ident = validations.identidade_contabil(fatos)
    linha = ident[ident["periodo"] == periodo]
    assert len(linha) == 1, f"esperada uma linha para {periodo}:\n{ident}"
    return linha.iloc[0]


def test_balanco_de_um_documento_so_e_coerente():
    r = _identidade(_balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0))
    assert r["montagem"] == validations.COERENTE
    assert r["status"] == "OK"


def test_reapresentacao_parcial_marca_montagem_mista():
    """A DFP de 2023 republica SO o PL de 2022. O balanco passa a vir de dois
    documentos e a decomposicao nao fecha."""
    linhas = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0)
    linhas.append(
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="BPP",
                ordem="PENÚLTIMO", cd_conta="2.03",
                ds_conta="Patrimônio Líquido Consolidado", vl=372.0,
                dt_fim="2022-12-31")
    )
    r = _identidade(linhas)

    assert r["montagem"] == validations.MISTA
    assert r["status"] == "FALHA", "8 mil de residuo com tolerancia de R$ 1,00"
    # 980 publicado em 2 contra 300 + 300 + 372 restated.
    assert r["residuo_decomposto"] == pytest.approx(8_000.0)


def test_documentos_mostra_de_onde_veio_cada_conta():
    linhas = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0)
    linhas.append(
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="BPP",
                ordem="PENÚLTIMO", cd_conta="2.03",
                ds_conta="Patrimônio Líquido Consolidado", vl=372.0,
                dt_fim="2022-12-31")
    )
    doc = _identidade(linhas)["documentos"]

    assert "pl@2023-12-31" in doc, doc
    assert "passivo@2022-12-31" in doc, doc
    assert "ativo@2022-12-31" in doc, doc


def test_o_motivo_distingue_explicada_de_inexplicada():
    """É a diferença que importa na tela."""
    mista = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0)
    mista.append(
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="BPP",
                ordem="PENÚLTIMO", cd_conta="2.03",
                ds_conta="Patrimônio Líquido Consolidado", vl=372.0,
                dt_fim="2022-12-31")
    )
    assert "datas diferentes" in _identidade(mista)["motivo"]

    # Mesmo documento, mas o total nao bate com as partes: nada explica.
    coerente = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0, total=999.0)
    r = _identidade(coerente)
    assert r["montagem"] == validations.COERENTE
    assert r["status"] == "FALHA"
    assert "MESMO documento" in r["motivo"]
    assert "nao explica" in r["motivo"]


def test_reapresentacao_completa_continua_fechando():
    """Reapresentar o balanco INTEIRO nao gera montagem mista: todas as contas
    passam a vir do documento novo."""
    linhas = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0)
    linhas += _balanco(2022, "2023-12-31", "PENÚLTIMO", pl=372.0)
    r = _identidade(linhas)

    assert r["montagem"] == validations.COERENTE
    assert r["status"] == "OK"
    assert r["patrimonio_liquido"] == pytest.approx(372_000.0), "vence o restated"


def test_a_marca_nao_muda_o_valor_de_nenhum_numero():
    """Sinalizar, nunca corrigir: a decisao foi manter a resolucao conta a
    conta, entao os valores tem que ser os mesmos de antes da marca."""
    linhas = _balanco(2022, "2022-12-31", "ÚLTIMO", pl=380.0)
    linhas.append(
        fx.fato(dt_refer="2023-12-31", doc="DFP", demonstrativo="BPP",
                ordem="PENÚLTIMO", cd_conta="2.03",
                ds_conta="Patrimônio Líquido Consolidado", vl=372.0,
                dt_fim="2022-12-31")
    )
    r = _identidade(linhas)

    assert r["passivo_total"] == pytest.approx(980_000.0), "o publicado em 2"
    assert r["patrimonio_liquido"] == pytest.approx(372_000.0), "o restated"
