"""Busca 100% vetorial (cópia do runtime de PRE-PROJETO/app/busca_vetorial.py).

  candidatos    candidatos de funcionalidade da entrevista (desde 05/11/2026; dados/melhora_catalogo.txt): fusão RRF de três
                listas sobre as funções vigentes do sistema — vetor do texto ENRIQUECIDO (nome + arquivos + campos da contagem
                de criação), palavra-chave (BM25) e histórico (chamados anteriores parecidos → funções que eles alteraram) —
                e, se houver LLM, reordenação das 30 primeiras (`reordena`). Top-5 certo: 19% → 43% (34% sem o LLM).
  catalogo      só vetor do texto simples "<tipo> <nome> (<módulo>)" (modo antigo; usado com SOSIA_EMB=e5);
  wiki_paginas  páginas da wiki mais próximas (RRF entre consultas), criadas antes da data;
  wiki_trechos  k trechos mais próximos de um vetor, no máximo 1 por página.
Catálogo e wiki seguem o provedor configurado (SOSIA_EMB; padrão 3-large desde 05/11/2026, coluna embedding_3l).
"""
import collections
import math
import os
import re
import unicodedata

from .. import config, embeddings, llm
from .busca import RRF_K

PROFUNDIDADE = 30       # candidatos de cada lista e os que vão para a reordenação
VIZINHOS = 15           # chamados anteriores parecidos (histórico)
MAX_TD = 30
PROMPT_REORDENA = os.path.join(config.RAIZ, "prompts", "reordena_catalogo_v1.md")
SCHEMA_REORDENA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ids"],
                   "additionalProperties": False}

vetor = embeddings.vetor_sql


def embeda_catalogo(conn):
    """Gera o vetor das funções do catálogo que ainda não têm (ingestão; incremental), no provedor configurado. Com o 3-large, a
    coluna do e5 fica vazia (o e5 do catálogo não é usado pelo histórico, que tem o próprio cache)."""
    rows = conn.execute("""SELECT f.id, f.tipo_funcao, f.nome_canonico, coalesce(f.modulo, '') modulo FROM apf.funcao_sistema f
                           LEFT JOIN apf.funcao_sistema_emb e ON e.funcao_sistema_id = f.id
                           WHERE e.funcao_sistema_id IS NULL""").fetchall()
    for i in range(0, len(rows), 512):
        lote = rows[i:i + 512]
        txt = [f"{r['tipo_funcao']} {r['nome_canonico'].lower()}" + (f" ({r['modulo'].lower()})" if r["modulo"] else "") for r in lote]
        emb, _ = embeddings.passagens(txt)
        col = embeddings.coluna_catalogo()
        modelo = embeddings.E5_MODELO if col == "embedding" else config.MODELO_EMBEDDING
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO apf.funcao_sistema_emb (funcao_sistema_id, texto, modelo, {col}) VALUES (%s,%s,%s,%s::vector) "
                            "ON CONFLICT (funcao_sistema_id) DO NOTHING",
                            [(r["id"], t, modelo, vetor(e)) for r, t, e in zip(lote, txt, emb)])
        conn.commit()
    return len(rows)


def catalogo(conn, sistema_id, dt, consultas, k):
    """{fid: (cosseno máximo, índice da consulta que o trouxe)} das k funções mais próximas de cada consulta."""
    if not consultas:
        return {}
    col = embeddings.coluna_catalogo()
    out = {}
    for qi, q in enumerate(consultas):
        v = embeddings.consulta_sql(q)
        rows = conn.execute(
            f"""SELECT f.id, 1 - (e.{col} <=> %s::vector) cos FROM apf.funcao_sistema_emb e JOIN apf.funcao_sistema f ON f.id = e.funcao_sistema_id
               WHERE e.{col} IS NOT NULL AND f.sistema_id = %s AND f.vigente_desde < %s AND (f.vigente_ate IS NULL OR f.vigente_ate >= %s)
               ORDER BY (e.{col} <=> %s::vector) + 0 LIMIT %s""", (v, sistema_id, dt, dt, v, k)).fetchall()   # + 0: busca exata
        for r in rows:
            if r["id"] not in out or r["cos"] > out[r["id"]][0]:
                out[r["id"]] = (float(r["cos"]), qi)
    return out


def _hnsw(conn):
    try:
        conn.execute("SET hnsw.ef_search = 200")
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
    except Exception:  # noqa: BLE001
        conn.rollback()


