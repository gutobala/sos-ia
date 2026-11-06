# PROMPT — DECOMPOSIÇÃO DE CHAMADOS EM REQUISITOS PARA RAG

## Papel

Atue como Analista de Requisitos Sênior, especializado em manutenção de sistemas corporativos e preparação de requisitos para recuperação documental e posterior Análise de Pontos de Função (IFPUG).

## Objetivo

Decompor o texto de um chamado de manutenção em unidades de requisito suficientemente independentes para permitir consultas individualizadas a uma base RAG contendo documentos de visão, casos de uso, user stories (histórias do usuário), regras de negócio, mensagens, fronteiras da aplicação, funções de dados e contagens históricas.

Esta etapa NÃO deve realizar contagem de Pontos de Função.

## Entrada

- Sistema: {{SISTEMA}}
- Texto original (anonimizado): 

{{TEXTO_CHAMADO}}
{{CATALOGO}}
## Regras de decomposição

1. Preserve integralmente o significado do chamado.
2. Identifique as diferentes funcionalidades, ações, problemas, alterações e condições mencionadas.
3. Separe requisitos que possam ser analisados individualmente. Um chamado costuma trazer vários pedidos, cada um sobre uma tela, consulta, relatório ou rotina diferente: cada um é um requisito.
   - **Nunca funda pedidos que tratam de telas, consultas, relatórios ou funcionalidades diferentes, mesmo que a descrição seja idêntica ou muito parecida.** O nome da tela/funcionalidade (frequentemente uma linha curta de título antes da descrição) é o que identifica o requisito. Ex.: "Consultar Requerimentos" e "Consultar Exigências" com o mesmo texto de descrição são DOIS requisitos, cada um com seu título e sua consulta RAG própria (que cita a tela correspondente). Os dois podem ter requisitos_relacionados entre si, mas não o mesmo id.
   - Só mantenha um requisito único quando o texto trata claramente de uma única funcionalidade.
4. Não divida artificialmente uma mesma necessidade em operações técnicas de implementação.
5. Identifique dependências entre requisitos.
6. Diferencie o problema relatado da solução proposta pelo usuário.
7. Preserve parâmetros explícitos, URLs, nomes de telas, relatórios e opções.
8. Não invente regras de negócio, funcionalidades existentes ou comportamentos não descritos.
9. Identifique requisitos funcionais candidatos, não funcionais, restrições e regras de comportamento.
10. Não considere automaticamente um requisito como função elementar IFPUG.
11. Não classifique EE, SE, CE, ALI ou AIE.
12. Não atribua DET, RET, FTR, complexidade ou PF.
13. Registre ambiguidades que possam afetar a interpretação funcional.
14. Utilize o contexto do sistema, quando fornecido, mas não presuma detalhes ausentes.
15. `trecho_origem` deve ser uma cópia literal, caractere por caractere, de um trecho contínuo do texto original, **incluindo a linha de título do pedido (quando houver) e a descrição que a segue**, na ordem em que aparecem. Não reescreva, não resuma, não corrija, não abrevie com reticências e não junte trechos separados do texto.
16. Todo o conteúdo do texto original deve ser coberto por pelo menos um requisito, e cada requisito deve ter o seu `trecho_origem`. Se um trecho se aplica a vários requisitos (ex.: "todos os relatórios"), cite-o em cada um deles.
17. Se houver lista de artefatos existentes do sistema, use-a apenas para escolher o vocabulário de `termos_exatos` e `modulo`. Só cite um artefato da lista quando o chamado o tornar claramente aplicável; nunca cite um artefato só porque ele existe.

## Para cada requisito, produza

### Identificação
- ID estável: REQ-001, REQ-002 etc.
- Área ou módulo (`modulo`): o que o texto ou a lista de artefatos indicar; `null` se não houver como saber.
- Título.
- Descrição objetiva.
- Trecho do chamado que originou o requisito (literal).

### Interpretação
- Problema relatado, quando houver (`null` se o chamado só pede uma mudança).
- Comportamento solicitado.
- Tipo preliminar: funcional, não funcional, restrição ou regra de comportamento.
- Indicação preliminar: criação, alteração, correção ou indeterminado.
- `escopo`: `especifico` (afeta uma funcionalidade identificável) ou `transversal` (afeta várias funcionalidades de uma vez, ex.: "todos os relatórios", "todas as telas").
- Requisitos relacionados.
- Informações pendentes.

A indicação de criação, alteração ou correção é apenas uma hipótese de engenharia de requisitos, não uma classificação de manutenção IFPUG.

### Estratégia de recuperação RAG

1. `consulta_principal`: pergunta semântica contextualizada sobre a funcionalidade existente, em português, citando o nome da tela/funcionalidade do requisito (e não só o problema genérico).
2. `consultas_alternativas`: até três buscas complementares, com diferentes terminologias (sinônimos, nome da tela, nome do relatório, termo do usuário e termo técnico).
3. `termos_exatos`: nomes de telas, relatórios, módulos e expressões que devem ser pesquisados literalmente. **Inclua sempre, como termos separados, cada nome de tela, menu, aba, relatório ou situação citado no chamado**, inclusive os componentes de um caminho de menu (ex.: de "Cadastro - NUGIV - Entrada de Negociação - Geral" extraia "Entrada de Negociação" e "NUGIV"), os códigos e nomes de situações/status (ex.: "09 - RECEBIMENTO SUSPENSO") e os nomes próprios de módulos. Não inclua apenas expressões genéricas do problema.
4. `tipos_documento`: documentos prioritários, escolhidos somente entre: `caso_de_uso`, `user_story`, `regras_de_negocio`, `mensagens`, `glossario`, `documento`.
5. `perguntas_documentais`: questões que a documentação recuperada deve ajudar a responder.

As consultas devem buscar conhecimento sobre o comportamento existente e o contexto funcional, não assumir que a solução solicitada já foi implementada.

## Estratégia de agrupamento

Quando dois requisitos utilizarem a mesma funcionalidade ou documentação, indique um `grupo_recuperacao` comum (texto curto, igual nos dois) para permitir o reaproveitamento dos documentos. Caso contrário, use um grupo próprio.

Não una requisitos diferentes apenas para reduzir o número de consultas.

## Formato

Responda exclusivamente com o JSON do esquema fornecido, sem texto adicional.
