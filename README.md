# sos-ia — núcleo de IA do SOS (entrevista de abertura, RAG, estimador)

Serviço Python (FastAPI) que o SOS chama pelo proxy `uc/manterSolicitacao/entrevistaApi.asp`.
Plano completo: `C:\Users\gutob\.claude\plans\bom-agora-que-temos-cozy-dream.md` (núcleo) e
`D:\Projetos\leia-o-sistema-de-floating-shell.md` (lado SOS).

Estado (05/11/2026): **passos 1 e 2 concluídos** — contrato da API, serviço mock, testes, banco do núcleo exportado e tudo em Docker. O motor real (LLM + RAG) entra
no passo 5; até lá, `SOSIA_MOCK=1`.

## Subir tudo (Docker)
```
copy .env.example .env             (ajuste SOSIA_TOKEN, chaves etc.)
docker volume create sosia_app_pgdata   (uma vez só: volume externo do banco da aplicação)
docker compose up -d --build       (banco do núcleo + banco da aplicação + API; na 1ª vez restaura dados\sosia.dump, ~2 min)
docker compose ps                  (sosia_db e sosia_app_db healthy, sosia_api em http://127.0.0.1:8010)
docker compose down                (para; "down -v" apaga o volume do núcleo e força nova restauração; o da aplicação fica)
```
- `sosia_db`: pgvector/pg16, porta 5434 só em 127.0.0.1, volume `sos-ia_sosia_pgdata`, `shm_size: 1gb` (índices HNSW).
  Banco do RAG: restaurável do dump e descartável.
- `sosia_app_db` (1.2.0): banco da **aplicação** (database `sosia_app`, schema `app`), porta 5435 só em 127.0.0.1, volume
  **externo** `sosia_app_pgdata` (o compose nunca remove volume externo). Guarda entrevistas, versões do Escopo do Pedido e o vínculo
  com os chamados do SOS: é dado de produção. Backup:
  `docker exec sosia_app_db pg_dump -U sosia_app -Fc sosia_app > backup\sosia_app_AAAAMMDD.dump`.
  As entrevistas de teste antigas em `apf.entrevista` do `sosia` não foram migradas.
- `sosia_api`: imagem python:3.13-slim, sem modelos locais; `/saude` informa `banco: true` quando o núcleo está carregado.
- `dados\sosia.dump` não vai para o git: é gerado por `migracao\exporta_nucleo.py`.

## Banco do núcleo (exportação da base de pesquisa)
```
.venv\Scripts\python migracao\exporta_nucleo.py              (simula: plano e contagens)
.venv\Scripts\python migracao\exporta_nucleo.py --aplicar    (recria a database sosia no apf_postgres e gera dados\sosia.dump)
```
Só texto anonimizado (SOS e OS de projeto), wiki, histórico inteiro de contagens sem nomes, chamados do legado só como registro
mínimo; depois mascara CPF, e-mail, telefone e nomes conhecidos (também na wiki) e varre o resultado.
Relatório: `dados\exporta_nucleo_relatorio.txt`. Em 05/11/2026: 1,24 GB, dump 335 MB, varredura OK.

## Catálogo de menus (1.5.0)
Tabela `apf.menu_sistema` (`migracao/sql/04_menu_sistema.sql`), carregada por `migracao/carrega_menus.py`. Esse script sempre
simula: gera `dados\menus.sql` e o relatório `dados\menus_cobertura.txt`. Só grava com `--aplicar`. Roda no container da API, com
o projeto montado:
```
docker run --rm -v "D:/Projetos/sos-ia:/app" -w /app --network sos-ia_default -e "SOSIA_DSN=host=db port=5432 dbname=sosia user=sosia password=sosia_local" sos-ia-api python -m migracao.carrega_menus --mmb GAI=fontes/gai_menu.mmb --wiki [--aplicar]
```
- **FORMS:** a árvore sai do `.mmb`, lido direto do binário (`nucleo/menus/forms.py`), sem Oracle Forms. Com vários sistemas,
  repita `--mmb SIGLA=arquivo`. A pasta `fontes\` não vai para o git.
- **Wiki** (reserva para todos): caminhos "Menu: …" dos casos de uso e seções "CAMINHO DE ACESSO" das user stories
  (`nucleo/menus/wiki.py`); só folhas.
- **Dump:** o `exporta_nucleo.py --aplicar` aplica a tabela e o `dados\menus.sql` antes do `pg_dump`. Em 08/11/2026: GAI com 187
  itens (forms) e 39 sistemas pela wiki, num total de 528 itens.

## Sincronização da wiki (1.5.5)
O RAG da wiki (`apf.conhecimento_trecho` e `conhecimento_referencia`) se atualiza sozinho pela API XML-RPC da DokuWiki. Antes,
era uma cópia de 23/10/2026. O pedido do laboratório está em `docs/pedido-sincronizacao-wiki.md` e o código em `nucleo/wiki/`.
```
docker exec sosia_api python -m nucleo.wiki              (simula: baixa o que mudou, converte e mostra o que seria gravado)
docker exec sosia_api python -m nucleo.wiki --aplicar    (grava só o que mudou)
            ... --sistema gr     só os trechos de um sistema    --tudo   baixa todas as páginas de novo
