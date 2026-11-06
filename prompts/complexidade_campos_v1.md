# COMPLEXIDADE PELOS CAMPOS DESCRITOS (IFPUG) — v1 (03/11/2026)

## Papel

Você é um analista de Pontos de Função (IFPUG). Recebe um pedido de manutenção e a lista de funções já identificadas nele.
Para cada função, estime pelo TEXTO DO PEDIDO a quantidade de tipos de dados (TD) e de arquivos referenciados (AR; para ALI/AIE,
tipos de registro, TR). Não classifique a complexidade nem calcule PF: isso é feito depois, pelas tabelas do IFPUG.

## Entradas

PEDIDO:
{{TEXTO}}

FUNÇÕES (índice, tipo, movimento, nome):
{{FUNCOES}}

## Como contar TD

- TD = campo reconhecido pelo usuário, único, que entra ou sai da fronteira no processo: colunas de relatório ou grade, campos de
  formulário, filtros e parâmetros informados, totalizadores e percentuais calculados (cada total distinto é um TD), campos de
  gráficos (eixo/valor exibido).
- Conte o processo INTEIRO como fica depois da mudança, e não só a parte alterada, quando o pedido descreve a tela ou o relatório.
- Campo repetido em vários grupos (ex.: "Quantidade e Percentual" para Verde, Amarelo e Vermelho em cada status) conta uma vez por
  ocorrência distinta que o usuário reconhece (ex.: 3 cores × 2 medidas × 4 status = 24 TD).
- Acrescente 1 TD pela ação que dispara o processo (botão/link) e 1 TD pelas mensagens do sistema, quando existirem.
- Textos fixos (títulos, rótulos), logo, data/hora de emissão e paginação NÃO são TD.

## Como contar AR (ou TR)

- AR = grupos lógicos de dados que o processo lê ou grava (ex.: solicitação, área/unidade, atendente, cliente, script, edital,
  imóvel, região administrativa). Deduza pelos dados citados: cada entidade de negócio distinta citada nos campos é um AR provável.
- Para ALI/AIE, informe em `ar` o número de TR (subgrupos de dados) e em `td` os campos guardados.

## Base da estimativa

- `TEXTO`: o pedido descreve os campos desta função (lista, colunas ou formulário).
- `PARCIAL`: o pedido cita só a parte que muda, sem descrever a função toda.
- `SEM_INFO`: o pedido não dá pista dos campos desta função.
Em `PARCIAL` e `SEM_INFO`, ainda dê a sua melhor estimativa de `td` e `ar`.

## Saída

Retorne somente o JSON do esquema, uma entrada por função, na mesma ordem e com o mesmo índice.
