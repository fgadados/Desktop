"""Contratos de schema: o parser declara o que espera e falha alto se divergir.

Por que isto existe
-------------------
O pipeline nao pode adivinhar nome de coluna. Cada contrato abaixo foi
declarado a partir do layout publicado pela CVM/B3, mas nenhum contrato e
considerado verdadeiro ate ser confrontado com o arquivo real. Todo contrato
carrega `verificado_em`: `None` significa "declarado, ainda nao confrontado
nesta instalacao".

Fluxo:

1. `validar()` roda em toda leitura de bruto. Coluna faltando -> erro.
   Coluna nova no arquivo -> erro tambem (a CVM muda layout entre anos, e um
   campo novo silenciosamente ignorado e a origem classica de numero errado).
2. `etl.cvm_common.contrato_da_fonte()` baixa o diretorio META da propria CVM
   e extrai a lista de campos publicada pelo orgao. `tests/contract/` compara
   os dois e e a unica coisa que promove um contrato a verificado.

Nada aqui preenche, renomeia ou tolera divergencia por conta propria.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SchemaContractError(AssertionError):
    """O arquivo real nao bate com o contrato declarado."""


@dataclass(frozen=True)
class Contrato:
    nome: str
    colunas: tuple[str, ...]
    chave: tuple[str, ...]
    origem: str
    verificado_em: str | None = None
    opcionais: tuple[str, ...] = field(default=())

    def validar(self, colunas_reais, *, strict: bool = True) -> None:
        """Confere as colunas do arquivo real contra as declaradas.

        Coluna DECLARADA E AUSENTE e falha dura: o codigo leria o campo errado
        ou nenhum, e isso produz numero errado.

        Coluna NOVA, presente e nao declarada, e apenas aviso. O CSV e por
        cabecalho nomeado, entao uma coluna a mais nao desloca nada: nenhum
        numero fica incorreto. Derrubar a extracao inteira porque a CVM
        acrescentou um campo custa caro e nao protege nada. Ver `etl/avisos.py`.
        """
        from etl import avisos

        reais = tuple(colunas_reais)
        esperadas = set(self.colunas)
        obtidas = set(reais)

        faltando = sorted(esperadas - obtidas - set(self.opcionais))
        sobrando = sorted(obtidas - esperadas)

        if sobrando and strict and not faltando:
            avisos.avisar(
                self.nome, "coluna_nova",
                f"colunas presentes no arquivo e nao declaradas: {sobrando}. "
                "Nenhum valor fica incorreto -- o CSV e por cabecalho nomeado. "
                "Declare-as em etl/contracts.py se forem passar a ser usadas.",
            )
            sobrando = []

        if faltando:
            partes = [f"contrato '{self.nome}' nao bate com o arquivo real."]
            partes.append(f"  colunas ausentes no arquivo: {faltando}")
            if sobrando:
                partes.append(f"  colunas presentes e nao declaradas: {sobrando}")
            partes.append(f"  declarado a partir de: {self.origem}")
            partes.append(
                f"  verificado contra arquivo real em: {self.verificado_em or 'NUNCA'}"
            )
            partes.append(
                "  acao: confira o layout em "
                "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/<DOC>/META/ e "
                "atualize etl/contracts.py. Nao contorne este erro."
            )
            raise SchemaContractError("\n".join(partes))

        ausentes_chave = [c for c in self.chave if c not in obtidas]
        if ausentes_chave:
            raise SchemaContractError(
                f"contrato '{self.nome}': coluna de chave ausente {ausentes_chave}"
            )


# ---------------------------------------------------------------------------
# CVM -- demonstracoes (DFP e ITR compartilham o layout)
# ---------------------------------------------------------------------------
_ORIGEM_CVM = (
    "layout publicado em dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{DFP,ITR}/META/ "
    "-- NAO confrontado com arquivo real nesta instalacao"
)

# Campos comuns a todos os demonstrativos patrimoniais (BPA, BPP).
_BASE_PATRIMONIAL = (
    "CNPJ_CIA",
    "DT_REFER",
    "VERSAO",
    "DENOM_CIA",
    "CD_CVM",
    "GRUPO_DFP",
    "MOEDA",
    "ESCALA_MOEDA",
    "ORDEM_EXERC",
    "DT_FIM_EXERC",
    "CD_CONTA",
    "DS_CONTA",
    "VL_CONTA",
    "ST_CONTA_FIXA",
)

# Demonstrativos de fluxo (DRE, DFC, DVA) acrescentam a data inicial do periodo.
_BASE_FLUXO = (
    "CNPJ_CIA",
    "DT_REFER",
    "VERSAO",
    "DENOM_CIA",
    "CD_CVM",
    "GRUPO_DFP",
    "MOEDA",
    "ESCALA_MOEDA",
    "ORDEM_EXERC",
    "DT_INI_EXERC",
    "DT_FIM_EXERC",
    "CD_CONTA",
    "DS_CONTA",
    "VL_CONTA",
    "ST_CONTA_FIXA",
)

BPA = Contrato("cvm_bpa", _BASE_PATRIMONIAL, ("CNPJ_CIA", "DT_REFER", "VERSAO", "ORDEM_EXERC", "CD_CONTA"), _ORIGEM_CVM)
BPP = Contrato("cvm_bpp", _BASE_PATRIMONIAL, BPA.chave, _ORIGEM_CVM)
DRE = Contrato("cvm_dre", _BASE_FLUXO, ("CNPJ_CIA", "DT_REFER", "VERSAO", "ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC", "CD_CONTA"), _ORIGEM_CVM)
DFC_MI = Contrato("cvm_dfc_mi", _BASE_FLUXO, DRE.chave, _ORIGEM_CVM)
DFC_MD = Contrato("cvm_dfc_md", _BASE_FLUXO, DRE.chave, _ORIGEM_CVM)
DVA = Contrato("cvm_dva", _BASE_FLUXO, DRE.chave, _ORIGEM_CVM)
DRA = Contrato("cvm_dra", _BASE_FLUXO, DRE.chave, _ORIGEM_CVM)
DMPL = Contrato("cvm_dmpl", _BASE_FLUXO + ("COLUNA_DF",), DRE.chave + ("COLUNA_DF",), _ORIGEM_CVM)

# Cabecalho do pacote: um registro por documento entregue.
CABECALHO = Contrato(
    "cvm_cabecalho",
    (
        "CNPJ_CIA",
        "DT_REFER",
        "VERSAO",
        "DENOM_CIA",
        "CD_CVM",
        "CATEG_DOC",
        "ID_DOC",
        "DT_RECEB",
        "LINK_DOC",
    ),
    ("CNPJ_CIA", "DT_REFER", "VERSAO"),
    _ORIGEM_CVM,
)

# ---------------------------------------------------------------------------
# Composicao do capital -- quantidade de acoes em circulacao
# ---------------------------------------------------------------------------
# Este arquivo existe dentro dos pacotes DFP e ITR e foi descoberto ao
# confrontar o ZIP real com o parser (12/09/2026). Ele e o insumo que faltava
# para P/L e P/VP: a quantidade de acoes nunca esteve nas demonstracoes, mas
# esta aqui, ao lado delas, na propria CVM.
#
# So os campos de identificacao sao declarados -- esses sao iguais em todo
# arquivo da CVM e ja foram confirmados nos demais contratos. Os campos de
# quantidade NAO sao declarados porque ainda nao foram vistos: a validacao
# roda em modo nao-estrito e `python run.py schema` imprime os nomes reais.
# Declarar nome de coluna por suposicao aqui seria exatamente o que este
# modulo existe para impedir.
COMPOSICAO_CAPITAL = Contrato(
    "cvm_composicao_capital",
    (
        "CNPJ_CIA",
        "DT_REFER",
        "VERSAO",
        "DENOM_CIA",
        # Capital integralizado e tesouraria, por especie. Acoes em circulacao
        # = integralizado - tesouraria; papel em tesouraria nao participa de
        # lucro por acao nem de valor de mercado.
        "QT_ACAO_ORDIN_CAP_INTEGR",
        "QT_ACAO_PREF_CAP_INTEGR",
        "QT_ACAO_TOTAL_CAP_INTEGR",
        "QT_ACAO_ORDIN_TESOURO",
        "QT_ACAO_PREF_TESOURO",
        "QT_ACAO_TOTAL_TESOURO",
    ),
    ("CNPJ_CIA", "DT_REFER", "VERSAO"),
    "CONFRONTADO com DFP/ITR reais em 12/09/2026. Note que este arquivo NAO "
    "tem CD_CVM, ao contrario dos demonstrativos",
    verificado_em="2026-09-12",
)

# Mapa demonstrativo -> contrato. As chaves sao os sufixos que aparecem no nome
# dos CSVs dentro do ZIP da CVM (ex.: dfp_cia_aberta_BPA_con_2023.csv).
CONTRATOS_DEMONSTRATIVO = {
    "BPA": BPA,
    "BPP": BPP,
    "DRE": DRE,
    "DRA": DRA,
    "DFC_MI": DFC_MI,
    "DFC_MD": DFC_MD,
    "DVA": DVA,
    "DMPL": DMPL,
}

# ---------------------------------------------------------------------------
# CVM -- cadastro de companhias abertas
# ---------------------------------------------------------------------------
# O cadastro tem dezenas de campos de endereco/contato irrelevantes aqui, e a
# CVM acrescenta campos entre versoes. Validacao nao-estrita: exigimos o que
# usamos, aceitamos o resto.
CAD_CIA = Contrato(
    "cvm_cad_cia_aberta",
    (
        "CNPJ_CIA",
        "DENOM_SOCIAL",
        "DENOM_COMERC",
        "DT_REG",
        "DT_CANCEL",
        "MOTIVO_CANCEL",
        "SIT",
        "DT_INI_SIT",
        "CD_CVM",
        "SETOR_ATIV",
        "TP_MERC",
        "CATEG_REG",
        "DT_INI_CATEG",
        "SIT_EMISSOR",
    ),
    ("CNPJ_CIA",),
    "layout de dados.cvm.gov.br/dados/CIA_ABERTA/CAD/META/ -- NAO confrontado",
)

# ---------------------------------------------------------------------------
# CVM -- FCA, tabela de valores mobiliarios. Fonte oficial do de-para ticker.
# ---------------------------------------------------------------------------
FCA_VALOR_MOBILIARIO = Contrato(
    "cvm_fca_valor_mobiliario",
    (
        "CNPJ_Companhia",
        "Data_Referencia",
        "Versao",
        "ID_Documento",
        "Valor_Mobiliario",
        "Sigla_Classe_Acao_Preferencial",
        "Codigo_Negociacao",
        "Mercado",
        "Sigla_Entidade_Administradora",
        "Data_Inicio_Negociacao",
        "Data_Fim_Negociacao",
    ),
    ("CNPJ_Companhia", "Data_Referencia", "Versao", "Codigo_Negociacao"),
    "layout de dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/META/ -- NAO confrontado",
)

# ---------------------------------------------------------------------------
# CVM -- IPE (fatos relevantes e comunicados)
# ---------------------------------------------------------------------------
IPE = Contrato(
    "cvm_ipe",
    (
        "CNPJ_Companhia",
        "Data_Referencia",
        "Categoria",
        "Tipo",
        "Especie",
        "Assunto",
        "Data_Entrega",
        "Link_Download",
    ),
    ("CNPJ_Companhia", "Data_Entrega"),
    "layout de dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/META/ -- NAO confrontado",
    opcionais=("Protocolo_Entrega", "Tipo_Apresentacao", "Nome_Companhia", "Codigo_CVM"),
)

TODOS = {
    c.nome: c
    for c in (
        BPA, BPP, DRE, DRA, DFC_MI, DFC_MD, DVA, DMPL,
        CABECALHO, CAD_CIA, FCA_VALOR_MOBILIARIO, IPE, COMPOSICAO_CAPITAL,
    )
}
