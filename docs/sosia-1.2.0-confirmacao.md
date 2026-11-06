# sos-ia 1.2.0 — Confirmação do Escopo do Pedido (plano de execução)

Para executar no repositório `D:\Projetos\sos-ia`. Plano geral do SOS: `C:\inetpub\wwwroot\CHAMADOS\docs\plano-abertura-ia-fase1.md`.
Contrato atual: 1.1.0. Esta entrega é **aditiva** (1.2.0): nenhum campo ou rota existente muda de significado.

## Objetivo
Fechar o ciclo da abertura: o SOS confirma o Escopo do Pedido editado pelo usuário, grava 1 ou 2 chamados no Oracle e informa
os números ao serviço. O serviço guarda as versões do Escopo do Pedido e o vínculo com os chamados num **banco próprio da
aplicação**, separado do banco do RAG.

## Decisões (já tomadas com o usuário)
- **Um chamado por tipo, no máximo 2**: `CORRETIVO` e `EVOLUTIVO`. Evolutivo de algo existente e evolutivo novo vão no mesmo
  chamado, em seções separadas ("Alterações no que já existe" / "Funcionalidades novas"). A frase do `PASSAGEM_SOS.md` "cada
  segmento corresponde a um chamado" está errada e deve ser corrigida.
- **O tipo de cada requisito vem do segmento de origem**, nunca do navegador. O SOS manda só `segmento`, `descricao` e
  `criterio_aceite`.
- **Validação no serviço**: `/confirmacao` recusa (409) enquanto `faltando` não estiver vazio. O SOS não replica
  `TRIVIAIS`/`conteudo` em VBScript.
- **A API mantém o nome `Contrato`** (`POST /contrato`). "Escopo do Pedido" é só o nome na tela do SOS.
- **Banco da aplicação em instância própria**: um database separado no mesmo container **não protege** contra
  `docker compose down -v` (o volume inteiro é apagado). Por isso: container `app_db` com volume **externo**
  `sosia_app_pgdata` (o compose nunca remove volume externo). O banco `sosia` (RAG) continua restaurável e descartável.
- Nada muda no Oracle. O vínculo entre os 2 chamados fica em `SOS.TS_PACOTE` / `TS_SOLICITACAO_PACOTE` (gravado pelo SOS).

## Fluxo
```
SOS confirmarAbertura.asp
  1. POST /entrevistas/{id}/confirmacao          {escopo editado}            → grupos + texto de cada chamado; situacao PENDENTE
  2. Oracle: 1–2 chamados (+ TS_PACOTE se 2), uma transação
  3. POST /entrevistas/{id}/confirmacao/chamados {chamados[], cd_pacote}     → situacao CONFIRMADA (idempotente)
  Falha entre 2 e 3: o chamado existe, a entrevista fica PENDENTE e aparece em GET /confirmacoes/pendentes.
```

## Contrato 1.2.0

### `POST /entrevistas/{id}/confirmacao` — entrada `EscopoEditado`, saída `EscopoConfirmado`
Entrada:
```json
{ "titulo": "...", "objetivo": "...",
  "requisitos": [{"segmento": 1, "descricao": "...", "criterio_aceite": "..."}],
  "fora_escopo": ["..."], "duvidas": ["..."],
  "integracoes": [{"sistema": "Benner", "sentido": "BUSCA", "descricao": "..."}],
  "cd_usuario": 123 }
```
Saída:
```json
{ "id": "...", "versao_contrato": "1.2.0", "situacao": "PENDENTE",
  "grupos": [{ "tipo": "CORRETIVO", "titulo": "... (correção)", "texto": "OBJETIVO\n...",
               "requisitos": [{"n": 1, "tipo": "CORRETIVO", "natureza": "CORRECAO", "segmento": 1,
                               "descricao": "...", "criterio_aceite": "..."}] }],
  "analise_ia": true, "nota": 80, "regra_documentada": null }
```
Regras:
- 404 entrevista inexistente.
- 409 se a situação for `CONFIRMADA`; se o contrato ainda não foi gerado (`POST /contrato` antes); se `faltando` não estiver vazio
  (o `detalhe` lista o que falta).
- 422 se um requisito aponta para segmento inexistente.
- Tipo pelo segmento: `CORRETIVO` → `CORRETIVO`; `EVOLUTIVO_*` → `EVOLUTIVO`. Natureza: `CORRECAO` | `ALTERACAO` | `NOVO`.
- Grupos em ordem fixa: `CORRETIVO` antes de `EVOLUTIVO`. Com 2 grupos, o título ganha o sufixo " (correção)" / " (evolução)"
  (no limite de 200).
- `n` é a numeração do Escopo do Pedido inteiro (a mesma nos dois chamados).
- `texto` (para `DS_SOLICITACAO`, que o SOS corta em ~3900 bytes): o mais importante primeiro — OBJETIVO; requisitos por seção
  com "Critério de aceite"; "Os requisitos X deste pedido estão no chamado <outro tipo> vinculado (mesmo pacote)"; FORA DE
  ESCOPO; INTEGRAÇÕES; DÚVIDAS EM ABERTO; e, se a regra documentada foi aceita, REGRA DOCUMENTADA QUE O USUÁRIO QUER MUDAR.
