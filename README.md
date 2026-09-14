# Sistema pessoal de suporte à decisão — renda variável B3

Projeto pessoal, usuário único, sem fim comercial.

O sistema é **descritivo, não prescritivo**. Ele expõe dados, séries históricas
e os gatilhos que você configura. Ele não emite recomendação de compra, venda
ou alocação, não calcula score e não calcula preço-alvo. Toda conclusão é sua.

**Prioridade absoluta: rastreabilidade.** Todo número exibido na interface tem
um caminho de volta até `(arquivo, linha)` do arquivo de origem, com a fórmula
e as contas contábeis explícitas.

---

## Estado atual

| Camada | Situação |
|---|---|
| `etl/` | Completa. Não executada contra a CVM nesta instalação (ver **Contratos não confrontados**). |
| `transform/` | Completa. As 6 regras implementadas e testadas. |
| `db/` | Schema DuckDB completo, carga idempotente. |
| `app/` | Streamlit funcional, com drill-down de linhagem. |
| `tests/` | 118 testes offline passando; `tests/contract/` aguarda rede. |

### Contratos não confrontados

Os nomes de coluna dos arquivos da CVM estão declarados em `etl/contracts.py`
a partir do layout publicado pelo órgão, mas **nenhum foi confrontado com um
arquivo real** nesta instalação — o ambiente onde o código foi escrito tem
egresso bloqueado para `dados.cvm.gov.br`, `api.bcb.gov.br` e
`bvmf.bmfbovespa.com.br`. Cada contrato carrega `verificado_em = None` para
marcar isso.

Consequência prática e desenho deliberado: o pipeline **falha alto** se o
arquivo real divergir do contrato, em vez de assumir. Coluna faltando é erro;
coluna nova não declarada também é erro (campo novo silenciosamente ignorado é
a origem clássica de número errado).

Primeira coisa a fazer na sua máquina:

```bash
pytest -m live tests/contract -v
```

Isso baixa os arquivos reais, compara coluna a coluna e diz exatamente onde o
contrato diverge. Ajuste `etl/contracts.py` conforme o resultado e preencha
`verificado_em`. Até esse passo, trate os contratos como hipótese.

---

## Do zero até a tela, no macOS

Três comandos. Abra o **Terminal** (⌘+Espaço, digite "Terminal") e cole um de
cada vez:

```bash
git clone https://github.com/fgadados/Desktop.git b3-dss
cd b3-dss
git checkout claude/b3-decision-support-system-nf408g
```

```bash
./comecar.sh
```

O navegador abre sozinho com a interface. É isso.

O que `./comecar.sh` faz: procura Python 3.11+, cria o ambiente virtual em
`.venv/`, instala as dependências, monta o cenário de demonstração e abre o
Streamlit. Se faltar Python, ele para e diz como instalar — não segue pela
metade. Para encerrar, `Ctrl+C` na janela do Terminal.

**Se `git` não estiver instalado**, o macOS oferece instalar as Ferramentas de
Linha de Comando do Xcode na primeira vez que você digitar `git`; aceite e
repita o comando. Alternativa sem git: baixe o ZIP do branch pelo botão
**Code → Download ZIP** na página do repositório no GitHub, descompacte, e
rode `./comecar.sh` dentro da pasta.

### Os três modos

| Comando | O que faz |
|---|---|
| `./comecar.sh` | Cenário sintético, sem rede. Para ver o formato das telas. |
| `./comecar.sh teste` | Roda os 118 testes e para. Sem rede. |
| `./comecar.sh real` | Baixa CVM, B3 e BCB de verdade e abre a interface sobre o dado real. |

`./comecar.sh real` roda antes a conferência dos contratos de schema contra os
arquivos reais da CVM. Se divergir, ele **para** e mostra qual coluna mudou —
ver **Contratos não confrontados** acima. Isso é proposital: seguir com um
contrato errado produziria número errado em silêncio.

### Uso avançado (comandos individuais)

