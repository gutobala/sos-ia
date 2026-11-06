"""Estado das entrevistas no banco do núcleo (schema apf do `sosia`): rascunho fica aqui até o SOS confirmar (nada no Oracle antes).

  entrevista         uma linha por entrevista; `estado` (jsonb) guarda segmentos, perguntas, respostas, candidatos, regra, nota,
                     texto enviado ao LLM e o contrato gerado. Retenção: definir com a Segurança (coluna `expira_em`).
  entrevista_evento  cada chamada ao LLM ou ação (criar, responder, contrato, fallback): modelo, tokens, custo, segundos, conteúdo
                     da resposta (nunca o prompt inteiro).
As tabelas são criadas na primeira conexão (idempotente), também num banco restaurado do dump.
"""
import datetime
import json

from psycopg.types.json import Jsonb

DDL = """
CREATE TABLE IF NOT EXISTS apf.entrevista (
    id            uuid PRIMARY KEY,
    cd_sistema    integer,
    sigla         text NOT NULL,
    sistema_id    integer,
    intencao      text NOT NULL,
    rodada        integer NOT NULL DEFAULT 1,
    situacao      text NOT NULL DEFAULT 'EM_ANDAMENTO',   -- EM_ANDAMENTO | PRONTA | CONTRATO_GERADO
    analise_ia    boolean NOT NULL DEFAULT true,
    modo_pii      text NOT NULL,
    nota          integer,
    custo_usd     numeric(10,5) NOT NULL DEFAULT 0,
    estado        jsonb NOT NULL,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    expira_em     timestamptz
);
CREATE TABLE IF NOT EXISTS apf.entrevista_evento (
    id             bigserial PRIMARY KEY,
    entrevista_id  uuid NOT NULL REFERENCES apf.entrevista(id) ON DELETE CASCADE,
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
CREATE INDEX IF NOT EXISTS entrevista_evento_entrevista_idx ON apf.entrevista_evento (entrevista_id);
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
    conn.execute("""INSERT INTO apf.entrevista (id, cd_sistema, sigla, sistema_id, intencao, analise_ia, modo_pii, nota, estado, expira_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (eid, req.cd_sistema, req.sigla, sistema_id, req.intencao.value, analise_ia, modo_pii, nota, Jsonb(estado), expira))


def salvar(conn, eid, rodada, situacao, analise_ia, nota, estado):
    conn.execute("""UPDATE apf.entrevista SET rodada = %s, situacao = %s, analise_ia = %s, nota = %s, estado = %s, atualizado_em = now(),
                           custo_usd = (SELECT coalesce(sum(custo_usd), 0) FROM apf.entrevista_evento WHERE entrevista_id = %s)
                    WHERE id = %s""", (rodada, situacao, analise_ia, nota, Jsonb(estado), eid, eid))


def evento(conn, eid, rodada, tipo, info=None, conteudo=None):
    info = info or {}
    conn.execute("""INSERT INTO apf.entrevista_evento (entrevista_id, rodada, tipo, modelo, tokens_entrada, tokens_saida, custo_usd, segundos, conteudo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (eid, rodada, tipo, info.get("modelo"), info.get("tokens_entrada"), info.get("tokens_saida"), info.get("custo_usd"),
                  info.get("segundos"), Jsonb(json.loads(json.dumps(conteudo, default=str))) if conteudo is not None else None))


def obter(conn, eid):
    """Linha da entrevista ou None (id inválido também dá None)."""
    garante(conn)
    try:
        return conn.execute("SELECT * FROM apf.entrevista WHERE id = %s::uuid", (eid,)).fetchone()
    except Exception:  # noqa: BLE001  (uuid malformado)
        conn.rollback()
        return None
