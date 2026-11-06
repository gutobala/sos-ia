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

## Subir só a API em modo mock, sem Docker
```
iniciar_mock.bat            (ou: set SOSIA_MOCK=1 && .venv\Scripts\python -m uvicorn servico.app:app --port 8010)
```
- `http://127.0.0.1:8010/docs` — documentação interativa (OpenAPI).
- Cabeçalho obrigatório (menos em `/saude`): `X-SOSIA-Token: mock` (em produção: valor de `SOSIA_TOKEN`).
- Respostas sempre `application/json; charset=utf-8`; erros sempre no formato `Erro` `{erro, detalhe}`.

## Contrato da API (versão 1.3.0) — `contrato_api/`
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

- `intencao`: `PAROU_DE_FUNCIONAR` · `MUDAR_EXISTENTE` · `ALGO_NOVO` (garantia não passa pela IA).
- `segmentos[].tipo`: `CORRETIVO` · `EVOLUTIVO_EXISTENTE` · `EVOLUTIVO_NOVO`; `requisitos[].tipo`: `CORRETIVO` · `EVOLUTIVO`
  (um chamado por tipo no desmembramento).
- Slots obrigatórios e mínimos: `nucleo/entrevista/roteiros.py` (o `confirmarAbertura.asp` deve repetir a mesma regra).
- Pergunta com `slot="gatilho"`: pergunta de complexidade oculta (integração, gravar × exibir...); a resposta não preenche slot,
  vai para `duvidas` do contrato.
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
catálogo. Na comparação com o e5 em todos os sistemas (`dadosvalia_todos.log`; 556 chamados na wiki, 316 no catálogo), a wiki
empatou e o catálogo ganhou (@10 de 20,2% para 25,9%, p=0,002). A imagem Docker não leva modelo local. O e5 continua obrigatório
só no histórico de complexidade da Fase 3. Depois de recriar o `sosia` com `exporta_nucleo.py`, rode
`migracao\embeda_3large.py` (cerca de US$ 1,20 para o corpus todo; a exportação só leva os vetores do e5).

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