def wiki_paginas(conn, sistema, dt, consultas, k=60, n_paginas=10):
    """Páginas (título) mais próximas, só pelo vetor: melhor cosseno por página, somado entre consultas por RRF."""
    if not consultas or not sistema:
        return []
    col = embeddings.coluna_rag()
    embs = [embeddings.consulta_sql(q) for q in consultas]
    _hnsw(conn)
    esc = collections.defaultdict(float)
    for v in embs:
        rows = conn.execute(
            f"""SELECT titulo_pagina, origem_dokuwiki, 1 - ({col} <=> %(v)s::vector) cos FROM apf.conhecimento_trecho
               WHERE {col} IS NOT NULL AND %(s)s = ANY(sistemas_acesso) AND tipo <> 'plano_de_teste'
                 AND (dt_pagina_criada IS NULL OR dt_pagina_criada < %(d)s::date)
               ORDER BY {col} <=> %(v)s::vector LIMIT %(k)s""", dict(v=v, s=sistema, d=dt, k=k)).fetchall()
        vistos = []
        for r in rows:
            if r["origem_dokuwiki"] not in [o for o, _ in vistos]:
                vistos.append((r["origem_dokuwiki"], r["titulo_pagina"]))
        for pos, (org, tit) in enumerate(vistos):
            esc[(org, tit)] += 1 / (RRF_K + pos)
    return [tit for (org, tit), _ in sorted(esc.items(), key=lambda x: -x[1])[:n_paginas]]


def wiki_trechos(conn, sistema, dt, texto, k=4, max_car=900):
    """Os k trechos da wiki mais próximos do texto (só vetorial), no máximo 1 por página: [(título / seção, texto)]."""
    if not sistema:
        return []
    col = embeddings.coluna_rag()
    _hnsw(conn)
    rows = conn.execute(
        f"""SELECT titulo_pagina, origem_dokuwiki, secao, texto FROM apf.conhecimento_trecho
           WHERE {col} IS NOT NULL AND %(s)s = ANY(sistemas_acesso) AND tipo <> 'plano_de_teste'
             AND (dt_pagina_criada IS NULL OR dt_pagina_criada < %(d)s::date)
           ORDER BY {col} <=> %(v)s::vector LIMIT %(k)s""", dict(s=sistema, d=dt, v=embeddings.consulta_sql(texto), k=k * 4)).fetchall()
    out, vistas = [], set()
    for r in rows:
        if r["origem_dokuwiki"] in vistas:
            continue
        vistas.add(r["origem_dokuwiki"])
        out.append((f"{r['titulo_pagina']} / {r['secao']}", r["texto"][:max_car]))
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------- catálogo enriquecido (05/11/2026)
def garante_rico(conn):
    """Colunas do texto enriquecido e tabela dos vetores dos chamados (migracao/sql/03_catalogo_rico.sql), idempotente."""
    conn.execute(open(os.path.join(config.RAIZ, "migracao", "sql", "03_catalogo_rico.sql"), encoding="utf-8").read())
    conn.commit()


def textos_ricos(conn, ids):
    """{fid: "<texto simples> | arquivos: ... | campos: ..."} com AR/TR e TD da contagem que criou a função."""
    base = {r["id"]: r["t"] for r in conn.execute(
        "SELECT funcao_sistema_id id, texto t FROM apf.funcao_sistema_emb WHERE funcao_sistema_id = ANY(%s)", (list(ids),))}
    ar, td = collections.defaultdict(list), collections.defaultdict(list)
    for destino, tabela, col in ((ar, "contagem_funcao_ar_tr", "nome"), (td, "contagem_funcao_td", "nome_td")):
        for r in conn.execute(f"""SELECT f.id, a.{col} nome FROM apf.funcao_sistema f
                                  JOIN apf.contagem_funcao cf ON cf.contagem_id = f.contagem_criacao_id AND cf.funcao_sistema_id = f.id
                                  JOIN apf.{tabela} a ON a.contagem_funcao_id = cf.id
                                  WHERE f.id = ANY(%s) ORDER BY a.id""", (list(base),)):
            n = r["nome"].strip() if destino is ar else r["nome"].strip()[:40]
            lst = destino[r["id"]]
            if n and n.lower() not in {x.lower() for x in lst} and (destino is ar or len(lst) < MAX_TD):
                lst.append(n)
    out = {}
    for fid, t in base.items():
        partes = [t] + ([f"arquivos: {', '.join(ar[fid])}"] if ar[fid] else []) + ([f"campos: {', '.join(td[fid])}"] if td[fid] else [])
        out[fid] = " | ".join(partes)
    return out


