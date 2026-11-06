# Laboratório — regra documentada no motor real (pedido para a máquina pessoal)

Origem: pedido 1.3.2 do SOS (`docs/sosia-1.3.2-regra-documentada.md`, parte 1). A parte 2 (concordância no texto) já foi feita no
produto. Este é um **experimento**: o produto só recebe a mudança de prompt se ela passar nos critérios do fim.

Base de código: `main` do GitHub no commit `7812d3b` ou depois (o prompt e o motor do produto são os mesmos desde então).

## 1. O que já se sabe (investigado no produto em 06/11/2026)
Teste de ponta a ponta do SOS, motor real, `SOSIA_PII_MODO=local`, sistema GAI, `gpt-5.4-mini`. Relato (2 entrevistas iguais):

> Nao consigo emitir o boleto bancario da alienacao do prestamista 2023/0110. O sistema bloqueia a emissao do boleto e nao gera nada.

A regra esperada é a **RN13 — Bloqueio de emissão de Boleto Bancário para Alienação de Prestamista inscrito no SERASA**
(`sistemas:gai:documentacao_tecnica:regras_de_negocio:inicio`, `apf.conhecimento_trecho.id = 35` no banco do produto).

**A busca não é o problema.** Os 7 trechos que o `rag()` passou ao prompt, nas duas entrevistas:

| # | tipo | cos | página / seção |
|---|---|---|---|
| 1 | regras_de_negocio | 0,602 | regras_de_negocio / RN239. Verifica Bloqueio e Liberação da Alienação para Emissão de Boleto |
| 2 | user_story | 0,598 | us016 / Protótipo: Tela de seleção de alienação para emissão de boleto |
| 3 | caso_de_uso | 0,595 | uc110 / FE5 Alienação Bloqueada |
| 4 | caso_de_uso | 0,584 | uc066 / Informar Bloqueio/Desbloqueio de Emissão de Boleto, FE1 |
| 5 | caso_de_uso | 0,559 | uc046 / FA2 Emitir Boleto por Alienação |
| 6 | regras_de_negocio | 0,601 | regras_de_negocio / **RN13** (SERASA) |
| 7 | regras_de_negocio | 0,590 | regras_de_negocio / RN85. TXT Banco Daycoval |

**O LLM não apontou.** O evento `llm_entrevista` veio com `regra_documentada_trecho = 0` e orientação vazia nas duas. A regra
fixa (`motor.aplica`) nem chegou a ser testada.

**Causa provável: o prompt.** `prompts/entrevista_v1.md`, linhas 73–76: a regra só deve ser indicada se "um trecho de regra de
negócio acima **explica claramente** o comportamento que o usuário relatou". A RN13 descreve a **causa** (prestamista no SERASA),
o relato descreve o **efeito** (boleto bloqueado), e o usuário não disse nada sobre SERASA: o LLM preferiu 0.

**Repetido com acentos** (06/11/2026, a pedido do SOS: os relatos do passo 6 foram escritos sem acento de propósito, pelo script
de teste em ASCII; o caminho SOS → serviço preserva os acentos, conferido): "Não consigo emitir o boleto bancário da alienação do
prestamista 2023/0110. O sistema bloqueia a emissão do boleto e não gera nada."
- A busca deu praticamente os mesmos 7 trechos (RN239 em [1] com cos 0,604; RN13 de novo em [6] com 0,601).
- Desta vez o LLM **apontou** o trecho 1 (**RN239**), com a orientação "Pela regra documentada, o sistema verifica se a alienação
  está bloqueada ou liberada para emissão de boleto em alguns tipos de baixa. Se a regra precisa mudar…".
- Leitura: a busca está estável; **a decisão do LLM não**. Sem acento: 0 nas 2 execuções; com acento: RN239 em 1 execução.
  Isso reforça medir com repetições, e a questão RN13 × RN239 do item 2.2 deixa de ser hipotética. A orientação da RN239 também
  ficou genérica ("em alguns tipos de baixa"), sem explicar a ligação com o relato.

Controle negativo (deve continuar **sem** regra): "O sistema nao deixa alterar o contrato de alienacao depois que a primeira
parcela foi paga…" — não existe RN sobre isso no GAI.

