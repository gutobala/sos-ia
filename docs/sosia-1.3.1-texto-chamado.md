# sos-ia 1.3.1 — Texto do chamado e critério de aceite sem LLM (plano de execução)

Para executar no repositório `D:\Projetos\sos-ia`, depois da 1.3.0 (complemento). **Sem mudança de contrato**: nenhum campo novo;
muda só o conteúdo de `GrupoChamado.texto` e o critério de aceite do roteiro fixo. Versão de correção: `VERSAO_CONTRATO = "1.3.1"`.

## Problema (visto no teste de ponta a ponta do SOS, chamado 5693)
```
OBJETIVO
Ao salvar a alienação do imóvel aparece HTTP 500 e nada é gravado.

PROBLEMAS A CORRIGIR
1. Ao salvar a alienação do imóvel aparece HTTP 500 e nada é gravado.
   Critério de aceite: <texto da resposta de "como reproduzir">
```
1. **Critério de aceite errado sem LLM.** `mock.py` (`contrato()`) e `motor.py` (`contrato_fixo()`, usado quando o LLM falha ou
   está fora) fazem `criterio_aceite = exemplo_concreto or como_reproduzir`. Passos para reproduzir não são critério de aceite.
   Em produção isso acontece sempre que o LLM estiver indisponível — e vira dado ruim para a Fase 3.
2. **O texto do chamado não traz as respostas da entrevista.** `confirmacao.texto()` monta OBJETIVO, requisitos, FORA DE ESCOPO,
   INTEGRAÇÕES e DÚVIDAS, mas não os itens coletados (onde ocorre, como reproduzir, desde quando/quem é afetado, funcionalidade,
   exemplo concreto…). Para a equipe, num corretivo, é o que mais importa — e hoje só existe no banco do sos-ia.

## 1. Critério de aceite no roteiro fixo (mock e `contrato_fixo`)
Por tipo do segmento:
| Segmento | Critério de aceite |
|---|---|
| `CORRETIVO` | "Ao repetir os passos informados, a operação é concluída sem o erro relatado." (fixo; os passos vão para a seção do item 2) |
| `EVOLUTIVO_EXISTENTE` / `EVOLUTIVO_NOVO` com `exemplo_concreto` | "Conferir com o exemplo: " + `exemplo_concreto` |
| sem nada | "Definir com o usuário." |

O mesmo em `mock.py` e `motor.py`: extrair para uma função pura em `roteiros.py` (`criterio_fixo(tipo_segmento, valores)`) e usar
nos dois.

## 2. Seção "INFORMAÇÕES DO PEDIDO" no texto de cada chamado
Em `confirmacao.texto()`, logo depois de OBJETIVO, os itens da entrevista **dos segmentos daquele grupo** (corretivo: segmentos
`CORRETIVO`; evolutivo: `EVOLUTIVO_EXISTENTE` + `EVOLUTIVO_NOVO`), no formato `Rótulo: valor`, na ordem do roteiro
(`roteiros.ROTEIROS`):
```
OBJETIVO
...

INFORMAÇÕES DO PEDIDO
Onde ocorre (sistema, tela ou funcionalidade): Tela de alienação, botão Salvar
O que aconteceu (mensagem, comportamento): aparece HTTP 500 e nada é gravado
Como reproduzir (passo a passo): 1) abrir a alienação; 2) informar o contrato; 3) Salvar
Desde quando e quem é afetado: desde segunda, toda a GEVEN

PROBLEMAS A CORRIGIR
1. ...
```
Regras:
- Fonte: `contrato.segmentos[].slots[]` (`rotulo`, `valor`), já disponível em `monta_grupos(contrato, escopo)`; passar os
  segmentos do grupo para `texto()`.
- Com mais de um segmento no grupo (evolutivo de algo existente + algo novo), um subtítulo por segmento:
  `Parte N - <Alteração no que já existe | Funcionalidade nova>: <resumo>`.
- Omitir o slot `fora_de_escopo` (já está na seção FORA DE ESCOPO, editada pelo usuário) e slots sem valor.
- Cada valor limitado a 600 caracteres no texto (o completo continua nas versões do escopo): o SOS corta o texto inteiro em
  ~3.900 bytes, e o que vem antes não pode empurrar os requisitos para fora.
- Valores com várias respostas juntadas (`"a; b"`) ficam como estão.
- Observação: no modo de PII `local`, os valores estão anonimizados (`[PESSOA]`, `[CPF]`…). É o mesmo que já acontece com o
  objetivo e os requisitos gerados; não tentar desfazer.

## Testes
- `tests/test_confirmacao.py`: texto do corretivo com a seção e os rótulos na ordem do roteiro; evolutivo com 2 segmentos tem os
  2 subtítulos; `fora_de_escopo` não aparece na seção; valor de 2.000 caracteres sai cortado em 600; seção ausente quando nenhum
  slot tem valor.
- `tests/test_mock.py` e `tests/test_motor.py` (LLM falso falhando → `contrato_fixo`): critério do corretivo = frase fixa; do
  evolutivo = "Conferir com o exemplo: ..."; nunca o valor de `como_reproduzir`.

## Pronto quando
- Testes verdes (fora os 2 que dependem da chave de embeddings).
- Com o mock, um corretivo confirmado pelo SOS grava no `DS_SOLICITACAO` a seção INFORMAÇÕES DO PEDIDO e o critério fixo.
- `PASSAGEM_SOS.md` com uma linha sobre a 1.3.1 (o SOS não muda nada: o texto já vem pronto do serviço, e o e-mail do
  solicitante usa o mesmo texto).
