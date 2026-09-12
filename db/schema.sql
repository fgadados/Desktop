-- Schema analitico (DuckDB). Arquivo local unico, sem servidor.
--
-- Principio: toda tabela de numero derivado carrega, na propria linha, como o
-- numero foi obtido. `indicador` guarda formula e status; `indicador_entrada`
-- guarda cada insumo com (arquivo, linha) do bruto.

-- ---------------------------------------------------------------------------
-- Proveniencia do download
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fonte (
    url            VARCHAR PRIMARY KEY,
    caminho        VARCHAR NOT NULL,
    sha256         VARCHAR NOT NULL,
    bytes          BIGINT  NOT NULL,
    baixado_em     TIMESTAMP NOT NULL,
    etag           VARCHAR,
    last_modified  VARCHAR
);

-- ---------------------------------------------------------------------------
-- Fatos contabeis normalizados (regras 2, 3, 4, 5 ja aplicadas)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fato_contabil (
    cnpj              VARCHAR NOT NULL,
    denom_cia         VARCHAR,
    doc               VARCHAR NOT NULL,   -- DFP | ITR | DFP-ITR (Q4 derivado)
    demonstrativo     VARCHAR NOT NULL,   -- BPA | BPP | DRE | DFC_MI | ...
    base              VARCHAR NOT NULL,   -- CON | IND
    periodo           VARCHAR NOT NULL,   -- 2023 | 2023T3
    tipo_janela       VARCHAR NOT NULL,
    origem_periodo    VARCHAR NOT NULL,   -- DIRETO | DERIVADO_Q4
    cd_conta          VARCHAR NOT NULL,
    ds_conta          VARCHAR,
    valor             DOUBLE,
    escala_fator      DOUBLE,
    moeda             VARCHAR,
    versao            INTEGER,
    versoes_descartadas VARCHAR,
    ordem_exerc       VARCHAR,
    dt_refer          DATE,
    dt_ini_exerc      DATE,
    dt_fim_exerc      DATE,
    valor_as_filed    DOUBLE,
    valor_restated    DOUBLE,
    divergente        BOOLEAN,
    n_publicacoes     INTEGER,
    base_escolhida    VARCHAR,
    criterio_base     VARCHAR,
    src_archive       VARCHAR,
    src_file          VARCHAR,
    src_line          BIGINT,
    src_sha256        VARCHAR,
    src_derivacao     VARCHAR,            -- JSON, preenchido no Q4 derivado
    PRIMARY KEY (cnpj, base, demonstrativo, periodo, cd_conta)
);

-- ---------------------------------------------------------------------------
-- Cadastro, setor e de-para
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS empresa (
    cnpj                  VARCHAR PRIMARY KEY,
    denom_social          VARCHAR,
    cd_cvm                VARCHAR,
    setor_ativ            VARCHAR,
    plano_contas          VARCHAR NOT NULL,
    origem_classificacao  VARCHAR NOT NULL,
    situacao              VARCHAR,
    ultimo_doc_dt_refer   DATE,            -- criterio de aceite: data do ultimo
    ultimo_doc_versao     INTEGER,         -- documento CVM processado e sua versao
    ultimo_doc_tipo       VARCHAR,
    base_escolhida        VARCHAR,
    cobertura_con         INTEGER,
    cobertura_ind         INTEGER,
    criterio_base         VARCHAR
);

CREATE TABLE IF NOT EXISTS depara_ticker (
    cnpj             VARCHAR NOT NULL,
    ticker           VARCHAR NOT NULL,
    valor_mobiliario VARCHAR,
    classe           VARCHAR,
    mercado          VARCHAR,
    data_inicio      DATE,
    data_fim         DATE,
    negociado_b3     BOOLEAN,
    src_file         VARCHAR,
    src_line         BIGINT,
    PRIMARY KEY (cnpj, ticker)
);

CREATE TABLE IF NOT EXISTS depara_divergencia (
    tipo     VARCHAR NOT NULL,  -- fca_sem_pregao | pregao_sem_fca
    cnpj     VARCHAR,
    ticker   VARCHAR NOT NULL,
    detalhe  VARCHAR
);

-- ---------------------------------------------------------------------------
-- Precos
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS preco_diario (
    ticker         VARCHAR NOT NULL,
    data           DATE NOT NULL,
    fech           DOUBLE,      -- como negociado, sem ajuste
    fech_aj_split  DOUBLE,      -- ajustado so por evento de quantidade -> multiplos
    fech_aj_total  DOUBLE,      -- ajustado por quantidade e provento    -> risco
    abertura       DOUBLE,
    maxima         DOUBLE,
    minima         DOUBLE,
    volume         DOUBLE,
    quantidade     DOUBLE,
    src_file       VARCHAR,
    src_line       BIGINT,
    PRIMARY KEY (ticker, data)
);