```
- **Quando roda:** todo dia às 03:00 de Brasília (`SOSIA_WIKI_SYNC_HORA`; vazio ou `0` desliga), numa thread da API, ou à mão. A
  entrevista continua respondendo durante a execução, porque cada página é gravada numa transação curta. O `/saude` mostra a
  última execução com sucesso em `wiki_atualizada_em`.
- **Como funciona:**
  - baixa só as páginas com data ou tamanho novos. O texto fica mascarado em `apf.wiki_pagina_sync`; a 1ª carga das ~4.900
    páginas leva uns 10 min;
  - converte a wiki inteira em memória. É a mesma lógica do laboratório (`migracao/referencia_wiki/`), portada para
    `conversao.py` e `trechos.py`; leva ~1,5 min;
  - grava só as páginas cujos trechos mudaram. A versão antiga vai para `apf.wiki_trecho_arquivo`;
  - refaz por diferença as referências, o `sistemas_acesso`, a `data_versao` (= `lastModified` da página) e os menus de fonte wiki
    dos sistemas afetados;
  - o vetor 3-large só é gerado para texto novo; texto igual reaproveita o vetor. O e5 fica vazio nas linhas novas.
- **LGPD:** CPF, e-mail, telefone e os nomes de `dados/nomes_anonimizacao.txt` são mascarados no texto em cache e de novo nos
  trechos, antes de gravar e de gerar o vetor. A varredura de cada execução tem de dar zero.
- **Travas:** uma execução por vez (`pg_advisory_lock`). Ela para sem gravar se a API listar 5% menos páginas que o cache, se
  mais de 5% das páginas forem sair do RAG ou se os vetores passarem do teto (US$ 1). `--forcar` e `--teto` passam por cima.
- **Relatório:** `apf.wiki_sync_execucao`, uma linha por execução, com as páginas novas, alteradas e removidas, os trechos por
  sistema, os vetores, as referências e os erros.
- **Paridade:** `python -m migracao.confere_paridade_wiki` converte o dump de 23/10 (montado em `/dump`) e compara com o banco.
  Em 11/11/2026, 99,81% dos trechos ficaram iguais (40.578 de 40.657) e 100% dos metadados e das referências. As 34 páginas
  diferentes são todas de mídia que o laboratório não achava: 19 com acento no nome e 15 com arquivo ausente do dump. O núcleo
  acha a mídia, e o texto perde o aviso "*(mídia não encontrada)*".
- **Dump:** o `dados/sosia.dump` passa a servir só para distribuição. O banco vivo é o do container; gere o dump de novo quando
  for levar uma cópia.

## Ligação wiki ↔ sistema (11/11/2026)
Na entrevista, na árvore, na busca e na página, a documentação de um sistema é o **Escopo** que `motor.resolve_sistema(conn,
sigla, cd_sistema)` devolve (`nucleo/rag/escopo.py`). Ele é montado em duas camadas:
- **Ligação automática:** as wikis cujo nome, sem pontuação, é a sigla oficial ou um alias do sistema (`apf.sistema_alias`). Por
  exemplo, o GLP fica com a `glpv2` (alias GLPV2, páginas em `sistemas:glpweb:`) e com a `glp`; o E-SOCIAL fica com a `esocial` e
  a `esocial_v2`. A maior wiki é a principal.
- **`config/wiki_sistemas.yaml`:** as decisões que o nome não resolve, tomadas pelo usuário:
  - `ignorar`: wikis que não valem para nenhum sistema (`gso` inativa, `gfov3`, `gecv2`, `gacv2`);
  - `wikis`: wikis somadas a um sistema. Exemplos: GSO e GSOV2 ficam com `portal_do_cliente`, `gsov2` e `portal_interno`, e
    GESTAO-RISCO fica com a `gr`;
  - `paginas`: divide uma wiki entre sistemas. Na `gai-web`, as UC001–UC003 são do GAI-WEB (SOS 803) e o resto é do GFA (SOS
    10006). As mensagens e regras que as páginas do sistema citam também entram, por id.
  - A entrada é achada pelo `cd` (o CD_SISTEMA do SOS) e, sem ele, pela sigla. A rota `/documentacao?sigla=` não recebe o
    código; nela vale a sigla.
- **Mudar o yaml exige rebuild da api**, porque `config/` entra na imagem. O resultado fica em cache até a sincronização diária
  da wiki.
- Os menus de fonte wiki (`nucleo/menus/catalogo.py`) ainda usam só a ligação automática.
- `migracao/sql/06_alias_servicos_online.sql` move o alias SERVICOS ONLINE do GAC-GEATE para o GSO. A base de contagens não muda.

## Subir só a API em modo mock, sem Docker
```
iniciar_mock.bat            (ou: set SOSIA_MOCK=1 && .venv\Scripts\python -m uvicorn servico.app:app --port 8010)
```
- `http://127.0.0.1:8010/docs` — documentação interativa (OpenAPI).
- Cabeçalho obrigatório (menos em `/saude`): `X-SOSIA-Token: mock` (em produção: valor de `SOSIA_TOKEN`).
- Respostas sempre `application/json; charset=utf-8`; erros sempre no formato `Erro` `{erro, detalhe}`.

