"""Recuperação de documentação (wiki) para um texto: busca híbrida (vetor + texto completo, RRF) + peso por tipo + expansão de
referências. Cópia de PRE-PROJETO/app/busca_docs.py (calibrada em 27/10/2026); só a geração do vetor mudou (nucleo.embeddings,
coluna conforme SOSIA_EMB). Tabelas: apf.conhecimento_trecho / conhecimento_referencia.
"""
import re
import time

from .. import embeddings
from .escopo import Escopo

K_CANDIDATOS = 60       # candidatos de cada busca (vetorial e textual) antes da fusão
RRF_K = 60
MAX_POR_PAGINA = 3      # evita que um documento só ocupe o topo
MAX_EXPANSAO_CAR = 900  # por referência


def embedding_consulta(texto):
    return embeddings.consulta_sql(texto)


def filtro_sistema(sistema, prefixo="", por_dono=False):
    """(SQL, parâmetros) do filtro de wiki: `sistema` é uma chave ("gai"), uma lista de chaves ou um Escopo
    (motor.resolve_sistema; ver escopo.py)."""
    return Escopo.de(sistema).filtro(prefixo, por_dono)


def termos(texto):
    """Palavras e códigos (RN152, UC002) do chamado para a busca textual (OR, sem duplicar)."""
    vistos, saida = set(), []
    for w in re.findall(r"[A-Za-zÀ-ÿ0-9_]{3,}", texto):
        k = w.lower()
        if k not in vistos:
            vistos.add(k)
            saida.append(k)
    return saida[:60]


def sistemas_indexados(conn):
    return conn.execute(
        """SELECT sistema, max(situacao) AS situacao, max(categoria) AS categoria, count(*) AS trechos
           FROM apf.conhecimento_trecho GROUP BY sistema ORDER BY sistema""").fetchall()


def expande(conn, resultados):
    """Anexa a cada resultado os textos de RN/MN/FE/FA citados (Tela só como rótulo) e 'citada por'."""
    por_id = {r["id"]: r for r in resultados}
    vistos = set()
    for ref in conn.execute(
            """SELECT r.origem_trecho_id, r.tipo_ref, r.codigo, r.resolvido, r.texto_destino,
                      d.titulo_pagina, d.secao, d.id AS destino_id
               FROM apf.conhecimento_referencia r LEFT JOIN apf.conhecimento_trecho d ON d.id = r.destino_trecho_id
               WHERE r.origem_trecho_id = ANY(%s) ORDER BY r.id""", (list(por_id),)).fetchall():
        alvo = por_id[ref["origem_trecho_id"]]
        if ref["tipo_ref"] == "Tela":                      # tela: só o rótulo, sem texto
            alvo["refs_expandidas"].append({**ref, "texto_destino": None})
            continue
        chave = (ref["origem_trecho_id"], ref["codigo"])
        if chave in vistos:
            continue
        vistos.add(chave)
        tx = (ref["texto_destino"] or "")[:MAX_EXPANSAO_CAR]
        alvo["refs_expandidas"].append({**ref, "texto_destino": tx})
    # quem cita este trecho (impacto): útil quando o resultado é uma RN/MN
    for r in resultados:
        if r["tipo"] in ("regras_de_negocio", "mensagens", "glossario"):
            r["citada_por"] = conn.execute(
                """SELECT DISTINCT o.titulo_pagina, o.tipo FROM apf.conhecimento_referencia c
                   JOIN apf.conhecimento_trecho o ON o.id = c.origem_trecho_id
                   WHERE c.destino_trecho_id = %s AND o.id <> %s ORDER BY o.titulo_pagina LIMIT 8""",
                (r["id"], r["id"])).fetchall()


# Pesos por tipo de documento (multiplicam o RRF). PESO_BASE é o que está gravado em `peso` (frontmatter do vault); CFG é o que
# a busca usa de fato e pode ser calibrado (ver calibra_pesos.py). Trecho de seção de tela vem com peso base x 0,6 no banco.
PESO_BASE = {"caso_de_uso": 1.0, "user_story": 0.85, "plano_de_teste": 0.4, "regras_de_negocio": 0.8, "glossario": 0.8,
             "mensagens": 0.8, "eventos": 0.8, "documento": 0.6, "indice": 0.3}