CREATE TABLE IF NOT EXISTS cobertura_ajuste (
    ticker   VARCHAR PRIMARY KEY,
    status   VARCHAR NOT NULL,  -- AJUSTADO | SEM_EVENTOS | SUSPEITA_NAO_RESOLVIDA
    motivo   VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS evento_corporativo (
    ticker          VARCHAR NOT NULL,
    data_ex         DATE NOT NULL,
    tipo            VARCHAR NOT NULL,
    fator           DOUBLE,
    valor_por_acao  DOUBLE,
    fonte_url       VARCHAR NOT NULL,
    observacao      VARCHAR,
    src_file        VARCHAR,
    src_line        BIGINT
);

CREATE TABLE IF NOT EXISTS evento_suspeito (
    ticker                 VARCHAR NOT NULL,
    data                   DATE NOT NULL,
    sinal                  VARCHAR NOT NULL,
    detalhe                VARCHAR,
    tem_evento_cadastrado  BOOLEAN NOT NULL
);

-- ---------------------------------------------------------------------------
-- Macro (BCB/SGS)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS serie_macro (
    serie       VARCHAR NOT NULL,
    codigo_sgs  INTEGER NOT NULL,
    data        DATE NOT NULL,
    valor       DOUBLE,
    src_file    VARCHAR,
    src_line    BIGINT,
    PRIMARY KEY (serie, data)
);

-- ---------------------------------------------------------------------------
-- Indicadores e sua linhagem
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS indicador (
    entidade       VARCHAR NOT NULL,   -- CNPJ ou ticker
    periodo        VARCHAR NOT NULL,
    indicador      VARCHAR NOT NULL,
    valor          DOUBLE,
    formula        VARCHAR NOT NULL,
    status         VARCHAR NOT NULL,
    motivo         VARCHAR,
    contas_origem  VARCHAR,
    extra_json     VARCHAR,
    PRIMARY KEY (entidade, periodo, indicador)
);

CREATE TABLE IF NOT EXISTS indicador_entrada (
    entidade     VARCHAR NOT NULL,
    periodo      VARCHAR NOT NULL,
    indicador    VARCHAR NOT NULL,
    ordem        INTEGER NOT NULL,
    rotulo       VARCHAR NOT NULL,
    valor        DOUBLE,
    cd_conta     VARCHAR,
    ds_conta     VARCHAR,
    demonstrativo VARCHAR,
    base         VARCHAR,
    versao       VARCHAR,
    ordem_exerc  VARCHAR,
    dt_refer     VARCHAR,
    referencia   VARCHAR,           -- "arquivo.csv:1234"
    src_archive  VARCHAR,
    src_file     VARCHAR,
    src_line     BIGINT,
    src_sha256   VARCHAR,
    PRIMARY KEY (entidade, periodo, indicador, ordem)
);

-- ---------------------------------------------------------------------------
-- Qualidade: identidade contabil, soma de trimestres, reapresentacao
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teste_identidade (
    cnpj                    VARCHAR NOT NULL,
    base                    VARCHAR NOT NULL,
    periodo                 VARCHAR NOT NULL,
    ativo_total             DOUBLE,
    passivo_total           DOUBLE,
    passivo_circulante      DOUBLE,
    passivo_nao_circulante  DOUBLE,
    patrimonio_liquido      DOUBLE,
    residuo_principal       DOUBLE,
    residuo_decomposto      DOUBLE,
    tolerancia              DOUBLE,
    status                  VARCHAR NOT NULL,
    motivo                  VARCHAR,
    src_ativo               VARCHAR,
    src_passivo             VARCHAR,
    PRIMARY KEY (cnpj, base, periodo)
);

CREATE TABLE IF NOT EXISTS reapresentacao (
    cnpj             VARCHAR NOT NULL,
    base             VARCHAR NOT NULL,
    demonstrativo    VARCHAR NOT NULL,
    cd_conta         VARCHAR NOT NULL,
    periodo          VARCHAR NOT NULL,
    ds_conta         VARCHAR,
    valor_as_filed   DOUBLE,
    valor_restated   DOUBLE,
    variacao_pct     DOUBLE,
    dt_refer_as_filed VARCHAR,
    dt_refer_usada   VARCHAR,
    n_publicacoes    INTEGER,
    src_file         VARCHAR,
    src_line         BIGINT
);

-- ---------------------------------------------------------------------------
-- Gatilhos avaliados
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gatilho_resultado (
    gatilho     VARCHAR NOT NULL,
    escopo      VARCHAR NOT NULL,
    entidade    VARCHAR NOT NULL,
    periodo     VARCHAR NOT NULL,
    status      VARCHAR NOT NULL,    -- DISPAROU | NAO_DISPAROU | INDETERMINADO
    explicacao  VARCHAR NOT NULL,
    avaliado_em TIMESTAMP NOT NULL,
    PRIMARY KEY (gatilho, entidade, periodo)
);

-- ---------------------------------------------------------------------------
-- Execucao do pipeline
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execucao (
    executado_em      TIMESTAMP NOT NULL,
    etapa             VARCHAR NOT NULL,
    linhas            BIGINT,
    hash_conteudo     VARCHAR,     -- usado no teste de idempotencia
    observacao        VARCHAR
);
