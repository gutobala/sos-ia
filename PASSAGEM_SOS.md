# sos-ia → sessão do SOS: o que mudou e o que o SOS precisa fazer (06/11/2026)

O serviço sos-ia (núcleo da abertura de chamados com IA) está pronto para a integração. O motor real da entrevista funciona de
ponta a ponta, e o modo mock continua disponível para desenvolver as telas. Contrato da API: **1.3.0** (aditivo sobre a 1.2.0:
nenhum campo ou rota existente mudou de significado). Os JSON Schemas de cada endpoint ficam em `contrato_api/`, com o
`openapi.json`.

**Novo na 1.3.0:** a rodada de complemento (seção 4.6). Depois das 2 rodadas, a entrevista não se encerra mais com item
obrigatório faltando: o usuário não precisa recomeçar.
**Novo na 1.2.0:** a confirmação do Escopo do Pedido (seção 4.5). É o que o `confirmarAbertura.asp` usa.

## 1. Como subir
- Uma vez só, antes do primeiro `up`: `docker volume create sosia_app_pgdata` (volume do banco da aplicação, seção 8).
- `docker compose up -d --build` na raiz do sos-ia. Na primeira vez, o banco é restaurado de `dados\sosia.dump` e demora alguns minutos.
- A API fica em `http://<servidor>:8010`.
- `SOSIA_MOCK=1` no `.env`: respostas fixas, sem LLM e sem banco, para testar as telas. `SOSIA_MOCK=0`: motor real.
- Depois de mudar o `.env`, recrie o container com `docker compose up -d api`.

## 2. Autenticação (mudou)
- Todo endpoint, exceto `/saude`, exige o cabeçalho **`X-SOSIA-Token`** com o valor de `SOSIA_TOKEN` do `.env` do sos-ia.
- O token já está definido. **O valor "mock" não é mais aceito, nem no modo mock.**
- O token fica **só no servidor**, no proxy ASP. Nunca vai para o JavaScript nem para o navegador, e não entra no git.
- Token errado: 401. Serviço sem token configurado: 503.