- Grava a **v0** (o `Contrato` gerado, `origem=0`) e a **v1** (`{escopo, grupos}`, `origem=1`, `cd_usuario`), substituindo se a
  chamada se repetir enquanto `PENDENTE`. Evento `confirmacao`. Situação → `PENDENTE`.
- `regra_documentada` na saída só quando `decisao_regra = ACEITA`.

### `POST /entrevistas/{id}/confirmacao/chamados` — entrada `VinculoChamados`, saída `Confirmacao`
```json
{ "chamados": [{"tipo": "CORRETIVO", "cd_solicitacao": 50123}, {"tipo": "EVOLUTIVO", "cd_solicitacao": 50124}], "cd_pacote": 7 }
```
- 409 se a situação não for `PENDENTE` nem `CONFIRMADA` ("chame POST /confirmacao antes").
- Os tipos têm de ser exatamente os dos grupos da v1 (409); tipo repetido ou 2 chamados sem `cd_pacote` → 422.
- Já `CONFIRMADA`: repetição idêntica → 200 com o mesmo corpo; outros números → 409.
- `cd_solicitacao` é único no banco: chamado já vinculado a outra entrevista → 409.
- Grava `entrevista_chamado`, situação → `CONFIRMADA`, `expira_em` → NULL. Evento `vinculo`.

### `GET /solicitacoes/{cd}/escopo` — saída `EscopoChamado`
`{cd_solicitacao, entrevista_id, tipo, cd_pacote, versoes:[{versao, origem, cd_usuario, criado_em, conteudo}]}`. 404 se o chamado
não veio do fluxo novo. Para as telas da Fase 2.

### `GET /confirmacoes/pendentes?horas=1` — saída `list[EntrevistaPendente]`
Entrevistas `PENDENTE` há mais de `horas` (0–720): `{id, cd_sistema, sigla, atualizado_em, grupos[]}`. Para reconciliar com o
Oracle (o SOS grava o id da entrevista no `DS_INFO_ADICIONAL`).

### Bloqueios em rotas existentes
- `POST /respostas` e `POST /regra` numa entrevista `CONFIRMADA` → 409.
- `POST /contrato` numa entrevista `PENDENTE`/`CONFIRMADA` não muda a situação (hoje grava `CONTRATO_GERADO`, o que quebraria o
  passo 3).

### `GET /saude`
Novo campo `banco_app: bool` (`ping_app`). `ok = motor e banco e banco_app`. No mock, `banco_app = true`.

## Arquivos

| Arquivo | Mudança |
|---|---|
| `nucleo/config.py` | `APP_DSN = env("SOSIA_APP_DSN", "host=127.0.0.1 port=5435 dbname=sosia_app user=sosia_app password=sosia_app_local sslmode=disable gssencmode=disable")` |
| `nucleo/db.py` | `conectar_app()` (schema `app`, criado se faltar), `db_app()`, `ping_app()` — rascunho no Apêndice A |
| `nucleo/entrevista/armazem.py` | schema `apf` → `app`, tabelas `entrevista_escopo` e `entrevista_chamado`, funções `muda_situacao`, `grava_escopo`, `escopo`, `versoes`, `vincula`, `chamados`, `chamado`, `pendentes` — rascunho no Apêndice B |
| `nucleo/entrevista/modelos.py` | `VERSAO_CONTRATO = "1.2.0"` + comentário; `from typing import Annotated, Optional`; `Saude.banco_app`; modelos novos no fim — Apêndice C |
| `nucleo/entrevista/confirmacao.py` (novo) | regras puras: `ConfirmacaoInvalida` (409), `EscopoInvalido` (422), `verifica_confirmavel`, `monta_grupos`, `titulo`, `texto`, `valida_vinculo` — Apêndice D |
| `nucleo/entrevista/motor.py` | duas conexões (abaixo) + `confirmacao`, `vincular`, `escopo_do_chamado`, `pendentes`; bloqueios |
| `nucleo/entrevista/mock.py` | mesmos 4 métodos em memória; `contrato()` guarda o último em `st["contrato"]`; `responder`/`decidir_regra` zeram |
| `servico/app.py` | 4 rotas novas, handlers `ConfirmacaoInvalida`→409 e `EscopoInvalido`→422, `banco_app` no `/saude` |
| `contrato_api/gera_schemas.py` | `MODELOS` + `EscopoEditado`, `EscopoConfirmado`, `VinculoChamados`, `Confirmacao`, `EscopoChamado`, `EntrevistaPendente`; rodar e commitar os `.schema.json` e o `openapi.json` |
| `docker-compose.yml` | serviço `app_db`; `api` com `SOSIA_APP_DSN` e `depends_on: app_db` |
| `.env.example` | `SOSIA_APP_DB_SENHA`, `SOSIA_APP_DB_PORTA` |
| `tests/` | ver "Testes" |
| `README.md`, `PASSAGEM_SOS.md` | contrato 1.2.0, banco da aplicação, backup, "um chamado por tipo" |

