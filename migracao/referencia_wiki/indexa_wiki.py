"""Indexa o vault da wiki (D:\\Projetos\\Terrawiki) no pgvector: trechos por seção + referências entre eles.

Etapas:  1) lê o vault e divide cada página em trechos coerentes (por título/passo/linha de tabela);
         2) extrai os códigos definidos (RN01, FE01, FA01, MN001, Tela02, P3) e os citados em cada trecho;
         3) resolve as citações pelo CÓDIGO (nunca pela âncora do Obsidian);
         4) (sem --dry-run) gera embeddings locais (multilingual-e5-large, GPU) e grava em apf.conhecimento_trecho /
            apf.conhecimento_referencia (ver modelo_bd\\15_conhecimento_trecho.sql). Incremental: só reembeda o que
            mudou (hash do texto indexado).

Uso:  python indexa_wiki.py --dry-run [--sistema gai] [--mostra UC002]
      python indexa_wiki.py [--sistema gai] [--reindexar]
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys
import time

VAULT = r"D:\Projetos\Terrawiki\sistemas"
AQUI = os.path.dirname(os.path.abspath(__file__))
SQL = os.path.join(AQUI, "..", "modelo_bd", "15_conhecimento_trecho.sql")
MODELO = "intfloat/multilingual-e5-large"

MAX_CHARS = 1500      # cabe nos 512 tokens do modelo, com o cabeçalho de contexto
MIN_CHARS = 200       # trecho menor que isso se junta ao seguinte
FATOR_TELA = 0.6      # seções de tela/interface pesam menos (pouco útil para achar a regra)

RE_CODIGO = re.compile(r"\b(RNF|RN|FE|FA|MN|RF|Tela)[ .-]?0*(\d{1,4})\b")
RE_CODIGO_TITULO = re.compile(r"^\[?\s*(RNF|RN|FE|FA|MN|RF|Tela)[ .-]?0*(\d{1,4})\b")
RE_CODIGO_LINHA = re.compile(r"^\|\s*\**\s*(RNF|RN|FE|FA|MN|RF|Tela)[ .-]?0*(\d{1,4})\b")
RE_PASSO = re.compile(r"^\s*P0*(\d{1,3})\.")
RE_ROTULO_PASSO = re.compile(r"(?m)^\s*(?:FA|FE)\d+(?:\.\d+)+\.?")
RE_TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RE_HISTORICO = re.compile(r"hist[óo]rico de mudan", re.I)
RE_EMBED = re.compile(r"!\[\[[^\]]*\]\]")
RE_WIKILINK = re.compile(r"\[\[([^\]|]*)(?:\\?\|([^\]]*))?\]\]")
RE_TABELA_SEP = re.compile(r"^\|[\s:|-]+\|?\s*$")


def chave(tipo, num):
    return f"{tipo}:{int(num)}"


# ------------------------------------------------------------------ leitura e limpeza
def le_pagina(caminho):
    with open(caminho, encoding="utf-8") as f:
        txt = f.read()
    fm = {}
    if txt.startswith("---"):
        fim = txt.find("\n---", 3)
        for ln in txt[3:fim].split("\n"):
            if ":" in ln:
                k, v = ln.split(":", 1)
                fm[k.strip()] = v.strip()
        txt = txt[fim + 4:]
    return fm, txt


def limpa_linha(ln):
    ln = RE_EMBED.sub("", ln)

    def link(m):
        alvo, rotulo = m.group(1), m.group(2)
        if rotulo:
            return rotulo
        return alvo.split("#")[-1] if "#" in alvo else alvo.split("/")[-1]
    ln = RE_WIKILINK.sub(link, ln)
    ln = re.sub(r"\[(RNF|RN|FE|FA|MN|RF|Tela)(\s?-?\d+)\]", r"\1\2", ln)       # [RN01] -> RN01
    ln = ln.replace("\\|", "|").replace("***", "").replace("**", "")
    if ln.lstrip().startswith("|"):
        ln = re.sub(r"(<br>\s*)+", " / ", ln)
    else:
        ln = re.sub(r"\s*(<br>\s*)+", "\n", ln)
    return ln.rstrip()


def sistema_do_caminho(rel):
    p = rel.replace("\\", "/").split("/")
    if p[0] == "ATIVOS" and len(p) > 2:
        return p[2]
    if len(p) > 1:
        return p[1]
    return p[0]


# ------------------------------------------------------------------ acesso por sistema
RE_LINK_AUX = re.compile(r"\[\[sistemas/_auxiliares/([^/\]|#]+)/")


def mapa_acesso():
    """{pasta de _auxiliares: [sistemas ATIVOS/INATIVOS cujas páginas apontam para ela]}.

    A documentação de um sistema pode morar em `_auxiliares/<outro>` (ex.: as user stories da intranet_terranet estão em
    `_auxiliares/intranet_terracap`). Quem escolhe o sistema `intranet_terranet` deve achar essas páginas."""
    pais = collections.defaultdict(set)
    for raiz in ("ATIVOS", "INATIVOS"):
        base = os.path.join(VAULT, raiz)
        for dp, _, fs in os.walk(base):
            rel = os.path.relpath(dp, base).split(os.sep)
            sis = rel[1] if raiz == "ATIVOS" and len(rel) > 1 else rel[0]
            for f in fs:
                if f.endswith(".md"):
                    with open(os.path.join(dp, f), encoding="utf-8") as fh:
                        for m in RE_LINK_AUX.finditer(fh.read()):
                            pais[m.group(1)].add(sis)
    return {k: sorted(v) for k, v in pais.items()}


def atualiza_acesso(conn):
    """Preenche apf.conhecimento_trecho.sistemas_acesso (o próprio sistema + os que apontam para a pasta auxiliar)."""
    conn.execute("UPDATE apf.conhecimento_trecho SET sistemas_acesso = ARRAY[sistema]")
    for aux, pais in mapa_acesso().items():
        conn.execute("UPDATE apf.conhecimento_trecho SET sistemas_acesso = ARRAY[sistema] || %s::text[] WHERE sistema = %s",
                     (pais, aux))
        print(f"  acesso: {aux} <- {', '.join(pais)}")
    conn.commit()


# ------------------------------------------------------------------ chunking
def secoes(linhas):
    """Gera (caminho_da_secao, titulo_final, nivel, corpo_linhas). Ignora o histórico de mudanças."""
    pilha, corpo, saida, em_codigo, pulando = [], [], [], False, None

    def fecha():
        if corpo and any(l.strip() for l in corpo) and pulando is None:
            caminho = " > ".join(t for n, t in pilha[1:]) if len(pilha) > 1 else (pilha[0][1] if pilha else "")
            saida.append((caminho, pilha[-1][1] if pilha else "", pilha[-1][0] if pilha else 0, list(corpo)))
    for ln in linhas:
        if ln.lstrip().startswith("```"):
            em_codigo = not em_codigo
        m = None if em_codigo else RE_TITULO.match(ln)
        if m:
            fecha()
            corpo = []
            nivel, titulo = len(m.group(1)), limpa_linha(m.group(2)).strip()
            while pilha and pilha[-1][0] >= nivel:
                pilha.pop()
            if pulando is not None and nivel <= pulando:
                pulando = None
            if pulando is None and RE_HISTORICO.search(titulo):
                pulando = nivel
            pilha.append((nivel, titulo))
        else:
            corpo.append(ln)
    fecha()
    return saida


def divide_corpo(linhas):
    """Empacota linhas em trechos <= MAX_CHARS; tabela que continua num trecho novo repete o cabeçalho."""
    trechos, atual, tam, cab, i = [], [], 0, [], 0
    linhas = [l for l in linhas]
    while i < len(linhas):
        ln = linhas[i]
        if ln.lstrip().startswith("|") and i + 1 < len(linhas) and RE_TABELA_SEP.match(linhas[i + 1].strip()):
            cab = [ln, linhas[i + 1]]
        elif not ln.lstrip().startswith("|"):
            cab = []
        partes = [ln[k:k + MAX_CHARS] for k in range(0, len(ln), MAX_CHARS)] or [""]
        for parte in partes:
            if tam + len(parte) + 1 > MAX_CHARS and atual:
                trechos.append(atual)
                atual = list(cab) if (cab and parte.lstrip().startswith("|") and parte not in cab) else []
                tam = sum(len(x) + 1 for x in atual)
            atual.append(parte)
            tam += len(parte) + 1
        i += 1
    if atual:
        trechos.append(atual)
    return trechos


def monta_pagina(caminho, rel):
    fm, txt = le_pagina(caminho)
    sistema = sistema_do_caminho(rel)
    stem = os.path.splitext(os.path.basename(caminho))[0]
    tipo = fm.get("tipo", "documento")
    peso = float(fm.get("peso", 0.6))
    linhas = [limpa_linha(l) for l in txt.split("\n")]
    linhas = [l for l in linhas if not l.startswith("*Legenda")]
    trechos = []
    for caminho_sec, titulo_fim, nivel, corpo in secoes(linhas):
        corpo = [l for l in corpo]
        while corpo and not corpo[0].strip():
            corpo.pop(0)
        while corpo and not corpo[-1].strip():
            corpo.pop()
        # linhas vazias consecutivas viram uma só; quebras embutidas (<br>) viram linhas separadas
        plano, vazio = [], False
        for l in corpo:
            for sub in l.split("\n"):
                if not sub.strip():
                    if not vazio:
                        plano.append("")
                    vazio = True
                else:
                    plano.append(sub)
                    vazio = False
        mt = RE_CODIGO_TITULO.match(titulo_fim)
        def_titulo = chave(mt.group(1), mt.group(2)) if mt else None
        tela = bool(re.search(r"\bTela\s?\d|INTERFACE|Legendas", caminho_sec, re.I))
        for k, pedaco in enumerate(divide_corpo(plano)):
            texto = "\n".join(pedaco).strip()
            if len(texto) < 15 and not def_titulo:
                continue
            defs = {}
            if def_titulo and k == 0:
                defs[def_titulo] = None            # preenchido depois com a seção inteira
            for ln in pedaco:
                m = RE_PASSO.match(ln)
                if m:
                    defs.setdefault(chave("P", m.group(1)), ln.strip())
                m = RE_CODIGO_LINHA.match(ln)
                if m:
                    defs.setdefault(chave(m.group(1), m.group(2)), ln.strip().strip("|").strip().replace(" | ", ": ", 1))
            trechos.append({"secao": caminho_sec or titulo_fim or stem, "texto": texto, "defs": defs,
                            "def_titulo": def_titulo if k == 0 else None, "tela": tela})
    # junta trechos curtos ao seguinte (sem perder o título do curto); não funde quem define RN/FE/FA/Tela
    fundidos, pend = [], None
    for t in trechos:
        if pend is not None:
            ini = pend["texto"] if pend["secao"] == t["secao"] else pend["secao"].split(" > ")[-1] + ": " + pend["texto"]
            t["texto"] = ini + "\n" + t["texto"]
            t["defs"] = {**pend["defs"], **t["defs"]}
            pend = None
        if len(t["texto"]) < MIN_CHARS and not t["def_titulo"] and t is not trechos[-1]:
            pend = t
            continue
        fundidos.append(t)
    if pend is not None:
        fundidos.append(pend)
    # definição de heading (RN01, FE01, ...) = o texto da seção (este trecho e os filhos seguintes)
    for n, t in enumerate(fundidos):
        t["ordem"] = n
        if t["def_titulo"]:
            partes, tam = [t["texto"]], len(t["texto"])
            for u in fundidos[n + 1:]:
                if not u["secao"].startswith(t["secao"] + " > ") or tam > 1800:
                    break
                partes.append(u["texto"])
                tam += len(u["texto"])
            t["defs"][t["def_titulo"]] = "\n".join(partes)[:1800]
    saida = []
    for t in fundidos:
        corpo_refs = RE_ROTULO_PASSO.sub("", t["texto"])
        refs = sorted({chave(m.group(1), m.group(2)) for m in RE_CODIGO.finditer(corpo_refs)} - set(t["defs"]))
        cab = f"Sistema {sistema} | {tipo} | {stem} | {t['secao']}"
        saida.append({
            "sistema": sistema, "origem_dokuwiki": fm.get("origem_dokuwiki", stem), "arquivo_vault": rel,
            "tipo": tipo, "peso": round(peso * (FATOR_TELA if t["tela"] else 1.0), 2),
            "situacao": fm.get("situacao"), "categoria": fm.get("categoria"), "titulo_pagina": stem,
            "secao": t["secao"], "ordem": t["ordem"], "definicoes": {k: v for k, v in t["defs"].items() if v},
            "refs": refs, "texto": t["texto"], "texto_indexado": cab + "\n" + t["texto"],
        })
    return saida


def carrega_vault(sistema=None):
    todos = []
    for d, _, fs in os.walk(VAULT):
        for f in fs:
            if not f.endswith(".md"):
                continue
            caminho = os.path.join(d, f)
            rel = os.path.relpath(caminho, VAULT)
            if sistema and sistema_do_caminho(rel) != sistema:
                continue
            if rel == "Sistemas.md":
                continue
            todos.extend(monta_pagina(caminho, rel))
    for t in todos:
        t["n_caracteres"] = len(t["texto"])
        t["hash"] = hashlib.sha256(t["texto_indexado"].encode("utf-8")).hexdigest()
    return todos


# ------------------------------------------------------------------ resolução das referências
PREFERENCIA = {"regras_de_negocio": 0, "mensagens": 0, "glossario": 1, "caso_de_uso": 2, "user_story": 3}


def resolve(trechos):
    por_sistema = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, t in enumerate(trechos):
        for k in t["definicoes"]:
            por_sistema[t["sistema"]][k].append(i)
    refs = []
    for i, t in enumerate(trechos):
        for k in t["refs"]:
            tipo_ref, num = k.split(":")
            cand = [j for j in por_sistema[t["sistema"]].get(k, []) if j != i]
            mesma = [j for j in cand if trechos[j]["origem_dokuwiki"] == t["origem_dokuwiki"]]
            if tipo_ref in ("FE", "FA", "Tela"):       # fluxos e telas: da própria página
                cand = mesma
            elif mesma and tipo_ref not in ("RN", "MN", "RNF", "RF"):
                cand = mesma
            cand.sort(key=lambda j: (PREFERENCIA.get(trechos[j]["tipo"], 9), j))
            destino = cand[0] if cand else None
            refs.append({"origem": i, "tipo_ref": tipo_ref, "chave": k, "codigo": f"{tipo_ref}{int(num):02d}",
                         "destino": destino,
                         "texto_destino": trechos[destino]["definicoes"][k] if destino is not None else None})
    return refs


# ------------------------------------------------------------------ relatório (dry-run)
def relatorio(trechos, refs, mostra):
    print(f"trechos: {len(trechos)}  páginas: {len({t['arquivo_vault'] for t in trechos})}  "
          f"sistemas: {len({t['sistema'] for t in trechos})}")
    tam = sorted(t["n_caracteres"] for t in trechos)
    print(f"tamanho: min {tam[0]}  mediana {tam[len(tam)//2]}  p95 {tam[int(len(tam)*.95)]}  máx {tam[-1]}")
    print("por tipo:", dict(collections.Counter(t["tipo"] for t in trechos).most_common()))
    por = collections.Counter(r["tipo_ref"] for r in refs)
    ok = collections.Counter(r["tipo_ref"] for r in refs if r["destino"] is not None)
    print("referências:", len(refs), "resolvidas:", sum(ok.values()))
    for k, n in por.most_common():
        print(f"   {k:5} {n:6}  resolvidas {ok[k]:6}  ({100*ok[k]/n:.0f}%)")
    nao = [r for r in refs if r["destino"] is None and r["tipo_ref"] in ("RN", "MN", "FE", "FA")]
    print("exemplos não resolvidos (RN/MN/FE/FA):")
    for r in nao[:12]:
        t = trechos[r["origem"]]
        print(f"   {t['sistema']:12} {t['titulo_pagina'][:48]:48} {r['codigo']}")
    if mostra:
        for i, t in enumerate(trechos):
            if mostra.lower() in t["titulo_pagina"].lower():
                print(f"\n--- [{i}] {t['sistema']} | {t['tipo']} | {t['secao']} | peso {t['peso']} | {t['n_caracteres']} car.")
                print("defs:", list(t["definicoes"]), " refs:", t["refs"])
                print(t["texto"][:600])
                for r in refs:
                    if r["origem"] == i:
                        d = trechos[r["destino"]]["secao"] if r["destino"] is not None else None
                        print(f"   -> {r['chave']} => {d}")


# ------------------------------------------------------------------ gravação
def vetor(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def grava(trechos, refs, reindexar, sistema):
    sys.path.insert(0, os.path.join(AQUI, "..", "importador"))
    from comum import conectar
    conn = conectar()
    with open(SQL, encoding="utf-8") as f:
        conn.execute(f.read())
    conn.commit()
    atual = {(r[0], r[1], r[2]): (r[3], r[4], r[5]) for r in conn.execute(
        "SELECT origem_dokuwiki, secao, ordem, id, hash, modelo_embedding FROM apf.conhecimento_trecho "
        "WHERE data_versao = 'epoch'" + (" AND sistema = %s" if sistema else ""), (sistema,) if sistema else ())}
    novos = [t for t in trechos if reindexar or (t["origem_dokuwiki"], t["secao"], t["ordem"]) not in atual
             or atual[(t["origem_dokuwiki"], t["secao"], t["ordem"])][1] != t["hash"]
             or atual[(t["origem_dokuwiki"], t["secao"], t["ordem"])][2] != MODELO]
    print(f"a embedar: {len(novos)} de {len(trechos)} trechos")
    emb = {}
    if novos:
        import torch
        from sentence_transformers import SentenceTransformer
        modelo = SentenceTransformer(MODELO, device="cuda" if torch.cuda.is_available() else "cpu")
        if torch.cuda.is_available():
            modelo.half()
        modelo.max_seq_length = 512
        ordem = sorted(range(len(novos)), key=lambda i: len(novos[i]["texto_indexado"]))
        t0 = time.time()
        for ini in range(0, len(ordem), 512):
            lote = ordem[ini:ini + 512]
            vs = modelo.encode(["passage: " + novos[i]["texto_indexado"] for i in lote], batch_size=32,
                               normalize_embeddings=True, show_progress_bar=False)
            for i, v in zip(lote, vs):
                emb[(novos[i]["origem_dokuwiki"], novos[i]["secao"], novos[i]["ordem"])] = v
            print(f"  embeddings {min(ini + 512, len(ordem))}/{len(ordem)} ({time.time()-t0:.0f}s)", flush=True)
    ids = {}
    for t in trechos:
        k = (t["origem_dokuwiki"], t["secao"], t["ordem"])
        v = emb.get(k)
        r = conn.execute(
            """INSERT INTO apf.conhecimento_trecho (sistema, origem_dokuwiki, arquivo_vault, tipo, peso, situacao,
                   categoria, titulo_pagina, secao, ordem, definicoes, refs, texto, texto_indexado, n_caracteres, hash,
                   modelo_embedding, embedding)
               VALUES (%(sistema)s, %(origem_dokuwiki)s, %(arquivo_vault)s, %(tipo)s, %(peso)s, %(situacao)s,
                   %(categoria)s, %(titulo_pagina)s, %(secao)s, %(ordem)s, %(definicoes)s::jsonb, %(refs)s, %(texto)s,
                   %(texto_indexado)s, %(n_caracteres)s, %(hash)s, %(modelo)s, %(emb)s::vector)
               ON CONFLICT (origem_dokuwiki, secao, ordem, data_versao) DO UPDATE SET
                   sistema = EXCLUDED.sistema, arquivo_vault = EXCLUDED.arquivo_vault, tipo = EXCLUDED.tipo,
                   peso = EXCLUDED.peso, situacao = EXCLUDED.situacao, categoria = EXCLUDED.categoria,
                   titulo_pagina = EXCLUDED.titulo_pagina, definicoes = EXCLUDED.definicoes, refs = EXCLUDED.refs,
                   texto = EXCLUDED.texto, texto_indexado = EXCLUDED.texto_indexado,
                   n_caracteres = EXCLUDED.n_caracteres, hash = EXCLUDED.hash,
                   modelo_embedding = COALESCE(EXCLUDED.modelo_embedding, apf.conhecimento_trecho.modelo_embedding),
                   embedding = COALESCE(EXCLUDED.embedding, apf.conhecimento_trecho.embedding)
               RETURNING id""",
            {**t, "definicoes": json.dumps(t["definicoes"], ensure_ascii=False),
             "modelo": MODELO if v is not None else None, "emb": vetor(v) if v is not None else None}).fetchone()
        ids[k] = r[0]
    # trechos que sumiram do vault (página mudou/saiu): remove
    vivos = set(ids.values())
    sobrando = [v[0] for v in atual.values() if v[0] not in vivos]
    if sobrando:
        conn.execute("DELETE FROM apf.conhecimento_trecho WHERE id = ANY(%s)", (sobrando,))
    print(f"trechos removidos (não existem mais no vault): {len(sobrando)}")
    conn.execute("DELETE FROM apf.conhecimento_referencia WHERE origem_trecho_id = ANY(%s)", (list(vivos),))
    for r in refs:
        o, d = trechos[r["origem"]], (trechos[r["destino"]] if r["destino"] is not None else None)
        conn.execute(
            """INSERT INTO apf.conhecimento_referencia (origem_trecho_id, tipo_ref, chave, codigo, destino_trecho_id,
                   texto_destino, resolvido) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (ids[(o["origem_dokuwiki"], o["secao"], o["ordem"])], r["tipo_ref"], r["chave"], r["codigo"],
             ids[(d["origem_dokuwiki"], d["secao"], d["ordem"])] if d else None, r["texto_destino"], d is not None))
    conn.commit()
    print("gravado:", conn.execute("SELECT count(*) FROM apf.conhecimento_trecho").fetchone()[0], "trechos,",
          conn.execute("SELECT count(*) FROM apf.conhecimento_referencia").fetchone()[0], "referências")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="só chunking/referências, sem banco nem modelo")
    ap.add_argument("--sistema", help="slug do sistema (ex.: gai)")
    ap.add_argument("--mostra", help="imprime os trechos das páginas cujo nome contém este texto")
    ap.add_argument("--reindexar", action="store_true", help="refaz todos os embeddings")
    ap.add_argument("--so-acesso", action="store_true", help="só atualiza sistemas_acesso (sem chunking nem embeddings)")
    a = ap.parse_args()
    if a.so_acesso:
        sys.path.insert(0, os.path.join(AQUI, "..", "importador"))
        from comum import conectar
        atualiza_acesso(conectar())
        return
    t0 = time.time()
    trechos = carrega_vault(a.sistema)
    refs = resolve(trechos)
    print(f"chunking: {time.time()-t0:.1f}s")
    relatorio(trechos, refs, a.mostra)
    if not a.dry_run:
        grava(trechos, refs, a.reindexar, a.sistema)
        from comum import conectar
        atualiza_acesso(conectar())


if __name__ == "__main__":
    main()
