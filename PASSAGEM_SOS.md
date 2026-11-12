# sos-ia → sessão do SOS: o que mudou e o que o SOS precisa fazer (11/11/2026)

O serviço sos-ia (núcleo da abertura de chamados com IA) está pronto para a integração. O motor real da entrevista funciona de
ponta a ponta, e o modo mock continua disponível para desenvolver as telas. Contrato da API: **1.7.0** (aditivo: nenhum campo ou
rota existente mudou de significado, e os campos novos de entrada são opcionais). Os JSON Schemas de cada endpoint ficam em
`contrato_api/`, com o `openapi.json`.

**Novo na 1.7.0 (11/11/2026): documentos anexados ao pedido** (pedido `docs/sosia-1.7.0-documentos-anexados.md`). Mande
`documentos` só quando o `/saude` responder `versao_contrato` ≥ 1.7.0, porque antes disso o campo dá 422.
1. **Entrada:** `POST /entrevistas` aceita `documentos: [{nome, mime, base64}]`, até 3.
   - `mime`: `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document` ou `text/plain`.
     Outro tipo dá 422, e mais de 3 documentos também.
   - Cada arquivo pode ter até 10 MB (base64 de até 14.000.000 caracteres).
2. **Leitura:**
   - O texto é extraído, anonimizado como o OCR das imagens (o modo contrato manda como veio) e entra no relato da análise. Por
     isso ele vale nas perguntas, na nota e no Escopo.
   - Cada documento entra com até 8.000 caracteres, e o total é de 15.000; o que passa é cortado no fim (`cortado: true`).
   - **PDF digitalizado não é lido:** volta com `lido: false` e o motivo "sem texto (PDF digitalizado)".
3. **Pergunta do anexo:** com pelo menos um documento lido de 300 caracteres ou mais, a pergunta "Você citou um anexo…" não
   aparece. Sem documento lido, ela continua.
4. **Saída:** `EstadoEntrevista.anexos_lidos: [{nome, lido, paginas, caracteres, cortado, motivo}]` vem em criar, obter e
   responder, na ordem dos `documentos`.
   - `paginas` é nulo para DOCX e TXT.
   - Os motivos possíveis são:
     - "sem texto (PDF digitalizado)";
     - "arquivo protegido por senha";
     - "arquivo corrompido";
     - "arquivo sem texto";
     - **"só imagens (sem texto)"**: um Word só com prints colados. Sugira ao usuário anexar os prints como imagem;
     - "limite de texto dos anexos atingido".
5. **Mock:** um nome com "digitalizado" ou "digitalizada" volta `lido: false`. Os demais voltam `lido: true`, com `paginas: 1` e
   `caracteres` igual ao tamanho do arquivo.

O teste com 295 anexos reais do SOS (feito localmente) leu 96% dos PDF, 82% dos DOCX e todos os TXT. Os DOCX não lidos eram todos
Word só com prints.

**Novo na 1.6.0 (11/11/2026): consulta da documentação de um sistema** (pedido `docs/sosia-1.6.0-consulta-documentacao.md`).
Só acréscimos; mostre a tela quando o `/saude` responder `versao_contrato` ≥ 1.6.0. Todas as rotas exigem o `X-SOSIA-Token`.
1. **Árvore:** `GET /sistemas/{cd_sistema}/documentacao?sigla=GAI` → `{wiki, titulo, grupos: [{tipo, titulo, paginas: [{pagina, titulo,
   secoes, revogada}]}]}`.
   - Grupos na ordem: inicio, caso_de_uso, user_story, regras_de_negocio, mensagens, glossario, eventos, documento,
     plano_de_teste, indice. Só vêm os grupos que têm página.
   - Páginas em ordem natural de título.
   - `revogada: true` quando todas as seções são revogadas (mostrar em cinza).
   - Sem wiki ou sigla desconhecida: `wiki: null`, `grupos: []`.
   - Medido no banco: GAI com 202 páginas em 0,4 s; Benner com 477 em 0,2 s.