### Motor: duas conexões
- `Motor.__init__(self, conectar=None, conectar_app=None)`: `self.conectar_app = conectar_app or dbm.conectar_app`.
- Em cada método, `app = self.conectar_app()` para **todo** `armazem.*` e para `_carrega`; `conn = self.conectar()` (RAG) só onde
  há RAG ou anonimização: `criar` (`resolve_sistema`, `pii.prepara`, `rag`), `responder` e `decidir_regra` (`pii.anonimizar`).
  `obter`, `contrato`, `confirmacao`, `vincular`, `escopo_do_chamado` e `pendentes` não abrem a do RAG.
- `_rodada(self, app, eid, est, imagens)` e `_reordena_candidatos(app, eid, est)`: recebem a conexão da aplicação (só gravam evento).
- Commit em `app`; fechar as duas no `finally`.
- Extrair de `contrato()` um `_monta_contrato(app, eid, row, est) -> Contrato` (gera pelo LLM só se `est["contrato"]` estiver
  vazio). `contrato()` = carregar + `_monta_contrato` + salvar (mantendo `PENDENTE`/`CONFIRMADA` se for o caso).
  `confirmacao()` exige `est["contrato"]` já gerado e usa `_monta_contrato` sem LLM.
- `vincular()`: `psycopg.errors.UniqueViolation` no `cd_solicitacao` → rollback e `ConfirmacaoInvalida`.

### docker-compose: banco da aplicação
```yaml
  app_db:
    image: pgvector/pgvector:pg16          # a mesma imagem do db (já baixada); não usa vetores
    container_name: sosia_app_db
    environment:
      POSTGRES_DB: sosia_app
      POSTGRES_USER: sosia_app
      POSTGRES_PASSWORD: ${SOSIA_APP_DB_SENHA:-sosia_app_local}
    ports:
      - "127.0.0.1:${SOSIA_APP_DB_PORTA:-5435}:5432"
    volumes:
      - sosia_app_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sosia_app -d sosia_app"]
      interval: 10s
      timeout: 5s
      retries: 30
    restart: unless-stopped
# api: SOSIA_APP_DSN: "host=app_db port=5432 dbname=sosia_app user=sosia_app password=${SOSIA_APP_DB_SENHA:-sosia_app_local}"
#      depends_on: app_db (service_healthy)
volumes:
  sosia_pgdata:
  sosia_app_pgdata:
    external: true      # criado uma vez: docker volume create sosia_app_pgdata. "down -v" não o apaga.
```
No README: `docker volume create sosia_app_pgdata` antes do primeiro `up`; backup com
`docker exec sosia_app_db pg_dump -U sosia_app -Fc sosia_app > backup\sosia_app_AAAAMMDD.dump`.
As entrevistas de teste que estão hoje em `apf.entrevista` do `sosia` não precisam ser migradas.

## Testes
- **Problema já existente, corrigir primeiro**: `tests/test_mock.py` usa o token fixo `"mock"`, mas o `config.py` carrega o
  `SOSIA_TOKEN` real do `.env` (com `setdefault`), e os 8 testes de fluxo dão 401 numa máquina com `.env`. Trocar por
  `H = {"X-SOSIA-Token": config.TOKEN}` (importando `from nucleo import config`).
- `tests/test_confirmacao.py` (novo, puro, sem banco): tipo e natureza pelo segmento; ordem CORRETIVO→EVOLUTIVO; sufixo do título
  só com 2 grupos e limite de 200; texto com as seções certas e a referência ao outro chamado; segmento inexistente → `EscopoInvalido`;
  `valida_vinculo` (tipos divergentes, repetido, sem pacote, repetição idêntica, outros números).
- `tests/test_mock.py` (pela API): misto → 2 grupos e vínculo com pacote; 409 sem contrato gerado, com `faltando` e depois de
  confirmada; 422 segmento inexistente; idempotência de `/confirmacao/chamados`; `/respostas` 409 após confirmada;
  `GET /solicitacoes/{cd}/escopo` com v0 e v1; `/confirmacoes/pendentes`; schemas validados com `valida(...)`.
- `tests/test_motor.py`: `pytestmark` também pulando sem `db.ping_app()`; limpeza e consultas de evento em `app.entrevista*` via
  `db.db_app()`; `test_motor_mostra_5_candidatos...` cria a entrevista no banco da aplicação; um teste novo de confirmação de ponta
  a ponta com `LLMFalso` (v0, v1, PENDENTE → CONFIRMADA, `expira_em` nulo).
- `test_schemas_gerados_atualizados` passa depois de rodar `gera_schemas.py`.

