"""Regra 5: normalizacao de periodo no ITR e derivacao do 4o trimestre.

O problema
----------
No ITR, DT_INI_EXERC/DT_FIM_EXERC trazem tanto o trimestre isolado quanto o
acumulado do exercicio. No ITR do 3o trimestre, a mesma conta aparece com
janela de 3 meses (jul-set) e com janela de 9 meses (jan-set). Somar as duas
conta o mesmo resultado duas vezes.

E o 4o trimestre nao existe no ITR: a companhia entrega DFP anual no lugar.
Ele e obtido por diferenca:

    Q4_isolado = DFP_anual(12M) - ITR_acumulado(9M)

Contas patrimoniais (BPA/BPP) sao saldo, nao fluxo: nao tem DT_INI_EXERC e nao
entram em derivacao. Elas sao carimbadas como INSTANTE na data de fechamento.

Exercicio social nao-civil
--------------------------
O inicio do exercicio nao e assumido como 1 de janeiro. Ele e lido do proprio
arquivo: dentro de um documento, e o menor DT_INI_EXERC observado. O indice do
trimestre e contado a partir dai.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from etl.config import TOL_Q4_ABS, TOL_Q4_REL

DEMONSTRATIVOS_FLUXO = {"DRE", "DRA", "DFC_MI", "DFC_MD", "DVA", "DMPL"}
DEMONSTRATIVOS_SALDO = {"BPA", "BPP"}

TRIMESTRE_ISOLADO = "TRIMESTRE_ISOLADO"
ACUM_6M = "ACUM_6M"
ACUM_9M = "ACUM_9M"
ANUAL = "ANUAL"
INSTANTE = "INSTANTE"
DESCONHECIDO = "DESCONHECIDO"

_DIAS_POR_MES = 30.436875  # ano gregoriano medio / 12

# Chave de um documento entregue: dentro dela, o menor DT_INI e o inicio do
# exercicio social.
_CHAVE_DOC = ["CNPJ_CIA", "DT_REFER", "doc", "base", "demonstrativo", "ordem_exerc_norm"]


def _meses(ini: pd.Series, fim: pd.Series) -> pd.Series:
    dias = (fim - ini).dt.days + 1
    return (dias / _DIAS_POR_MES).round()


def classificar_janela(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta `meses_janela` e `tipo_janela` a cada fato."""
    out = df.copy()
    out["DT_FIM_EXERC"] = pd.to_datetime(out["DT_FIM_EXERC"], errors="raise")

    if "DT_INI_EXERC" in out.columns:
        out["DT_INI_EXERC"] = pd.to_datetime(out["DT_INI_EXERC"], errors="coerce")
    else:
        out["DT_INI_EXERC"] = pd.NaT

    saldo = out["demonstrativo"].isin(DEMONSTRATIVOS_SALDO) | out["DT_INI_EXERC"].isna()
    out["meses_janela"] = np.where(
        saldo, np.nan, _meses(out["DT_INI_EXERC"], out["DT_FIM_EXERC"])
    )

    mapa = {3.0: TRIMESTRE_ISOLADO, 6.0: ACUM_6M, 9.0: ACUM_9M, 12.0: ANUAL}
    out["tipo_janela"] = out["meses_janela"].map(mapa).astype("object")
    out.loc[saldo, "tipo_janela"] = INSTANTE
    out["tipo_janela"] = out["tipo_janela"].fillna(DESCONHECIDO)
    return out