2. **Página inteira:** `GET /documentacao?pagina=…&sigla=GAI`. `entrevista` e `sigla` são excludentes: os dois, ou nenhum → 422.
   - Vêm todas as seções, inclusive as revogadas (`revogada: true`, com o `html` dentro de `<del>`). `parcial` é sempre false.
   - Campos novos em cada seção: `referencias: [{codigo, tipo_ref, resolvida, pagina, secao}]`. A página ganha
     `citada_por: [{pagina, titulo, tipo}]`, com até 50 itens.
   - No `html`, a referência resolvida vira `<a class="sosia-ref" data-pagina="…" data-secao="…">RN257</a>`, **sem href**.
     Ponha o `href="#"` e o clique: abrir `GET /documentacao?pagina=<data-pagina>&sigla=` e rolar até a seção `data-secao`.
     A tag `a` entra na lista de permitidas, só com `class` e `data-*`. A referência não resolvida continua texto.
   - O link pode levar a uma página fora de `sistemas:<wiki>:` (regra de outro sistema que este cita, ou página em
     `_auxiliares`). Com a mesma `sigla`, ela abre (200).
   - Com `entrevista=` nada muda, só os campos novos: o `html` pode trazer os links (a tela da entrevista pode ignorar o `a`).
3. **Busca:** `GET /sistemas/{cd_sistema}/documentacao/busca?sigla=GAI&q=…&n=10&testes=0` → `[{pagina, titulo, tipo, secao,
   trecho_html, similaridade}]`.
   - Limites: `q` de 3 a 500 caracteres, `n` de 1 a 30, `testes` 0 ou 1. Fora disso → 422.
   - Até 2 trechos por página. Planos de teste só com `testes=1`. Sem wiki: `[]`.
   - `similaridade` é o cosseno (0–1; mostre em %). A ordem é a da busca, que também pesa a palavra-chave e o tipo, então nem
     sempre fica decrescente.
   - Na página de regras de negócio, o título vem como "Regras de negócio: RN257. Desconto de 25% para Quitação".
   - Tempo de cerca de 1 s.
4. **Mock:** a árvore traz UC001 e US001. As duas páginas vêm com uma seção revogada e a RN01 resolvida, cujo link abre a
   página de regras de exemplo. A busca devolve as duas.
CSS sugerido, além do da 1.5.4: `.sosia-ref{color:#0b5cad;cursor:pointer;text-decoration:underline dotted}`.

**Novo na 1.5.5 (11/11/2026): a documentação da wiki se atualiza sozinha.** O sos-ia sincroniza o RAG com a wiki todo dia,
de madrugada. Antes, era uma cópia de 23/10. As páginas novas e alteradas passam a aparecer na busca, nos documentos da
entrevista e no `GET /documentacao`; as removidas dão 404. Para o SOS, só há um campo opcional novo: **`/saude`
`wiki_atualizada_em`**, com a data e hora da última sincronização com sucesso (nula antes da 1ª). Serve para a Sustentação ver se
a documentação está em dia. Nenhuma tela precisa mudar.

**Novo na 1.5.4: documentação pronta para mostrar (`GET /documentacao`).**
- Cada seção ganha **`html`**: o texto da wiki já formatado. As tabelas `| a | b |` (2 de cada 3 seções) viram tabela. Também
  são formatados:
  - listas, inclusive as numeradas;
  - texto riscado (regra revogada);
  - passos ("P1.") e rótulos ("BREVE DESCRIÇÃO:") em negrito.
- As referências soltas, uma por linha (RN01, Tela02, FE01), vão para o fim do passo, num `<span class="sosia-refs">`. Links
  viram só o texto, e as sobras da conversão ("---", "Voltar") somem. `texto` continua como era.
- **É seguro inserir direto na página** (`Response.Write` do `html`, sem escapar de novo). O sos-ia escapa todo o texto da wiki
  (um `<ano de referência>` aparece como texto) e só gera as tags p, br, table, thead, tbody, tr, th, td, ul, ol, li, del,
  strong, span e div.