Rodar (sem `.venv` nesta máquina; container descartável, com `.env` vazio por cima para não pegar o token real):
```
docker run --rm -v "D:/Projetos/sos-ia:/app" -v "<arquivo vazio>:/app/.env:ro" -w /app --network sos-ia_default ^
  -e SOSIA_DSN="host=db port=5432 dbname=sosia user=sosia password=sosia_local" ^
  -e SOSIA_APP_DSN="host=app_db port=5432 dbname=sosia_app user=sosia_app password=sosia_app_local" ^
  sos-ia-api python -m pytest -q -p no:cacheprovider
```
Base antes desta entrega: 31 passam; `test_rag_real_traz_documentacao_e_candidatos` e `test_candidatos_fusao_real_e_reordenacao`
precisam da chave de embeddings, que só existe no container `sosia_api`.

## Pronto quando
- Testes verdes (fora os 2 que dependem da chave).
- `docker compose up -d --build` sobe `sosia_app_db` saudável; `/saude` com `banco_app: true`.
- `docker compose down -v && docker compose up -d` mantém as entrevistas confirmadas.
- `contrato_api/` regenerado (1.2.0) e `PASSAGEM_SOS.md` atualizado — é o que a sessão do SOS lê para o `confirmarAbertura.asp`.

## Observação sobre a sessão do SOS
O hook de codificação do projeto CHAMADOS (`.claude/enforce-encoding.ps1`) vale para qualquer caminho: bloqueia Edit em arquivos
UTF-8 com acentos e reconverte arquivos novos para CP1252. Por isso esta entrega deve ser feita numa sessão aberta no próprio
`D:\Projetos\sos-ia`, onde o hook não está ativo.

---
## Apêndices — rascunhos (UTF-8, prontos para revisar e copiar)

### Apêndice A - `nucleo/db.py`
```python
"""Conexões com os dois bancos do serviço.

  núcleo     database sosia, schema apf (SOSIA_DSN): RAG, catálogo, histórico. Restaurável do dump; descartável.
  aplicação  database sosia_app, schema app (SOSIA_APP_DSN): entrevistas, Escopo do Pedido confirmado e vínculo com os
             chamados do SOS. Instância e volume próprios, com backup: é dado de produção.
"""
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from . import config


def conectar(timeout=5):
    c = psycopg.connect(config.DSN, connect_timeout=timeout, row_factory=dict_row)
    c.execute("SET search_path TO apf, public")
    try:
        from pgvector.psycopg import register_vector
        register_vector(c)
    except Exception:  # noqa: BLE001  (extensão ausente num banco vazio)
        pass
    return c


@contextmanager
def db():
    c = conectar()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def conectar_app(timeout=5):
    """Conexão com o banco da aplicação (schema app, criado se faltar)."""
    c = psycopg.connect(config.APP_DSN, connect_timeout=timeout, row_factory=dict_row)
    c.execute("CREATE SCHEMA IF NOT EXISTS app")
    c.execute("SET search_path TO app, public")
    c.commit()
    return c


@contextmanager
def db_app():
    c = conectar_app()
    try:
        yield c
        c.commit()
    finally:
        c.close()


def ping():
    """True se o banco responde e tem o núcleo carregado (tabela de chamados)."""
    try:
        with psycopg.connect(config.DSN, connect_timeout=2) as c:
            return c.execute("SELECT to_regclass('apf.chamado') IS NOT NULL").fetchone()[0]
    except Exception:  # noqa: BLE001
        return False


def ping_app():
    """True se o banco da aplicação responde."""
    try:
        with psycopg.connect(config.APP_DSN, connect_timeout=2) as c:
            return c.execute("SELECT 1").fetchone()[0] == 1
    except Exception:  # noqa: BLE001
        return False
```