## Contrato da API (versão 1.7.0) — `contrato_api/`
| Rota | Entrada | Saída |
|---|---|---|
| `GET /saude` | — | `Saude` (o SOS decide IA × roteiro fixo por `ok`) |
| `POST /entrevistas` | `NovaEntrevista` `{cd_sistema, sigla, intencao, relato, imagens[]}` | `EstadoEntrevista` |
| `POST /entrevistas/{id}/respostas` | `Respostas` `{respostas[{id, texto}]}` | `EstadoEntrevista` (nova rodada ou `pronto:true`) |
| `GET /entrevistas/{id}` | — | `EstadoEntrevista` (retomar) |
| `POST /entrevistas/{id}/regra` (1.1.0) | `DecisaoRegraDocumentada` `{aceita, comentario?}` | `EstadoEntrevista` (409 sem regra ou já decidida) |
| `POST /entrevistas/{id}/contrato` | — | `Contrato` (`pode_confirmar=false` enquanto houver `faltando`) |
| `POST /entrevistas/{id}/confirmacao` (1.2.0) | `EscopoEditado` | `EscopoConfirmado`: grupos (um chamado por tipo) → PENDENTE |
| `POST /entrevistas/{id}/confirmacao/chamados` (1.2.0) | `VinculoChamados` `{chamados[{tipo, cd_solicitacao}], cd_pacote}` | `Confirmacao` → CONFIRMADA (idempotente) |
| `GET /solicitacoes/{cd}/escopo` (1.2.0) | — | `EscopoChamado` (versões 0 = IA, 1 = usuário) |
| `GET /confirmacoes/pendentes?horas=1` (1.2.0) | — | `EntrevistaPendente[]` (para reconciliar com o Oracle) |
| `GET /sistemas/{cd}/menus?sigla=` (1.5.0) | — | `Menus` (fonte própria > wiki > `nenhuma`; nunca 404) |
| `GET /documentacao?pagina=&entrevista=` (1.5.0) | — | `Documentacao` (só páginas da wiki do sistema da entrevista) |