## 3. Endpoints
| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /saude` | — | `{ok, versao, versao_contrato, mock, llm, banco, banco_app, embeddings, modo_pii}` |
| `POST /entrevistas` | `{cd_sistema, sigla, intencao, relato, imagens[]}` | `EstadoEntrevista` |
| `GET /entrevistas/{id}` | — | `EstadoEntrevista` (retomar) |
| `POST /entrevistas/{id}/respostas` | `{respostas:[{id, texto}]}` | `EstadoEntrevista` |
| `POST /entrevistas/{id}/regra` (1.1.0) | `{aceita: bool, comentario?: string}` | `EstadoEntrevista` |
| `POST /entrevistas/{id}/contrato` | — | `Contrato` |
| `POST /entrevistas/{id}/confirmacao` **(novo, 1.2.0)** | `EscopoEditado` | `EscopoConfirmado` |
| `POST /entrevistas/{id}/confirmacao/chamados` **(novo, 1.2.0)** | `VinculoChamados` | `Confirmacao` |
| `GET /solicitacoes/{cd}/escopo` **(novo, 1.2.0)** | — | `EscopoChamado` |
| `GET /confirmacoes/pendentes?horas=1` **(novo, 1.2.0)** | — | `EntrevistaPendente[]` |

`/saude`: `ok` agora exige também `banco_app=true` (banco da aplicação no ar).

Detalhes dos campos:
- `intencao`: `PAROU_DE_FUNCIONAR` | `MUDAR_EXISTENTE` | `ALGO_NOVO`.
- `relato`: de 10 a 20.000 caracteres.
- `imagens`: até 5, cada uma `{nome, mime: image/png|jpeg|gif|bmp|webp, base64}`, com o base64 sem o prefixo `data:`.

Erros, sempre no formato `{erro, detalhe}`:
| Código | Quando |
|---|---|
| 401 | token errado |
| 404 | entrevista não existe; em `/solicitacoes/{cd}/escopo`, chamado que não veio do fluxo novo |
| 409 | `/regra` sem regra documentada, ou já decidida; `/respostas` ou `/regra` numa entrevista CONFIRMADA; as regras da confirmação (4.5) |
| 422 | corpo inválido; requisito com segmento inexistente; tipo de chamado repetido; 2 chamados sem `cd_pacote` |
| 500 | erro interno |
| 503 | sem token, ou motor real indisponível |

## 4. O que o SOS precisa implementar ou ajustar

### 4.1 Regra documentada (novo na 1.1.0)
1. Num `PAROU_DE_FUNCIONAR`, o sos-ia pode concluir que o comportamento relatado é uma **regra de negócio documentada** na wiki. Nesse caso, `EstadoEntrevista.regra_documentada` vem preenchida com `{pagina, secao, trecho, similaridade, orientacao}`.
2. Se `regra_documentada` vier preenchida e `decisao_regra` vier `null`, o SOS **mostra ao usuário** a `orientacao` e o `trecho` e pergunta: *"É isso que está acontecendo e você quer mudar a regra, ou é defeito mesmo?"*
3. Conforme a resposta, o SOS chama `POST /entrevistas/{id}/regra`:
   - `{aceita: true, comentario}`: o segmento 1 vira **EVOLUTIVO_EXISTENTE**, o comentário entra em `o_que_muda` e pode vir uma nova rodada de perguntas;
   - `{aceita: false, comentario}`: o segmento continua **CORRETIVO** e o comentário vai para as dúvidas.
4. Enquanto o usuário não decide, o segmento fica CORRETIVO. O `Contrato` volta com `pode_confirmar=false`, e `faltando` traz o slot `decisao_regra`.
5. Quando a regra é aceita, o `Contrato` traz `regra_documentada`. É o trecho a anexar ao chamado.

### 4.2 Candidatos de funcionalidade (mudou o significado)
- `candidatos_funcionalidade` traz até 5 itens **já em ordem de relevância**. Mostre nessa ordem.
- **Não reordene nem filtre pela `similaridade`.** Ela é só informativa e mudou de escala: agora fica em torno de 0,4–0,7, antes era cerca de 0,85. Qualquer corte fixo antigo esconderia tudo.
- Em testes, a função certa ficou entre as 5 em 43% dos casos (antes, 19%).
- Não há candidatos em `ALGO_NOVO`.

### 4.3 Respostas sem conteúdo (replicar no `confirmarAbertura.asp`)
- Respostas como "ok", "sim", "não sei", "?" e "-" **não preenchem** o slot. "Não" e "nada" **contam** como resposta.
- Depois de tirar essas partes, o que sobra precisa ter o mínimo de caracteres do slot.
- O sos-ia já aplica a regra. Se o `confirmarAbertura.asp` revalida os slots por conta própria, precisa aplicar a mesma regra; a referência é `nucleo/entrevista/roteiros.py` (`TRIVIAIS`, `conteudo`, `preenchido`).

### 4.4 Tempo de resposta e fallback
- No modo real, `POST /entrevistas` leva de **8 a 11 s**: busca, reordenação dos candidatos (2 a 4 s) e LLM (cerca de 5 s). As outras chamadas levam de 5 a 10 s.
- Use **timeout de 60 s** no proxy e mostre "analisando…" na tela.
- `analise_ia=false` significa que a resposta veio do roteiro fixo, sem IA, porque o LLM falhou ou está fora. O fluxo continua igual, e o chamado pode ser marcado como "sem análise de IA".
- Para decidir entre o fluxo com IA e o formulário antigo: `/saude` com `ok=true` e `llm=true` → IA.

### 4.5 Confirmação do Escopo do Pedido (novo na 1.2.0) — `confirmarAbertura.asp`
**Um chamado por tipo, no máximo 2:** `CORRETIVO` e `EVOLUTIVO`. Evolutivo de algo existente e evolutivo novo vão no **mesmo**
chamado, em seções separadas. O vínculo entre os 2 chamados fica no Oracle (`SOS.TS_PACOTE` / `TS_SOLICITACAO_PACOTE`), gravado
pelo SOS. Na API o objeto continua se chamando `Contrato`; "Escopo do Pedido" é só o nome na tela.

Fluxo:
```
1. POST /entrevistas/{id}/confirmacao          {escopo editado}         → grupos + texto de cada chamado; situacao PENDENTE
2. Oracle: 1–2 chamados (+ TS_PACOTE se 2), numa transação; grave o id da entrevista no DS_INFO_ADICIONAL
3. POST /entrevistas/{id}/confirmacao/chamados {chamados[], cd_pacote}  → situacao CONFIRMADA (idempotente)
Falha entre 2 e 3: o chamado existe, a entrevista fica PENDENTE e aparece em GET /confirmacoes/pendentes. Repita o passo 3.
```

**Passo 1 — `POST /entrevistas/{id}/confirmacao`.** Entrada (`EscopoEditado`):
```json
{ "titulo": "...", "objetivo": "...",
  "requisitos": [{"segmento": 1, "descricao": "...", "criterio_aceite": "..."}],
  "fora_escopo": ["..."], "duvidas": ["..."],
  "integracoes": [{"sistema": "Benner", "sentido": "BUSCA", "descricao": "..."}],
  "cd_usuario": 123 }
