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
  entrevista_chamado_historico  vínculos retirados quando o número do chamado foi reaproveitado (Oracle de teste restaurado).
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
CREATE TABLE IF NOT EXISTS app.entrevista_chamado_historico (   -- vínculos substituídos (número de chamado reaproveitado)
    entrevista_id   uuid NOT NULL REFERENCES app.entrevista(id) ON DELETE CASCADE,
    tipo            text NOT NULL,
    cd_solicitacao  bigint NOT NULL,
    cd_pacote       bigint,
    criado_em       timestamptz NOT NULL,
    substituido_em  timestamptz NOT NULL DEFAULT now(),
    substituido_por uuid,                         -- entrevista que recebeu o número (nulo = só retirado)
    motivo          text NOT NULL
);
CREATE INDEX IF NOT EXISTS entrevista_chamado_historico_cd_idx ON app.entrevista_chamado_historico (cd_solicitacao);
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


MOTIVO_REAPROVEITADO = "número de chamado reaproveitado (o anterior não existe mais no Oracle)"


def retira_vinculo(conn, cd_solicitacao, substituido_por=None, motivo=MOTIVO_REAPROVEITADO):
    """Move o vínculo atual do chamado para o histórico. Devolve a linha retirada (ou None se não havia vínculo)."""
    v = conn.execute("""DELETE FROM app.entrevista_chamado WHERE cd_solicitacao = %s
                        RETURNING entrevista_id, tipo, cd_solicitacao, cd_pacote, criado_em""", (cd_solicitacao,)).fetchone()
    if v:
        conn.execute("""INSERT INTO app.entrevista_chamado_historico
                        (entrevista_id, tipo, cd_solicitacao, cd_pacote, criado_em, substituido_por, motivo)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                     (v["entrevista_id"], v["tipo"], v["cd_solicitacao"], v["cd_pacote"], v["criado_em"], substituido_por, motivo))
    return v


def vincula(conn, eid, chamados, cd_pacote):
    """chamados: [(tipo, cd_solicitacao)]. cd_solicitacao é único: um chamado só pertence a uma entrevista.
    Número já ligado a OUTRA entrevista: o vínculo antigo é obsoleto (o número acabou de sair da sequence do Oracle, então o
    chamado antigo não existe mais — acontece quando o Oracle de teste é restaurado). Ele vai para o histórico e o novo entra.
    Devolve os vínculos retirados."""
    retirados = []
    for _, cd in chamados:
        atual = chamado(conn, cd)
        if atual and str(atual["entrevista_id"]) != str(eid):
            retirados.append(retira_vinculo(conn, cd, eid))
    for tipo, cd in chamados:
        conn.execute("INSERT INTO app.entrevista_chamado (entrevista_id, tipo, cd_solicitacao, cd_pacote) VALUES (%s,%s,%s,%s)",
                     (eid, tipo, cd, cd_pacote))
    return retirados


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