- CSS sugerido (ajuste ao visual do SOS):
  ```css
  .sosia-tabela{overflow-x:auto;margin:8px 0}
  .sosia-tabela table{border-collapse:collapse;font-size:13px}
  .sosia-tabela th,.sosia-tabela td{border:1px solid #ccc;padding:4px 6px;vertical-align:top;text-align:left}
  .sosia-tabela th{background:#eef2f7}
  .sosia-refs{color:#666;font-size:12px;background:#f1f1f1;border-radius:3px;padding:0 4px}
  .sosia-lista{margin:4px 0 8px 18px;padding:0}
  .sosia-nivel1{margin-left:18px} .sosia-nivel2{margin-left:36px} .sosia-nivel3{margin-left:54px}
  del{color:#999}
  ```
- O mock devolve um exemplo no formato real (passos, referências, riscado e tabela), para estilizar a tela.

**Mudança 1.5.3 (pedido do usuário, 10/11): "fora do pedido" e "outros sistemas" saem da tela do usuário; a Sustentação responde.**
- **Entrevista:** os itens "O que fica fora do escopo" (`fora_de_escopo`) e "Busca ou grava dados de outros sistemas?"
  (`outros_sistemas`) saíram dos roteiros evolutivos. Eles não vêm mais em `segmentos[].slots`, `perguntas`, `faltando` nem no texto
  do chamado. A pergunta de gatilho sobre outro sistema ("Você citou outro sistema…") também não é mais feita, em nenhum roteiro.
  Entrevista já começada perde os dois itens na próxima chamada e deixa de ficar presa neles.
- **Escopo do Pedido:** `Contrato.fora_escopo` e `Contrato.integracoes` vêm sempre vazios (os campos continuam no esquema).
  O sistema que o usuário citou no relato vira uma dúvida, para a Sustentação ver no chamado:
  `"Para a Sustentação: o pedido cita outro sistema (Benner). Confirmar se as informações são só consultadas nele ou também gravadas ou enviadas."`
- **O que o SOS faz:**
  1. Tirar da tela de confirmação os campos "O que não faz parte deste pedido" e "Isso depende de outro sistema".
  2. No `POST /confirmacao`, mandar `fora_escopo: []` e `integracoes: []`, ou não mandar os dois (o padrão é lista vazia).
  3. Se o `confirmarAbertura.asp` tem cópia dos itens obrigatórios por roteiro, tirar os dois de lá também. Senão, o SOS
     bloqueia o que o sos-ia já libera.
- **Correção junto (teste do GFV):** "chefe do NUVIS" virava "chefe do [PESSOA]" nas respostas. A anonimização tomava a sigla
  da unidade, depois de "chefe do", por um nome. Agora uma palavra só em maiúsculas depois de "do/da/de" é tratada como sigla.
  Nome com sobrenome ("chefe do NUVIS, JOÃO SILVA") continua mascarado. Entrevistas já gravadas não mudam, porque a resposta
  original não é guardada.

**Correção 1.5.2 (teste do SOS de 10/11; o SOS não precisa mudar nada):**
- **Telas juntadas:** a pergunta não junta mais os caminhos de telas diferentes como se fossem uma só. Eles saem separados por
  "; " ("Em Venda Direta; Edital Venda por Licitação > Cadastrar Controle Edital; Tela de impressao do controle, …").
- **Página geral de regras de negócio** ("gai - regras de negocio", de 111 a 368 seções):
  - na lista, o título passa a ser a regra que casou ("Regras de negócio: RN99. Cálculo para 1°, 2° e 3° Edital"), e ela vem
    depois dos casos de uso e user stories da mesma tela;
  - no `GET /documentacao`, vem só essa regra, com a regra-mãe e as sub-regras, até 10 seções;
  - `Documentacao` ganhou `parcial` (true quando veio só parte) e `secoes_total`. A tela pode mostrar "2 de 111 seções".
- As outras páginas continuam vindo inteiras.