```
- Mande só `segmento`, `descricao` e `criterio_aceite` de cada requisito. **O tipo vem do segmento de origem**, nunca da tela:
  `CORRETIVO` → chamado corretivo; `EVOLUTIVO_*` → chamado evolutivo.
- `cd_usuario`: o `session("CD_USUARIO")`, só para auditoria. Nunca mande o nome.
- **Quem valida é o serviço:** 409 enquanto `faltando` não estiver vazio (o `detalhe` lista o que falta). O SOS **não precisa
  replicar** `TRIVIAIS`/`conteudo` em VBScript (isso substitui o item 4.3 para a confirmação).
- Outros 409: entrevista já CONFIRMADA; `POST /contrato` ainda não chamado. 422: requisito apontando para segmento inexistente.
- Pode ser chamado de novo enquanto PENDENTE (o usuário voltou e editou): a versão do usuário é substituída.

Saída (`EscopoConfirmado`):
```json
{ "id": "...", "versao_contrato": "1.2.0", "situacao": "PENDENTE",
  "grupos": [{ "tipo": "CORRETIVO", "titulo": "... (correção)", "texto": "OBJETIVO\n...",
               "requisitos": [{"n": 1, "tipo": "CORRETIVO", "natureza": "CORRECAO", "segmento": 1,
                               "descricao": "...", "criterio_aceite": "..."}] }],
  "analise_ia": true, "nota": 80, "regra_documentada": null }