- `intencao`: `PAROU_DE_FUNCIONAR` · `MUDAR_EXISTENTE` · `ALGO_NOVO` (garantia não passa pela IA).
- `segmentos[].tipo`: `CORRETIVO` · `EVOLUTIVO_EXISTENTE` · `EVOLUTIVO_NOVO`; `requisitos[].tipo`: `CORRETIVO` · `EVOLUTIVO`
  (um chamado por tipo no desmembramento).
- Slots obrigatórios e mínimos: `nucleo/entrevista/roteiros.py` (o `confirmarAbertura.asp` deve repetir a mesma regra).
- Pergunta com `slot="gatilho"`: pergunta de complexidade oculta (integração, gravar × exibir...); a resposta não preenche slot,
  vai para `duvidas` do contrato.
- **Abertura guiada (1.4.0; detalhes e JSON em `PASSAGEM_SOS.md` 4.7):** "Não sei" aceito só em itens não bloqueantes
  (`roteiros.ACEITA_NAO_SEI`: como reproduzir, desde quando, exemplo, fora de escopo, outros sistemas) — vira `NAO_INFORMADO`,
  dúvida determinística no contrato e nota menor; `Pergunta.opcoes` (até 4 da IA, limpas em `motor.opcoes`, + "Não sei informar"
  posto pela aplicação); `origem` (usuario | documentacao | inferido) nos requisitos e integrações, marcada pelo LLM do contrato,
  com evento `inferidos` (confirmados/removidos) na confirmação; `candidatos_confirmados` (ids do catálogo) gravados na v1 e no
  texto do chamado. Correção junto: "não sei" deixou de disparar o gatilho de integração do SEI (`roteiros.sem_nao_sei`).
- **1.4.1 (teste com usuário):** gatilho de integração some quando há item "outros sistemas" (`motor._pendentes`); palavras
  curtas dos gatilhos só com contexto (`config/gatilhos.yaml`); item repetido cita a resposta anterior e usa pergunta/dica do
  roteiro (`roteiros.pergunta_fixa`, `PERGUNTA_SLOT_TIPO`); número de chamado reaproveitado vai para
  `app.entrevista_chamado_historico`. **Aquecimento** (`nucleo/aquecimento.py`): `pg_prewarm` das tabelas/índices da entrevista
  (só a coluna de vetor em uso) + busca de mentira, na subida e a cada `SOSIA_AQUECER_MIN` (30); `shared_buffers` 1 GB no
  `sosia_db` (`SOSIA_DB_SHARED_BUFFERS`). Evento `tempos` em cada criação. Medição em `dados\aquecimento_medicao.txt`.
- **1.5.0 (menu e documentação; `PASSAGEM_SOS.md` 4.8):**
  - `NovaEntrevista.ponto_partida`: o caminho entra na busca (`motor.consultas_rag`) e preenche `onde_ocorre` ou
    `funcionalidade` no segmento 1 (`roteiros.slot_do_ponto`). O LLM não troca esse valor (`aplica`). Em "algo novo", o caminho
    vai só para o prompt. Evento `ponto_partida`.
  - `EstadoEntrevista.documentos` (`motor.documentos`: UC, US e RN, uma vez por página, até 5) e `GET /documentacao`.
  - `EscopoEditado.documentos_lidos`, validado contra os documentos mostrados e gravado na v1, junto com o ponto de partida.
- **1.5.1 (vários pontos; `docs/sosia-1.5.1-pontos-de-partida.md`, medição em `migracao/mede_ponto_partida.py`):**
  - `NovaEntrevista.pontos_partida` (até 5; `ponto_partida` vale como lista de 1): `Motor._busca` faz uma busca por ponto
    (`motor.consultas_rag`; catálogo só na 1ª), junta os trechos em rodízio (`junta_docs`) e marca o `ponto`. O prompt recebe até
    12 trechos (`docs_prompt`), e o índice da regra documentada se refere a essa lista.
  - `documentos`: até 5 por ponto, sem repetir página. `funcionalidade` / `onde_ocorre` com os caminhos unidos por "; ";
    `Segmento.ponto` quando o LLM separa uma mudança por ponto (o segmento recebe só o caminho dele).
  - "O que muda" por ponto (`motor.perguntas_fixas`, `Pergunta.ponto`), com as opções da aplicação (`roteiros.opcoes_do_ponto`:
    `OPCOES_GENERICAS` + "Não sei informar"; até 7). A resposta vai com a marca `[caminho]` (`roteiros.marca_ponto`), que não
    conta no mínimo; "não sei informar" deixou de contar como conteúdo. Texto do chamado com um caminho por linha
    (`confirmacao.informacoes`).