**Novo na 1.5.1 (seção 4.9; detalhes e JSON em `docs/sosia-1.5.1-pontos-de-partida.md`):**
- `pontos_partida` na criação: até 5 telas, com uma busca por ponto. O `ponto_partida` antigo continua valendo como lista de 1.
  Os dois juntos dão 422;
- `ponto` em `documentos`, `perguntas` e `segmentos`, para a tela agrupar por ponto;
- "o que muda" perguntado por ponto, com opções do tipo de mudança: `Pergunta.opcoes` passa a ter **até 7** itens;
- texto do chamado com um caminho por linha.

**Novo na 1.5.0 (seção 4.8; pedido `docs/sosia-1.5.0-menu-e-documentacao.md`):**
- `GET /sistemas/{cd}/menus?sigla=`: menu dos sistemas que não são PHP (o GAI vem do `.mmb`, com a árvore inteira, e mais 39
  sistemas vêm pelos caminhos citados na wiki);
- `ponto_partida` na criação: entra na busca e preenche "onde ocorre" ou "funcionalidade", que deixam de ser perguntados;
- `documentos` no estado e `GET /documentacao`: os casos de uso que o RAG achou, para o usuário ler no SOS;
- `documentos_lidos` na confirmação.

Quando mandar `ponto_partida`, deixe de pôr "Menu: …" no começo do relato.

**Correção 1.4.1 (o SOS não muda nada; pedido 1.3.3 do teste com usuário):**
- a pergunta de integração não repete mais o item "outros sistemas" nos pedidos evolutivos;
- SEI/API só contam em maiúsculas ou com contexto ("processo no SEI"); "caixa" só como Caixa Econômica; "enviar" e "retorno"
  sozinhos não disparam mais a pergunta de arquivo;
- item repetido cita a resposta anterior ("Você respondeu “HOPE”, mas faltou detalhe para “Exemplo concreto”…"), com pergunta e
  dica próprias de cada roteiro (no "algo novo", o exemplo pede uma situação de uso, não "valor do contrato");
- número de chamado repetido (Oracle de teste restaurado) é aceito: o vínculo antigo vai para o histórico (4.5);
- aquecimento da busca na subida e a cada 30 min, e `shared_buffers` de 1 GB no banco do núcleo: a 1ª entrevista a frio caiu de
  11,3 s para 5,6 s aqui (o SOS mediu 38,6 s depois de dois dias parado). Cada criação grava o evento `tempos` com os segundos
  por etapa.

**Novo na 1.4.0 (seção 4.7):** "Não sei" aceito em alguns itens, opções de resposta nas perguntas, origem de cada requisito
(o `inferido` o usuário confirma ou remove) e funcionalidades afetadas marcadas pelo usuário. A tela atual continua funcionando
sem mudança; o SOS adota os campos novos quando puder.

**Correção 1.3.2 (o SOS não muda nada):** a frase que aponta o outro chamado do pacote agora concorda em número: "O requisito 2
deste pedido está no chamado evolutivo vinculado" / "Os requisitos 2 e 3 … estão". A outra parte do pedido 1.3.2 (regra
documentada no motor real) está em teste à parte: o RAG já traz a RN13, mas o LLM não a aponta porque o prompt pede que a regra
"explique claramente" o relato. Mudança de prompt só depois de medida num gabarito.

**Correção 1.3.1 (o SOS não muda nada):** o `texto` de cada grupo da confirmação ganhou a seção **INFORMAÇÕES DO PEDIDO**, logo
depois de OBJETIVO, com as respostas da entrevista daquele chamado (onde ocorre, como reproduzir, desde quando/quem...; cada uma
com até 600 caracteres). Sem LLM, o critério de aceite deixou de repetir o "como reproduzir": no corretivo é uma frase fixa, e no
evolutivo, "Conferir com o exemplo: ...". O texto já vem pronto para o `DS_SOLICITACAO` e para o e-mail do solicitante.

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
| `GET /sistemas/{cd_sistema}/menus?sigla=GAI` **(novo, 1.5.0)** | — | `Menus` |
| `GET /documentacao?pagina=…&entrevista=…` **(novo, 1.5.0)** | — | `Documentacao` |