def _inicio_exercicio(df: pd.DataFrame) -> pd.Series:
    """Descobre o inicio do exercicio social sem assumir 1 de janeiro.

    O sinal confiavel e a janela ACUMULADA: 6M, 9M e o anual sempre comecam no
    primeiro dia do exercicio. Dentro de um documento, portanto, o inicio e o
    DT_INI da linha de maior janela.

    Quando um documento traz apenas o trimestre isolado (Q2 sem o acumulado,
    por exemplo), o inicio dele seria lido errado -- entao a data ancora da
    companhia, obtida dos documentos que TEM acumulado, e usada no lugar.
    """
    chave = [c for c in _CHAVE_DOC if c in df.columns]
    fluxo = df[df["tipo_janela"] != INSTANTE].copy()
    if fluxo.empty:
        # So balanco, nenhuma linha de fluxo: nao ha como observar o inicio do
        # exercicio. Antes isto devolvia NaT e o saldo ficava sem periodo
        # ("?"), o que so nao aparecia porque `rotular_saldos` herdava o
        # trimestre do documento. Recua para o ano civil -- ver o comentario
        # do mesmo recuo mais abaixo.
        return pd.Series(
            [_inicio_ancorado(f, 1, 1) if pd.notna(f) else pd.NaT
             for f in df["DT_FIM_EXERC"]],
            index=df.index, name="inicio_exercicio", dtype="datetime64[ns]",
        )

    idx = fluxo.groupby(chave, dropna=False)["meses_janela"].idxmax()
    por_doc = fluxo.loc[idx, chave + ["DT_INI_EXERC", "meses_janela"]].rename(
        columns={"DT_INI_EXERC": "_inicio_doc", "meses_janela": "_maior_janela"}
    )

    # Ancora da companhia: (mes, dia) do inicio observado em documentos com
    # janela acumulada (>= 6 meses), que nao sao ambiguos.
    confiaveis = por_doc[por_doc["_maior_janela"] >= 6]
    ancora: dict[str, tuple[int, int]] = {}
    if not confiaveis.empty:
        modo = (
            confiaveis.assign(
                _md=list(zip(confiaveis["_inicio_doc"].dt.month,
                             confiaveis["_inicio_doc"].dt.day))
            )
            .groupby("CNPJ_CIA")["_md"]
            .agg(lambda s: s.value_counts().idxmax())
        )
        ancora = modo.to_dict()

    juncao = df.merge(por_doc, on=chave, how="left")
    inicios = []
    for cnpj, ini_doc, maior, fim in zip(
        juncao["CNPJ_CIA"], juncao["_inicio_doc"], juncao["_maior_janela"],
        juncao["DT_FIM_EXERC"],
    ):
        if pd.notna(maior) and maior >= 6:
            inicios.append(ini_doc)
        elif cnpj in ancora and pd.notna(fim):
            inicios.append(_inicio_ancorado(fim, *ancora[cnpj]))
        elif pd.isna(ini_doc) and pd.notna(fim):
            # Documento so de balanco: nao ha linha de fluxo de onde tirar o
            # inicio do exercicio, e sem ele o saldo nao teria periodo nenhum.
            # Recua para o ano civil, que e o exercicio social da esmagadora
            # maioria e o padrao da CVM. Se a companhia tiver exercicio
            # deslocado, algum documento dela traz fluxo e a ancora acima
            # vence -- este ramo so pega quem nao tem nenhuma.
            inicios.append(_inicio_ancorado(fim, 1, 1))
        else:
            inicios.append(ini_doc)
    return pd.Series(pd.to_datetime(inicios), index=df.index, name="inicio_exercicio")


def _inicio_ancorado(fim: pd.Timestamp, mes: int, dia: int) -> pd.Timestamp:
    """Ultimo inicio de exercicio (mes/dia) em ou antes de `fim`."""
    candidato = pd.Timestamp(year=fim.year, month=mes, day=dia)
    if candidato > fim:
        candidato = pd.Timestamp(year=fim.year - 1, month=mes, day=dia)
    return candidato