- **1.5.2 (teste do SOS):** `motor.separa_caminhos` desfaz caminhos de pontos diferentes juntados com " > " na pergunta da IA.
  Página de regras de negócio: título com a regra que casou (`titulo_documento`), depois dos UC/US do ponto, e o
  `GET /documentacao` só com essa regra, a mãe e as sub-regras (`secoes_da_regra`, até 10; `Documentacao.parcial` / `secoes_total`).
- **1.5.3 (pedido do usuário):** `fora_de_escopo` e `outros_sistemas` saíram dos roteiros evolutivos, e `revalida` tira os dois
  das entrevistas começadas antes. O gatilho de integração tem `para: sustentacao` no `gatilhos.yaml` e não vira pergunta; os
  sistemas citados entram nas dúvidas do contrato (`motor.termos_integracao` + `roteiros.duvida_integracao`). `fora_escopo` e
  `integracoes` do contrato vêm vazios.
- **1.5.4:** `SecaoDocumentacao.html` com o texto da wiki formatado por `nucleo/entrevista/doc_html.py`. O conversor é
  determinístico e sem dependência: escapa tudo e gera só as próprias tags. Trata tabelas, listas, riscado, referências soltas
  e as sobras da conversão. Nos 27 mil trechos de UC/US/RN, leva 0,3 ms por trecho, sem nenhuma tag fora da lista.
- **1.5.5:** `Saude.wiki_atualizada_em`, a data da última sincronização da wiki aplicada com sucesso (ver "Sincronização da wiki").
- **1.6.0:** consulta da documentação fora da entrevista (pedido `docs/sosia-1.6.0-consulta-documentacao.md`).
  - Rotas:
    - `GET /sistemas/{cd}/documentacao` devolve a árvore por tipo (`motor.arvore_documentacao`);
    - `GET /documentacao?pagina=&sigla=` devolve a página inteira, com as revogadas riscadas, `referencias` e `citada_por`;
    - `GET /sistemas/{cd}/documentacao/busca` usa a `busca.busca` da entrevista, com até 2 trechos por página e sem planos
      de teste por padrão.
  - No HTML, a referência resolvida vira `<a class="sosia-ref" data-pagina data-secao>`, sem href
    (`doc_html.para_html(texto, refs)`).
  - Uma página entra na wiki do sistema quando está em `sistemas:<wiki>:` ou quando o sistema alcança todos os trechos dela
    (`sistemas_acesso`). Por sigla, também abre a página de destino de um link (`motor.pagina_da_wiki`).
- **1.7.0:** documentos anexados ao pedido (pedido `docs/sosia-1.7.0-documentos-anexados.md`).
  - `NovaEntrevista.documentos`: até 3 PDF, DOCX ou TXT, com até 10 MB cada.
  - Extração (`nucleo/anexos.py`):
    - PDF pela camada de texto (`pypdf`);
    - DOCX direto do `word/document.xml`, com parágrafos e tabelas na ordem;
    - TXT em UTF-8, ou CP1252 como reserva.
  - PDF digitalizado não é lido: a imagem não tem OCR de página.
  - O que não abre volta com `lido: false` e o motivo, sem derrubar a criação.
  - Depois, `pii.prepara_documentos`:
    - anonimiza como o OCR das imagens (menos no modo contrato);
    - corta em 8.000 caracteres por documento e 15.000 no total;
    - junta ao relato como `[Texto do anexo <nome>]: …`.
  - Com um documento lido de 300 caracteres ou mais, o gatilho `conforme_anexo` não pergunta. O nome do arquivo no marcador não
    dispara o gatilho de arquivo.
  - `EstadoEntrevista.anexos_lidos` vem em todas as respostas, e o evento `anexos` guarda só os metadados.
  - Medição em 11/11/2026 com os anexos reais do SOS (`SOS_ANEXOS`), feita localmente e sem LLM:
    - PDF: 213 de 221 lidos; 7 digitalizados e 1 corrompido;
    - DOCX: 45 de 55 lidos; os outros 10 são Word só com prints ("só imagens (sem texto)");
    - TXT: 18 de 18;
    - mediana de 0,09 s por arquivo; pior caso, 4 s.
