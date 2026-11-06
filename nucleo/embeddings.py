"""Embeddings do núcleo.

Dois provedores (SOSIA_EMB), para o RAG da wiki (conhecimento_trecho) e o catálogo de funções (funcao_sistema_emb):
  azure  PADRÃO desde 05/11/2026. text-embedding-3-large (dimensions=1024) pela API (OpenAI nos testes, Azure no contrato), na
         coluna `embedding_3l`. Corpus inteiro × e5 (dados/avalia_todos.log): empata na wiki e ganha no catálogo (@10 20,2→25,9%,
         p=0,002). Mesmo modelo do ANIA. A API não precisa de modelo local.
  e5     intfloat/multilingual-e5-large local (coluna `embedding`). Opcional: o histórico de complexidade (Fase 3) também segue
         SOSIA_EMB desde 06/11/2026 (limiar 0,95 no e5, 0,78 no 3-large; ver estimador/historico.py).
Ingestão: `passagens` gera o vetor no provedor configurado; `preenche_3l` completa o que estiver sem vetor 3-large (wiki e catálogo).
"""
import functools
import os
import threading

import numpy as np

from . import config

E5_MODELO = config.env("SOSIA_E5_MODELO", "intfloat/multilingual-e5-large")
DIM = 1024
_modelo = None
_trava = threading.Lock()


def coluna_rag():
    """Coluna de vetor do provedor configurado (a mesma nas duas tabelas: conhecimento_trecho e funcao_sistema_emb)."""
    return "embedding" if config.EMB == "e5" else "embedding_3l"


coluna_catalogo = coluna_rag


# ------------------------------------------------------------------ e5 local
def _e5():
    global _modelo
    with _trava:
        if _modelo is None:
            import torch
            from sentence_transformers import SentenceTransformer
            gpu = torch.cuda.is_available() and config.env("SOSIA_E5_GPU", "1") == "1"
            m = SentenceTransformer(E5_MODELO, device="cuda" if gpu else "cpu")
            if gpu:
                m.half()
            m.max_seq_length = 512
            _modelo = m
    return _modelo


def e5_passagens(textos, prefixo="passage: ", batch=64):
    return _e5().encode([prefixo + t[:2000] for t in textos], normalize_embeddings=True, batch_size=batch, show_progress_bar=False)


@functools.lru_cache(maxsize=4000)
def _e5_consulta(texto):
    return _e5().encode(["query: " + texto[:4000]], normalize_embeddings=True, show_progress_bar=False)[0]


def e5_consulta(texto):
    return _e5_consulta(texto)


# ------------------------------------------------------------------ API (OpenAI / Azure)
def api_textos(textos):
    """Vetores normalizados do text-embedding-3-large (1024 dimensões). Devolve (matriz, tokens)."""
    from . import llm
    cli = llm.cliente()
    r = cli.embeddings.create(model=config.MODELO_EMBEDDING, input=[t[:8000] for t in textos], dimensions=DIM)
    m = np.array([d.embedding for d in r.data], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m, r.usage.total_tokens


@functools.lru_cache(maxsize=4000)
def _api_consulta(texto):
    return api_textos([texto])[0][0]


# ------------------------------------------------------------------ interface do RAG
def vetor_sql(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def consulta_sql(texto):
    """Literal pgvector da consulta, no provedor do RAG."""
    return vetor_sql(e5_consulta(texto) if config.EMB == "e5" else _api_consulta(texto))


def passagens(textos, prefixo="passage: "):
    """Vetores de textos a indexar, no provedor configurado. Devolve (matriz, tokens; tokens=0 no e5)."""
    if config.EMB == "e5":
        return e5_passagens(textos, prefixo), 0
    return api_textos(textos)


def garante_3l(conn):
    """Colunas e índices do 3-large (migracao/sql/02_embedding_3l.sql), idempotente."""
    conn.execute(open(os.path.join(config.RAIZ, "migracao", "sql", "02_embedding_3l.sql"), encoding="utf-8").read())
    conn.commit()


def preenche_3l(conn, teto_usd=None, lote=96, log=print):
    """Gera o vetor 3-large do que estiver sem ele (wiki: texto_indexado; catálogo: texto). Incremental; para no teto. Devolve US$."""
    from . import precos
    garante_3l(conn)
    gasto = 0.0
    for nome, tabela, chave, col_txt in (("wiki", "conhecimento_trecho", "id", "texto_indexado"),
                                         ("catálogo", "funcao_sistema_emb", "funcao_sistema_id", "texto")):
        linhas = conn.execute(f"SELECT {chave} id, {col_txt} t FROM apf.{tabela} WHERE embedding_3l IS NULL ORDER BY 1").fetchall()
        for i in range(0, len(linhas), lote):
            if teto_usd is not None and gasto >= teto_usd:
                log(f"parado no teto de US$ {teto_usd:.2f} (rode de novo para continuar)")
                return gasto
            pedaco = linhas[i:i + lote]
            m, tok = api_textos([r["t"] or "" for r in pedaco])
            gasto += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok) or 0
            with conn.cursor() as cur:
                cur.executemany(f"UPDATE apf.{tabela} SET embedding_3l = %s::vector WHERE {chave} = %s",
                                [(vetor_sql(v), r["id"]) for v, r in zip(m, pedaco)])
            conn.commit()
            log(f"  {nome}: {i + len(pedaco)}/{len(linhas)}  (US$ {gasto:.3f})")
    return gasto


def disponivel():
    if config.EMB == "e5":
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