def preenche_rico(conn, teto_usd=None, lote=96, log=print):
    """Ingestão incremental: texto enriquecido das funções sem ele e vetores 3-large que faltam (funções e chamados SOS). Devolve US$."""
    from .. import precos
    garante_rico(conn)
    sem = [r["id"] for r in conn.execute("SELECT funcao_sistema_id id FROM apf.funcao_sistema_emb WHERE texto_rico IS NULL")]
    if sem:
        with conn.cursor() as cur:
            cur.executemany("UPDATE apf.funcao_sistema_emb SET texto_rico = %s, embedding_rico = NULL WHERE funcao_sistema_id = %s",
                            [(t, f) for f, t in textos_ricos(conn, sem).items()])
    conn.execute("""INSERT INTO apf.chamado_emb (chamado_id, texto)
                    SELECT id, left(coalesce(resumo_anon, '') || E'\\n' || coalesce(descricao_anon, ''), 4000) FROM apf.chamado
                    WHERE origem = 'SOS' AND descricao_anon IS NOT NULL ON CONFLICT DO NOTHING""")
    conn.commit()
    gasto = 0.0
    for nome, tabela, chave, ctx, cvet in (("catálogo enriquecido", "funcao_sistema_emb", "funcao_sistema_id", "texto_rico", "embedding_rico"),
                                           ("chamados", "chamado_emb", "chamado_id", "texto", "embedding_3l")):
        linhas = conn.execute(f"SELECT {chave} id, {ctx} t FROM apf.{tabela} WHERE {cvet} IS NULL AND {ctx} IS NOT NULL ORDER BY 1").fetchall()
        for i in range(0, len(linhas), lote):
            if teto_usd is not None and gasto >= teto_usd:
                log(f"parado no teto de US$ {teto_usd:.2f}")
                return gasto
            pedaco = linhas[i:i + lote]
            m, tok = embeddings.api_textos([r["t"] or "" for r in pedaco])
            gasto += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok) or 0
            with conn.cursor() as cur:
                cur.executemany(f"UPDATE apf.{tabela} SET {cvet} = %s::vector WHERE {chave} = %s",
                                [(vetor(v), r["id"]) for v, r in zip(m, pedaco)])
            conn.commit()
            log(f"  {nome}: {i + len(pedaco)}/{len(linhas)}  (US$ {gasto:.3f})")
    return gasto


_STOP = set("""para pela pelo pelas pelos com sem que nos nas dos das uma uns umas por como mais mas foi ser sao esta este esse essa
isso nao sim ate seu sua seus suas ele ela eles elas tem ter quando onde qual quais todo toda todos todas apos sobre entre
deve devem favor solicito solicitamos solicitacao sistema tela campo""".split())


def _tokens(s):
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return [w for w in re.findall(r"[a-z0-9]{3,}", s) if w not in _STOP]


def _bm25(docs, consulta, k, k1=1.2, b=0.75):
    """docs {fid: texto} → fids em ordem de BM25 para a consulta (só os com pontuação > 0)."""
    tok = {f: _tokens(t) for f, t in docs.items()}
    if not tok:
        return []
    df = collections.Counter(w for d in tok.values() for w in set(d))
    n, med = len(tok), (sum(len(d) for d in tok.values()) / len(tok)) or 1
    q = set(_tokens(consulta))
    pont = {}
    for f, d in tok.items():
        tf = collections.Counter(d)
        s = sum(math.log(1 + (n - df[w] + .5) / (df[w] + .5)) * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / med))
                for w in q if w in tf)
        if s > 0:
            pont[f] = s
    return [f for f, _ in sorted(pont.items(), key=lambda x: -x[1])[:k]]


def _historico(conn, sistema_id, dt, v, vig, k):
    """Funções vigentes alteradas por chamados anteriores parecidos (soma da similaridade dos vizinhos)."""
    viz = conn.execute(
        """SELECT h.id, 1 - (e.embedding_3l <=> %(v)s::vector) cos FROM apf.chamado_emb e JOIN apf.chamado h ON h.id = e.chamado_id
           WHERE e.embedding_3l IS NOT NULL AND h.sistema_id = %(s)s AND h.dt_abertura::date < %(d)s
             AND EXISTS (SELECT 1 FROM apf.contagem x WHERE x.chamado_id = h.id AND x.tipo = 'DETALHADA')
           ORDER BY (e.embedding_3l <=> %(v)s::vector) + 0 LIMIT %(n)s""", dict(v=v, s=sistema_id, d=dt, n=VIZINHOS)).fetchall()
    cos = {r["id"]: float(r["cos"]) for r in viz}
    pont = collections.defaultdict(float)
    if cos:
        for r in conn.execute("""SELECT DISTINCT x.chamado_id, cf.funcao_sistema_id fid FROM apf.contagem x
                                 JOIN apf.contagem_funcao cf ON cf.contagem_id = x.id
                                 WHERE x.chamado_id = ANY(%s) AND x.tipo = 'DETALHADA' AND cf.funcao_sistema_id IS NOT NULL""", (list(cos),)):
            if r["fid"] in vig:
                pont[r["fid"]] += cos[r["chamado_id"]]
    return [f for f, _ in sorted(pont.items(), key=lambda x: -x[1])[:k]]