def indice_trimestre(df: pd.DataFrame) -> pd.DataFrame:
    """Numera o trimestre a partir do inicio do exercicio social da companhia.

    Convencao: `ano_exercicio` e o ano civil em que o exercicio COMECA. Um
    exercicio de abril de 2023 a marco de 2024 e o exercicio 2023.
    """
    out = df.copy()
    out["inicio_exercicio"] = _inicio_exercicio(out)

    meses_ate_fim = _meses(out["inicio_exercicio"], out["DT_FIM_EXERC"])
    out["trimestre"] = (meses_ate_fim / 3).round().astype("Int64")
    out["ano_exercicio"] = out["inicio_exercicio"].dt.year
    # O saldo patrimonial usa a MESMA conta: `inicio_exercicio` ja e ancorado
    # na data do proprio saldo (ver `_inicio_ancorado`), entao um balanco de
    # 31/12/2019 cai em 2019T4 venha ele de onde vier. Ver `rotular_saldos`
    # para o porque de isto nao poder ser herdado do documento.
    return out


def _rotulo(ano, trimestre, tipo) -> str:
    if pd.isna(ano):
        return "?"
    if tipo == ANUAL or pd.isna(trimestre):
        # Saldo de um documento sem linha de fluxo nao tem trimestre a herdar:
        # fica rotulado pelo exercicio, sem inventar trimestre.
        return f"{int(ano)}"
    return f"{int(ano)}T{int(trimestre)}"


# Chave do documento entregue, sem o demonstrativo: BPA e DRE do mesmo ITR
# pertencem ao mesmo trimestre de reporte.
_CHAVE_ENTREGA = ["CNPJ_CIA", "DT_REFER", "doc", "base", "ordem_exerc_norm"]


def rotular_saldos(df: pd.DataFrame) -> pd.DataFrame:
    """Marca o saldo que fecha o exercicio. O periodo ja veio da data dele.

    Esta funcao herdava o trimestre do DOCUMENTO, com o argumento de que "o
    balanco de um ITR do 2o trimestre fecha em T2". Isso vale para a coluna
    ULTIMO e e FALSO para a PENULTIMA. Num relatorio intermediario as duas
    colunas usam convencoes de comparativo diferentes:

        DRE      compara com o mesmo periodo do ano anterior;
        BALANCO  compara com o FECHAMENTO do exercicio anterior (31/12).

    O codigo aplicava a regra da DRE tambem ao balanco. Resultado, visto no
    dado real do Itau em 13/09/2026: o ITR de 2020T1 traz o balanco de
    31/12/2019 e o sistema carimbava 2019T1. Os quatro trimestres de 2019
    ficavam com o MESMO saldo, o de 31 de dezembro.

        2019     1.637.481.000.000   do DFP de 2020
        2019T1   1.637.481.000.000   do ITR de 2020T1   <- e 31/12/2019
        2019T2   1.637.481.000.000   do ITR de 2020T2   <- e 31/12/2019
        2019T3   1.637.481.000.000   do ITR de 2020T3   <- e 31/12/2019

    `indice_trimestre` agora rotula o saldo pela data dele proprio, o que vale
    para as duas colunas. Efeito colateral aceito (decisao do usuario): um
    trimestre antigo cujo ITR proprio nao foi baixado deixa de ter balanco, em
    vez de exibir o de 31/12. Faltou, aparece como faltando.

    Resta so marcar quem fecha o exercicio: esse saldo entra na serie anual e
    na trimestral, sob os dois rotulos.
    """
    out = df.copy()
    saldo = out["tipo_janela"] == INSTANTE
    # Fecha o exercicio quando a data do saldo e o fim do exercicio social --
    # 12 meses depois do inicio, independente do documento que o trouxe.
    fecha = saldo & out["trimestre"].eq(4)
    out["saldo_de_exercicio_anual"] = fecha.fillna(False)
    return out


def somente_isolados(df: pd.DataFrame) -> pd.DataFrame:
    """Regra 5, parte 1: descarta os acumulados do ITR.

    Mantem trimestre isolado (ITR), anual (DFP) e saldo patrimonial. Janelas
    de 6 e 9 meses ficam de fora da serie trimestral -- mas nao sao jogadas
    fora do pipeline: `derivar_q4` depende da de 9 meses.
    """
    return df[df["tipo_janela"].isin({TRIMESTRE_ISOLADO, ANUAL, INSTANTE})].reset_index(
        drop=True
    )