`/saude`: `ok` agora exige também `banco_app=true` (banco da aplicação no ar).

Detalhes dos campos:
- `intencao`: `PAROU_DE_FUNCIONAR` | `MUDAR_EXISTENTE` | `ALGO_NOVO`.
- `relato`: de 10 a 20.000 caracteres.
- `imagens`: até 5, cada uma `{nome, mime: image/png|jpeg|gif|bmp|webp, base64}`, com o base64 sem o prefixo `data:`.

Erros, sempre no formato `{erro, detalhe}`:
| Código | Quando |
|---|---|
| 401 | token errado |
| 404 | entrevista não existe; em `/solicitacoes/{cd}/escopo`, chamado que não veio do fluxo novo; em `/documentacao`, página que não é da wiki do sistema da entrevista |
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
- `texto` → `DS_SOLICITACAO`. Já vem com o mais importante primeiro (objetivo, informações do pedido (1.3.1), requisitos com critério de aceite, referência ao
  outro chamado, fora de escopo, integrações, dúvidas, regra documentada), então pode cortar no limite de bytes (~3900).
- `n` é a numeração do pedido inteiro, igual nos dois chamados. `natureza`: `CORRECAO` | `ALTERACAO` | `NOVO`.
- `regra_documentada` só vem quando o usuário aceitou mudar a regra: é o trecho a anexar.

**Passo 3 — `POST /entrevistas/{id}/confirmacao/chamados`.** Entrada (`VinculoChamados`):
```json
{ "chamados": [{"tipo": "CORRETIVO", "cd_solicitacao": 50123}, {"tipo": "EVOLUTIVO", "cd_solicitacao": 50124}], "cd_pacote": 7 }
```
- Os tipos têm de ser exatamente os dos grupos do passo 1 (409). Tipo repetido ou 2 chamados sem `cd_pacote` → 422.
- **Idempotente:** repetir com os mesmos números devolve 200 com o mesmo corpo. Outros números numa entrevista já confirmada → 409.
  Um `cd_solicitacao` já vinculado a **outra** entrevista é tratado como número reaproveitado (o Oracle de teste foi restaurado e
  a sequence repetiu o número): o vínculo antigo vai para o histórico (`app.entrevista_chamado_historico`, com evento nas duas
  entrevistas) e o novo é aceito (200). Antes da 1.4.0 isso dava 409.
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

### 4.7 Abertura guiada (novo na 1.4.0) — `novaAbertura.asp` e `confirmarAbertura.asp`
Nada muda nas rotas nem no proxy. Os campos novos de entrada são opcionais: sem eles, o serviço se comporta como na 1.3.2.

**a) "Não sei".** Alguns itens aceitam "Não sei" e deixam de prender o usuário no complemento:

| Item | Aceita "Não sei"? |
|---|---|
| Corretivo: onde ocorre, o que aconteceu | **não** (bloqueia a abertura) |
| Corretivo: como reproduzir, desde quando/quem | sim |
| Evolutivo existente: funcionalidade, o que muda | **não** |
| Evolutivo novo: quem usa, o que guarda, o que faz | **não** |
| Exemplo (evolutivos) | sim |

- Resposta "Não sei" (ou "não sei informar", "sei lá", "não lembro") num item que aceita: o item fica com o valor
  `"Não informado (o usuário não soube responder)"`, conta como respondido, não é perguntado de novo e vira uma dúvida no Escopo do
  Pedido ("Não informado: Desde quando e quem é afetado (segmento 1)"). A nota do medidor cai.
- Num item que bloqueia, "Não sei" continua não valendo: a pergunta volta (no complemento, se for o caso).