```
- Um item em `grupos` = um chamado a gravar. Ordem fixa: `CORRETIVO` antes de `EVOLUTIVO`.
- `titulo` → `DS_RESUMO` (até 200). Com 2 grupos ganha o sufixo " (correção)" / " (evolução)".
- `texto` → `DS_SOLICITACAO`. Já vem com o mais importante primeiro (objetivo, requisitos com critério de aceite, referência ao
  outro chamado, fora de escopo, integrações, dúvidas, regra documentada), então pode cortar no limite de bytes (~3900).
- `n` é a numeração do pedido inteiro, igual nos dois chamados. `natureza`: `CORRECAO` | `ALTERACAO` | `NOVO`.
- `regra_documentada` só vem quando o usuário aceitou mudar a regra: é o trecho a anexar.

**Passo 3 — `POST /entrevistas/{id}/confirmacao/chamados`.** Entrada (`VinculoChamados`):
```json
{ "chamados": [{"tipo": "CORRETIVO", "cd_solicitacao": 50123}, {"tipo": "EVOLUTIVO", "cd_solicitacao": 50124}], "cd_pacote": 7 }
```
- Os tipos têm de ser exatamente os dos grupos do passo 1 (409). Tipo repetido ou 2 chamados sem `cd_pacote` → 422.
- **Idempotente:** repetir com os mesmos números devolve 200 com o mesmo corpo. Outros números numa entrevista já confirmada → 409.
  Um `cd_solicitacao` já vinculado a outra entrevista → 409.
- Sem o passo 1 antes → 409. Saída (`Confirmacao`): `{id, versao_contrato, situacao: "CONFIRMADA", chamados[], cd_pacote}`.

**Depois de CONFIRMADA:** `/respostas`, `/regra` e `/confirmacao` dão 409. `/contrato` devolve o mesmo escopo, sem mudar nada.

**Reconciliação — `GET /confirmacoes/pendentes?horas=1`** (`horas` de 0 a 720): entrevistas PENDENTE há mais de `horas`,
`[{id, cd_sistema, sigla, atualizado_em, grupos: ["CORRETIVO", ...]}]`. Procure no Oracle o chamado com esse id no
`DS_INFO_ADICIONAL`: se existir, chame o passo 3; se não, a abertura foi abandonada.

**Fase 2 — `GET /solicitacoes/{cd}/escopo`:** `{cd_solicitacao, entrevista_id, tipo, cd_pacote, versoes: [{versao, origem,
cd_usuario, criado_em, conteudo}]}`. Versão 0 = gerada pela IA, 1 = confirmada pelo usuário (2+ ficam para a Sustentação). 404 se
o chamado não veio do fluxo novo.

### 4.6 Rodada de complemento (novo na 1.3.0) — `novaAbertura.asp`
**Problema que resolve:** antes, depois da 2ª rodada a entrevista se encerrava (`pronto=true`) mesmo com item obrigatório
faltando. O `/contrato` voltava com `pode_confirmar=false`, o `/confirmacao` recusava, e o `/respostas` não aceitava mais nada.
Bastava um "ok" num item obrigatório para o usuário só ter a opção de recomeçar.

**Agora:** depois das 2 rodadas, se ainda faltar item obrigatório, o `POST /respostas` devolve `pronto=false`,
**`complemento=true`** e perguntas **só dos itens que faltam**:
- são as perguntas fixas do roteiro, sem IA: resposta imediata, custo zero, funciona com o LLM fora do ar;
- no máximo 5 por vez; nunca pergunta de gatilho, nunca a decisão da regra documentada (essa continua no painel da regra +
  `POST /regra`);
- respondidas pelo mesmo `POST /entrevistas/{id}/respostas`, com as mesmas regras ("ok", "sim", "não sei" continuam não contando);
- **sem limite de rodadas**: se a resposta for rasa de novo, volta `complemento=true` com as mesmas perguntas e **ids novos**
  (`c1p1`, `c2p1`...). O usuário sai quando responder de verdade ou quando usar "Recomeçar";
- quando não falta nada: `pronto=true`, `complemento=false`, e o fluxo segue igual (`/contrato` → `/confirmacao`).

`rodada` fica em 3 durante o complemento. `complemento` também vem no `GET /entrevistas/{id}` (retomar).

**O que muda na tela:** com `complemento=true`, troque o título das perguntas por "Para concluir, faltam estas informações" (em
vez de "rodada X de 2"), e o aviso do Escopo do Pedido sobre item faltando deixa de mandar recomeçar. Nada muda no proxy.

## 5. Regras do fluxo (determinísticas, não dependem do LLM)
- No máximo **2 rodadas** de perguntas do LLM e **5 perguntas** por rodada. `pronto=true` → chamar `/contrato`.
- Depois das 2 rodadas, se ainda faltar item obrigatório: rodadas de complemento até completar (4.6). `pronto=true` só quando não
  falta item obrigatório (a decisão da regra documentada é tratada à parte).
- `rodada=3`: as 2 rodadas acabaram (entrevista encerrada, ou em complemento quando `complemento=true`).
- Pedido misto vem com até 3 segmentos. **Os chamados não são por segmento, e sim por tipo** (no máximo 2: um corretivo e um
  evolutivo; os segmentos evolutivos vão juntos no mesmo chamado). Quem agrupa é o `POST /confirmacao` (4.5).
- `faltando` e `pode_confirmar` do contrato são calculados por regra fixa, por roteiro:

| Roteiro | Slots obrigatórios |
|---|---|
| Corretivo | onde ocorre, o que aconteceu, como reproduzir, desde quando/quem |
| Evolutivo de algo existente | funcionalidade, o que muda, exemplo, fora de escopo, outros sistemas |
| Evolutivo novo | quem usa, o que guarda, o que faz, exemplo, fora de escopo, outros sistemas |

- Pergunta com `slot="gatilho"` é sobre uma complexidade oculta detectada no texto: integração (Benner, SEI, banco), arquivo, rotina, layout legal, "conforme anexo" e similares.

## 6. Mock (para as telas)
O mock reage a palavras do relato:
- "também", "além disso" ou "aproveitando" → 2 segmentos;
- Benner, SEI, banco, Receita, arquivo, planilha ou e-mail → pergunta de gatilho;
- "não permite", "não deixa" ou "bloque…" num `PAROU_DE_FUNCIONAR` → regra documentada de exemplo, e o `/regra` funciona como no real;
- `MUDAR_EXISTENTE` → 2 candidatos de exemplo.
- A confirmação (4.5) segue as mesmas regras do motor real, em memória: um relato misto ("… e também …") gera 2 grupos.
- O complemento (4.6) também: responda "ok" nas 2 rodadas para ver `complemento=true`.

No mock, `analise_ia` é sempre `false`. O estado do mock some quando o container reinicia.

## 7. LGPD
- `modo_pii=local`, o padrão nos testes com a chave pessoal: o texto é anonimizado no sos-ia antes do LLM e as imagens não são enviadas.
- O SOS **não deve enviar o nome do solicitante** no relato.
- O modo `contrato` (Azure) depende de aprovação da Segurança.
- O modo `teste` (só testes controlados) anonimiza o texto mas envia as imagens originais ao LLM: use apenas imagens sem dado pessoal.

## 8. Banco da aplicação (novo na 1.2.0)
- As entrevistas, as versões do Escopo do Pedido e o vínculo com os chamados ficam no container `sosia_app_db` (database
  `sosia_app`, porta 5435 só em 127.0.0.1), separado do banco do RAG (`sosia_db`, restaurável do dump).
- O volume `sosia_app_pgdata` é **externo**: `docker compose down -v` não o apaga. É dado de produção; faça backup:
  `docker exec sosia_app_db pg_dump -U sosia_app -Fc sosia_app > backup\sosia_app_AAAAMMDD.dump`.
- Uma entrevista CONFIRMADA não expira (`expira_em` nulo): o Escopo do Pedido de um chamado aberto fica guardado.