### Apêndice B - `nucleo/entrevista/armazem.py`
```python
"""Estado das entrevistas no banco da APLICAÇÃO (database sosia_app, schema app; db.conectar_app).

O rascunho fica aqui até o SOS confirmar (nada no Oracle antes). Instância e volume próprios: o banco do núcleo (RAG) é
restaurável do dump; este guarda dado de produção (Escopo do Pedido confirmado) e precisa de backup.

  entrevista          uma linha por entrevista; `estado` (jsonb) guarda segmentos, perguntas, respostas, candidatos, regra, nota,
                      texto enviado ao LLM e o contrato gerado. `situacao`:
                        EM_ANDAMENTO | PRONTA | CONTRATO_GERADO | PENDENTE (escopo enviado, SOS gravando no Oracle) | CONFIRMADA
                      `expira_em` (retenção) só vale enquanto não confirmada.
  entrevista_evento   cada chamada ao LLM ou ação (criar, responder, contrato, confirmação, fallback): modelo, tokens, custo,
                      segundos, conteúdo da resposta (nunca o prompt inteiro).
  entrevista_escopo   versões do Escopo do Pedido: 0 = gerado pela IA, 1 = editado e confirmado pelo usuário, 2+ = Sustentação
                      (Fase 2). Conteúdo em jsonb.
  entrevista_chamado  chamados do SOS gerados pela confirmação: um por tipo (CORRETIVO/EVOLUTIVO) e o pacote (TS_PACOTE).
As tabelas são criadas na primeira conexão (idempotente).
"""
import datetime
import json

from psycopg.types.json import Jsonb

DDL = """
CREATE SCHEMA IF NOT EXISTS app;
CREATE TABLE IF NOT EXISTS app.entrevista (
    id            uuid PRIMARY KEY,
    cd_sistema    integer,
    sigla         text NOT NULL,
    sistema_id    integer,
    intencao      text NOT NULL,
    rodada        integer NOT NULL DEFAULT 1,
    situacao      text NOT NULL DEFAULT 'EM_ANDAMENTO',
    analise_ia    boolean NOT NULL DEFAULT true,
    modo_pii      text NOT NULL,
    nota          integer,
    custo_usd     numeric(10,5) NOT NULL DEFAULT 0,
    estado        jsonb NOT NULL,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    expira_em     timestamptz
);
CREATE INDEX IF NOT EXISTS entrevista_situacao_idx ON app.entrevista (situacao, atualizado_em);
CREATE TABLE IF NOT EXISTS app.entrevista_evento (
    id             bigserial PRIMARY KEY,
    entrevista_id  uuid NOT NULL REFERENCES app.entrevista(id) ON DELETE CASCADE,
    rodada         integer,
    tipo           text NOT NULL,
    modelo         text,
    tokens_entrada integer,
    tokens_saida   integer,
    custo_usd      numeric(10,5),
    segundos       numeric(8,1),
    conteudo       jsonb,
    criado_em      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entrevista_evento_entrevista_idx ON app.entrevista_evento (entrevista_id);
CREATE TABLE IF NOT EXISTS app.entrevista_escopo (
    entrevista_id  uuid NOT NULL REFERENCES app.entrevista(id) ON DELETE CASCADE,
    versao         integer NOT NULL,
    origem         integer NOT NULL,              -- 0 = IA, 1 = usuário, 2 = Sustentação
    cd_usuario     integer,                       -- TC_USUARIO.CD_USUARIO do SOS (nunca o nome)
    conteudo       jsonb NOT NULL,
    criado_em      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entrevista_id, versao)
);
CREATE TABLE IF NOT EXISTS app.entrevista_chamado (
    entrevista_id   uuid NOT NULL REFERENCES app.entrevista(id) ON DELETE CASCADE,
    tipo            text NOT NULL,                -- CORRETIVO | EVOLUTIVO
    cd_solicitacao  bigint NOT NULL UNIQUE,       -- TS_SOLICITACAO.CD_SOLICITACAO
    cd_pacote       bigint,                       -- TS_PACOTE.CD_PACOTE (só com 2 chamados)
    criado_em       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entrevista_id, tipo)
);
"""
_pronto = set()


def garante(conn):
    chave = conn.info.dsn
    if chave in _pronto:
        return
    conn.execute(DDL)
    conn.commit()
    _pronto.add(chave)


def criar(conn, eid, req, sistema_id, modo_pii, analise_ia, estado, nota, dias_retencao=None):
    garante(conn)
    expira = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=dias_retencao) if dias_retencao else None
    conn.execute("""INSERT INTO app.entrevista (id, cd_sistema, sigla, sistema_id, intencao, analise_ia, modo_pii, nota, estado, expira_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (eid, req.cd_sistema, req.sigla, sistema_id, req.intencao.value, analise_ia, modo_pii, nota, Jsonb(estado), expira))


def salvar(conn, eid, rodada, situacao, analise_ia, nota, estado):
    conn.execute("""UPDATE app.entrevista SET rodada = %s, situacao = %s, analise_ia = %s, nota = %s, estado = %s, atualizado_em = now(),
                           custo_usd = (SELECT coalesce(sum(custo_usd), 0) FROM app.entrevista_evento WHERE entrevista_id = %s)
                    WHERE id = %s""", (rodada, situacao, analise_ia, nota, Jsonb(estado), eid, eid))


def muda_situacao(conn, eid, situacao):
    """Só a situação. CONFIRMADA tira a expiração: o Escopo do Pedido de um chamado aberto não pode sumir."""
    conn.execute("""UPDATE app.entrevista SET situacao = %s, atualizado_em = now(),
                           expira_em = CASE WHEN %s = 'CONFIRMADA' THEN NULL ELSE expira_em END
                    WHERE id = %s""", (situacao, situacao, eid))


def evento(conn, eid, rodada, tipo, info=None, conteudo=None):
    info = info or {}
    conn.execute("""INSERT INTO app.entrevista_evento (entrevista_id, rodada, tipo, modelo, tokens_entrada, tokens_saida, custo_usd, segundos, conteudo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (eid, rodada, tipo, info.get("modelo"), info.get("tokens_entrada"), info.get("tokens_saida"), info.get("custo_usd"),
                  info.get("segundos"), Jsonb(_json(conteudo)) if conteudo is not None else None))


def obter(conn, eid):
    """Linha da entrevista ou None (id inválido também dá None)."""
    garante(conn)
    try:
        return conn.execute("SELECT * FROM app.entrevista WHERE id = %s::uuid", (eid,)).fetchone()
    except Exception:  # noqa: BLE001  (uuid malformado)
        conn.rollback()
        return None


# ---------------------------------------------------------------- Escopo do Pedido e chamados
def grava_escopo(conn, eid, versao, origem, cd_usuario, conteudo):
    """Grava (ou substitui, enquanto não confirmada) uma versão do Escopo do Pedido."""
    conn.execute("""INSERT INTO app.entrevista_escopo (entrevista_id, versao, origem, cd_usuario, conteudo) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (entrevista_id, versao) DO UPDATE
                    SET origem = EXCLUDED.origem, cd_usuario = EXCLUDED.cd_usuario, conteudo = EXCLUDED.conteudo, criado_em = now()""",
                 (eid, versao, origem, cd_usuario, Jsonb(_json(conteudo))))


def escopo(conn, eid, versao):
    return conn.execute("SELECT * FROM app.entrevista_escopo WHERE entrevista_id = %s::uuid AND versao = %s", (eid, versao)).fetchone()


def versoes(conn, eid):
    return conn.execute("SELECT * FROM app.entrevista_escopo WHERE entrevista_id = %s::uuid ORDER BY versao", (eid,)).fetchall()


def vincula(conn, eid, chamados, cd_pacote):
    """chamados: [(tipo, cd_solicitacao)]. cd_solicitacao é único: um chamado só pertence a uma entrevista."""
    for tipo, cd in chamados:
        conn.execute("INSERT INTO app.entrevista_chamado (entrevista_id, tipo, cd_solicitacao, cd_pacote) VALUES (%s,%s,%s,%s)",
                     (eid, tipo, cd, cd_pacote))


def chamados(conn, eid):
    return conn.execute("""SELECT tipo, cd_solicitacao, cd_pacote FROM app.entrevista_chamado WHERE entrevista_id = %s::uuid
                           ORDER BY tipo""", (eid,)).fetchall()


def chamado(conn, cd_solicitacao):
    garante(conn)
    return conn.execute("SELECT * FROM app.entrevista_chamado WHERE cd_solicitacao = %s", (cd_solicitacao,)).fetchone()


def pendentes(conn, horas):
    """Entrevistas em PENDENTE há mais de `horas`: o SOS pode ter gravado o chamado e não ter conseguido avisar o serviço."""
    garante(conn)
    return conn.execute("""SELECT e.id, e.cd_sistema, e.sigla, e.atualizado_em, s.conteudo -> 'grupos' AS grupos
                           FROM app.entrevista e LEFT JOIN app.entrevista_escopo s ON s.entrevista_id = e.id AND s.versao = 1
                           WHERE e.situacao = 'PENDENTE' AND e.atualizado_em < now() - make_interval(hours => %s)
                           ORDER BY e.atualizado_em""", (horas,)).fetchall()


def _json(x):
    return json.loads(json.dumps(x, default=str))
```