**b) Opções de resposta (`Pergunta.opcoes`).** Lista de 0 a 5 textos curtos; mostre como botões, mantendo o campo de texto livre.
A opção clicada vai como texto em `Resposta.texto`, no mesmo `POST /respostas`. `"Não sei informar"` só vem nos itens que aceitam
(e nunca nas perguntas de gatilho). Lista vazia = só texto livre.
```json
{ "id": "r1p2", "segmento": 1, "slot": "desde_quando_quem", "texto": "Desde quando isso acontece e quem é afetado?",
  "dica": "Só você, sua área ou todos?", "opcoes": ["Desde ontem", "Há mais de uma semana", "Só comigo", "Toda a área", "Não sei informar"] }
```

**c) Origem de cada requisito (`origem`: `usuario` | `documentacao` | `inferido`).** Vem em `Contrato.requisitos[]` e
`Contrato.integracoes[]`. `inferido` é uma hipótese da análise que o usuário não disse (ex.: "criar uma nova rubrica"): mostre
destacado ("Sugerido pela análise — confirme ou remova"). Na confirmação, mande de volta em cada requisito o `n_contrato` (o `n` do
requisito do contrato de onde ele veio; nulo se o usuário criou na tela) e a `origem`. Requisito `inferido` que não voltar foi
removido pelo usuário; o que voltar sai no chamado com "(sugerido pela análise e confirmado pelo usuário)". A origem final é sempre
a do contrato (o serviço confere pelo `n_contrato`).
```json
"requisitos": [
  { "n": 1, "tipo": "EVOLUTIVO", "segmento": 1, "origem": "usuario",
    "descricao": "O sistema deve limitar o reembolso de línguas a R$ 250 por mês.", "criterio_aceite": "..." },
  { "n": 2, "tipo": "EVOLUTIVO", "segmento": 1, "origem": "inferido",
    "descricao": "O sistema deve criar uma rubrica para o reembolso na folha.", "criterio_aceite": "..." } ]
```

**d) Funcionalidades afetadas (`candidatos_confirmados`).** `candidatos_funcionalidade[]` agora traz `id`. Mostre os candidatos com
caixa de marcar ("Quais destas funcionalidades o pedido envolve?") e mande os ids marcados na confirmação. Id que não estava entre
os mostrados → 422. As marcadas voltam em `EscopoConfirmado.funcionalidades` e saem no texto dos chamados (nos dois, quando há 2),
na seção "FUNCIONALIDADES INDICADAS PELO USUÁRIO", logo depois de INFORMAÇÕES DO PEDIDO. Nenhuma marcada é válido.

Entrada do `POST /confirmacao` com os campos novos:
```json
{ "titulo": "Limite de reembolso de línguas", "objetivo": "...", "cd_usuario": 123,
  "requisitos": [ { "segmento": 1, "n_contrato": 1, "origem": "usuario", "descricao": "...", "criterio_aceite": "..." },
                  { "segmento": 1, "n_contrato": null, "descricao": "Pedido acrescentado pelo usuário na tela." } ],
  "candidatos_confirmados": [4101],
  "fora_escopo": [], "duvidas": [], "integracoes": [] }
```
Saída (`EscopoConfirmado`), além do que já vinha:
```json
"funcionalidades": [ { "id": 4101, "nome": "REEMBOLSO DE LINGUAS CALCULAR", "tipo_ifpug": "EE", "similaridade": 0.62 } ]
```

**Avisos (sem mudança agora):**
- Regra documentada: a distinção "a regra explica o comportamento" × "o sistema descumpre a regra" ainda não está resolvida; o
  laboratório vai testar um campo `regra_documentada_relacao`. Até lá, a tela segue como está (aceita/recusa).
- Roteiro SISP 3.0 (Portaria SGD/MGI nº 3.656, junho/2026): vale preferencialmente para contratações novas. O contrato vigente da
  Terracap é anterior; o estimador segue a versão citada no contrato de cada demanda.
- Correção junto: "não sei" no relato ou numa resposta disparava a pergunta de integração com o **SEI** (o "sei" de "não sei").
  Não dispara mais.

