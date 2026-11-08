"""Aquecimento do serviço: evita a 1ª entrevista lenta depois de horas parado (38,6 s a frio × 6,1 s quente, medido pelo SOS em
08/11/2026; em 06/11 passou de 60 s e a resposta se perdeu).

O tempo some antes do LLM: a busca lê do disco, aos pedaços, o índice HNSW da wiki (317 MB no 3-large) e os trechos. Aqui:
  1. pg_prewarm das tabelas e índices que a entrevista usa (só os da coluna de vetor configurada: no 3-large, os índices do e5
     ficam de fora), incluindo as tabelas TOAST, onde moram os vetores;
  2. uma busca de verdade (RAG da wiki + catálogo) num sistema, com texto que muda a cada vez, para exercitar também a chamada
     à API de embeddings (custo desprezível: ~10 tokens).
Roda na subida da API e a cada SOSIA_AQUECER_MIN minutos (padrão 30; 0 desliga), numa thread em segundo plano. Falha nunca derruba
o serviço: só vai para o log.
"""
import datetime
import logging
import threading
import time

from . import config
from . import db as dbm

log = logging.getLogger("uvicorn.error.aquecimento")     # filho do logger do uvicorn: aparece no `docker compose logs api`
MINUTOS = int(config.env("SOSIA_AQUECER_MIN", "30"))
SISTEMA = config.env("SOSIA_AQUECER_SISTEMA", "GAI")
# Tabelas que a entrevista lê (wiki, referências, catálogo de funções e histórico de chamados do catálogo)
TABELAS = ("conhecimento_trecho", "conhecimento_referencia", "funcao_sistema_emb", "funcao_sistema", "chamado", "chamado_emb",
           "contagem", "contagem_funcao", "sistema", "sistema_alias")
_ultimo = {}


def _relacoes(conn):
    """Tabelas, TOAST e índices a pré-carregar. Índice de vetor da coluna que não está em uso (e5 × 3-large) fica de fora."""
    from . import embeddings
    usar = embeddings.coluna_rag()                       # "embedding_3l" ou "embedding"
    outra = "embedding" if usar == "embedding_3l" else "embedding_3l"
    rels = []
    for r in conn.execute(
            """SELECT c.oid::regclass::text AS rel, c.reltoastrelid::regclass::text AS toast,
                      coalesce(array_agg(i.indexrelid::regclass::text) FILTER (WHERE i.indexrelid IS NOT NULL), '{}') AS idx,
                      coalesce(array_agg(pg_get_indexdef(i.indexrelid)) FILTER (WHERE i.indexrelid IS NOT NULL), '{}') AS defs
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'apf'
               LEFT JOIN pg_index i ON i.indrelid = c.oid
               WHERE c.relkind = 'r' AND c.relname = ANY(%s)
               GROUP BY c.oid, c.reltoastrelid""", (list(TABELAS),)).fetchall():
        rels.append(r["rel"])
        if r["toast"] and r["toast"] != "-":
            rels.append(r["toast"])
        for nome, d in zip(r["idx"], r["defs"]):
            if f"({outra} " in d or f"({outra})" in d:      # índice da outra coluna de vetor
                continue
            rels.append(nome)
    return rels


def prewarm(conn):
    """pg_prewarm das relações; devolve (blocos lidos, segundos). Sem a extensão (ou sem permissão), devolve (None, 0)."""
    t = time.time()
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm")
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        log.warning("pg_prewarm indisponível: %s", e)
        return None, 0.0
    blocos = 0
    for rel in _relacoes(conn):
        blocos += conn.execute("SELECT pg_prewarm(%s::regclass) AS n", (rel,)).fetchone()["n"]
    return blocos, round(time.time() - t, 1)


def busca_de_mentira(conn):
    """Uma busca da entrevista (wiki + catálogo) com texto novo, para passar também pela API de embeddings."""
    from .entrevista import motor
    from .entrevista.modelos import Intencao
    t = time.time()
    sid, wiki = motor.resolve_sistema(conn, SISTEMA)
    texto = f"aquecimento {datetime.datetime.now():%Y-%m-%d %H:%M} erro ao salvar o cadastro"
    motor.rag(conn, Intencao.PAROU_DE_FUNCIONAR, sid, wiki, texto)
    return round(time.time() - t, 1)


def aquecer():
    """Uma rodada de aquecimento. Devolve o resumo (também guardado em `_ultimo`, para o log e para os testes)."""
    t = time.time()
    res = dict(inicio=datetime.datetime.now().isoformat(timespec="seconds"))
    try:
        conn = dbm.conectar(timeout=10)
        try:
            res["blocos"], res["prewarm_s"] = prewarm(conn)
            try:
                res["busca_s"] = busca_de_mentira(conn)
            except Exception as e:  # noqa: BLE001  (sem chave de embeddings, por exemplo: o prewarm já ajudou)
                conn.rollback()
                res["busca_erro"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        res["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    res["total_s"] = round(time.time() - t, 1)
    _ultimo.clear()
    _ultimo.update(res)
    log.info("aquecimento: %s", res)
    return res


def _laco():
    while True:
        aquecer()
        time.sleep(MINUTOS * 60)


def inicia():
    """Chamado na subida da API (motor real). Thread daemon: não segura o desligamento."""
    if config.MOCK or MINUTOS <= 0:
        return None
    th = threading.Thread(target=_laco, name="sosia-aquecimento", daemon=True)
    th.start()
    return th
