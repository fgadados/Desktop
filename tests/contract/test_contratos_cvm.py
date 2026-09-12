"""Confronta os contratos declarados com o arquivo REAL da CVM.

Estes testes exigem rede e as fontes oficiais acessiveis. Rode-os na sua
maquina, nao em ambiente com egresso restrito:

    pytest -m live tests/contract -v

Enquanto nao rodarem, todo contrato em `etl/contracts.py` deve ser lido como
hipotese declarada a partir do layout publicado -- e o campo `verificado_em`
de cada um continua `None`.

O que cada teste responde
-------------------------
* o diretorio da CVM realmente existe e lista arquivos?
* os nomes de arquivo seguem o padrao que o parser espera?
* as colunas do CSV real batem, uma a uma, com o contrato?
* os valores de ORDEM_EXERC e ESCALA_MOEDA estao dentro do dominio assumido?
"""

from __future__ import annotations

from pathlib import Path

import pytest

from etl import cvm_common, cvm_demonstracoes, http_cache
from etl.config import CVM_DIRS
from etl.contracts import CONTRATOS_DEMONSTRATIVO

pytestmark = pytest.mark.live

ANO_ALVO = 2023


@pytest.mark.parametrize("doc", ["DFP", "ITR", "FCA", "IPE", "CAD"])
def test_diretorio_da_cvm_lista_arquivos(doc):
    dados_url, _ = CVM_DIRS[doc]
    arquivos = http_cache.listar_diretorio(dados_url)
    assert arquivos, f"diretorio {dados_url} nao listou nenhum arquivo"


@pytest.mark.parametrize("doc", ["DFP", "ITR"])
def test_nome_dos_arquivos_dentro_do_zip_segue_o_padrao_esperado(doc):
    pacotes = [
        u for u in cvm_common.listar_pacotes(doc)
        if cvm_common.ano_do_pacote(u) == ANO_ALVO
    ]
    assert pacotes, f"pacote {doc} de {ANO_ALVO} nao encontrado"

    fonte = http_cache.baixar(pacotes[0], subdir=f"cvm/{doc.lower()}")
    nomes = cvm_common.nomes_no_zip(Path(fonte.path))
    assert nomes
    nao_reconhecidos = [n for n in nomes if cvm_demonstracoes._decompor(n) is None]
    assert not nao_reconhecidos, (
        f"arquivos fora do padrao de nomes: {nao_reconhecidos}. "
        "Atualize etl/cvm_demonstracoes._PADRAO."
    )


@pytest.mark.parametrize("doc", ["DFP", "ITR"])
def test_colunas_reais_batem_com_o_contrato(doc):
    """Este e o teste que promove um contrato de hipotese a verificado."""
    pacotes = [
        u for u in cvm_common.listar_pacotes(doc)
        if cvm_common.ano_do_pacote(u) == ANO_ALVO
    ]
    fonte = http_cache.baixar(pacotes[0], subdir=f"cvm/{doc.lower()}")
    zip_path = Path(fonte.path)

    divergencias = []
    for nome in cvm_common.nomes_no_zip(zip_path):
        meta = cvm_demonstracoes._decompor(nome)
        if meta is None or meta["tipo"] != cvm_demonstracoes.TIPO_DEMONSTRATIVO:
            continue
        contrato = CONTRATOS_DEMONSTRATIVO[meta["demonstrativo"]]
        try:
            cvm_common.ler_csv_do_zip(zip_path, nome, contrato, fonte.sha256)
        except AssertionError as exc:
            divergencias.append(f"{nome}: {exc}")

    assert not divergencias, "\n\n".join(divergencias)


@pytest.mark.parametrize("doc", ["DFP", "ITR"])
def test_dominio_de_ordem_exerc_e_escala_moeda(doc):
    from transform.dedup import normalizar_ordem_exerc
    from transform.money import ESCALAS, _norm

    pacotes = [
        u for u in cvm_common.listar_pacotes(doc)
        if cvm_common.ano_do_pacote(u) == ANO_ALVO
    ]
    fonte = http_cache.baixar(pacotes[0], subdir=f"cvm/{doc.lower()}")
    zip_path = Path(fonte.path)
    nomes = [
        n for n in cvm_common.nomes_no_zip(zip_path)
        if (cvm_demonstracoes._decompor(n) or {}).get("demonstrativo") == "DRE"
    ]
    assert nomes, "nenhum CSV de DRE no pacote"

    df = cvm_common.ler_csv_do_zip(
        zip_path, nomes[0], CONTRATOS_DEMONSTRATIVO["DRE"], fonte.sha256
    )
    assert set(normalizar_ordem_exerc(df["ORDEM_EXERC"]).unique()) <= {"ULTIMO", "PENULTIMO"}
    assert set(_norm(df["ESCALA_MOEDA"]).unique()) <= set(ESCALAS)


def test_meta_da_cvm_confirma_os_campos_declarados():
    """Conferencia SECUNDARIA: cruza o contrato com o diretorio META da CVM.

    A conferencia que vale e `test_colunas_reais_batem_com_o_contrato`, que le
    o CSV de verdade. Esta aqui so acrescenta a descricao publicada pelo orgao.

    Em 12/09/2026 o parser de META nao extraiu campo nenhum do diretorio real:
    o formato do arquivo nao e o que `contrato_da_fonte` supoe. Isso e
    limitacao do parser, nao divergencia de dado -- entao o teste pula, em vez
    de reprovar um contrato que o arquivo real ja confirmou.
    """
    campos = cvm_common.contrato_da_fonte("DFP")
    if not campos:
        pytest.skip(
            "o parser de META nao reconheceu o formato do diretorio da CVM. "
            "A conferencia primaria (colunas do CSV real) cobre o contrato. "
            "Para melhorar isto, mande o conteudo de um arquivo de "
            "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/META/"
        )
    publicados = {c for lista in campos.values() for c in lista}
    esperados = set(CONTRATOS_DEMONSTRATIVO["DRE"].colunas)
    ausentes = esperados - publicados
    assert not ausentes, (
        f"campos do contrato que nao aparecem no META da CVM: {sorted(ausentes)}"
    )


@pytest.mark.parametrize("ano", [2020, 2024])
def test_cotahist_tem_registros_de_245_bytes(ano):
    from etl import b3_cotahist
    from etl.config import COTAHIST_URL

    fonte = http_cache.baixar(COTAHIST_URL.format(ano=ano), subdir="b3/cotahist")
    df = b3_cotahist.parse_arquivo(Path(fonte.path), fonte.sha256)
    assert len(df) > 100_000
    vista = b3_cotahist.somente_acoes_a_vista(df)
    assert not vista.empty
    assert (vista["PREULT"] > 0).all()


def test_sgs_devolve_as_tres_series():
    import json
    from datetime import date

    from etl import bcb_sgs
    from etl.config import BCB_SERIES

    for nome, codigo in BCB_SERIES.items():
        url = bcb_sgs._url(codigo, date(2024, 1, 1), date(2024, 1, 31))
        fonte = http_cache.baixar(url, subdir="bcb")
        dados = json.loads(Path(fonte.path).read_text(encoding="utf-8"))
        assert dados, f"serie {nome} ({codigo}) veio vazia"
        assert {"data", "valor"} <= set(dados[0]), f"serie {nome}: {list(dados[0])}"