- Máximo de 2 rodadas do LLM. **Complemento (1.3.0):** depois delas, se ainda faltar item obrigatório, a entrevista não se
  encerra: `complemento=true` e só as perguntas fixas do que falta (`roteiros.perguntas_complemento`; sem IA, sem gatilho, sem a
  decisão da regra, no máximo 5, ids `c{n}p{i}`), repetidas até completar. Evento `complemento`; `analise_ia` não muda.
  Plano: `docs\sosia-1.3.0-complemento.md`.
- **Regra documentada (1.1.0):** quando vier `regra_documentada` com `decisao_regra=null`, o SOS mostra o trecho e a `orientacao` e
  pergunta ao usuário se o sistema está seguindo essa regra. O segmento fica `CORRETIVO` até a decisão.
  - `aceita=true`: o usuário quer mudar a regra. O segmento 1 vira `EVOLUTIVO_EXISTENTE` e ganha mais uma rodada de perguntas;
    o `comentario` vira "o que muda", e o trecho vai no contrato (`regra_documentada`) para anexar ao chamado.
  - `aceita=false`: o usuário mantém que é defeito. O pedido segue corretivo, e o comentário vai para as dúvidas.
  - Sem decisão, `faltando` traz o slot `decisao_regra` e `pode_confirmar=false`.
- Resposta sem conteúdo não preenche slot: "ok", "sim", "não sei" e parecidas são ignoradas antes de medir o tamanho mínimo
  (`roteiros.conteudo`/`TRIVIAIS`). "não" e "nada" valem. O `confirmarAbertura.asp` deve repetir a mesma regra.
- Imagens: `{nome, mime image/*, base64}`, até 5.
- **Confirmação (1.2.0; regras em `nucleo/entrevista/confirmacao.py`):** **um chamado por tipo, no máximo 2** (`CORRETIVO`,
  `EVOLUTIVO`; evolutivo de algo existente e novo vão no mesmo chamado, em seções separadas). O tipo de cada requisito vem do
  segmento de origem, nunca da tela. O serviço recusa (409) enquanto `faltando` não estiver vazio, então o SOS não precisa
  repetir `TRIVIAIS`/`conteudo`. Grava a versão 0 (contrato gerado) e a 1 (escopo editado + grupos). Situações:
  `EM_ANDAMENTO` → `PRONTA` → `CONTRATO_GERADO` → `PENDENTE` (escopo enviado, SOS gravando no Oracle) → `CONFIRMADA` (sem
  expiração; `/respostas`, `/regra` e `/confirmacao` dão 409). Fluxo e regras completas em `PASSAGEM_SOS.md` (4.5) e no plano
  `docs\sosia-1.2.0-confirmacao.md`.
- Os arquivos `*.schema.json` e `openapi.json` são gerados de `nucleo/entrevista/modelos.py`:
  `.venv\Scripts\python contrato_api\gera_schemas.py` (o teste falha se ficarem desatualizados).

### Comportamento do mock (previsível, para testar as telas)
- "também" / "além disso" / "aproveitando" no relato → 2 segmentos (pedido misto → 2 chamados).
- Benner / SEI / banco / Receita / arquivo / planilha / e-mail → pergunta de gatilho na 1ª rodada.
- "não permite" / "não deixa" / "bloque..." num `PAROU_DE_FUNCIONAR` → `regra_documentada` de exemplo; `POST /regra`
  converte (aceita) ou mantém (recusa) o corretivo, como no motor real.
- `MUDAR_EXISTENTE` → 2 candidatos de funcionalidade de exemplo.
- Respostas curtas demais (abaixo do mínimo do slot) → o contrato volta com `faltando` e `pode_confirmar=false`.
- `analise_ia=false` no mock (o chamado seria marcado "sem análise de IA").