# Calibrado em 27/10/2026 com o gabarito automático (665 chamados, método LLM; wiki\resultados\calibra_resultado.txt):
# UC = US (a fábrica adota US nos contratos novos), RN/mensagens/glossário/eventos baixos (chegam pela expansão de
# referências, e competiam com UC/US no ranking), seção de tela sem desconto e sem bônus de tipo/termo. Em validação
# cruzada (5 partes) o recall@1 foi de 27,0% para 31,3% e o @5 de 50,9% para 53,1%.
CFG_27_10 = {"pesos": {**PESO_BASE, "user_story": 1.0, "regras_de_negocio": 0.2, "glossario": 0.2, "mensagens": 0.2, "eventos": 0.2},
             "fator_tela": 1.0, "bonus_tipo": 1.0, "bonus_termo": 1.0, "prop": 0.5}
# Recalibrado em 05/11/2026 no núcleo (migracao\calibra_wiki.py, dados\calibra_wiki.txt) com o gabarito OBJETIVO — páginas que a
# fábrica alterou por chamado, 556 chamados, vetor 3-large, configuração da entrevista — e validação cruzada em 5 partes (a mesma
# escolha nas 5). Os pesos de 27/10 rebaixavam RN/mensagens, que são 28% das páginas alteradas. Fora da amostra: MRR 48,2% → 69,2%,
# @5 41,5% → 66,7% (pesos neutros: 56,9% / 57,9%). Valores além da borda da grade dão o mesmo (platô).
# "peso_texto": peso da lista da palavra-chave no RRF (o vetor vale 1). A PRE-PROJETO continua com CFG_27_10.
CFG = {"pesos": {**PESO_BASE, "caso_de_uso": 1.0, "user_story": 1.0, "regras_de_negocio": 1.2, "mensagens": 0.8, "glossario": 0.4,
                 "eventos": 0.4, "documento": 0.6, "indice": 0.3},
       "fator_tela": 0.6, "peso_texto": 0.25, "bonus_tipo": 1.0, "bonus_termo": 1.0, "prop": 0.5}
# "prop": quanto o acerto de uma RN/mensagem/glossário/evento (pelo RRF sem peso) vale para as páginas que a citam (UC/US).
# 0 desliga. PROP_TOP: quantos desses trechos de cada consulta propagam.
AUX = ("regras_de_negocio", "mensagens", "glossario", "eventos")
PROP_TOP = 5


def peso_efetivo(x, cfg=None):
    cfg = cfg or CFG
    tipo, p = x["tipo"], float(x["peso"])
    base = PESO_BASE.get(tipo)
    if base is None:
        return p
    tela = p < base * 0.99
    return cfg["pesos"].get(tipo, base) * (cfg["fator_tela"] if tela else 1.0)


def _candidatos(conn, texto, sistema, incluir_teste, so_ativos, ate_data):
    """Parte que acessa o banco: ranks vetorial e textual (sem peso) e as linhas dos trechos candidatos."""
    t0 = time.time()
    v = embedding_consulta(texto)
    t_emb = time.time() - t0
    col = embeddings.coluna_rag()
    filtro, par = [f"{col} IS NOT NULL"], {"v": v, "k": K_CANDIDATOS}
    if sistema:
        sql, p = filtro_sistema(sistema)
        filtro.append(sql)
        par.update(p)
    if not incluir_teste:
        filtro.append("tipo <> 'plano_de_teste'")
    if ate_data:                          # só páginas criadas antes do dia do chamado
        filtro.append("(dt_pagina_criada IS NULL OR dt_pagina_criada < %(ate)s::date)")
        par["ate"] = ate_data
    if so_ativos:
        filtro.append("(situacao = 'ATIVO' OR situacao IS NULL)")
    where = " AND ".join(filtro)
    t1 = time.time()
    try:
        conn.execute("SET LOCAL hnsw.ef_search = 200")
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")   # pgvector >= 0.8: filtro não esvazia o top-K
    except Exception:
        conn.rollback()
    vet = conn.execute(
        f"""SELECT id, 1 - ({col} <=> %(v)s::vector) AS cos FROM apf.conhecimento_trecho
            WHERE {where} ORDER BY {col} <=> %(v)s::vector LIMIT %(k)s""", par).fetchall()
    ts = termos(texto)
    txt = []
    if ts:
        par["q"] = " | ".join(ts)
        try:
            txt = conn.execute(
                f"""WITH q AS (SELECT to_tsquery('portuguese', %(q)s) AS q)
                    SELECT id, ts_rank_cd(tsv, q.q) AS rk FROM apf.conhecimento_trecho, q
                    WHERE {where} AND tsv @@ q.q ORDER BY rk DESC LIMIT %(k)s""", par).fetchall()
        except Exception:
            conn.rollback()
    rank_v = {r["id"]: i + 1 for i, r in enumerate(vet)}
    cos = {r["id"]: float(r["cos"]) for r in vet}
    rank_t = {r["id"]: i + 1 for i, r in enumerate(txt)}
    ids = set(rank_v) | set(rank_t)
    linhas = {}
    if ids:
        linhas = {r["id"]: r for r in conn.execute(
            """SELECT id, sistema, tipo, peso, situacao, titulo_pagina, secao, arquivo_vault, origem_dokuwiki, texto,
                      definicoes, refs FROM apf.conhecimento_trecho WHERE id = ANY(%s)""", (list(ids),)).fetchall()}
    return dict(linhas=linhas, rank_v=rank_v, rank_t=rank_t, cos=cos, termos=ts, t_emb=t_emb, t_busca=time.time() - t1)


