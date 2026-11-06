"""Universo de funções conhecidas por sistema (todas as planilhas), com o vetor e5 de cada nome distinto em cache local
(cópia do runtime de PRE-PROJETO/app/universo_funcoes.py). Cache: SOSIA_CACHE_UNIVERSO (padrão dados/cache_universo.pkl);
nomes novos são embutidos e acrescentados ao cache na primeira chamada.
"""
import os
import pickle
import re
import unicodedata

import numpy as np

from .. import config, embeddings

CACHE = config.env("SOSIA_CACHE_UNIVERSO", os.path.join(config.RAIZ, "dados", "cache_universo.pkl"))
TIPOS = ("ALI", "AIE", "EE", "CE", "SE")


def norm(s):
    s = "".join(ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s.upper()).strip()


def ocorrencias(conn):
    return conn.execute(
        """SELECT ct.sistema_id, ct.tipo tipo_contagem, ct.dt_contagem, c.numero chamado, cf.tipo_funcao, cf.acao, cf.nome_funcao
           FROM apf.contagem_funcao cf JOIN apf.contagem ct ON ct.id = cf.contagem_id LEFT JOIN apf.chamado c ON c.id = ct.chamado_id
           WHERE cf.tipo_funcao = ANY(%s)""", (list(TIPOS),)).fetchall()


def carrega(conn):
    """(ocorrências, {(sistema, tipo, nome_norm): vetor})."""
    oc = ocorrencias(conn)
    chaves = sorted({(o["sistema_id"], o["tipo_funcao"], norm(o["nome_funcao"])) for o in oc})
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as f:
            cache = pickle.load(f)
    falta = [k for k in chaves if k not in cache]
    if falta:
        m = embeddings._e5()
        for i in range(0, len(falta), 1024):
            lote = falta[i:i + 1024]
            emb = m.encode([f"passage: {t} {n.lower()}" for _, t, n in lote], normalize_embeddings=True, batch_size=128, show_progress_bar=False)
            for k, e in zip(lote, emb):
                cache[k] = e.astype(np.float32)
            print(f"  embeddings {i + len(lote)}/{len(falta)}", flush=True)
        with open(CACHE, "wb") as f:
            pickle.dump(cache, f)
    return oc, cache