### 4.8 Menu e documentação (novo na 1.5.0) — `novaAbertura.asp` e `confirmarAbertura.asp`
**Menus.** Para os sistemas que não são PHP, o SOS pede `GET /sistemas/{cd_sistema}/menus?sigla=<sigla>`. A ordem de prioridade é:
- o menu próprio do sistema (`fonte` = `forms`, e depois `benner`, `hope` e `agendamento`, quando houver);
- senão, os caminhos citados na wiki (`wiki`);
- senão, `fonte: "nenhuma"` com `menus: []`. Nesse caso o SOS mostra o campo livre.

Sigla desconhecida também dá 200 com `nenhuma`. Os PHP continuam pelo FGCA, no SOS.

```json
{ "fonte": "forms", "modulos": [],
  "menus": [ { "id": "CADASTRO_MENU.CADASTRO_NUNEG", "pai": "MAIN_MENU.CADASTRO", "nome": "NUGIV", "folha": false,
               "caminho": "Cadastro > NUGIV", "detalhe": null, "modulo": null },
             { "id": "CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO", "pai": "CADASTRO_MENU.CADASTRO_NUNEG", "nome": "Parcelamento Resíduo",
               "folha": true, "caminho": "Cadastro > NUGIV > Parcelamento Resíduo", "detalhe": null, "modulo": null } ] }
```
- **Ordem:** os itens vêm em ordem de leitura, com cada item seguido dos filhos. Quem tem `folha: false` só agrupa.
- **Fonte `wiki`:** vem só com folhas, com `pai` nulo e o `caminho` completo. Mostre como lista com busca.
- **Cobertura hoje:** o GAI tem 187 itens pelo `.mmb`. Outros 39 sistemas vêm pela wiki (ex.: GRH 38, GAD 22, BENNER 20, GOP 2), e os demais dão `nenhuma`. Os outros sistemas FORMS entram quando os `.mmb` chegarem.
- **Volume:** a resposta pode ter centenas de itens; guarde-a na sessão.

**Ponto de partida.** Na criação, mande o item escolhido em `ponto_partida`:
```json
{ "cd_sistema": 1, "sigla": "GAI", "intencao": "PAROU_DE_FUNCIONAR", "relato": "Ao tentar salvar dá erro…",
  "ponto_partida": { "caminho": "Cadastro > NUGIV > Parcelamento Resíduo", "menu_id": "CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO",
                     "fonte": "forms", "detalhe": null } }
```
- **Campos:** `fonte` é `fgca` (menu PHP do SOS), `livre` (o usuário digitou) ou a `fonte` do `/menus`. `caminho` tem até 300 caracteres e `menu_id` é o `MenuItem.id`, nulo quando `fonte` é `livre`.
- **Corretivo:** preenche `onde_ocorre` com "Menu: Cadastro > NUGIV > Parcelamento Resíduo". Em `fonte` livre, o texto fica como o usuário escreveu.
- **Evolutivo existente:** preenche `funcionalidade`. Esses itens não são perguntados.
- **Algo novo:** o caminho é "onde ficaria". Vai só para o contexto da IA e não preenche nenhum item.
- **Busca:** o caminho entra na consulta do RAG.
- **Perguntas:** a IA pergunta a partir dali ("Em Cadastro > NUGIV > Parcelamento Resíduo, o que você faz até aparecer o erro?").
- **Texto do chamado:** o caminho aparece em INFORMAÇÕES DO PEDIDO, como "Onde ocorre" ou "Funcionalidade que será alterada".
- **Dado pessoal:** um caminho digitado (`livre`) é tratado como o relato e anonimizado.