def _seleciona(c, n, max_por_pagina, cfg=None, uma_por_secao=False):
    """Parte pura: RRF x peso do tipo, no máximo `max_por_pagina` trechos por página. `uma_por_secao`: só o melhor trecho de
    cada seção (seção longa vira vários trechos; sem isso, dois trechos da mesma seção ocupavam as vagas da página)."""
    linhas, rank_v, rank_t = c["linhas"], c["rank_v"], c["rank_t"]
    pt = (cfg or CFG).get("peso_texto", 1.0)
    pontuados = []
    for i in linhas:
        rrf = (1 / (RRF_K + rank_v[i]) if i in rank_v else 0) + (pt * (1 / (RRF_K + rank_t[i])) if i in rank_t else 0)
        pontuados.append((rrf * peso_efetivo(linhas[i], cfg), i, rrf))
    pontuados.sort(reverse=True)
    resultados, por_pagina, secoes = [], {}, set()
    for score, i, rrf in pontuados:
        pg = linhas[i]["origem_dokuwiki"]
        if por_pagina.get(pg, 0) >= max_por_pagina:
            continue
        if uma_por_secao:
            k = (pg, linhas[i]["secao"] or "")
            if k in secoes:
                continue
            secoes.add(k)
        por_pagina[pg] = por_pagina.get(pg, 0) + 1
        r = dict(linhas[i])
        r.update(score=score, rrf=rrf, rank_vetor=rank_v.get(i), rank_texto=rank_t.get(i), cos=c["cos"].get(i),
                 peso_usado=round(peso_efetivo(r, cfg), 3), refs_expandidas=[], citada_por=[])
        resultados.append(r)
        if len(resultados) >= n:
            break
    return resultados


def busca(conn, texto, sistema=None, n=8, incluir_teste=True, so_ativos=False, expandir=True, max_por_pagina=MAX_POR_PAGINA,
          ate_data=None, uma_por_secao=False):
    c = _candidatos(conn, texto, sistema, incluir_teste, so_ativos, ate_data)
    resultados = _seleciona(c, n, max_por_pagina, uma_por_secao=uma_por_secao)
    if expandir and resultados:
        expande(conn, resultados)
    return {"resultados": resultados, "t_emb": c["t_emb"], "t_busca": c["t_busca"], "termos": c["termos"]}


def segmenta(texto):
    """Divide o chamado em assuntos: linha curta (sem ponto final) = título; ela e o que vem depois formam um segmento.

    Um texto com vários pedidos num só embedding dilui todos; por segmento cada pedido acha o seu artefato.
    Sem títulos detectados, devolve o texto inteiro como um único segmento.
    """
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    segs, cur = [], []
    for l in linhas:
        titulo = len(l) < 70 and not l.endswith((".", ":", ";", ","))
        if titulo and cur and any(not (len(c) < 70 and not c.endswith((".", ":", ";", ","))) for c in cur):
            segs.append(cur)
            cur = []
        cur.append(l)
    if cur:
        segs.append(cur)
    saida = [{"titulo": c[0] if len(c) > 1 and len(c[0]) < 70 else c[0][:60], "texto": " ".join(c)} for c in segs]
    return saida if len(saida) > 1 else [{"titulo": "Chamado", "texto": texto}]


def busca_chamado(conn, texto, sistema=None, n=5, incluir_teste=True, so_ativos=False, expandir=True, ate_data=None):
    """Busca por segmento do chamado, um artefato por página em cada segmento."""
    segs = segmenta(texto)
    t_emb = t_busca = 0.0
    for sg in segs:
        r = busca(conn, sg["texto"], sistema, n, incluir_teste, so_ativos, expandir, max_por_pagina=1, ate_data=ate_data)
        sg["resultados"], sg["termos"] = r["resultados"], r["termos"]
        t_emb += r["t_emb"]
        t_busca += r["t_busca"]
    return {"segmentos": segs, "t_emb": t_emb, "t_busca": t_busca}


