"""`src_line` tem que apontar a linha física certa, inclusive com campo multilinha.

Achado no arquivo real da CVM (12/09/2026):

    itr_cia_aberta_DFC_MI_ind_2019.csv: 133057 linhas físicas para 133055 registros
    ipe_cia_aberta_2020.csv:             43310 linhas físicas para  43280 registros

Ou seja: existem campos com quebra de linha dentro de aspas. A contagem
ingênua (uma linha = um registro) erra, e a partir do registro afetado toda
referência `arquivo:linha` passaria a apontar para a linha errada.

A saída NÃO é relaxar a checagem — é numerar direito.
"""

from __future__ import annotations

import zipfile

import pytest

from etl import cvm_common
from etl.contracts import Contrato

CONTRATO = Contrato(
    "teste_multilinha", ("A", "B", "C"), ("A",), "fixture de teste"
)


def _bytes(texto: str) -> bytes:
    return texto.encode("latin-1")


SIMPLES = "A;B;C\n1;um;x\n2;dois;y\n3;tres;z\n"

# O registro 2 tem uma quebra de linha dentro do campo B, entre aspas.
MULTILINHA = 'A;B;C\n1;um;x\n2;"dois\ncontinua";y\n3;tres;z\n'


def test_caminho_rapido_numera_sequencialmente():
    linhas = cvm_common.numeros_de_linha(_bytes(SIMPLES), 3, "simples.csv")
    assert linhas == [2, 3, 4]


def test_campo_multilinha_desloca_os_registros_seguintes():
    """O registro 3 está na linha 5 do arquivo, não na 4."""
    linhas = cvm_common.numeros_de_linha(_bytes(MULTILINHA), 3, "multi.csv")
    assert linhas == [2, 3, 5]


def test_a_linha_apontada_contem_mesmo_o_registro():
    """A verificação que importa: sed -n '{linha}p' devolve o registro."""
    linhas = cvm_common.numeros_de_linha(_bytes(MULTILINHA), 3, "multi.csv")
    fisicas = MULTILINHA.split("\n")
    for esperado, numero in zip(["1;um;x", '2;"dois', "3;tres;z"], linhas):
        assert fisicas[numero - 1] == esperado


def test_discordancia_entre_leitores_ainda_levanta():
    """Se nem o csv.reader casar com o pandas, para -- nao chuta."""
    with pytest.raises(cvm_common.RastreabilidadeError, match="discordam"):
        cvm_common.numeros_de_linha(_bytes(MULTILINHA), 99, "multi.csv")


def test_leitura_do_zip_propaga_a_linha_correta(tmp_path):
    z = tmp_path / "pacote.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("multi.csv", MULTILINHA)

    df = cvm_common.ler_csv_do_zip(z, "multi.csv", CONTRATO, "0" * 64)
    assert list(df["src_line"]) == [2, 3, 5]
    # O conteudo tem que ter sobrevivido a quebra de linha.
    assert df.loc[1, "B"] == "dois\ncontinua"


def test_arquivo_sem_quebra_continua_no_caminho_rapido(tmp_path):
    z = tmp_path / "pacote.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("simples.csv", SIMPLES)

    df = cvm_common.ler_csv_do_zip(z, "simples.csv", CONTRATO, "0" * 64)
    assert list(df["src_line"]) == [2, 3, 4]


def test_anotar_origem_recusa_lista_de_tamanho_errado():
    import pandas as pd

    from etl import provenance

    df = pd.DataFrame({"x": [1, 2, 3]})
    with pytest.raises(ValueError, match="numeros de linha"):
        provenance.anotar_origem(df, archive="a", file="a.csv", sha256="0" * 64,
                                 linhas=[2, 3])
