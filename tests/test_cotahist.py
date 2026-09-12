"""Parser posicional do COTAHIST: 245 bytes, campo por posicao."""

from __future__ import annotations

import pytest

from etl import b3_cotahist as cot


def _campo(valor: str, tamanho: int, numerico: bool = False) -> str:
    if numerico:
        return str(valor).rjust(tamanho, "0")[:tamanho]
    return str(valor).ljust(tamanho)[:tamanho]


def registro_cotacao(
    *, data="20240102", codneg="TEST3", preult=1234, quatot=1000, voltot=123400,
    fatcot=1, codisi="BRTESTACNOR0", codbdi="02", tpmerc="010",
) -> bytes:
    """Monta um registro tipo 01 valido de 245 bytes."""
    partes = [
        "01", data, codbdi, _campo(codneg, 12), tpmerc, _campo("TESTE", 12),
        _campo("ON", 10), _campo("", 3), _campo("R$", 4),
    ]
    for p in (preult, preult, preult, preult, preult, 0, 0):  # abe max min med ult ofc ofv
        partes.append(_campo(p, 13, numerico=True))
    partes += [
        _campo(10, 5, numerico=True),        # TOTNEG
        _campo(quatot, 18, numerico=True),   # QUATOT
        _campo(voltot, 18, numerico=True),   # VOLTOT
        _campo(0, 13, numerico=True),        # PREEXE
        "0",                                  # INDOPC
        "99991231",                           # DATVEN
        _campo(fatcot, 7, numerico=True),    # FATCOT
        _campo(0, 13, numerico=True),        # PTOEXE
        _campo(codisi, 12),                  # CODISI
        _campo(0, 3, numerico=True),         # DISMES
    ]
    linha = "".join(partes)
    assert len(linha) == 245, len(linha)
    return linha.encode("latin-1")


def arquivo(registros: list[bytes], total: int | None = None) -> bytes:
    """Monta um COTAHIST completo: header, registros e trailer.

    A contagem do trailer e o numero de registros de DADOS -- confrontado com
    COTAHIST_A2025 real em 12/09/2026. O fixture antes escrevia len+2, o que
    era a suposicao errada do parser espelhada no teste: os dois concordavam
    entre si e discordavam do arquivo da B3.
    """
    header = ("00COTAHIST.2024BOVESPA 20240102" + " " * 214).encode("latin-1")[:245]
    n = total if total is not None else len(registros)
    trailer = ("99COTAHIST.2024BOVESPA 20240102" + str(n).rjust(11, "0")).ljust(245).encode("latin-1")
    return b"\r\n".join([header] + registros + [trailer]) + b"\r\n"


def test_layout_soma_245_bytes():
    assert cot.LAYOUT[-1].fim == 245
    for a, b in zip(cot.LAYOUT, cot.LAYOUT[1:]):
        assert b.inicio == a.fim + 1, f"buraco entre {a.nome} e {b.nome}"


def test_parse_por_posicao_e_decimal_implicito():
    df = cot.parse_bytes(arquivo([registro_cotacao(preult=1234)]), "teste.txt", "x" * 64)
    assert len(df) == 1
    assert df.loc[0, "CODNEG"] == "TEST3"
    assert df.loc[0, "PREULT"] == pytest.approx(12.34)  # (11)V99
    assert df.loc[0, "VOLTOT"] == pytest.approx(1234.00)
    assert str(df.loc[0, "data"].date()) == "2024-01-02"


def test_registro_com_tamanho_errado_levanta():
    mau = registro_cotacao()[:-1]
    with pytest.raises(cot.CotahistError, match="244 bytes"):
        cot.parse_bytes(arquivo([mau]), "teste.txt", "x" * 64)


def test_arquivo_sem_header_levanta():
    conteudo = b"\r\n".join([registro_cotacao()]) + b"\r\n"
    with pytest.raises(cot.CotahistError, match="header"):
        cot.parse_bytes(conteudo, "teste.txt", "x" * 64)


def test_trailer_com_contagem_muito_divergente_levanta():
    """Diferenca grande e assinatura de download truncado: falha dura."""
    conteudo = arquivo([registro_cotacao(), registro_cotacao(codneg="OUTR4")], total=99)
    with pytest.raises(cot.CotahistError, match="truncado"):
        cot.parse_bytes(conteudo, "teste.txt", "x" * 64)


def test_trailer_fora_por_poucos_vira_aviso_e_nao_derruba():
    """O caso do arquivo do ano corrente, que a B3 atualiza durante o ano.

    Nenhum valor fica incorreto por causa da contagem do rodape, entao a
    extracao segue -- com o desencontro registrado, nao engolido.
    """
    from etl import avisos

    avisos.limpar()
    # Convencao real: o total inclui header e trailer, logo len+2. Aqui a
    # contagem vem 2 a menos, exatamente como no COTAHIST_A2025.
    conteudo = arquivo([registro_cotacao(), registro_cotacao(codneg="OUTR4")], total=2)
    df = cot.parse_bytes(conteudo, "COTAHIST_A2026.TXT", "x" * 64)

    assert len(df) == 2, "os registros tem que ser lidos apesar do desencontro"
    registrados = avisos.registrados()
    assert len(registrados) == 1
    assert registrados[0].categoria == "trailer_cotahist"
    assert "COTAHIST_A2026.TXT" in registrados[0].origem
    avisos.limpar()


def test_trailer_na_convencao_correta_nao_gera_aviso():
    """2020 e 2024 reais: o total declarado e len(dados) + 2."""
    from etl import avisos

    avisos.limpar()
    cot.parse_bytes(arquivo([registro_cotacao()], total=3), "t.txt", "x" * 64)
    assert avisos.registrados() == []


def test_linhagem_aponta_para_a_linha_fisica():
    df = cot.parse_bytes(
        arquivo([registro_cotacao(), registro_cotacao(codneg="OUTR4")]), "t.txt", "x" * 64
    )
    assert list(df["src_line"]) == [2, 3]  # linha 1 e o header


def test_filtro_de_acoes_a_vista():
    regs = [
        registro_cotacao(codneg="VIST3", codbdi="02", tpmerc="010"),
        registro_cotacao(codneg="FRAC3F", codbdi="96", tpmerc="010"),
        registro_cotacao(codneg="OPCAO", codbdi="02", tpmerc="070"),
    ]
    df = cot.parse_bytes(arquivo(regs), "t.txt", "x" * 64)
    vista = cot.somente_acoes_a_vista(df)
    assert list(vista["CODNEG"]) == ["VIST3"]


def test_fatcot_e_preservado_cru_para_a_camada_de_ajuste():
    df = cot.parse_bytes(arquivo([registro_cotacao(fatcot=1000)]), "t.txt", "x" * 64)
    assert df.loc[0, "FATCOT"] == 1000