## Núcleo portado (passo 4, 05/11/2026)
`nucleo/`: `llm` (OpenAI/Azure, modelo por papel, imagens só nos modos contrato e teste), `pii` (anonimização local; nomes em
`dados\nomes_anonimizacao.txt`, gerado por `migracao\exporta_nomes.py`, fora do git), `embeddings` (e5 local ou
text-embedding-3-large), `rag/busca` e `rag/catalogo` (RAG da wiki e catálogo de funções), `estimador/` (regras, histórico,
universo, prévia, campos). Paridade com a pesquisa (`migracao\confere_paridade.py`): histórico dos 83 chamados MAE 19,87 e
Σ 0,411 em `apf` e em `sosia`, 0 chamados diferentes; RAG top-5 idêntico nas 5 consultas fixas (e5 em CPU).
Para rodar o RAG/histórico fora do Docker: `pip install torch --index-url https://download.pytorch.org/whl/cpu` e
`pip install sentence-transformers`; cache do universo em `dados\cache_universo.pkl`.
**Embeddings (05/11/2026): o padrão é o text-embedding-3-large** (`SOSIA_EMB=azure`, coluna `embedding_3l`), na wiki e no
catálogo. Na comparação com o e5 em todos os sistemas (`dados\avalia_todos.log`; 556 chamados na wiki, 316 no catálogo), a wiki
empatou e o catálogo ganhou (@10 de 20,2% para 25,9%, p=0,002). A imagem Docker não leva modelo local. Depois de recriar o
`sosia` com `exporta_nucleo.py`, rode `migracao\embeda_3large.py` (cerca de US$ 1,20 para o corpus todo; a exportação só leva os
vetores do e5).
**Histórico do estimador no 3-large (06/11/2026):** o casamento de nomes do histórico (Fase 3) também segue `SOSIA_EMB`. O limiar
padrão é 0,95 no e5 e **0,78 no 3-large** (`APF_HIST_LIMIAR` sobrepõe). Calibrado em `migracao\calibra_historico.py`
(`dados\calibra_historico.txt`): menor corte com acerto de complexidade >= o do e5 em 3.239 funções de alteração/exclusão
(cobertura 64,4% × 65,1%, acerto 83,2% × 82,9%); nos 83 chamados da paridade, MAE 19,85 × 19,87, 78 iguais, p = 0,69. Com
isso o núcleo inteiro roda sem modelo local. Cache do universo no 3-large: `dados\cache_universo_3l.pkl`; se faltar, é refeito
pela API na primeira estimativa (~17,8 mil nomes, ~US$ 0,02, cerca de 1 min).

## Motor real da entrevista (passo 5, 05/11/2026)
`SOSIA_MOCK=0` + `SOSIA_TOKEN` + chave do LLM → `nucleo/entrevista/motor.py` (o mesmo contrato 1.0.0 do mock):
- **RAG:** trechos da wiki do sistema (busca híbrida; no "parou de funcionar", também as regras de negócio e mensagens mais
  próximas) e as 5 funcionalidades mais prováveis do catálogo (menos em "algo novo").
- **Busca da wiki recalibrada** (05/11/2026, `migracao\calibra_wiki.py`, `dados\calibra_wiki.txt`): pesos por tipo e da
  palavra-chave ajustados no gabarito das páginas que a fábrica alterou (556 chamados, validação cruzada). Fora da amostra, a página
  certa entre as 5 primeiras foi de 41% para 67%, e o MRR de 48% para 69%. A PRE-PROJETO segue com os pesos de 27/10 (`CFG_27_10`).
- **Candidatos de funcionalidade** (`nucleo/rag/catalogo.py`, desde 05/11/2026): fusão de três listas sobre as funções vigentes do
  sistema e reordenação das 30 primeiras pelo LLM (`prompts/reordena_catalogo_v1.md`, evento `llm_candidatos`, ~US$ 0,003 e ~2 s).
  As três listas são o vetor do texto enriquecido (nome, arquivos e campos da contagem de criação), a palavra-chave (BM25) e o
  histórico (chamados anteriores parecidos e as funções que eles alteraram). Em 285 chamados sem vazamento, a função certa entre
  as 5 primeiras subiu de 19% para 43% (`dados\melhora_catalogo.txt`, `migracao\melhora_catalogo.py`). A lista sai na ordem de
  relevância; a `similaridade` é só informativa (cosseno do texto enriquecido) e não ordena. Sem LLM, fica a ordem da fusão.