def _rrf(*listas):
    s = collections.defaultdict(float)
    for li in listas:
        for i, f in enumerate(li, 1):
            s[f] += 1 / (RRF_K + i)
    return [f for f, _ in sorted(s.items(), key=lambda x: -x[1])]


def _tem_rico(conn):
    return conn.execute("""SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'apf'
                           AND table_name = 'funcao_sistema_emb' AND column_name = 'embedding_rico')""").fetchone()["exists"]


def candidatos(conn, sistema_id, dt, consultas, k=PROFUNDIDADE):
    """Candidatos de funcionalidade: [{id, nome, tipo, modulo, texto, similaridade}] na ordem da fusão (até k).
    Sem as colunas do catálogo enriquecido, ou com SOSIA_EMB=e5, cai no modo antigo (só vetor do texto simples)."""
    if not consultas:
        return []
    if config.EMB == "e5" or not _tem_rico(conn):
        return _candidatos_simples(conn, sistema_id, dt, consultas, k)
    q = "\n".join(consultas)
    v = embeddings.consulta_sql(q)
    vig = {r["id"]: r for r in conn.execute(
        """SELECT f.id, f.nome_canonico nome, f.tipo_funcao tipo, f.modulo, coalesce(e.texto_rico, e.texto) texto,
                  1 - (coalesce(e.embedding_rico, e.embedding_3l) <=> %(v)s::vector) cos
           FROM apf.funcao_sistema f JOIN apf.funcao_sistema_emb e ON e.funcao_sistema_id = f.id
           WHERE coalesce(e.embedding_rico, e.embedding_3l) IS NOT NULL AND f.sistema_id = %(s)s
             AND f.vigente_desde < %(d)s AND (f.vigente_ate IS NULL OR f.vigente_ate >= %(d)s)""", dict(v=v, s=sistema_id, d=dt))}
    if not vig:
        return []
    vet = [f for f, _ in sorted(((f, r["cos"]) for f, r in vig.items()), key=lambda x: -x[1])[:PROFUNDIDADE]]
    lex = _bm25({f: r["texto"] for f, r in vig.items()}, q, PROFUNDIDADE)
    his = _historico(conn, sistema_id, dt, v, vig, PROFUNDIDADE)
    ordem = _rrf(vet, lex, his)[:k]
    return [dict(id=f, nome=vig[f]["nome"], tipo=vig[f]["tipo"], modulo=vig[f]["modulo"], texto=vig[f]["texto"],
                 similaridade=round(float(vig[f]["cos"]), 3)) for f in ordem]


def _candidatos_simples(conn, sistema_id, dt, consultas, k):
    achados = catalogo(conn, sistema_id, dt, consultas, k)
    if not achados:
        return []
    rows = {r["id"]: r for r in conn.execute("SELECT id, nome_canonico, tipo_funcao, modulo FROM apf.funcao_sistema WHERE id = ANY(%s)",
                                             (list(achados),)).fetchall()}
    out = [dict(id=i, nome=rows[i]["nome_canonico"], tipo=rows[i]["tipo_funcao"], modulo=rows[i]["modulo"], texto=None,
                similaridade=round(c, 3)) for i, (c, _) in achados.items() if i in rows]
    return sorted(out, key=lambda x: -x["similaridade"])[:k]


def reordena(cands, pedido, sigla):
    """LLM escolhe as até 10 mais prováveis entre os candidatos (texto já anonimizado no modo local). Devolve (lista reordenada, info).
    Ids fora da lista são ignorados; os que o LLM não citou seguem na ordem da fusão."""
    lista = "\n".join(f"{c['id']} | {(c.get('texto') or c['nome'])[:300]}" for c in cands)
    prompt = (open(PROMPT_REORDENA, encoding="utf-8").read().replace("{{SISTEMA}}", sigla or "")
              .replace("{{PEDIDO}}", pedido[:4000]).replace("{{FUNCIONALIDADES}}", lista))
    r, info = llm.json_estruturado(prompt, SCHEMA_REORDENA, "reordena_catalogo", "chat", reasoning_effort="low")
    por_id = {c["id"]: c for c in cands}
    vistos, out = set(), []
    for i in list(r["ids"]) + list(por_id):
        if i in por_id and i not in vistos:
            vistos.add(i)
            out.append(por_id[i])
    return out, info
