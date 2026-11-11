"""Notas do vault → trechos e referências, em memória (porta de migracao/referencia_wiki/indexa_wiki.py e
resolve_refs_cruzadas.py, 10/11/2026). Mesma lógica do laboratório, para o `hash` de cada trecho sair igual.

  carrega(notas)                  trechos de todas as notas, na ordem em que o laboratório percorria o vault
  resolve(trechos)                citações (RN01, FE01, ...) resolvidas pelo CÓDIGO dentro do sistema
  acesso_e_cruzadas(...)          sistemas_acesso de cada trecho e citações RN/MN resolvidas no documento de outro sistema
O `hash` é o sha256 do texto_indexado SEM máscara (como no laboratório); a máscara entra só na gravação (sincroniza.py).
"""
import collections
import hashlib
import re

from .conversao import ordem_walk

MAX_CHARS = 1500      # cabe nos 512 tokens do e5, com o cabeçalho de contexto
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
RE_LINK_AUX = re.compile(r"\[\[sistemas/_auxiliares/([^/\]|#]+)/")
RE_LINK_DOC = re.compile(r"\[\[sistemas/(?:ATIVOS|INATIVOS)/(.+?)/documentacao_tecnica/")
PREFERENCIA = {"regras_de_negocio": 0, "mensagens": 0, "glossario": 1, "caso_de_uso": 2, "user_story": 3}


def chave(tipo, num):
    return f"{tipo}:{int(num)}"


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


def rel_vault(id_vault):
    """'sistemas/ATIVOS/PHP/gr/x/EU010 - Nome' -> 'ATIVOS\\PHP\\gr\\x\\EU010 - Nome.md' (o `arquivo_vault` do laboratório, que
    rodava no Windows)."""
    return "\\".join(id_vault.split("/")[1:]) + ".md"


def sistema_do_caminho(rel):
    p = rel.replace("\\", "/").split("/")
    if p[0] == "ATIVOS" and len(p) > 2:
        return p[2]
    if len(p) > 1:
        return p[1]
    return p[0]


# ------------------------------------------------------------------ divisão em trechos
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
    linhas = list(linhas)
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


def monta_pagina(id_vault, nota):
    """Trechos de uma nota. `id_vault` = caminho da nota no vault, sem '.md'."""
    rel = rel_vault(id_vault)
    sistema = sistema_do_caminho(rel)
    stem = id_vault.split("/")[-1]
    tipo, peso = nota.tipo, float(nota.peso)
    # o laboratório lia o arquivo: frontmatter, linha em branco e o corpo
    linhas = [limpa_linha(l) for l in ("\n\n" + nota.md).split("\n")]
    linhas = [l for l in linhas if not l.startswith("*Legenda")]
    trechos = []
    for caminho_sec, titulo_fim, nivel, corpo in secoes(linhas):
        corpo = list(corpo)
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
        texto_indexado = cab + "\n" + t["texto"]
        saida.append({
            "sistema": sistema, "origem_dokuwiki": nota.origem_dokuwiki, "arquivo_vault": rel,
            "tipo": tipo, "peso": round(peso * (FATOR_TELA if t["tela"] else 1.0), 2),
            "situacao": nota.situacao, "categoria": nota.categoria, "titulo_pagina": stem,
            "secao": t["secao"], "ordem": t["ordem"], "definicoes": {k: v for k, v in t["defs"].items() if v},
            "refs": refs, "texto": t["texto"], "texto_indexado": texto_indexado, "n_caracteres": len(t["texto"]),
            "hash": hashlib.sha256(texto_indexado.encode("utf-8")).hexdigest(),
        })
    return saida


def ordem_vault(notas):
    """Ids das notas na ordem em que o laboratório percorria o vault (os.walk), sem a nota raiz 'Sistemas'."""
    por_caminho = {"/".join(i.split("/")[1:]) + ".md": i for i in notas}
    return [por_caminho[c] for c in ordem_walk(por_caminho) if c != "Sistemas.md"]


def carrega(notas):
    """Todos os trechos, na ordem do vault. A posição na lista decide empates na resolução das referências."""
    todos = []
    for id_vault in ordem_vault(notas):
        todos.extend(monta_pagina(id_vault, notas[id_vault]))
    return todos


# ------------------------------------------------------------------ referências dentro do sistema
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


# ------------------------------------------------------------------ acesso por sistema e referências cruzadas
def mapa_acesso(notas):
    """{pasta de _auxiliares: [sistemas ATIVOS/INATIVOS cujas páginas apontam para ela]}. A documentação de um sistema pode
    morar em `_auxiliares/<outro>`; quem escolhe o sistema deve achar essas páginas."""
    pais = collections.defaultdict(set)
    for id_vault, nota in notas.items():
        p = id_vault.split("/")
        if len(p) < 3 or p[1] not in ("ATIVOS", "INATIVOS"):
            continue
        rel = p[2:-1] or ["."]                      # pastas abaixo de ATIVOS (ou INATIVOS)
        sis = rel[1] if p[1] == "ATIVOS" and len(rel) > 1 else rel[0]
        for m in RE_LINK_AUX.finditer(nota.md):
            pais[m.group(1)].add(sis)
    return {k: sorted(v) for k, v in pais.items()}


def acesso_e_cruzadas(trechos, refs, notas):
    """Preenche `sistemas_acesso` em cada trecho e resolve, em `refs`, as citações RN/MN ainda abertas no documento de regras de
    OUTRO sistema que a página de origem linka. O sistema de origem ganha acesso ao trecho de destino.
    Devolve (nº de cruzadas resolvidas, nº de abertas sem destino)."""
    mapa = mapa_acesso(notas)
    for t in trechos:
        t["sistemas_acesso"] = [t["sistema"]] + mapa.get(t["sistema"], [])
    sistemas = {t["sistema"] for t in trechos}
    destinos = collections.defaultdict(list)           # (sistema, chave) -> [(preferência, índice, texto)]
    for j, t in enumerate(trechos):
        for k, v in t["definicoes"].items():
            destinos[(t["sistema"], k)].append((PREFERENCIA.get(t["tipo"], 9), j, v))
    por_arquivo = {rel_vault(i): n for i, n in notas.items()}
    linkados = {}

    def sistemas_linkados(arquivo):
        if arquivo not in linkados:
            nota = por_arquivo.get(arquivo)
            linkados[arquivo] = {m.group(1).replace("\\", "/").split("/")[-1]
                                 for m in RE_LINK_DOC.finditer(nota.md)} if nota else set()
        return linkados[arquivo]
    resolvidas, sem_dest, acesso = 0, 0, collections.defaultdict(set)
    for r in refs:
        if r["destino"] is not None or r["tipo_ref"] not in ("RN", "MN"):
            continue
        o = trechos[r["origem"]]
        alvos = (sistemas_linkados(o["arquivo_vault"]) & sistemas) - {o["sistema"]}
        cand = [(sis, d) for sis in sorted(alvos) for d in destinos.get((sis, r["chave"]), [])]
        if not cand:
            sem_dest += 1
            continue
        _, (_, j, texto) = min(cand, key=lambda x: x[1][0])
        r["destino"], r["texto_destino"], r["cruzada"] = j, texto, True
        acesso[j].add(o["sistema"])
        resolvidas += 1
    for j, orig in acesso.items():
        trechos[j]["sistemas_acesso"] = sorted(set(trechos[j]["sistemas_acesso"]) | orig)
    return resolvidas, sem_dest