**Documentação relacionada.** `EstadoEntrevista.documentos` traz as páginas que o RAG achou:
```json
"documentos": [ { "pagina": "sistemas:gai:documentacao_tecnica:caso_de_uso:uc021:inicio",
                  "titulo": "UC021 - Cadastrar Parcelamento de Resíduo", "tipo": "caso_de_uso", "secao": "…", "similaridade": 0.63 } ]
```
- **O que vem:** até 5 páginas, uma vez cada, só `caso_de_uso`, `user_story` e `regras_de_negocio`, sem as revogadas. A lista vem vazia quando o sistema não tem wiki.
- **Texto da página:** sai de `GET /documentacao?pagina=<pagina>&entrevista=<id>`, no formato `{pagina, titulo, tipo, secoes:[{secao, texto}]}`, com as seções na ordem da wiki (versão mais recente, sem as riscadas). O texto é Markdown simples da wiki, com tabelas em `|`.
- **Restrição:** só páginas `sistemas:<sistema da entrevista>:…`; as demais dão 404. Não há link para a DokuWiki.
- **Na confirmação:** `EscopoEditado.documentos_lidos` leva até 10 ids de página que o usuário abriu ou marcou como "é isto que eu uso". Página fora de `documentos` dá 422. Fica na versão 1 do escopo, junto com o `ponto_partida`, para a Fase 3.

**Funcionalidades do catálogo.** `candidatos_funcionalidade` e `candidatos_confirmados` continuam no contrato (para a Sustentação), mas a tela do usuário não precisa mais mostrar nem mandar.

### 4.9 Vários pontos de partida (novo na 1.5.1) — `novaAbertura.asp`
O contrato completo, com os JSON de exemplo, está em `docs/sosia-1.5.1-pontos-de-partida.md`. Em resumo:
- **Escolha de telas:** o passo "Onde no sistema" deixa o usuário escolher até 5 telas. Elas vão em `pontos_partida`, na ordem em
  que ele escolheu. Com uma só, pode continuar mandando `ponto_partida`.
- **Documentos:** `documentos[].ponto` diz de qual tela veio cada página (1 = a primeira). Vêm até 5 por tela; mostre agrupados.
- **Perguntas:** `perguntas[].ponto` traz uma pergunta de "o que muda" por tela, com até 7 opções. Uma opção sozinha não basta: o
  item pede detalhe na rodada seguinte.
- **Segmentos:** `segmentos[].ponto` vem preenchido quando a IA separa uma mudança por tela.
- **Texto do chamado:** "Funcionalidades que serão alteradas" (ou "Onde ocorre") com uma tela por linha.

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
| Evolutivo de algo existente | funcionalidade, o que muda, exemplo |
| Evolutivo novo | quem usa, o que guarda, o que faz, exemplo |

- Pergunta com `slot="gatilho"` é sobre uma complexidade oculta detectada no texto: arquivo, rotina, layout legal, "conforme anexo" e similares. Outro sistema citado (Benner, SEI, banco) não é perguntado desde a 1.5.3: vira dúvida para a Sustentação.

## 6. Mock (para as telas)
O mock reage a palavras do relato:
- "também", "além disso" ou "aproveitando" → 2 segmentos;
- Benner, SEI, banco, Receita, arquivo, planilha ou e-mail → pergunta de gatilho;
- "não permite", "não deixa" ou "bloque…" num `PAROU_DE_FUNCIONAR` → regra documentada de exemplo, e o `/regra` funciona como no real;
- `MUDAR_EXISTENTE` → 2 candidatos de exemplo (ids 9001 e 9002, para testar `candidatos_confirmados`);
- "cálculo" / "calcular" no relato → o contrato ganha um requisito `inferido` de exemplo, para testar confirmar/remover;
- "Não sei informar" vem como opção nos itens que aceitam, e funciona como no real (4.7).
- A confirmação (4.5) segue as mesmas regras do motor real, em memória: um relato misto ("… e também …") gera 2 grupos.
- O complemento (4.6) também: responda "ok" nas 2 rodadas para ver `complemento=true`.
- 1.5.0: `/sistemas/{cd}/menus` devolve um menu de exemplo de 2 níveis (`fonte: "wiki"`) para qualquer sistema; toda entrevista
  traz 2 `documentos` fixos, cujo texto sai no `/documentacao`; com `ponto_partida`, `onde_ocorre` / `funcionalidade` já vêm
  preenchidos e não são perguntados.
- 1.5.1: `pontos_partida` como no real (caminhos unidos por "; ", uma pergunta de "o que muda" por ponto com as 7 opções, documentos
  com `ponto`).

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