### Apêndice C - `nucleo/entrevista/modelos.py (acrescentar no fim)`
```python


# ---------------------------------------------------------------- confirmação (1.2.0)
class Natureza(str, Enum):
    """De onde vem o requisito; separa as seções do texto e orienta a contagem (função alterada × incluída)."""
    CORRECAO = "CORRECAO"     # segmento CORRETIVO
    ALTERACAO = "ALTERACAO"   # segmento EVOLUTIVO_EXISTENTE
    NOVO = "NOVO"             # segmento EVOLUTIVO_NOVO


class RequisitoEditado(Base):
    segmento: int = Field(ge=1, description="segmento de origem (Contrato.segmentos[].n); define o tipo e a natureza")
    descricao: str = Field(min_length=1, max_length=2000)
    criterio_aceite: str = Field(default="", max_length=2000)


class EscopoEditado(Base):
    """Entrada de POST /entrevistas/{id}/confirmacao: o Escopo do Pedido como o usuário confirmou na tela.
    O tipo de cada requisito NÃO vem daqui: é sempre o do segmento de origem."""
    titulo: str = Field(min_length=1, max_length=200)
    objetivo: str = Field(min_length=1, max_length=4000)
    requisitos: list[RequisitoEditado] = Field(min_length=1, max_length=30)
    fora_escopo: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=30)
    duvidas: list[Annotated[str, Field(max_length=1000)]] = Field(default_factory=list, max_length=30)
    integracoes: list[Integracao] = Field(default_factory=list, max_length=20)
    cd_usuario: Optional[int] = Field(default=None, description="session(\"CD_USUARIO\") do SOS, para auditoria (nunca o nome)")


class RequisitoEscopo(Base):
    n: int = Field(ge=1, description="número no Escopo do Pedido inteiro (o mesmo nos dois chamados)")
    tipo: TipoRequisito
    natureza: Natureza
    segmento: int
    descricao: str
    criterio_aceite: str


class GrupoChamado(Base):
    """Um chamado a criar no SOS."""
    tipo: TipoRequisito
    titulo: str = Field(max_length=200, description="para DS_RESUMO; com 2 grupos, termina em (correção) ou (evolução)")
    texto: str = Field(description="Escopo do Pedido do grupo em texto corrido, para DS_SOLICITACAO (o SOS corta em bytes)")
    requisitos: list[RequisitoEscopo]


class EscopoConfirmado(Base):
    """Saída de POST /entrevistas/{id}/confirmacao. A entrevista fica PENDENTE até POST /confirmacao/chamados."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    situacao: str = Field(description="PENDENTE")
    grupos: list[GrupoChamado] = Field(min_length=1, max_length=2, description="CORRETIVO antes de EVOLUTIVO")
    analise_ia: bool
    nota: int = Field(ge=0, le=100)
    regra_documentada: Optional[RegraDocumentada] = Field(default=None, description="só quando aceita: trecho a anexar ao chamado")


class ChamadoGerado(Base):
    tipo: TipoRequisito
    cd_solicitacao: int = Field(gt=0, description="TS_SOLICITACAO.CD_SOLICITACAO")


class VinculoChamados(Base):
    """Entrada de POST /entrevistas/{id}/confirmacao/chamados: o que o SOS gravou no Oracle."""
    chamados: list[ChamadoGerado] = Field(min_length=1, max_length=2, description="um por grupo da confirmação")
    cd_pacote: Optional[int] = Field(default=None, gt=0, description="TS_PACOTE.CD_PACOTE; obrigatório com 2 chamados")


class Confirmacao(Base):
    """Saída de POST /entrevistas/{id}/confirmacao/chamados (idempotente)."""
    id: str
    versao_contrato: str = VERSAO_CONTRATO
    situacao: str = Field(description="CONFIRMADA")
    chamados: list[ChamadoGerado]
    cd_pacote: Optional[int] = None


class VersaoEscopo(Base):
    versao: int
    origem: int = Field(description="0 = IA, 1 = usuário, 2 = Sustentação")
    cd_usuario: Optional[int] = None
    criado_em: str
    conteudo: dict


class EscopoChamado(Base):
    """Saída de GET /solicitacoes/{cd}/escopo."""
    cd_solicitacao: int
    entrevista_id: str
    tipo: TipoRequisito
    cd_pacote: Optional[int] = None
    versoes: list[VersaoEscopo]


class EntrevistaPendente(Base):
    """Item de GET /confirmacoes/pendentes: confirmação iniciada e não concluída (para reconciliar com o Oracle)."""
    id: str
    cd_sistema: Optional[int] = None
    sigla: str
    atualizado_em: str
    grupos: list[TipoRequisito]
```