def com_dimensoes_extras(df: pd.DataFrame, colunas: list[str]) -> list[str]:
    """Acrescenta `COLUNA_DF` a uma chave de fato quando a dimensao existir.

    Toda chave de fato deste modulo passa por aqui, de proposito. Escrever a
    lista a mao foi o que colapsou a DMPL em silencio: a mesma conta em duas
    colunas do patrimonio liquido vira uma linha so e a outra desaparece --
    sem erro, sem aviso, so um numero a menos na tela.
    """
    if "COLUNA_DF" in df.columns and "COLUNA_DF" not in colunas:
        return list(colunas) + ["COLUNA_DF"]
    return list(colunas)


def _chave_conta(df: pd.DataFrame) -> list[str]:
    """Granularidade de um fato contabil dentro de um exercicio.

    `COLUNA_DF` entra porque a DMPL abre a mesma conta em varias colunas do
    patrimonio liquido; sem ela a chave nao e unica e a derivacao do Q4 tenta
    um casamento N-para-N.
    """
    return com_dimensoes_extras(
        df, ["CNPJ_CIA", "base", "demonstrativo", "CD_CONTA", "ano_exercicio"]
    )


class ChaveDuplicadaError(ValueError):
    """A chave do fato nao identifica uma linha so -- falta uma dimensao."""


def _exigir_chave_unica(df: pd.DataFrame, chave: list[str], lado: str) -> None:
    """Falha com diagnostico util em vez do despejo cru do pandas.

    Ja aconteceu duas vezes, por dimensoes diferentes -- COLUNA_DF na DMPL e
    a convivencia ULTIMO/PENULTIMO. O `MergeError` do pandas nao diz qual
    dimensao falta; esta mensagem diz.
    """
    dup = df[df.duplicated(chave, keep=False)]
    if dup.empty:
        return
    exemplo = dup.sort_values(chave).head(6)
    colunas_uteis = [c for c in ("DT_REFER", "ORDEM_EXERC", "COLUNA_DF", "DS_CONTA")
                     if c in exemplo.columns]
    raise ChaveDuplicadaError(
        f"derivacao do Q4, lado '{lado}': {len(dup)} linhas com chave repetida.\n"
        f"chave usada: {chave}\n"
        f"as colunas abaixo distinguem essas linhas e faltam na chave:\n"
        f"{exemplo[chave + colunas_uteis].to_string(index=False)}"
    )


def _publicacao_mais_recente(df: pd.DataFrame, chave: list[str]) -> pd.DataFrame:
    """Uma linha por chave: a publicada mais recentemente.

    O mesmo exercicio chega por mais de um documento -- na DFP do proprio ano
    (ORDEM_EXERC = ULTIMO) e na do ano seguinte, como comparativo
    (PENULTIMO). Ate a etapa de reapresentacao os dois convivem, entao aqui a
    chave nao e unica e um merge direto casaria N com N.

    A regra e a mesma da politica de reapresentacao escolhida para o projeto:
    vence a publicacao de maior DT_REFER, desempatada por VERSAO. Derivar o Q4
    de dois documentos de epocas diferentes seria pior do que escolher.
    """
    if df.empty:
        return df

    out = df.copy()
    out["_dt_refer"] = pd.to_datetime(out["DT_REFER"], errors="coerce")
    if "versao_int" in out.columns:
        out["_versao"] = pd.to_numeric(out["versao_int"], errors="coerce")
    else:
        out["_versao"] = pd.to_numeric(out.get("VERSAO"), errors="coerce")

    out = out.sort_values(chave + ["_dt_refer", "_versao"], kind="mergesort")
    out = out.groupby(chave, dropna=False, as_index=False).tail(1)
    return out.drop(columns=["_dt_refer", "_versao"])