def _stem(txt):
    """Minúsculas, sem acentos e sem 's' final por palavra (requerimentos ~ requerimento)."""
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", txt.casefold()) if unicodedata.category(c) != "Mn")
    return " ".join(w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", t))


MAX_PAGINAS_TITULO = 8   # termo que casa com mais títulos que isso é genérico demais (ex.: "Parcelamento")


def paginas_por_titulo(conn, termos_ex, sistema, incluir_teste, ate_data=None):
    """Páginas cujo título contém algum termo exato (nome de tela citado no chamado), sem exigir que a busca
    semântica ou por palavras as traga. -> lista de linhas (1º trecho da página), das mais curtas às mais longas."""
    if not termos_ex:
        return []
    filtro, par = ["tipo NOT IN ('indice')"], {}
    if sistema:
        sql, p = filtro_sistema(sistema)
        filtro.append(sql)
        par.update(p)
    if not incluir_teste:
        filtro.append("tipo <> 'plano_de_teste'")
    if ate_data:                          # só páginas criadas antes do dia do chamado
        filtro.append("(dt_pagina_criada IS NULL OR dt_pagina_criada < %(ate)s::date)")
        par["ate"] = ate_data
    titulos = conn.execute(
        f"SELECT DISTINCT origem_dokuwiki, titulo_pagina FROM apf.conhecimento_trecho WHERE {' AND '.join(filtro)}",
        par).fetchall()
    stems = {t: _stem(t) for t in termos_ex}
    stems = {t: st for t, st in stems.items() if len(st) >= 6}
    achadas = {}
    for t, st in stems.items():
        casam = [r for r in titulos if st in _stem(r["titulo_pagina"])]
        if 0 < len(casam) <= MAX_PAGINAS_TITULO:
            for r in casam:
                achadas[r["origem_dokuwiki"]] = r["titulo_pagina"]
    if not achadas:
        return []
    linhas = conn.execute(
        """SELECT DISTINCT ON (origem_dokuwiki) id, sistema, tipo, peso, situacao, titulo_pagina, secao, arquivo_vault,
                  origem_dokuwiki, texto, definicoes, refs FROM apf.conhecimento_trecho
           WHERE origem_dokuwiki = ANY(%s) ORDER BY origem_dokuwiki, ordem""", (list(achadas),)).fetchall()
    return sorted(linhas, key=lambda r: len(r["titulo_pagina"]))


BONUS_TIPO = CFG["bonus_tipo"]      # (referência: os valores em uso estão em CFG)
BONUS_TERMO = CFG["bonus_termo"]


def citantes(conn, ids_aux, sistema, ate_data):
    """{id do trecho RN/MN/...: [uma linha por página UC/US/documento que o cita]} (restrita ao sistema e à data)."""
    if not ids_aux:
        return {}
    filtro, par = ["c.destino_trecho_id = ANY(%(ids)s)", "o.tipo IN ('caso_de_uso', 'user_story', 'documento')",
                   f"o.{embeddings.coluna_rag()} IS NOT NULL"], {"ids": list(ids_aux)}
    if sistema:                           # páginas da própria wiki (coluna sistema), como antes
        sql, p = filtro_sistema(sistema, "o.", por_dono=True)
        filtro.append(sql)
        par.update(p)
    if ate_data:
        filtro.append("(o.dt_pagina_criada IS NULL OR o.dt_pagina_criada < %(ate)s::date)")
        par["ate"] = ate_data
    saida = {}
    for r in conn.execute(
            f"""SELECT DISTINCT ON (c.destino_trecho_id, o.origem_dokuwiki) c.destino_trecho_id AS aux_id, o.id, o.sistema,
                       o.tipo, o.peso, o.situacao, o.titulo_pagina, o.secao, o.arquivo_vault, o.origem_dokuwiki, o.texto,
                       o.definicoes, o.refs
                FROM apf.conhecimento_referencia c JOIN apf.conhecimento_trecho o ON o.id = c.origem_trecho_id
                WHERE {' AND '.join(filtro)} ORDER BY c.destino_trecho_id, o.origem_dokuwiki, o.id""", par).fetchall():
        saida.setdefault(r.pop("aux_id"), []).append(r)
    return saida


def votos_aux(c, top=None):
    """Os `top` trechos RN/MN/... de um candidato por RRF sem peso: [(posição, id)]."""
    rv, rt = c["rank_v"], c["rank_t"]
    pont = []
    for i, r in c["linhas"].items():
        if r["tipo"] in AUX:
            pont.append(((1 / (RRF_K + rv[i]) if i in rv else 0) + (1 / (RRF_K + rt[i]) if i in rt else 0), i))
    pont.sort(reverse=True)
    return [(p, i) for p, (_, i) in enumerate(pont[:top or PROP_TOP], 1)]


def plano_requisito(conn, rag, sistema=None, incluir_teste=True, so_ativos=False, ate_data=None):
    """Parte que acessa o banco na recuperação de um requisito: candidatos de cada consulta (principal + alternativas +
    termos exatos) e páginas cujo título contém um termo exato. Não depende dos pesos."""
    consultas = [rag["consulta_principal"]] + list(rag.get("consultas_alternativas") or [])[:3]
    termos_ex = []
    for t in (rag.get("termos_exatos") or []):
        # caminho de menu ("Cadastro - NUGIV - Entrada de Negociação - Geral") vira também as suas partes
        partes = [p.strip() for p in re.split(r"\s+[-–>›/]+\s+", t) if len(p.strip()) >= 4]
        for x in [t] + (partes if len(partes) > 1 else []):
            if x.strip() and x not in termos_ex:
                termos_ex.append(x)
    if termos_ex:
        consultas.append(" ".join(termos_ex))
    cands = [_candidatos(conn, q, sistema, incluir_teste, so_ativos, ate_data) for q in consultas]
    ids_aux = {i for c in cands for _, i in votos_aux(c)}
    return dict(consultas=consultas, tipos=set(rag.get("tipos_documento") or []), stems=[_stem(t) for t in termos_ex],
                cands=cands, cit=citantes(conn, ids_aux, sistema, ate_data), titulo=paginas_por_titulo(conn, termos_ex, sistema, incluir_teste, ate_data),
                t_emb=sum(c["t_emb"] for c in cands), t_busca=sum(c["t_busca"] for c in cands))


def pontua_requisito(plano, n=5, cfg=None):
    """Parte pura: funde as consultas por página (RRF) e aplica peso do tipo e bônus de tipo pedido / termo no título."""
    cfg = cfg or CFG
    fund = {}
    for c in plano["cands"]:
        for pos, x in enumerate(_seleciona(c, 12, 1, cfg), 1):
            e = fund.setdefault(x["origem_dokuwiki"], {"x": x, "rrf": 0.0, "consultas": 0})
            e["rrf"] += 1 / (RRF_K + pos)
            e["consultas"] += 1
            if x["score"] > e["x"]["score"]:
                e["x"] = x
    if cfg.get("prop"):                   # o acerto numa RN/mensagem conta para as páginas que a citam
        for c in plano["cands"]:
            for pos, i in votos_aux(c):
                for x in plano["cit"].get(i, []):
                    e = fund.setdefault(x["origem_dokuwiki"], {"x": dict(x, score=0.0, rrf=0.0), "rrf": 0.0, "consultas": 0})
                    e["rrf"] += cfg["prop"] / (RRF_K + pos)
                    e["consultas"] += 1
    for pos, x in enumerate(plano["titulo"], 1):
        e = fund.setdefault(x["origem_dokuwiki"], {"x": dict(x, score=0.0, rrf=0.0), "rrf": 0.0, "consultas": 0})
        e["rrf"] += 1 / (RRF_K + pos)
        e["consultas"] += 1
    saida = []
    for e in fund.values():
        x = dict(e["x"])
        bonus = 1.0
        if x["tipo"] in plano["tipos"]:
            bonus *= cfg["bonus_tipo"]
        tit = _stem(x["titulo_pagina"])
        if any(s and s in tit for s in plano["stems"]):
            bonus *= cfg["bonus_termo"]
        x.update(score=e["rrf"] * peso_efetivo(x, cfg) * bonus, rrf=e["rrf"], consultas_acertadas=e["consultas"],
                 peso_usado=round(peso_efetivo(x, cfg), 3),
                 refs_expandidas=[], citada_por=[])
        saida.append(x)
    saida.sort(key=lambda x: x["score"], reverse=True)
    return saida[:n]


def busca_requisito(conn, rag, sistema=None, n=5, incluir_teste=True, so_ativos=False, expandir=True, ate_data=None):
    """Recuperação de um requisito decomposto (ver plano_requisito / pontua_requisito)."""
    plano = plano_requisito(conn, rag, sistema, incluir_teste, so_ativos, ate_data)
    saida = pontua_requisito(plano, n)
    if expandir:
        expande(conn, saida)
    return {"resultados": saida, "t_emb": plano["t_emb"], "t_busca": plano["t_busca"], "consultas": plano["consultas"]}