## 2. O que testar
1. **Prompt (principal).** Variante do bloco da regra documentada que aceite uma regra que **pode ser a causa** do efeito relatado
   (bloqueio, mensagem, botão desabilitado), desde que a ligação seja específica (mesma operação e mesmo objeto: "emitir boleto da
   alienação"), e que peça uma orientação **condicional** que explique a ligação, por exemplo:
   "Se a alienação estiver inscrita no SERASA, a regra RN13 bloqueia a emissão do boleto. Se for o seu caso e a regra precisa
   mudar, o pedido é uma melhoria." Manter a saída: um único trecho (o mais específico) ou 0.
2. **Escolha entre regras concorrentes.** Aqui há duas regras de bloqueio plausíveis (RN13 e RN239). Ver qual a variante aponta e
   se a orientação faz sentido para o usuário. Apontar a RN239 também pode ser aceitável; registrar.
3. **Só se o prompt não bastar:** busca (BM25 para regras/mensagens no corretivo, ou `LIMIT` maior só para esse tipo). Neste caso
   a RN13 já veio, então a busca deve ficar para depois.

Não mudar o que é determinístico: só trecho vindo do RAG, só tipos `regras_de_negocio` / `mensagens` / `caso_de_uso`, só no
`PAROU_DE_FUNCIONAR`, só uma vez, segmento fica CORRETIVO até o usuário decidir (`POST /regra`).

## 3. Gabarito
- **16 a 20 relatos de "bloqueio / mensagem / não deixa"**, metade com regra documentada que explica (causa ou efeito) e metade
  sem. Começar pelo GAI; se der, 1 ou 2 outros sistemas com wiki de regras.
- Fonte: chamados reais anonimizados (corretivos do SOS em que a solução citou uma RN, ou em que a fábrica respondeu "o sistema
  funciona conforme a regra"), reescritos como o usuário escreveria, **sem citar a causa** e **com acentos** (como a tela do
  SOS envia). Incluir o boleto 2023/0110 e a parcela paga; para esses dois, rodar também a versão sem acento, para ver se isso
  muda a decisão.
- Para cada relato: a regra certa (página + seção) ou "nenhuma". Rotular à mão; quando houver dúvida, marcar e deixar fora da
  métrica.
- Rodar com os **trechos do RAG reais** de cada relato (para medir o sistema inteiro) e, à parte, com os trechos fixos (para
  isolar o efeito do prompt).
- 3 repetições por relato (o LLM varia); contar a decisão por maioria e anotar a instabilidade.

## 4. Métricas e critérios para trazer ao produto
| Métrica | Como contar | Critério |
|---|---|---|
| Falso alarme | relatos "sem regra" em que alguma regra foi apontada | **≤ 1 em 10** (mais importante: atrapalha um corretivo legítimo) |
| Sensibilidade | relatos "com regra" em que a regra certa (ou uma aceitável) foi apontada | maior que a do prompt atual (medir o atual também) |
| Orientação | as apontadas têm orientação condicional que explica a ligação | todas |
| Casos do SOS | boleto 2023/0110 → RN13 (ou RN239, se aceitável); parcela paga → nenhuma | os dois, na maioria das 3 repetições |
| Estabilidade | relatos em que as 3 repetições não concordam | relatar; menor que a do prompt atual |

Custo esperado: ~US$ 0,005 por chamada com o `gpt-5.4-mini`; 20 relatos × 3 repetições × 2 prompts ≈ US$ 0,60.

## 5. O que trazer de volta (só se passar)
- O novo bloco do prompt (`prompts/entrevista_v1.md`, ou um `entrevista_v2.md` se a mudança for grande).
- O resultado em `dados/` (como `dados/regra_documentada_gabarito.txt`): prompt atual × novo, as quatro métricas, a lista dos
  relatos com acerto/erro e o custo. **Sem dado pessoal**: relatos reescritos e anonimizados.
- O script de medição em `migracao/` (por exemplo `migracao/mede_regra_documentada.py`), para repetir no produto.
- Se mudar a busca, a justificativa medida e os testes.

No produto, entram também: um teste com o LLM falso (relato de efeito + trecho de causa no RAG falso → regra apontada) e um teste
com o banco real que confira que o relato do boleto traz a RN13 entre os trechos passados ao prompt.