def derivar_q4(df: pd.DataFrame) -> pd.DataFrame:
    """Regra 5, parte 2: Q4 isolado = DFP anual - ITR acumulado de 9 meses.

    Devolve os fatos do 4o trimestre com `origem_periodo='DERIVADO_Q4'` e a
    linhagem dos dois insumos em `src_derivacao` (JSON). Conta sem um dos dois
    lados nao gera linha -- ausencia aparece como ausencia.
    """
    fluxo = df[df["demonstrativo"].isin(DEMONSTRATIVOS_FLUXO)]

    anual = fluxo[(fluxo["doc"] == "DFP") & (fluxo["tipo_janela"] == ANUAL)]
    nove = fluxo[(fluxo["doc"] == "ITR") & (fluxo["tipo_janela"] == ACUM_9M)]
    if anual.empty or nove.empty:
        return df.head(0).assign(origem_periodo=None, src_derivacao=None)

    chave = _chave_conta(df)
    # Cada lado reduzido a uma linha por chave antes do casamento: sem isto o
    # mesmo exercicio, vindo de dois documentos, produz um merge N-para-N.
    anual = _publicacao_mais_recente(anual, chave)
    nove = _publicacao_mais_recente(nove, chave)

    esq = anual[chave + ["VL_CONTA_NUM", "DS_CONTA", "DT_FIM_EXERC", "inicio_exercicio",
                         "src_archive", "src_file", "src_line", "src_sha256",
                         "VERSAO", "ORDEM_EXERC", "DT_REFER", "ESCALA_MOEDA", "MOEDA"]]
    dir_ = nove[chave + ["VL_CONTA_NUM", "src_archive", "src_file", "src_line",
                         "src_sha256", "DT_FIM_EXERC"]].rename(
        columns={
            "VL_CONTA_NUM": "VL_9M",
            "src_archive": "src_archive_9m",
            "src_file": "src_file_9m",
            "src_line": "src_line_9m",
            "src_sha256": "src_sha256_9m",
            "DT_FIM_EXERC": "dt_fim_9m",
        }
    )

    _exigir_chave_unica(esq, chave, "DFP anual")
    _exigir_chave_unica(dir_, chave, "ITR acumulado de 9 meses")
    j = esq.merge(dir_, on=chave, how="inner", validate="one_to_one")
    if j.empty:
        return df.head(0).assign(origem_periodo=None, src_derivacao=None)

    j["VL_CONTA_NUM"] = j["VL_CONTA_NUM"] - j["VL_9M"]
    j["doc"] = "DFP-ITR"
    j["tipo_janela"] = TRIMESTRE_ISOLADO
    j["trimestre"] = 4
    j["DT_INI_EXERC"] = j["dt_fim_9m"] + pd.Timedelta(days=1)
    j["origem_periodo"] = "DERIVADO_Q4"
    j["meses_janela"] = 3.0
    j["src_derivacao"] = [
        json.dumps(
            {
                "formula": "Q4 = DFP_anual(12M) - ITR_acumulado(9M)",
                "anual": {"arquivo": a, "linha": int(la), "valor": float(va)},
                "acum_9m": {"arquivo": b, "linha": int(lb), "valor": float(vb)},
            },
            ensure_ascii=False,
        )
        for a, la, va, b, lb, vb in zip(
            j["src_file"], j["src_line"], j["VL_CONTA_NUM"] + j["VL_9M"],
            j["src_file_9m"], j["src_line_9m"], j["VL_9M"],
        )
    ]
    return j.drop(columns=["VL_9M", "dt_fim_9m"]).reset_index(drop=True)


