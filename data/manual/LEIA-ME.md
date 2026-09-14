# Entradas mantidas a mao

Três arquivos que **você** preenche. Eles existem porque a informação não está
disponível em fonte oficial estruturada, e o projeto proíbe estimar ou raspar
site de terceiro. Enquanto estiverem vazios, os indicadores que dependem deles
aparecem como **faltando** — nunca preenchidos por aproximação.

## `corporate_events.csv`

Desdobramento, grupamento, bonificação e proventos. Sem isto, `fech_aj_split`
é igual ao preço negociado e toda série de P/L e P/VP fica marcada como
`SEM_EVENTOS`.

| coluna | conteúdo |
|---|---|
| `ticker` | código de negociação, ex. `WEGE3` |
| `data_ex` | `YYYY-MM-DD`, primeiro pregão **sem** direito ao evento |
| `tipo` | `DESDOBRAMENTO`, `GRUPAMENTO`, `BONIFICACAO`, `DIVIDENDO`, `JCP`, `RENDIMENTO` |
| `fator` | razão ações_depois/ações_antes. Desdobra 1:2 → `2.0`. Grupa 10:1 → `0.1`. Só para evento de quantidade. |
| `valor_por_acao` | R$ por ação, bruto. Só para provento. |
| `fonte_url` | **obrigatório**. Onde você leu o evento. |
| `observacao` | livre |

O sistema recusa a linha se faltar `fonte_url`, se um evento de quantidade vier
sem `fator`, ou se um provento vier sem `valor_por_acao`.

Para saber o que preencher: a aba **Qualidade dos dados** lista os pregões com
salto de preço compatível com evento não cadastrado (`evento_suspeito`). Essa
lista é o seu roteiro de trabalho.

## `sector_overrides.csv`

Força o plano de contas de uma companhia quando o `SETOR_ATIV` do cadastro da
CVM não classifica direito (holding que na prática é banco, por exemplo).

| coluna | conteúdo |
|---|---|
| `cnpj` | 14 dígitos ou formatado, tanto faz |
| `plano` | `INDUSTRIAL`, `FINANCEIRO`, `SEGURADORA`, `ENERGIA`, `INDEFINIDO` |

## `shares_outstanding.csv`

Quantidade de ações em circulação por data. **Ainda não implementado no
pipeline** — veja "Limitações conhecidas" no README. P/L e P/VP dependem dele.