### Apêndice D - `nucleo/entrevista/confirmacao.py`
```python
"""Confirmação do Escopo do Pedido (contrato 1.2.0): regras puras, usadas pelo motor real e pelo mock.

  1. POST /confirmacao: o SOS manda o escopo editado. O serviço confere `faltando` (regra determinística, não o navegador),
     define o tipo de cada requisito pelo segmento de origem e devolve os grupos: um chamado por tipo, no máximo 2
     (CORRETIVO; EVOLUTIVO = alterações no que já existe + funcionalidades novas). A entrevista fica PENDENTE.
  2. O SOS grava os chamados no Oracle (e o TS_PACOTE quando são 2).
  3. POST /confirmacao/chamados: o SOS informa os CD_SOLICITACAO. Idempotente; a entrevista fica CONFIRMADA.
"""
from .modelos import (EscopoEditado, GrupoChamado, Natureza, RequisitoEscopo, TipoRequisito, TipoSegmento, VinculoChamados,
                      DecisaoRegra)


class ConfirmacaoInvalida(Exception):
    """Estado não permite a operação (409)."""


class EscopoInvalido(Exception):
    """Corpo coerente com o schema, mas inválido para esta entrevista (422)."""


NATUREZA = {TipoSegmento.CORRETIVO: Natureza.CORRECAO, TipoSegmento.EVOLUTIVO_EXISTENTE: Natureza.ALTERACAO,
            TipoSegmento.EVOLUTIVO_NOVO: Natureza.NOVO}
ORDEM = (TipoRequisito.CORRETIVO, TipoRequisito.EVOLUTIVO)
SUFIXO = {TipoRequisito.CORRETIVO: " (correção)", TipoRequisito.EVOLUTIVO: " (evolução)"}
SECOES = {TipoRequisito.CORRETIVO: [("PROBLEMAS A CORRIGIR", Natureza.CORRECAO)],
          TipoRequisito.EVOLUTIVO: [("ALTERAÇÕES NO QUE JÁ EXISTE", Natureza.ALTERACAO), ("FUNCIONALIDADES NOVAS", Natureza.NOVO)]}
NOME_TIPO = {TipoRequisito.CORRETIVO: "corretivo", TipoRequisito.EVOLUTIVO: "evolutivo"}
SITUACOES_ABERTAS = ("EM_ANDAMENTO", "PRONTA", "CONTRATO_GERADO", "PENDENTE")


def tipo_requisito(tipo_segmento):
    return TipoRequisito.CORRETIVO if TipoSegmento(tipo_segmento) == TipoSegmento.CORRETIVO else TipoRequisito.EVOLUTIVO


def verifica_confirmavel(contrato):
    if contrato.faltando:
        itens = "; ".join(f"segmento {f.segmento}: {f.rotulo}" for f in contrato.faltando)
        raise ConfirmacaoInvalida(f"o Escopo do Pedido ainda não pode ser confirmado. Falta: {itens}")


def monta_grupos(contrato, escopo: EscopoEditado):
    """Grupos (chamados) do escopo editado. O tipo vem do segmento; segmento inexistente é erro."""
    segs = {s.n: s for s in contrato.segmentos}
    reqs = []
    for i, r in enumerate(escopo.requisitos, 1):
        s = segs.get(r.segmento)
        if s is None:
            raise EscopoInvalido(f"requisito {i}: o segmento {r.segmento} não existe nesta entrevista")
        if not r.descricao.strip():
            raise EscopoInvalido(f"requisito {i}: descrição vazia")
        reqs.append(RequisitoEscopo(n=i, tipo=tipo_requisito(s.tipo), natureza=NATUREZA[TipoSegmento(s.tipo)], segmento=s.n,
                                    descricao=r.descricao.strip(), criterio_aceite=r.criterio_aceite.strip() or "Definir com o usuário."))
    tipos = [t for t in ORDEM if any(r.tipo == t for r in reqs)]
    regra = contrato.regra_documentada if contrato.decisao_regra == DecisaoRegra.ACEITA else None
    grupos = []
    for t in tipos:
        meus = [r for r in reqs if r.tipo == t]
        outros = [r for r in reqs if r.tipo != t]
        grupos.append(GrupoChamado(tipo=t, titulo=titulo(escopo.titulo, t, len(tipos)), requisitos=meus,
                                   texto=texto(t, escopo, meus, outros, regra)))
    return grupos


def titulo(base, tipo, n_grupos):
    base = " ".join(base.split())
    if n_grupos < 2:
        return base[:200]
    suf = SUFIXO[tipo]
    return base[:200 - len(suf)].rstrip() + suf


def texto(tipo, escopo, reqs, outros, regra=None):
    """Texto corrido para o DS_SOLICITACAO. O que mais importa vem primeiro: o SOS corta em ~3900 bytes."""
    L = ["OBJETIVO", escopo.objetivo.strip(), ""]
    for nome, nat in SECOES[tipo]:
        rs = [r for r in reqs if r.natureza == nat]
        if rs:
            L.append(nome)
            for r in rs:
                L += [f"{r.n}. {r.descricao}", f"   Critério de aceite: {r.criterio_aceite}"]
            L.append("")
    if outros:
        outro = NOME_TIPO[outros[0].tipo]
        ns = ", ".join(str(r.n) for r in outros)
        L += [f"Os requisitos {ns} deste pedido estão no chamado {outro} vinculado (mesmo pacote).", ""]
    for nome, itens in (("FORA DE ESCOPO", [x.strip() for x in escopo.fora_escopo if x.strip()]),
                        ("INTEGRAÇÕES", [f"{i.sistema} ({i.sentido}): {i.descricao}" for i in escopo.integracoes]),
                        ("DÚVIDAS EM ABERTO", [x.strip() for x in escopo.duvidas if x.strip()])):
        if itens:
            L += [nome] + [f"- {x}" for x in itens] + [""]
    if regra is not None:
        L += ["REGRA DOCUMENTADA QUE O USUÁRIO QUER MUDAR", f"{regra.pagina}" + (f" / {regra.secao}" if regra.secao else ""),
              regra.trecho.strip(), ""]
    return "\n".join(L).strip()


def valida_vinculo(tipos_grupos, vinculo: VinculoChamados, existentes=None):
    """Confere o que o SOS gravou contra os grupos da confirmação. `existentes`: vínculo já gravado (idempotência).
    Devolve True se é uma repetição idêntica (nada a gravar)."""
    tipos = [c.tipo.value for c in vinculo.chamados]
    if len(set(tipos)) != len(tipos):
        raise EscopoInvalido("tipo de chamado repetido")
    if set(tipos) != set(tipos_grupos):
        raise ConfirmacaoInvalida(f"a confirmação tem os grupos {sorted(tipos_grupos)}; recebidos {sorted(tipos)}")
    if len(tipos) == 2 and not vinculo.cd_pacote:
        raise EscopoInvalido("cd_pacote é obrigatório com 2 chamados")
    if existentes:
        atual = {(e["tipo"], int(e["cd_solicitacao"])) for e in existentes}
        pac = {e["cd_pacote"] for e in existentes}
        if atual == {(c.tipo.value, c.cd_solicitacao) for c in vinculo.chamados} and pac == {vinculo.cd_pacote}:
            return True
        raise ConfirmacaoInvalida("a entrevista já foi confirmada com outros chamados")
    return False
```