def conferir_soma_trimestres(df: pd.DataFrame) -> pd.DataFrame:
    """Q1+Q2+Q3 isolados devem reproduzir o acumulado de 9 meses.

    Nao corrige nada: devolve as divergencias acima da tolerancia declarada em
    `etl.config`, para a interface sinalizar. Uma divergencia aqui costuma ser
    reapresentacao no meio do ano, e o usuario precisa ver isso.
    """
    fluxo = df[df["demonstrativo"].isin(DEMONSTRATIVOS_FLUXO)]
    isolados = fluxo[
        (fluxo["tipo_janela"] == TRIMESTRE_ISOLADO) & (fluxo["trimestre"].isin([1, 2, 3]))
    ]
    nove = fluxo[fluxo["tipo_janela"] == ACUM_9M]
    chave = _chave_conta(df)
    if isolados.empty or nove.empty:
        return pd.DataFrame(columns=chave + ["soma_isolados", "acum_9m", "diferenca"])

    # Um trimestre publicado em dois documentos entraria duas vezes na soma e
    # a conferencia acusaria divergencia que nao existe. Reduz por trimestre
    # antes de somar, e reduz o acumulado antes de comparar.
    chave_tri = chave + ["trimestre"]
    isolados = _publicacao_mais_recente(isolados, chave_tri)
    nove = _publicacao_mais_recente(nove, chave)

    soma = (
        isolados.groupby(chave, dropna=False)
        .agg(soma_isolados=("VL_CONTA_NUM", "sum"), n_trimestres=("VL_CONTA_NUM", "size"))
        .reset_index()
    )
    soma = soma[soma["n_trimestres"] == 3]
    ac = nove.groupby(chave, dropna=False)["VL_CONTA_NUM"].sum().rename("acum_9m").reset_index()

    cmp_ = soma.merge(ac, on=chave, how="inner")
    cmp_["diferenca"] = cmp_["soma_isolados"] - cmp_["acum_9m"]
    tol = np.maximum(TOL_Q4_ABS, cmp_["acum_9m"].abs() * TOL_Q4_REL)
    return cmp_[cmp_["diferenca"].abs() > tol].reset_index(drop=True)


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Pipeline completo da regra 5.

    Entrada: fatos ja deduplicados (regras 2 e 3) com `VL_CONTA_NUM` numerico.
    Saida: serie de periodos sem sobreposicao, com Q4 derivado incluido e
    `origem_periodo` dizendo se o numero foi lido ou obtido por diferenca.
    """
    cls = rotular_saldos(indice_trimestre(classificar_janela(df)))
    cls["origem_periodo"] = "DIRETO"
    cls["src_derivacao"] = None

    q4 = derivar_q4(cls)
    base = somente_isolados(cls)

    # O anual da DFP nao e trimestre: ele permanece como periodo proprio.
    todos = pd.concat([base, q4], ignore_index=True) if not q4.empty else base
    todos["periodo"] = [
        _rotulo(a, t, tp)
        for a, t, tp in zip(todos["ano_exercicio"], todos["trimestre"], todos["tipo_janela"])
    ]

    # O saldo patrimonial de um exercicio anual fecha, ao mesmo tempo, o ano e
    # o 4o trimestre. Ele e publicado sob os dois rotulos -- mesma linha do
    # mesmo arquivo, mesma linhagem -- para que a serie anual case com a DRE
    # anual e a serie trimestral case com o Q4.
    if "saldo_de_exercicio_anual" in todos.columns:
        anuais = todos[todos["saldo_de_exercicio_anual"].fillna(False)].copy()
        if not anuais.empty:
            anuais["periodo"] = anuais["ano_exercicio"].astype("Int64").astype(str)
            todos = pd.concat([todos, anuais], ignore_index=True)
            todos = todos.drop_duplicates(
                subset=com_dimensoes_extras(
                    todos,
                    ["CNPJ_CIA", "base", "demonstrativo", "CD_CONTA", "periodo",
                     "ordem_exerc_norm", "DT_REFER"],
                ),
                keep="first",
            )

    return todos.reset_index(drop=True)