- **LLM** (papel `chat`, gpt-5.4-mini) com `prompts/entrevista_v1.md` e `prompts/contrato_v1.md`, saída em JSON Schema estrito.
- **Gatilhos ocultos:** `config/gatilhos.yaml` (integração, guardar × exibir, rotina, arquivo, layout legal, "conforme anexo"). Só
  vira pergunta de gatilho o que a regra detectou no texto.
- **Determinístico, nunca do LLM:**
  - os slots são revalidados pelo roteiro; respostas triviais como "ok", "sim" ou "não sei" não contam (`roteiros.conteudo`);
  - perguntas só para slot vazio ou gatilho detectado, no máximo 5; slot vazio que o LLM esqueceu ganha a pergunta fixa;
  - no máximo 2 rodadas do LLM; depois, complemento com as perguntas fixas do que falta (1.3.0);
  - a regra documentada só aparece se o trecho veio do RAG e for regra, mensagem ou caso de uso;
  - trecho riscado na wiki (`~~…~~`, regra revogada) não vai ao prompt (`motor.sem_riscados`; 126 trechos em 13 sistemas;
    laboratório da regra documentada, `docs/resultado-regra-documentada.md`);
  - `faltando` e `pode_confirmar`.
- **Estado no banco da aplicação** (desde a 1.2.0, `sosia_app`: `app.entrevista`, `app.entrevista_evento`,
  `app.entrevista_escopo`, `app.entrevista_chamado`, criados sozinhos): eventos guardam modelo, tokens, custo e a resposta do
  LLM, nunca o prompt. O banco do núcleo (`sosia`) só é lido (RAG, anonimização).
- **Sem LLM** (sem chave, erro ou recusa): roteiro fixo com `analise_ia=false`. **Sem RAG** (banco sem os vetores ou API sem
  chave de embedding): a entrevista segue sem candidatos e sem documentação.
- **Ponta a ponta:** `migracao\testa_entrevista.py` roda os 6 cenários do plano do SOS pela API, com o LLM de verdade.
  Resultado em `dados\entrevista_e2e.txt`: média de US$ 0,011 por entrevista com o gpt-5.4-mini (com a reordenação dos candidatos).

## Variáveis de ambiente (`.env` opcional na raiz, nunca versionado)
Comentário no `.env` vai **na linha de cima**, nunca no fim da linha: o docker compose entrega `VAR= # comentário` ao container
como o valor "# comentário" (o núcleo limpa isso desde 05/11/2026, mas outros programas não).
`SOSIA_MOCK` · `SOSIA_TOKEN` · `SOSIA_DSN` (database `sosia`) · `SOSIA_APP_DSN` (database `sosia_app`) ·
`SOSIA_APP_DB_PORTA` · `SOSIA_APP_DB_SENHA` · `SOSIA_LLM=openai|azure` · `SOSIA_EMB=e5|azure` ·
`SOSIA_PII_MODO=local|contrato|teste` (contrato só com Azure; teste envia imagens originais, só com imagens sem dado pessoal) ·
`SOSIA_IMAGEM_DETALHE=high|low|auto` · `OPENAI_API_KEY` · `AZURE_OPENAI_*`.

## Testes
Sem `.venv` nesta máquina: container descartável, com um `.env` vazio por cima para não pegar o token real
(`<arquivo vazio>` = qualquer arquivo vazio no disco).
```
docker run --rm -v "D:/Projetos/sos-ia:/app" -v "<arquivo vazio>:/app/.env:ro" -w /app --network sos-ia_default ^
  -e SOSIA_DSN="host=db port=5432 dbname=sosia user=sosia password=sosia_local" ^
  -e SOSIA_APP_DSN="host=app_db port=5432 dbname=sosia_app user=sosia_app password=sosia_app_local" ^
  sos-ia-api python -m pytest -q -p no:cacheprovider
```
`test_rag_real_traz_documentacao_e_candidatos` e `test_candidatos_fusao_real_e_reordenacao` precisam da chave de embeddings
(só no container `sosia_api`); fora dele falham com `LLMIndisponivel`.
