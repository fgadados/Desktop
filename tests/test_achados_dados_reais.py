"""Testes nascidos do confronto com o arquivo REAL da CVM (12/09/2026).

Cada caso aqui existe porque o layout publicado divergia do arquivo entregue.
São regressões: se alguém "arrumar" o código de volta para o que o documento
dizia, estes testes quebram.

Origem: `pytest -m live tests/contract` rodado contra DFP e ITR de 2023.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from etl import cvm_demonstracoes as cd
from tests import fixtures as fx
from transform import money


# ---------------------------------------------------------------------------
# Achado 1: a CVM grava ESCALA_MOEDA = "MIL", nao "MILHAR"
# ---------------------------------------------------------------------------
def test_escala_mil_e_a_forma_real_da_cvm():
    """O layout dizia MILHAR; o arquivo traz MIL. Sem isso, o ETL para."""
    linhas = [
        fx.fato(dt_refer="2023-12-31", cd_conta="3.11", vl=100.0, escala="MIL",
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = money.converter(fx.quadro(linhas))
    assert out.loc[0, "VL_CONTA_NUM"] == pytest.approx(100_000.0)
    assert out.loc[0, "escala_fator"] == 1000.0


def test_mil_e_milhar_sao_equivalentes():
    linhas = [
        fx.fato(dt_refer="2023-12-31", cd_conta="3.11", vl=7.0, escala="MIL",
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
        fx.fato(dt_refer="2023-12-31", cd_conta="3.01", vl=7.0, escala="MILHAR",
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    out = money.converter(fx.quadro(linhas))
    assert out["VL_CONTA_NUM"].nunique() == 1


def test_escala_desconhecida_continua_levantando():
    """A tolerancia e so para MIL. Escala nova ainda tem que parar o pipeline."""
    linhas = [
        fx.fato(dt_refer="2023-12-31", cd_conta="3.11", vl=1.0, escala="BILHAO",
                dt_ini="2023-01-01", dt_fim="2023-12-31"),
    ]
    with pytest.raises(ValueError, match="ESCALA_MOEDA desconhecida"):
        money.converter(fx.quadro(linhas))


# ---------------------------------------------------------------------------
# Achado 2: o pacote traz mais arquivos do que os demonstrativos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "nome,tipo_esperado",
    [
        ("dfp_cia_aberta_BPA_con_2023.csv", cd.TIPO_DEMONSTRATIVO),
        ("itr_cia_aberta_DRE_ind_2023.csv", cd.TIPO_DEMONSTRATIVO),
        ("dfp_cia_aberta_2023.csv", cd.TIPO_CABECALHO),
        ("dfp_cia_aberta_composicao_capital_2023.csv", cd.TIPO_COMPOSICAO_CAPITAL),
        ("itr_cia_aberta_composicao_capital_2023.csv", cd.TIPO_COMPOSICAO_CAPITAL),
        ("dfp_cia_aberta_parecer_2023.csv", cd.TIPO_PARECER),
        ("itr_cia_aberta_parecer_2023.csv", cd.TIPO_PARECER),
    ],
)
def test_reconhece_todos_os_arquivos_do_pacote_real(nome, tipo_esperado):
    meta = cd._decompor(nome)
    assert meta is not None, f"'{nome}' nao foi reconhecido"
    assert meta["tipo"] == tipo_esperado


def test_arquivo_desconhecido_continua_devolvendo_none():
    """A tolerancia e so para os tipos ja vistos. Nome novo ainda para o ETL."""
    assert cd._decompor("dfp_cia_aberta_coisa_nova_2023.csv") is None


# ---------------------------------------------------------------------------
# Roteamento dentro do ZIP
# ---------------------------------------------------------------------------
def _csv(colunas: list[str], linhas: list[list[str]]) -> bytes:
    buf = io.StringIO()
    buf.write(";".join(colunas) + "\n")
    for l in linhas:
        buf.write(";".join(l) + "\n")
    return buf.getvalue().encode("latin-1")


@pytest.fixture
def pacote(tmp_path):
    """ZIP com os quatro tipos de arquivo, como a CVM entrega."""
    ident = ["CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM"]
    z = tmp_path / "dfp_cia_aberta_2023.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(
            "dfp_cia_aberta_DRE_con_2023.csv",
            _csv(
                ["CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP",
                 "MOEDA", "ESCALA_MOEDA", "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC",
                 "CD_CONTA", "DS_CONTA", "VL_CONTA", "ST_CONTA_FIXA"],
                [["00.000.000/0001-91", "2023-12-31", "1", "TESTE", "1", "DF Consolidado",
                  "REAL", "MIL", "ÚLTIMO", "2023-01-01", "2023-12-31", "3.11",
                  "Lucro", "100.0", "S"]],
            ),
        )
        zf.writestr(
            "dfp_cia_aberta_2023.csv",
            _csv(ident + ["CATEG_DOC", "ID_DOC", "DT_RECEB", "LINK_DOC"],
                 [["00.000.000/0001-91", "2023-12-31", "1", "TESTE", "1", "DFP", "9",
                   "2024-03-01", "http://x"]]),
        )
        zf.writestr(
            "dfp_cia_aberta_composicao_capital_2023.csv",
            _csv(ident + ["QTD_ACAO_ORDINARIA_CAPITAL_INTEGRALIZADO"],
                 [["00.000.000/0001-91", "2023-12-31", "1", "TESTE", "1", "1000000"]]),
        )
        # Parecer: texto com quebra de linha dentro de campo, de proposito.
        zf.writestr(
            "dfp_cia_aberta_parecer_2023.csv",
            b'CNPJ_CIA;DT_REFER;TEXTO\n00.000.000/0001-91;2023-12-31;"linha um\nlinha dois"\n',
        )
    return z


def test_zip_real_e_roteado_por_tipo(pacote, monkeypatch):
    from etl import http_cache, provenance

    fonte = provenance.Fonte(url="x", path=str(pacote), sha256="0" * 64, bytes=1,
                             baixado_em="2026-09-12T00:00:00+00:00")
    monkeypatch.setattr(http_cache, "baixar", lambda *a, **k: fonte)

    r = cd.extrair_ano("DFP", "http://exemplo/dfp_cia_aberta_2023.zip")

    assert len(r["fatos"]) == 1
    assert r["fatos"].iloc[0]["CD_CONTA"] == "3.11"
    assert len(r["cabecalho"]) == 1
    assert len(r["composicao_capital"]) == 1
    assert "QTD_ACAO_ORDINARIA_CAPITAL_INTEGRALIZADO" in r["composicao_capital"].columns


def test_parecer_nao_entra_nos_fatos(pacote, monkeypatch):
    """Texto com quebra de linha quebraria a garantia de src_line."""
    from etl import http_cache, provenance

    fonte = provenance.Fonte(url="x", path=str(pacote), sha256="0" * 64, bytes=1,
                             baixado_em="2026-09-12T00:00:00+00:00")
    monkeypatch.setattr(http_cache, "baixar", lambda *a, **k: fonte)

    r = cd.extrair_ano("DFP", "http://exemplo/dfp_cia_aberta_2023.zip")
    assert not r["fatos"]["src_file"].str.contains("parecer").any()


def test_composicao_capital_preserva_linhagem(pacote, monkeypatch):
    from etl import http_cache, provenance

    fonte = provenance.Fonte(url="x", path=str(pacote), sha256="0" * 64, bytes=1,
                             baixado_em="2026-09-12T00:00:00+00:00")
    monkeypatch.setattr(http_cache, "baixar", lambda *a, **k: fonte)

    cap = cd.extrair_ano("DFP", "http://exemplo/dfp_cia_aberta_2023.zip")["composicao_capital"]
    assert cap.iloc[0]["src_file"] == "dfp_cia_aberta_composicao_capital_2023.csv"
    assert int(cap.iloc[0]["src_line"]) == 2
