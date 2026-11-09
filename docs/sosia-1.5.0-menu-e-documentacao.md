# Pedido do SOS ao sos-ia: menus dos sistemas e documentação relacionada (contrato 1.5.0)

Para a sessão do sos-ia (`D:\Projetos\sos-ia`). Data: 08/11/2026. Base: contrato 1.4.1. **Só acréscimos:** a tela atual
continua funcionando sem os campos novos.

## Por quê
No teste com usuário (GOP, relato "o sistema deve aceitar o desconto de 25% também no segundo edital nos controles de
operação de venda direta"), a lista de funcionalidades vinda do catálogo de PF trouxe "SCRIPT ATUALIZAR NIVEL DESC VENDA
DIRETA CONTROLE", "CORRECAO CONTROLE VENDA DIRETA" e outras parecidas. Nenhuma estava certa, e a lista confundiu até quem
trabalha na Sustentação. Os nomes são os que os contadores deram às funções nas contagens, não o que o usuário vê no sistema.

Decisões do lado do SOS:
- **As caixas de funcionalidades saem da tela do usuário.** O SOS deixa de enviar `candidatos_confirmados`; o campo continua
  no contrato para a Sustentação usar na Fase 2.
- **Ponto de partida:** a abertura passa a começar pelo **menu do sistema**. O usuário aponta por onde entra no sistema, e a
  entrevista pergunta o resto a partir dali.
- **Documentação relacionada:** o usuário vê os casos de uso que o RAG achou e pode lê-los dentro do SOS. Não há link para a
  DokuWiki, que não é acessível a todos.

## Fonte dos menus
| Sistemas | De onde vem o menu |
|---|---|
| PHP (tecnologia 50 em `SOS.TS_SISTEMA_TECNOLOGIA`; ex.: GOP = 666) | **SOS**, ao vivo, de `FGCA.TC_MENU` (o FGCA tem a hierarquia completa) |
| FORMS (tecnologia 24: GAI = 1, GCC = 691 e os demais), mesmo que o FGCA tenha linhas | **sos-ia** |
| Benner, HOPE (JAVA, 791), AGENDAMENTO e qualquer outro | **sos-ia** |
| Sistema sem catálogo no sos-ia | **sos-ia**, com os caminhos citados na wiki; sem nada na wiki, o SOS mostra um campo livre |

- **FORMS:** o FGCA devolve os itens, mas sem a hierarquia. A estrutura está nos arquivos `.mmb` (fonte binária do menu) e
  `.mmx` (compilado).
- **Benner:** é proprietário e tem um menu por módulo.
- **HOPE e AGENDAMENTO:** têm menu interno próprio. O usuário vai investigar onde fica.

## 1. Catálogo de menus (banco do sos-ia)
Tabela nova, por exemplo `apf.menu_sistema`, incluída no `sosia.dump`. Formato único para todas as fontes:

| Campo | Conteúdo |
|---|---|
| `sistema_id` | sistema do `apf.sistema` (ligado ao `cd_sistema` do SOS como hoje) |
| `id` | identificador estável do item **dentro do sistema** (texto ou número) |
| `pai` | `id` do pai; nulo na raiz |
| `nome` | rótulo como o usuário vê |
| `folha` | `true` se abre uma tela; `false` se só agrupa outros itens |
| `caminho` | "Pai > Filho > Item", sem a sigla do sistema no começo |
| `detalhe` | texto de ajuda, se houver |
| `modulo` | módulo do Benner; nulo nos demais |
| `ordem` | ordem entre os irmãos |
| `fonte` | `forms`, `benner`, `hope`, `agendamento`, `wiki` |

Um extrator por fonte em `migracao/`, nesta ordem:
1. **Wiki (reserva para todos):**
   - de onde extrair os caminhos:
     - linhas "Menu: …" dos `caso_de_uso` (ex.: "Menu: GAI > Relatório > NUGEC > Demonstrativo …");
     - seção "CAMINHO DE ACESSO" das `user_story`;
   - normalização:
     - separadores `>`, `->`, `→` e ` - ` viram " > ";
     - tirar a sigla do sistema do começo;
     - juntar os repetidos.

   O levantamento achou cerca de 1.144 seções em 848 páginas. Guardar também a página de origem: a documentação do item 3
   pode usar isso.
2. **FORMS:** ler os menus de GAI, GCC e dos demais sistemas FORMS:
   - `.mmb`: pelo `frmf2xml` do Oracle Forms (gera XML com os `MenuItem`, `Label` e submenus) ou por leitura direta do binário;
   - `.mmx`: só se o `.mmb` não estiver disponível.

   Usar o rótulo e a ordem dos itens, e descartar separadores e itens de sistema (Sair, Janela, Ajuda).
3. **Benner:** por módulo, a partir da fonte que o usuário levantar.
4. **HOPE e AGENDAMENTO:** depois da investigação do usuário.

Publicar um **relatório de cobertura** por sistema: fonte usada, quantidade de itens e sistemas sem nada.

## 2. `GET /sistemas/{cd_sistema}/menus?sigla=`
```json
{ "fonte": "forms", "modulos": [],
  "menus": [ { "id": "12", "pai": null, "nome": "Cadastro", "folha": false, "caminho": "Cadastro", "detalhe": null, "modulo": null },
             { "id": "13", "pai": "12", "nome": "Alienação", "folha": true, "caminho": "Cadastro > Alienação", "detalhe": null, "modulo": null } ] }
```
- **Prioridade:** catálogo da fonte própria do sistema; senão, wiki; senão, `fonte: "nenhuma"` com `menus: []`.
- **`modulos`:** lista dos módulos (Benner); vazia nos demais.
- **Fonte `wiki`:** pode vir só com folhas (`pai` nulo e `caminho` completo). O SOS mostra a lista com busca.
- **Sigla desconhecida:** responde 200 com `fonte: "nenhuma"`, não 404.
- **Volume:** a resposta pode ter centenas de itens (o GOP tem 19), e o SOS guarda o resultado na sessão.

## 3. Ponto de partida na entrevista
`NovaEntrevista.ponto_partida` (opcional): `{ "caminho": "...", "detalhe": "...", "menu_id": "...", "fonte": "fgca" }`.
`fonte` pode ser `fgca`, que é o menu do PHP vindo do SOS, `livre`, quando o usuário digitou, ou uma das fontes do item 1.
- **Busca:** o `caminho` entra na consulta do RAG junto com o relato. As páginas da wiki citam o menu, então isso deve
  melhorar a documentação e os candidatos.
- **Itens da entrevista:**
  - **corretivo:** preenche `onde_ocorre`;
  - **evolutivo existente:** preenche `funcionalidade`, que deixa de ser perguntada;
  - **"algo novo":** o caminho é "onde ficaria"; vai para o contexto do prompt e não preenche nenhum item.
- **Continuação:** a entrevista pergunta o que acontece a partir dali (no evolutivo, o que muda nessa tela; no corretivo, o que
  aconteceu), já citando o caminho.
- **Texto do chamado:** "Onde ocorre" ou "Funcionalidade que será alterada" mostra o caminho. Se `fonte` for `livre`, mostra
  como o usuário escreveu.

**Até a 1.5.0:** o SOS põe "Menu: <caminho>." no começo do `relato`, para a busca e o LLM já aproveitarem.

## 4. Documentação relacionada
**`EstadoEntrevista.documentos[]`:** `[{ "pagina", "titulo", "tipo", "secao", "similaridade" }]`
- **Origem:** o que o `rag()` já acha (`nucleo/entrevista/motor.py:137-168`), hoje usado só no prompt.
- **Quantidade e tipos:** até 5 páginas, só `caso_de_uso`, `user_story` e `regras_de_negocio`.
- **Filtro e ordem:** sem as páginas revogadas (as que o `sem_riscados` tira), na ordem do `rag()`.
- **Vazia:** quando o sistema não tem wiki.

**`GET /documentacao?pagina=<id>&entrevista=<uuid>`:** `{ "pagina", "titulo", "tipo", "secoes": [ { "secao", "texto" } ] }`
- **Montagem:** as seções da página na `ordem`, como em `nucleo/rag/busca.py:253-255`.
- **Páginas aceitas:** só `sistemas:*`, e só do sistema da entrevista; as demais dão 404.
- **Texto:** é a documentação, não precisa de anonimização. Se houver trecho com dado pessoal na wiki, seguir a mesma regra do
  RAG.

**`EscopoEditado.documentos_lidos[]`** (opcional): os ids das páginas que o usuário abriu ou marcou como "é isto que eu uso",
até 10. Ficam na versão 1 do escopo, para a Fase 3 medir se a documentação ajudou a reduzir as perguntas e acertar a estimativa.

## 5. Mock
- **Menus:** um de exemplo com 2 níveis em `/sistemas/{cd}/menus`; para qualquer `cd_sistema`, devolver `fonte: "wiki"`.
- **Documentos:** 2 fixos em `documentos`, e o `/documentacao` devolve o texto deles.
- **Ponto de partida:** o mock aceita `ponto_partida` e não pergunta `onde_ocorre` nem `funcionalidade` quando ele vem.

## O que o SOS já faz agora (sem a 1.5.0)
- tira as caixas de funcionalidades da tela;
- monta o menu dos sistemas PHP pelo FGCA;
- tem o passo "Onde no sistema", com campo livre para os demais sistemas até a 1.5.0;
- põe o caminho escolhido no começo do relato.

Quando a 1.5.0 sair, o SOS:
- passa a pedir os menus ao sos-ia;
- troca o caminho no relato pelo `ponto_partida`;
- mostra a documentação.