```bash
source .venv/bin/activate
python run.py demo           # cenário sintético, sem rede
python run.py extrair        # baixa brutos das fontes oficiais (idempotente)
python run.py transformar    # aplica as 6 regras e carrega o DuckDB
python run.py diagnostico    # estado do cache, contratos e gatilhos
B3DSS_DB=data/demo.duckdb streamlit run app/main.py
```

O conjunto de acompanhamento fica em `config/tickers.yml`. Comece pequeno.

### Onde está o dado

Enquanto `python run.py extrair` não rodar com rede aberta, **não há dado
nenhum** — `data/b3dss.duckdb` nem existe. O repositório contém a máquina, não
o resultado; brutos e banco são gerados localmente e ficam fora do controle de
versão (`.gitignore`).

`python run.py demo` existe para responder "o que este sistema mostra?" sem
esperar download: ele monta um cenário sintético no layout da CVM, roda as 6
regras, calcula os indicadores e imprime a saída — incluindo a cadeia de
rastreabilidade completa (conta, descrição, versão, ordem de exercício, base
contábil e `arquivo:linha`). Os números são inventados e o comando avisa isso
na primeira linha. O banco vai para `data/demo.duckdb`, separado do de
produção.

---

## Fontes e periodicidade

| Fonte | O que traz | Periodicidade | Defasagem típica |
|---|---|---|---|
| [CVM DFP](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/) | Demonstrações anuais | Anual | Até 3 meses após o fim do exercício |
| [CVM ITR](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/) | Demonstrações trimestrais (T1–T3) | Trimestral | Até 45 dias após o fim do trimestre |
| [CVM FCA](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/) | `Codigo_Negociacao` por CNPJ (de-para) | Anual, com reapresentações | Variável |
| [CVM CAD](https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/) | Cadastro, `SETOR_ATIV`, situação | Contínua | Diária |
| [CVM IPE](https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/) | Fatos relevantes e comunicados | Contínua | Diária |
| B3 COTAHIST | Cotações históricas, layout fixo 245 bytes | Anual (arquivo por ano) | Arquivo do ano corrente atualiza ao longo do ano |
| [BCB/SGS](https://api.bcb.gov.br) | Selic diária (11), IPCA mensal (433), dólar venda (1) | Diária / mensal | 1 a 10 dias úteis |

Nenhum site de terceiro é acessado. Não há scraping de Fundamentus,
StatusInvest ou similares.

O **de-para CNPJ → ticker** sai do FCA da própria CVM (tabela
`valor_mobiliario`), cruzado com o universo de códigos efetivamente negociados
no COTAHIST. Divergência dos dois lados vira relatório, não é resolvida em
silêncio.

---

## As seis regras de tratamento

Cada uma tem arquivo de teste próprio. São os pontos onde um pipeline da CVM
produz número errado sem reclamar.

### 1. Ticker não existe nos arquivos da CVM — `transform/depara.py`
A chave é o CNPJ. O vínculo com o ticker vem do FCA, é datado
(`Data_Inicio_Negociacao` / `Data_Fim_Negociacao`) e é confirmado contra o
COTAHIST. Ticker apontando para dois CNPJs levanta erro em vez de escolher um.
→ `tests/test_regra1_depara.py`

### 2. Coluna `VERSAO` — `transform/dedup.py`
Retém apenas a versão máxima por (CNPJ, período, documento, demonstrativo,
base). A comparação é **inteira**, não lexicográfica: `"10"` tem que vencer
`"9"`. As versões descartadas ficam registradas e aparecem na interface.
→ `tests/test_regra2_versao.py`

### 3. Coluna `ORDEM_EXERC` — `transform/dedup.py`
`ÚLTIMO` e `PENÚLTIMO` duplicam cada linha; somar sem filtrar dobra tudo. O
padrão é `ÚLTIMO`. O `PENÚLTIMO` é retido de propósito na etapa de
reapresentação, que é onde ele serve.
→ `tests/test_regra3_ordem_exerc.py`

### 4. `CON` vs `IND` — `transform/basis.py`
**Base fixa por empresa.** Para cada companhia conta-se a cobertura em CON e em
IND; vence a maior, empate vai para CON. Período sem a base escolhida fica
faltando — não há fallback período a período, porque alternar base dentro da
série é o que quebra a comparabilidade de um percentil de 10 anos. A base
escolhida, o critério, as duas coberturas e o número de períodos perdidos
aparecem na interface.
→ `tests/test_regra4_con_ind.py`

### 5. Períodos no ITR — `transform/periods.py`
As janelas são classificadas por duração (3, 6, 9, 12 meses). Só o trimestre
isolado entra na série trimestral. O 4º trimestre não existe no ITR e é
derivado:

```
Q4_isolado = DFP_anual(12M) − ITR_acumulado(9M)
```

A derivação guarda a linhagem dos **dois** insumos em `src_derivacao` (JSON com
arquivo, linha e valor de cada lado). Falta um dos lados → não há Q4, e ponto.

O início do exercício social **não é assumido como 1º de janeiro**: ele é lido
da linha de maior janela de cada documento (o acumulado sempre começa no
primeiro dia do exercício), com âncora por companhia para documentos que só
trazem o trimestre isolado. `ano_exercicio` é o ano civil em que o exercício
começa.

Saldo patrimonial (BPA/BPP) não tem período próprio: herda o trimestre do
documento. O balanço de uma DFP é publicado sob os dois rótulos — `2023` e
`2023T4` — para que a série anual case com a DRE anual e a trimestral com o Q4.

Há também uma conferência: `T1+T2+T3` tem que reproduzir o acumulado de 9
meses. Divergência é sinalizada, nunca corrigida.
→ `tests/test_regra5_periodos.py`

### 6. Plano de contas setorial — `transform/sector.py` + `config/sector_plans.yml`
Bancos e seguradoras não seguem o plano industrial. Cada plano mapeia
CONCEITO → códigos candidatos da CVM e declara o que **não se aplica**, com
motivo. Indicador que depende de conceito inaplicável aparece na interface como
`NAO_SE_APLICA` e o motivo é exibido. Nenhum número é forçado.

Há uma checagem de sanidade: se o código existir mas a descrição da conta no
arquivo não casar com o padrão esperado, o resolvedor devolve
`DIVERGENCIA_LAYOUT` em vez de aceitar o número calado.

Empresa cujo `SETOR_ATIV` não classifica cai no plano `INDEFINIDO`, que declara
quase tudo como inaplicável — o sistema prefere não exibir número a exibir
número incomparável. Corrija em `data/manual/sector_overrides.csv`.
→ `tests/test_regra6_setor.py`

---

## Ajuste de preços

O COTAHIST não é ajustado por evento corporativo. Duas séries são construídas:

| Série | Ajuste | Uso |
|---|---|---|
| `fech_aj_split` | Só desdobramento, grupamento e bonificação | P/L, P/VP, EV/EBITDA |
| `fech_aj_total` | Quantidade **e** proventos reinvestidos | Retorno, volatilidade, drawdown, correlação |

A separação é o ponto. Dividendo não altera a quantidade de ações; descontá-lo
do preço deprimiria artificialmente o múltiplo histórico de pagador de
dividendo — exatamente o erro que se quer evitar. Já o retorno total sem
dividendo subestima o resultado do papel.

Convenção: ajuste retroativo. O último preço da série é o preço negociado; os
anteriores são multiplicados pelo produto das razões de todos os eventos
posteriores a eles, com `razao(D) = (1 − provento(D)/fech(D−1)) / fator(D)`.

`FATCOT` (fator de cotação do layout da B3) é aplicado antes de tudo, para que
preço por 1 ação e preço por 1000 ações não se misturem na mesma série.

### Cobertura

Não existe série "ajustada por padrão". Cada ticker recebe um status:

- `AJUSTADO` — eventos cadastrados aplicados;
- `SEM_EVENTOS` — nenhum evento cadastrado; a série é o preço negociado;
- `SUSPEITA_NAO_RESOLVIDA` — há pregão com salto compatível com evento não
  cadastrado (queda ≥ 30%, mudança de `FATCOT` ou troca de ISIN).

Nos dois últimos casos a interface bloqueia a leitura do múltiplo histórico com
aviso explícito. O detector de suspeitas **nunca aplica nada**: ele produz a
lista de pendências que você resolve em `data/manual/corporate_events.csv`.

---

## Camada analítica

- **Múltiplo em percentil e z-score do próprio histórico**, janelas de 5 e 10
  anos. Janela com menos de 250 observações não produz número. Não há card de
  múltiplo absoluto.
- **DuPont**: `ROE = margem_líquida × giro_do_ativo × alavancagem`. Os quatro
  são calculados e a identidade é verificada; a diferença fica em `extra`.
- **Cobertura do dividendo por fluxo de caixa livre** (`FCL = FCO + FCI`), não
  por lucro contábil. O payout sobre lucro é exibido junto, como contraste.
- **Risco**: volatilidade anualizada (252 pregões), drawdown máximo com as
  datas de pico e fundo, matriz de correlação com a contagem de observações em
  comum por par (célula com menos de 60 pregões em comum fica vazia).
- **Gatilhos** em `config/triggers.yml`, definidos por você. Avaliação sem
  `eval`: operadores são um dicionário fechado. Quando um gatilho dispara, a
  interface mostra a condição satisfeita e os números que a satisfizeram.
  Métrica ausente devolve `INDETERMINADO` com motivo — nunca `falso`.

---

## Decisões de modelagem que afetam comparabilidade histórica

Registradas aqui porque mudam os números.

| Decisão | Escolha | Efeito colateral aceito |
|---|---|---|
| Ajuste de preço | Duas séries separadas (split / total) | Duas colunas a manter; uso errado da coluna errada é possível, então a interface rotula qual usa qual |
| Reapresentação | **Restated + flag**: a série usa o valor publicado mais recentemente | O histórico muda quando uma DFP nova reapresenta período antigo. Por isso `divergente` e `n_publicacoes` acompanham cada fato, e o valor `as-filed` é preservado |
| CON/IND | **Base fixa por empresa**, escolhida por cobertura | Períodos que só existem na outra base viram ausência. `periodos_perdidos` é exibido |
| EBITDA | **Obrigatório**: `EBITDA = EBIT + D&A`, com D&A localizada por descrição dentro de `6.01.*` da DFC indireta | Empresa sem D&A identificável fica sem EV/EBITDA. EV/EBIT **não** é calculado como substituto |

---

## Critérios de aceite

| Critério | Onde é verificado |
|---|---|
| Rodar o pipeline duas vezes não altera o resultado | `tests/test_idempotencia.py` — hash por tabela, estável à ordem das linhas; download revalida por ETag/Last-Modified; carga substitui, nunca acrescenta |
| Identidade contábil com tolerância explícita | `tests/test_identidade_contabil.py` — tolerância em `etl/config.py` (`1e-6` relativa, piso de R$ 1,00) |
| Toda linha de indicador carrega contas de origem e fórmula | `tests/test_linhagem.py` + `tests/test_ponta_a_ponta.py`. É garantia de **tipo**: `lineage.Indicador` recusa status `OK` sem fórmula e sem ao menos uma entrada rastreada |
| Cada empresa exibe a data e a versão do último documento CVM | Tabela `empresa`, colunas `ultimo_doc_*`, exibidas na barra lateral |

### Sobre a identidade contábil

Um detalhe do plano da CVM que muda o teste: a conta `2` é **Passivo Total** e
**já inclui** o patrimônio líquido (`2.03`). Escrever `1 == 2 + 2.03`
produziria erro sistemático do tamanho do PL em toda empresa do mercado. O
teste correto é duplo:

```
principal:   Ativo Total (1) == Passivo Total (2)
decomposta:  Passivo Total (2) == PC (2.01) + PNC (2.02) + PL (2.03)
```

A segunda é a leitura de "Ativo = Passivo + Patrimônio Líquido" nos códigos
reais do arquivo.

---

## Limitações conhecidas

1. **Contratos de schema não confrontados com arquivo real.** Ver acima. É a
   limitação mais importante e a primeira a resolver.

2. **Quantidade de ações em circulação não é ingerida.** Sem ela, `P/L` e
   `P/VP` não são calculáveis — e não são estimados. As demonstrações da CVM
   não trazem o número de ações em formato estruturado confiável. Duas saídas,
   nenhuma implementada ainda: (a) preencher
   `data/manual/shares_outstanding.csv`; (b) usar `3.99.01.01` (Lucro por Ação
   Básico) da DRE para `P/L` sem passar por valor de mercado — o que resolve
   `P/L` mas não `P/VP`. Hoje o percentil histórico roda sobre a série de preço
   ajustado; os múltiplos ficam como faltando.

3. **Eventos corporativos dependem de entrada manual.** Não há fonte oficial
   estruturada e de acesso permissivo com data-ex e valor por ação. O detector
   de suspeitas reduz o trabalho, mas não o elimina. Enquanto a tabela estiver
   vazia, a série fica `SEM_EVENTOS`.

4. **Moeda diferente de real é excluída, não convertida.** Converter exigiria
   escolher taxa e data — decisão de modelagem que muda comparabilidade. As
   linhas ficam num relatório à parte.

5. **Derivação do Q4 propaga reapresentação.** Se a DFP anual e o ITR de 9
   meses foram publicados sob premissas diferentes, o Q4 derivado carrega a
   diferença. A conferência `T1+T2+T3 vs 9M` existe para tornar isso visível,
   mas não conserta.

6. **Percentil histórico exige história.** Empresa com menos de 5 anos de
   pregão não produz percentil de 5 anos. Isso aparece como ausência, e a
   janela de 10 anos frequentemente fica vazia para listagens recentes.

7. **`GRUPO_DFP` não é usado para desambiguar.** A separação CON/IND vem do
   nome do arquivo dentro do ZIP. Se a CVM mudar essa convenção, o parser
   levanta erro em vez de adivinhar.

8. **IPE entra como contexto datado, não como sinal.** O conteúdo dos fatos
   relevantes não é interpretado — só listado por data.

9. **Plano `ENERGIA` hoje apenas herda o industrial.** Existe separado para
   permitir tratar ativo financeiro de concessão no futuro sem mexer no plano
   industrial.

10. **Sem autenticação, sem multiusuário, sem cloud.** Por desenho. O banco é
    um arquivo local em `data/b3dss.duckdb`.

---

## Layout

```
etl/          extração e parsing por fonte, idempotente, com cache local
  contracts.py      contratos de schema — falham alto, não assumem
  provenance.py     manifesto de download e colunas src_* linha a linha
  http_cache.py     download condicional (ETag/Last-Modified), rename atômico
  cvm_*.py          DFP, ITR, FCA, cadastro, IPE
  b3_cotahist.py    parser posicional de 245 bytes
  b3_eventos.py     eventos corporativos + detector de suspeitas
  bcb_sgs.py        Selic, IPCA, câmbio
transform/    normalização, de-para, cálculo de indicadores
  money.py          escala UNIDADE/MILHAR → reais
  dedup.py          regras 2 e 3
  periods.py        regra 5
  basis.py          regra 4
  depara.py         regra 1
  sector.py         regra 6
  restatement.py    política restated + flag
  prices.py         duas séries ajustadas
  indicators.py     DuPont, EBITDA, FCL, percentil
  risk.py           volatilidade, drawdown, correlação
  triggers.py       gatilhos YAML, sem eval
  validations.py    identidade contábil
  lineage.py        tipo que torna linhagem obrigatória
  pipeline.py       ordem de aplicação das regras
db/           schema DuckDB e carga idempotente
app/          Streamlit
tests/        testes das 6 regras + aceite; tests/contract/ exige rede
config/       tickers.yml, triggers.yml, sector_plans.yml
data/manual/  entradas mantidas por você (ver data/manual/LEIA-ME.md)
```

## Testes

```bash
pytest                        # 118 testes offline, sem rede
pytest -m live tests/contract # confronta contratos com arquivo real da CVM
```
