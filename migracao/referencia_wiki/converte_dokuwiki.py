"""Converte o dump da DokuWiki (D:\\Projetos\\dump_wiki) num vault Obsidian (D:\\Projetos\\Terrawiki).

Não depende do banco apf: é uma transformação pura de arquivos. Espelha a estrutura de `sistemas/` (a
`meta/` fica de fora, é metadado administrativo do DokuWiki, não conteúdo de navegação). Mídia só entra se
referenciada por alguma página (sem órfãs). Imagem ganha OCR local (EasyOCR, GPU) num bloco de citação
visível abaixo da imagem. Vídeo (nenhum presente no dump) vira um placeholder com o nome do arquivo.

O namespace "​gcf" (zero-width space antes de "gcf") é ignorado: comparado com o "gcf" normal, é um
rascunho anterior das mesmas páginas, já superado (confirmado por diff manual em 25/10/2026).

Uso:
  python converte_dokuwiki.py [--limite N] [--sem-ocr] [--refazer-ocr]
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import warnings
from urllib.parse import unquote

warnings.filterwarnings("ignore")
# a barra de progresso do download do modelo do EasyOCR imprime caracteres (ex. "█") que o console do
# Windows em cp1252 não consegue exibir, e isso derrubava o processo no meio do primeiro download
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

RAIZ = r"D:\Projetos"
WIKI = os.path.join(RAIZ, "dump_wiki")
PAGINAS_DIR = os.path.join(WIKI, "sistemas")
MEDIA_DIR = os.path.join(WIKI, "media")
SAIDA = os.path.join(RAIZ, "Terrawiki")
CACHE_OCR = os.path.join(os.path.dirname(__file__), "ocr_cache.json")

ZW = "\u200b"
# No conte\u00fado das p\u00e1ginas, o zero-width space aparece como o caractere real (removido via .replace(ZW, "")).
# No nome das PASTAS/ARQUIVOS em disco, por\u00e9m, foi preservado como texto percent-encoded literal
# ("%E2%80%8Bgcf", confirmado inspecionando os bytes do nome) em vez do caractere de fato -- por isso o
# filtro de diret\u00f3rio abaixo usa uma checagem de texto separada.
ZW_PASTA = "%e2%80%8b"


def tem_zw_pasta(nome):
    return ZW_PASTA in nome.lower()

EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".gif"}
EXT_VIDEO = {".mp4", ".webm", ".avi", ".mov", ".wmv", ".mkv"}

RE_MEDIA = re.compile(r"\{\{\s*([^}|?]+?)\s*(?:\?([^}|]*))?(?:\|([^}]*))?\s*\}\}")
# como o DokuWiki, o link termina no primeiro "]]"; um "]" isolado é permitido no alvo (âncora "#[Tela 01] Título")
# e no rótulo
RE_LINK = re.compile(r"(?<!!)\[\[\s*((?:[^\]|]|\](?!\]))+?)\s*(?:\|((?:[^\]]|\](?!\]))*))?\s*\]\]")


RE_URL_WIKI = re.compile(r"https?://terrawiki\.terracap\.df\.gov\.br/doku\.php\?id=([^&#\s]+)(#.*)?$", re.I)


def ancora_segura(a):
    """Âncora de seção p/ wikilink do Obsidian: [ ] | # ^ quebram o link, então viram espaço."""
    return re.sub(r"\s+", " ", re.sub(r"[\[\]|#^]", " ", a)).strip()


# ------------------------------------------------------------------ cleanID (replica o do DokuWiki)
def cleanid_segmento(s):
    s = s.strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9_\-.]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_.")
    return s


def cleanid(caminho):
    """'Documentação Técnica:Início' -> 'documentacao_tecnica/inicio'. Vazio no fim = namespace -> +inicio."""
    caminho = caminho.replace(ZW, "").strip().lstrip(":")
    partes = [cleanid_segmento(p) for p in caminho.split(":")]
    partes = [p for p in partes if p != ""] or ["inicio"]
    if caminho.endswith(":"):
        partes.append("inicio")
    return "/".join(partes)


# ------------------------------------------------------------------ exclusões (fonte única, vale também p/ wiki_sync)
ARQ_EXCLUSOES = os.path.join(os.path.dirname(__file__), "wiki_exclusoes.txt")


def carrega_exclusoes():
    """-> (namespaces, paginas). Linha 'sistemas:c:' (com ':' no fim) exclui o namespace inteiro;
    sem ':' no fim exclui só aquela página. '#' começa comentário."""
    namespaces, paginas = set(), set()
    if os.path.exists(ARQ_EXCLUSOES):
        with open(ARQ_EXCLUSOES, "r", encoding="utf-8") as f:
            for linha in f:
                item = linha.split("#", 1)[0].strip().lower()
                if not item:
                    continue
                (namespaces if item.endswith(":") else paginas).add(item)
    return namespaces, paginas


_EXCLUSOES = carrega_exclusoes()


def excluida(id_pagina):
    """id_pagina no formato DokuWiki, ex. 'sistemas:c:documentacao_tecnica:inicio'."""
    id_pagina = id_pagina.lower()
    namespaces, paginas = _EXCLUSOES
    return id_pagina in paginas or any(id_pagina.startswith(ns) for ns in namespaces)


# ------------------------------------------------------------------ auxiliares (fora do sistemas/inicio)
PASTA_AUX = "_auxiliares"


def calcula_auxiliares():
    """Namespaces de 1º nível que existem no dump, não estão excluídos e não são linkados pelo
    sistemas/inicio: são documentação alcançável só por links internos. No vault ficam agrupados em
    sistemas/_auxiliares/ (uma entrada só na barra lateral do Obsidian)."""
    inicio = os.path.join(PAGINAS_DIR, "inicio.txt")
    if not os.path.exists(inicio):
        return set()
    with open(inicio, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read().replace(ZW, "")
    linkados = set()
    for m in RE_LINK.finditer(txt):
        partes = cleanid(m.group(1).split("#")[0]).split("/")
        if len(partes) >= 2 and partes[0] == "sistemas":
            linkados.add(partes[1])
    aux = set()
    for nome in os.listdir(PAGINAS_DIR):
        if os.path.isdir(os.path.join(PAGINAS_DIR, nome)) and not tem_zw_pasta(nome):
            ns = cleanid_segmento(nome)
            if ns not in linkados and ns not in FUSOES and not excluida(f"sistemas:{ns}:inicio"):
                aux.add(ns)
    return aux


_AUXILIARES = None
# namespace de 1º nível que no vault passa a ser parte de outro sistema (mesmo sistema com dois nomes na wiki)
FUSOES = {"glpweb": "glpv2"}  # a documentação do GLPv2 está em sistemas:glpweb:*, o glpv2 só tem o inicio


def para_vault(id_barra):
    """'sistemas/gacv2/x/inicio' -> 'sistemas/_auxiliares/gacv2/x/inicio' se o 1º namespace for auxiliar."""
    global _AUXILIARES
    if _AUXILIARES is None:
        _AUXILIARES = calcula_auxiliares()
    partes = id_barra.split("/")
    if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in FUSOES:
        partes[1] = FUSOES[partes[1]]
        return "/".join(partes)
    if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in _AUXILIARES:
        return "/".join([partes[0], PASTA_AUX] + partes[1:])
    return id_barra


# ------------------------------------------------------------------ nomes dos artefatos (UC001 -> "UC001 - Manter Pessoa")
# O nome vem do rótulo dos links nas páginas de índice ("UC001 - Manter Pessoa"); o título da própria página
# costuma ser genérico (o nome do sistema). Pasta/arquivo no vault passa a ter "SIGLA - Nome".
RE_SIGLA = re.compile(r"^(uc|hu|us|eu|pt|rn|msg)(\d+)$")
RE_ROTULO = re.compile(r"^\W*(uc|hu|us|eu|pt|rn|msg)\s*0*(\d+)\s*[-_:.–—]*\s*(.*)$", re.I)
NOMES = {}  # chave "sistemas/gap/documentacao_tecnica/caso_de_uso/uc001" -> "UC001 - Manter Pessoa"


def nome_seguro(s):
    s = s.replace(":", " - ").replace("_", " ")
    s = re.sub(r"[<>\"/\\|?*#^\[\]]+", " ", s)  # inválidos no Windows ou que quebram [[links]] do Obsidian
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    return s[:90].rstrip(" .-_")


def calcula_nomes(indice_paginas):
    from collections import Counter
    candidatos = {}
    outro_tipo = {}  # rótulos com o mesmo número e sigla de outro tipo (eu x us)
    soltos = {}    # rótulos de link com a sigla certa e número diferente da página: {chave: [(nº do rótulo, nome)]}
    titulos_proprios = {}  # último recurso: nome do título da própria página
    proprios = {}  # número da sigla que a própria página mostra no título: {chave: nº}
    for caminho in listar_paginas():
        namespace_atual, _ = namespace_da_pagina(caminho)
        with open(caminho, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().replace(ZW, "")
        rel = os.path.relpath(caminho, PAGINAS_DIR).replace(ZW, "")
        segs = [cleanid_segmento(s) for s in os.path.splitext(rel)[0].split(os.sep)]
        if segs[-1] == "inicio":
            segs = segs[:-1]
        if segs and RE_SIGLA.match(segs[-1]):
            # primeiro cabeçalho que começa com a sigla ("===== US074 - Nome =====")
            for h in re.finditer(r"(?m)^[ 	]*=+[ 	]*(.+?)[ 	]*=+[ 	]*$", txt):
                rt = RE_ROTULO.match(re.sub(r"[*/']{2,}", "", h.group(1)).strip())
                # chave pelo id do DUMP (é o que aplica_nomes recebe): pre_id aplicaria FUSOES (glpweb -> glpv2) e o nome nunca casava
                chave_p = "/".join(x for x in ("sistemas/" + "/".join(segs)).split("/") if x != PASTA_AUX)
                if rt:
                    if rt.group(1).lower() == RE_SIGLA.match(segs[-1]).group(1):
                        proprios[chave_p] = int(rt.group(2))
                        if rt.group(1).lower() != "pt":
                            titulos_proprios[chave_p] = nome_seguro(rt.group(3))  # sem rótulo no índice (ex. glp/us001)
                    elif RE_SIGLA.match(segs[-1]).group(1) != "pt" and rt.group(1).lower() != "pt":
                        titulos_proprios[chave_p] = nome_seguro(rt.group(3))  # ex. uc058 com título "EU0058 - Nome"
                    break
                # título com prefixo do sistema ("GAC-GEATE - EU001 - Nome", "EU0058 - Nome"): sigla de qualquer tipo
                rs = re.search(r"\b(uc|hu|us|eu)\s*0*\d+\s*[-_:.–—]+\s*(.+)$", re.sub(r"[*/']{2,}", "", h.group(1)), re.I)
                if rs and not RE_SIGLA.match(segs[-1]).group(1) == "pt":
                    titulos_proprios[chave_p] = nome_seguro(rs.group(2))
                    break
        for m in RE_LINK.finditer(txt):
            if not m.group(2):
                continue
            destino = resolve_link(m.group(1), namespace_atual, indice_paginas)
            if destino is None:
                continue
            partes = [p for p in destino.split("#")[0].split("/") if p != PASTA_AUX]
            if partes[-1] == "inicio" and len(partes) > 1:
                partes = partes[:-1]
            alvo = RE_SIGLA.match(partes[-1])
            rot = RE_ROTULO.match(m.group(2).strip())
            if not (alvo and rot) or int(rot.group(2)) != int(alvo.group(2)) and rot.group(1).lower() != alvo.group(1):
                continue
            nome = nome_seguro(rot.group(3))
            if not nome:
                continue
            if rot.group(1).lower() != alvo.group(1):  # mesmo número, outro tipo (índice "EU001" p/ página us001)
                outro_tipo.setdefault("/".join(partes), Counter())[nome] += 1
                continue
            if int(rot.group(2)) == int(alvo.group(2)):
                candidatos.setdefault("/".join(partes), Counter())[nome] += 1
            else:
                soltos.setdefault("/".join(partes), []).append((int(rot.group(2)), nome))
    # índice com numeração diferente do id da página (ex. GLA: us089 é a "US074"): vale o rótulo cujo número
    # bate com o que a própria página mostra no título
    for chave, lista in soltos.items():
        if chave in candidatos or chave not in proprios:
            continue
        c = Counter(n for num, n in lista if num == proprios[chave])
        if c:
            candidatos[chave] = c
    for chave, c in outro_tipo.items():
        if chave not in candidatos and RE_SIGLA.match(chave.split("/")[-1]).group(1) != "pt":
            candidatos[chave] = c
    for chave, nome in titulos_proprios.items():
        if chave not in candidatos and nome:
            candidatos[chave] = Counter({nome: 1})
    return {k: f"{RE_SIGLA.match(k.split('/')[-1]).group(1).upper()}{RE_SIGLA.match(k.split('/')[-1]).group(2)} - "
               f"{c.most_common(1)[0][0]}" for k, c in candidatos.items()}


def aplica_nomes(id_barra):
    """Troca, em cada nível do caminho, 'uc001' por 'UC001 - Nome' quando houver nome. Ignora _auxiliares."""
    if not NOMES:
        return id_barra
    partes = id_barra.split("/")
    saida, chave = [], []
    for i, seg in enumerate(partes):
        if seg == PASTA_AUX:
            saida.append(seg)
            continue
        chave.append(seg.lower())
        saida.append(NOMES.get("/".join(chave), seg))
    return "/".join(saida)


# ------------------------------------------------------------------ realocação para dentro de documentacao_tecnica
# Páginas que a "Documentação Técnica" de um sistema lista, mas que no DokuWiki ficam ao lado dela
# (ex. sistemas:ania:mensagens), passam a ficar em sistemas/<sis>/documentacao_tecnica/ no vault.
REALOC = set()  # {"sistemas/ania/mensagens", ...}


def calcula_realocacoes(indice_paginas):
    achados = set()
    for nome in os.listdir(PAGINAS_DIR):
        doc = os.path.join(PAGINAS_DIR, nome, "documentacao_tecnica", "inicio.txt")
        if tem_zw_pasta(nome) or not os.path.exists(doc):
            continue
        sis = cleanid_segmento(nome)
        with open(doc, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().replace(ZW, "")
        for m in RE_LINK.finditer(txt):
            destino = resolve_link(m.group(1), [nome, "documentacao_tecnica"], indice_paginas)
            if destino is None:
                continue
            partes = [p for p in destino.split("#")[0].split("/") if p != PASTA_AUX]
            if len(partes) < 3 or partes[0] != "sistemas" or partes[1] != sis \
                    or partes[2] in ("inicio", "documentacao_tecnica", "manual_do_usuario"):
                continue
            base = "/".join(partes[:3])
            existe = not excluida(base.replace("/", ":")) and \
                any(i == base or i.startswith(base + "/") for i in indice_paginas)
            destino_ocupado = any(i.startswith(f"sistemas/{sis}/documentacao_tecnica/{partes[2]}")
                                  for i in indice_paginas)
            if existe and not destino_ocupado:
                achados.add(base)
    return achados


def aplica_realoc(id_barra):
    if not REALOC:
        return id_barra
    partes = id_barra.split("/")
    i0 = 2 if len(partes) > 1 and partes[1] == PASTA_AUX else 1  # posição do sistema
    if partes[0] != "sistemas" or len(partes) < i0 + 2:
        return id_barra
    if f"sistemas/{partes[i0].lower()}/{partes[i0 + 1].lower()}" in REALOC:
        partes.insert(i0 + 1, "documentacao_tecnica")
    return "/".join(partes)


# ------------------------------------------------------------------ nome do arquivo no lugar de "inicio"
# Pasta folha (só tem inicio): o arquivo sobe um nível ("uc001/inicio" -> "UC001 - Nome").
# Pasta com filhos: nota de pasta, arquivo com o nome da pasta ("gap/inicio" -> "gap/GAP – Gestão ...").
FINAL = {}  # id "pré-nota" (nomes+realoc+auxiliares aplicados) -> id final no vault
RE_ARTEFATO = re.compile(r"^(uc|hu|us|eu|pt|rn|msg)\d+", re.I)


def titulos_sistemas():
    """{'gap': 'GAP – Gestão de Apoio às Aplicações'} a partir do 1º cabeçalho do inicio.txt de cada sistema."""
    titulos = {}
    for nome in os.listdir(PAGINAS_DIR):
        arq = os.path.join(PAGINAS_DIR, nome, "inicio.txt")
        if tem_zw_pasta(nome) or not os.path.exists(arq):
            continue
        with open(arq, "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r"(?m)^\s*={2,6}\s*(.+?)\s*={2,6}\s*$", f.read().replace(ZW, ""))
        if m:
            t = nome_seguro(re.sub(r"[*/']{2,}|<[^>]*>|\[|\]", "", m.group(1)))
            if t:
                if len(t) > 80:  # corta no limite de uma palavra
                    t = t[:80].rsplit(" ", 1)[0]
                titulos[cleanid_segmento(nome)] = t.rstrip(" .,-_")
    return titulos


def pre_id(id_dump):
    return para_vault(aplica_realoc(aplica_nomes(id_dump)))


def calcula_notas(ids_dump):
    """Monta FINAL para todo id '.../inicio'. ids_dump = ids (cleanid) das páginas que entram no vault."""
    titulos = titulos_sistemas()
    pre = {i: pre_id(i) for i in ids_dump}
    prefixos = collections.Counter()
    for p in pre.values():
        partes = p.split("/")
        for k in range(1, len(partes)):
            prefixos["/".join(partes[:k])] += 1

    def sem_aux(p):
        return [s for s in p.split("/") if s != PASTA_AUX]

    def nome_nota(p):
        pasta = p[:-len("/inicio")]
        partes = sem_aux(pasta)
        if len(partes) == 1:  # sistemas
            return "Sistemas"
        if len(partes) == 2:  # sistemas/<sis>
            return titulos.get(partes[1].lower()) or partes[1].upper()
        if RE_ARTEFATO.match(partes[-1]):
            return partes[-1]
        return nome_seguro(f"{partes[1]} - {partes[-1]}")

    def monta(p, achatar):
        pasta = p[:-len("/inicio")]
        base = pasta.rsplit("/", 1)[0] if achatar else pasta
        return f"{base}/{nome_nota(p)}"

    candidatos = {}
    for p in pre.values():
        if p == "sistemas/inicio" or p.endswith("/inicio"):
            profundidade = len(sem_aux(p[:-len("/inicio")]))
            folha = prefixos[p[:-len("/inicio")]] == 1
            candidatos[p] = (monta(p, folha and profundidade >= 3), folha and profundidade >= 3)
    usados = collections.Counter(c[0].lower() for c in candidatos.values())
    for p in pre.values():
        if not p.endswith("/inicio"):
            usados[p.lower()] += 1
    for p, (final, achatado) in candidatos.items():
        if achatado and usados[final.lower()] > 1:  # colisão: mantém a pasta com nota de pasta
            final = monta(p, False)
            ultimo = sem_aux(p[:-len("/inicio")])[-1]
            if RE_ARTEFATO.match(ultimo):  # arquivo só com a sigla: evita repetir o nome longo no caminho
                final = f"{p[:-len('/inicio')]}/{RE_ARTEFATO.match(ultimo).group(0).upper()}"
        FINAL[p] = final
    return {i: p for i, p in pre.items()}


def nota_de_pasta(id_pre):
    return FINAL.get(id_pre, id_pre)


# ------------------------------------------------------------------ categorias (ATIVOS/INATIVOS e classificação)
CATEGORIAS = {}  # {"gap": "ATIVOS/PHP", "efd-reinf": "ATIVOS/PHP/Módulos PHP", "gaa": "INATIVOS"}


def calcula_categorias():
    """Lê os cabeçalhos do sistemas/inicio.txt do dump: cada sistema linkado herda a pilha de cabeçalhos
    (ATIVOS > PHP > Módulos PHP). O cabeçalho de nível 1 ('Sistemas') não entra."""
    inicio = os.path.join(PAGINAS_DIR, "inicio.txt")
    if not os.path.exists(inicio):
        return {}
    with open(inicio, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read().replace(ZW, "")
    pilha, cats = [], {}
    for linha in txt.split("\n"):
        m = re.match(r"^(={2,6})\s*(.*?)\s*=+\s*$", linha)
        if m:
            nivel = 7 - len(m.group(1))
            nome = nome_seguro(m.group(2).replace(" / ", " - "))
            pilha = pilha[:nivel - 1] + [nome]
            continue
        for lk in RE_LINK.finditer(linha):
            partes = cleanid(lk.group(1).split("#")[0]).split("/")
            if len(partes) >= 2 and partes[0] == "sistemas" and partes[1] not in cats:
                # subgrupo (h4, ex. "Módulos PHP") vira categoria própria ao lado da mãe, não dentro dela
                niveis = [pilha[1], pilha[-1]] if len(pilha) > 3 else pilha[1:]
                cats[partes[1]] = "/".join(niveis)
    return {k: v for k, v in cats.items() if v}


def com_categoria(id_vault):
    """'sistemas/gap/x' -> 'sistemas/ATIVOS/PHP/gap/x'. Auxiliares e a raiz ficam onde estão."""
    partes = id_vault.split("/")
    if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in CATEGORIAS:
        return "/".join([partes[0], CATEGORIAS[partes[1]]] + partes[1:])
    return id_vault


# ------------------------------------------------------------------ descoberta e índices
def listar_paginas(incluir_excluidas=False):
    for dirpath, dirnames, filenames in os.walk(PAGINAS_DIR):
        dirnames[:] = [d for d in dirnames if not tem_zw_pasta(d)]
        for f in filenames:
            if f.endswith(".txt"):
                caminho = os.path.join(dirpath, f)
                rel = os.path.relpath(caminho, PAGINAS_DIR).replace(ZW, "")
                if not incluir_excluidas and \
                        excluida("sistemas:" + ":".join(os.path.splitext(rel)[0].split(os.sep))):
                    continue
                yield caminho


def namespace_da_pagina(caminho_txt):
    rel = os.path.relpath(caminho_txt, PAGINAS_DIR)
    partes = rel.replace(ZW, "").split(os.sep)
    nome_pagina = os.path.splitext(partes[-1])[0]
    namespace = partes[:-1]
    return namespace, cleanid_segmento(nome_pagina) or "inicio"


def indexar_media():
    """-> {caminho/limpo/sem/extensao_ou_com: caminho_absoluto}, chave = cleanid completo com extensão."""
    indice = {}
    for dirpath, dirnames, filenames in os.walk(MEDIA_DIR):
        dirnames[:] = [d for d in dirnames if not tem_zw_pasta(d)]
        for f in filenames:
            abs_p = os.path.join(dirpath, f)
            rel = os.path.relpath(abs_p, MEDIA_DIR).replace(ZW, "")
            pasta, nome = os.path.split(rel)
            base, ext = os.path.splitext(nome)
            chave = cleanid(pasta.replace(os.sep, ":") + ":" + base) + ext.lower() if pasta else \
                cleanid_segmento(base) + ext.lower()
            indice[chave] = abs_p
    return indice


def resolve_media(alvo, namespace_atual, indice_media):
    """alvo cru do {{...}} -> caminho absoluto do arquivo, ou None se não encontrado."""
    alvo = alvo.strip()
    if alvo.lower().startswith(("http://", "https://")):
        return None
    base, ext = os.path.splitext(alvo)
    ext = ext.lower()
    absoluto = alvo.startswith(":")
    # a árvore de mídia não tem o prefixo "sistemas:" que a árvore de páginas tem (confirmado: não existe
    # pasta media/sistemas/ no dump) -- mesma peculiaridade já tratada em migracao/gera_pacote.py
    base_sem_sistemas = re.sub(r"^:*sistemas:", "", base, flags=re.I)
    limpo = cleanid(base_sem_sistemas) + ext
    candidatos = [limpo]
    if not absoluto and namespace_atual:
        candidatos.insert(0, cleanid(":".join(namespace_atual) + ":" + base.lstrip(":")) + ext)
    for c in candidatos:
        if c in indice_media:
            return indice_media[c]
    return None


def indexar_paginas(incluir_excluidas=True):
    """-> set de IDs de página limpos ('sistemas/gai/documentacao_tecnica/inicio'), para a resolução de
    links relativos "escalar" os namespaces como o DokuWiki faz de verdade (ver resolve_link)."""
    indice = set()
    for caminho in listar_paginas(incluir_excluidas=incluir_excluidas):  # padrão inclui as excluídas: "morto" = não existe no dump
        rel = os.path.relpath(caminho, PAGINAS_DIR)
        if tem_zw_pasta(rel):
            continue
        partes = rel.replace(ZW, "").split(os.sep)
        partes[-1] = os.path.splitext(partes[-1])[0]
        indice.add("sistemas/" + "/".join(cleanid_segmento(p) for p in partes))
    return indice


def resolve_link(alvo, namespace_atual, indice_paginas, cru=False):
    """[[alvo]] do DokuWiki -> caminho relativo ao vault (sem extensão), ou None se for link externo.

    Links sem ":" na frente são relativos ao namespace atual, mas o DokuWiki resolve "escalando": tenta no
    namespace atual, depois em cada namespace ancestral, até a raiz, e usa o primeiro que existir de
    verdade. É assim que páginas escritas como "sistemas:gai:...:inicio" (sem o ":" inicial) resolvem certo
    mesmo quando a página atual já está dentro de "sistemas:gai" -- sem essa escalada, o caminho ficaria
    duplicado ("sistemas/gai/sistemas/gai/...")."""
    alvo = alvo.strip()
    if alvo.lower().startswith(("http://", "https://", "ftp://", "mailto:")):
        return None
    ancora = None
    if "#" in alvo:
        alvo, ancora = alvo.split("#", 1)
    absoluto = alvo.startswith(":")
    sufixo = cleanid(alvo.lstrip(":"))
    if absoluto:
        # o texto do link absoluto já inclui "sistemas:" por convenção desta wiki (não existe outro
        # namespace de conteúdo na raiz) -- não prefixa de novo.
        destino = sufixo
    else:
        candidatos = []
        ns = ["sistemas"] + list(namespace_atual)
        while True:
            candidatos.append("/".join(ns) + "/" + sufixo if ns else sufixo)
            if not ns:
                break
            ns = ns[:-1]
        # se nenhum candidato existir de verdade (link morto), prefere o palpite mais "raso"
        # (o último da lista) em vez do mais aninhado -- fica mais parecido com o que o autor quis dizer.
        destino = next((c for c in candidatos if c in indice_paginas), candidatos[-1])
    if cru:  # id DokuWiki resolvido, sem nomes/realocação/auxiliares: serve p/ checar se a página existe
        return destino
    destino = com_categoria(nota_de_pasta(pre_id(destino)))
    if ancora and ancora_segura(ancora):
        destino += "#" + ancora_segura(ancora)
    return destino


# ------------------------------------------------------------------ OCR (EasyOCR, GPU, cache por sha256)
_leitor = None
_novos_desde_save = 0


def _carrega_cache():
    if os.path.exists(CACHE_OCR):
        with open(CACHE_OCR, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _salva_cache(cache):
    tmp = CACHE_OCR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_OCR)


def sha256_arquivo(caminho):
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ocr_imagem(caminho, cache):
    h = sha256_arquivo(caminho)
    if h in cache:
        return cache[h], False
    global _leitor
    if _leitor is None:
        import easyocr
        print("carregando EasyOCR (pt, GPU)...", flush=True)
        _leitor = easyocr.Reader(["pt"], gpu=True)
    try:
        linhas = _leitor.readtext(caminho, detail=0, paragraph=True)
        texto = "\n".join(l.strip() for l in linhas if l.strip())
    except Exception as e:
        texto = ""
        print(f"  aviso: OCR falhou em {caminho}: {type(e).__name__}: {e}", flush=True)
    cache[h] = texto
    global _novos_desde_save
    _novos_desde_save += 1
    if _novos_desde_save >= 50:
        _salva_cache(cache)
        _novos_desde_save = 0
    return texto, True


# ------------------------------------------------------------------ conversão de sintaxe DokuWiki -> Markdown
def _conv_del_multilinha(texto):
    """<del> que atravessa linhas/parágrafos (ex. regras de negócio revogadas do GAI) não vira ~~ válido no
    Obsidian, que não risca por cima de linha em branco; riscamos linha a linha e o título riscado vira cabeçalho."""
    def bloco(m):
        corpo = m.group(1)
        if "\n" not in corpo:
            h = re.match(r"^(={2,6})([^=]+?)(={2,6})$", re.sub(r"(?:\s*\\)+\s*$", "", corpo).strip())
            if h:  # título riscado numa linha só: <del>=== X ===</del>
                return f"{h.group(1)} ~~{h.group(2).strip()}~~ {h.group(3)}"
            return m.group(0)
        saida = []
        for ln in corpo.split("\n"):
            s = ln.strip()
            if not s:
                saida.append(ln)
                continue
            h = re.match(r"^(={2,6})([^=]+?)(={2,6})$", re.sub(r"(?:\s*\\)+\s*$", "", s))
            if h:
                saida.append(f"{h.group(1)} ~~{h.group(2).strip()}~~ {h.group(3)}")
                continue
            marca = re.match(r"^(\s*(?:[-*]\s+)?)(.*)$", ln)
            if s.startswith(("|", "^")):
                saida.append(ln)  # tabela: a linha não pode ser reescrita
            else:
                saida.append(f"{marca.group(1)}~~{marca.group(2).strip()}~~")
        return "\n".join(saida)
    return re.sub(r"<del>(.*?)</del>", bloco, texto, flags=re.S)


def _conv_cabecalhos(texto):
    def sub(m):
        # a contagem de "=" dos dois lados às vezes não bate (erro de digitação no texto original);
        # usa o menor dos dois lados para o nível, em vez de exigir simetria exata
        nivel = 7 - min(len(m.group(1)), len(m.group(3)))  # ====== (6) -> H1 ... == (2) -> H5
        titulo = m.group(2).strip()
        if "[[" in titulo and "]]" not in titulo:
            titulo = titulo.replace("[[", "[")  # "[[Tela02] Título": colchete dobrado por engano, sem fechar
        return f"\n{'#' * nivel} {titulo}\n"
    texto = re.sub(r"(?m)^[ \t ]*(={2,6})([^=\n]+?)(={2,6})[ \t ]*$", sub, texto)
    # "**Item Homologado..:** ===X===": título no meio da linha não é cabeçalho (no DokuWiki sai literal e
    # no Obsidian "==" vira destaque); o autor queria destacar o nome, então vira negrito
    return re.sub(r"(?m)^(\*\*[^*\n]+\*\*[ \t]*)(={2,6})([^=\n]+?)(={2,6})[ \t]*$", r"\1**\3**", texto)


def _conv_inline(texto):
    texto = re.sub(r"<del>(.*?)</del>", r"~~\1~~", texto, flags=re.S)
    # "//" precedido de ":" é de URL (https://...), não itálico; sem isso o "//" de uma URL fechava o itálico
    # aberto pelo "//" de outra URL na mesma linha e estragava os dois links
    # "//**" ... "**//" em linhas separadas = negrito+itálico em várias linhas; o itálico simples abaixo não
    # cruza linhas e deixava "//**" e "**//" literais. "***" precisa colar no texto, senão não abre/fecha
    texto = re.sub(r"(?<!:)//\*\*(.*?)\*\*//", lambda m: "***" + m.group(1).strip() + "***", texto, flags=re.S)
    texto = re.sub(r"(?<!:)//(.*?)(?<!:)//", r"*\1*", texto)
    texto = re.sub(r"''(.*?)''", r"`\1`", texto)
    # <br> em vez de quebra "dura" real: "\\" aparece dentro de célula de tabela com frequência, e uma
    # quebra de linha de verdade ali rebenta o parser de tabela (cada linha de tabela tem que ficar numa
    # única linha física). <br> funciona igual visualmente, dentro ou fora de tabela.
    texto = texto.replace("\\\\", "<br>")
    return texto


def _conv_blocos_codigo(texto):
    def sub(m):
        tag, lang, corpo = m.group(1), m.group(2) or "", m.group(3)
        return f"```{lang.strip()}\n{corpo.strip()}\n```"
    return re.sub(r"<(code|file)\s*(\w*)\s*>(.*?)</\1>", sub, texto, flags=re.S | re.I)


def _conv_listas(texto):
    def sub(m):
        indent, marcador = m.group(1), m.group(2)
        nivel = len(indent) // 2
        novo_marc = "1." if marcador == "-" else "-"
        return "  " * nivel + novo_marc + " "
    return re.sub(r"(?m)^( *)([*\-]) ", sub, texto)


def _conv_tabelas(texto):
    linhas = texto.split("\n")
    saida, i = [], 0
    while i < len(linhas):
        linha = linhas[i]
        if re.match(r"^\s*[|^]", linha) and ("|" in linha or "^" in linha):
            bloco = []
            tem_cabecalho = linha.strip().startswith("^")
            while i < len(linhas) and re.match(r"^\s*[|^]", linhas[i]):
                # o "|" de [[link|rótulo]] e ![[img|400]] não pode separar células: protege durante o split
                # e devolve escapado ("\|"), que é como o Obsidian aceita pipe de wikilink dentro de tabela
                prot = re.sub(r"\[\[.*?\]\]",
                              lambda m: m.group(0).replace("|", "\x01").replace("^", "\x02"), linhas[i])
                celulas = [c.strip().replace("\x01", "\\|").replace("\x02", "^")
                           for c in re.split(r"(?<!\\)[|^]", prot)[1:-1]]
                bloco.append(celulas)
                i += 1
            ncol = max(len(l) for l in bloco)
            for l in bloco:
                while len(l) < ncol:
                    l.append("")
            saida.append("| " + " | ".join(bloco[0]) + " |")
            saida.append("| " + " | ".join(["---"] * ncol) + " |")
            for l in bloco[1:]:
                saida.append("| " + " | ".join(c.replace("\n", "<br>") for c in l) + " |")
            continue
        saida.append(linha)
        i += 1
    return "\n".join(saida)


def _escapa_pipe_em_legenda(s):
    return s.replace("|", "\\|") if s else s


MORTO_INI, MORTO_FIM = "\x00M", "\x00"
RE_ITEM_MORTO = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*\x00M[^\x00]*\x00\s*$")


def remove_links_mortos(texto):
    """Item de lista que é só um link morto some; link morto no meio do texto vira o próprio rótulo."""
    linhas = [ln for ln in texto.split("\n") if not RE_ITEM_MORTO.match(ln)]
    return re.sub(r"\x00M([^\x00]*)\x00", r"\1", "\n".join(linhas))


def converte_links(texto, namespace_atual, indice_paginas):
    def sub(m):
        alvo, label = m.group(1), m.group(2)
        if alvo.strip().startswith("#"):  # âncora na própria página: não resolve para outra página
            ancora = ancora_segura(alvo.strip()[1:])
            return f"[[#{ancora}|{label.strip()}]]" if ancora and label else \
                (label or ancora or "").strip()
        wiki = RE_URL_WIKI.match(alvo.strip())
        if wiki:  # URL do próprio terrawiki (doku.php?id=...) = link interno escrito por extenso
            alvo = ":" + unquote(wiki.group(1)) + (wiki.group(2) or "")
        destino = resolve_link(alvo, namespace_atual, indice_paginas)
        if destino is None:  # externo
            return f"[{(label or alvo).strip()}]({alvo.strip()})"
        cru = resolve_link(alvo, namespace_atual, indice_paginas, cru=True)
        # página excluída individualmente (linha sem ':' no fim do wiki_exclusoes.txt) conta como inexistente
        if alvo.strip().split("#")[0].strip() and \
                (cru not in indice_paginas or cru.replace("/", ":").lower() in _EXCLUSOES[1]):
            # link para página que não existe (vermelho/tracejado no DokuWiki): vira texto, ou some se for
            # só um item de lista (ver remove_links_mortos)
            return MORTO_INI + (label.strip() if label else alvo.strip().rstrip(":").split(":")[-1]) + MORTO_FIM
        texto_label = label.strip() if label else alvo.strip().rstrip(":").split(":")[-1]
        return f"[[{destino}|{texto_label}]]"
    return RE_LINK.sub(sub, texto)


def converte_midia(texto, namespace_atual, indice_media, pasta_pagina_saida, cache_ocr, sem_ocr, stats):
    # imagem dentro de célula de tabela: a célula leva só o embed (legenda e bloco de OCR têm várias linhas e
    # quebrariam a linha da tabela); os blocos de OCR saem logo depois da tabela
    pendentes = []
    em_tabela = False

    def sub(m):
        alvo, params, legenda = m.group(1), m.group(2) or "", m.group(3)
        legenda = (legenda or "").strip()
        base, ext = os.path.splitext(alvo.strip())
        ext = ext.lower()
        if ext in EXT_VIDEO:
            stats["videos"] += 1
            return f"*[vídeo: {legenda or os.path.basename(alvo.strip())}]*"
        origem = resolve_media(alvo, namespace_atual, indice_media)
        if origem is None:
            stats["midia_nao_encontrada"] += 1
            return f"*(mídia não encontrada: {alvo.strip()})*"
        rel_media = "media/" + os.path.relpath(origem, MEDIA_DIR).replace(os.sep, "/").replace(ZW, "")
        destino_abs = os.path.join(SAIDA, rel_media)
        os.makedirs(os.path.dirname(destino_abs), exist_ok=True)
        if not os.path.exists(destino_abs) or os.path.getmtime(destino_abs) < os.path.getmtime(origem):
            shutil.copy2(origem, destino_abs)
        if ext in EXT_IMAGEM:
            largura_m = re.search(r"(\d+)", params)
            tag_img = f"![[{rel_media}|{largura_m.group(1)}]]" if largura_m else f"![[{rel_media}]]"
            partes = [tag_img]
            if legenda and not em_tabela:
                partes.append(f"*Legenda: {legenda}*")
            if not sem_ocr:
                texto_ocr, novo = ocr_imagem(origem, cache_ocr)
                stats["ocr_novo" if novo else "ocr_cache"] += 1
                if texto_ocr:
                    bloco = "\n".join("> " + l for l in texto_ocr.split("\n"))
                    ocr_md = f"> [!ocr]- Texto da imagem (OCR)\n{bloco}"
                    (pendentes if em_tabela else partes).append(ocr_md)
            stats["imagens"] += 1
            # linha em branco depois do callout: texto/outra imagem que vinha na mesma linha não pode ficar colado nele
            return "\n".join(partes) + ("\n" if len(partes) > 1 and partes[-1].startswith("> ") else "")
        stats["anexos"] += 1
        rotulo = _escapa_pipe_em_legenda(legenda) or os.path.basename(origem)
        return f"[[{rel_media}|{rotulo}]]"
    saida = []
    for linha in texto.split("\n"):
        eh_tabela = bool(re.match(r"^\s*[|^]", linha))
        if pendentes and not eh_tabela:
            saida.extend(["", "\n\n".join(pendentes), ""])
            pendentes = []
        em_tabela = eh_tabela
        saida.append(RE_MEDIA.sub(sub, linha))
    if pendentes:
        saida.extend(["", "\n\n".join(pendentes)])
    return "\n".join(saida)


# ------------------------------------------------------------------ OCR redundante com a tabela logo abaixo
LIM_OCR_TABELA = 0.70  # fração das palavras do OCR que já estão na tabela de campos logo abaixo da imagem
_RE_PALAVRA = re.compile(r"[a-zà-ÿ]{3,}", re.I)


def _palavras(texto):
    return [unicodedata.normalize("NFD", w.lower()).encode("ascii", "ignore").decode() for w in _RE_PALAVRA.findall(texto)]


def _tabela_de_campos(cabecalho):
    """Cabeçalho de tabela que descreve os campos da tela: "Nome do campo | Descrição do campo ..." ou
    "Nome | Tipo | Entidade | Descrição | Valores ..." (as tabelas de passos de teste não entram)."""
    cel = [" ".join(_palavras(c)) for c in cabecalho.strip().strip("|").split("|")]
    return any(re.search(r"(^| )campo( |$)", c) for c in cel) or ("nome" in cel and "tipo" in cel)


def remove_ocr_redundante(md, stats):
    """Tira o bloco [!ocr] cuja imagem é seguida (pulando linhas vazias, legenda e títulos como "Imagem 01 - X")
    por uma tabela que descreve os campos da tela (cabeçalho com "campo", ou nome+tipo), ou por qualquer tabela
    que já contém LIM_OCR_TABELA das palavras do OCR. A imagem e a tabela ficam; o texto bruto segue no
    ocr_cache.json."""
    linhas, saida, i = md.split("\n"), [], 0
    while i < len(linhas):
        if not linhas[i].startswith("> [!ocr]"):
            saida.append(linhas[i])
            i += 1
            continue
        j = i + 1
        while j < len(linhas) and linhas[j].startswith(">"):
            j += 1
        k = j
        while k < len(linhas) and (not linhas[k].strip() or linhas[k].startswith(("#", "*Legenda"))):
            k += 1
        tabela = []
        while k < len(linhas) and linhas[k].lstrip().startswith("|"):
            tabela.append(linhas[k])
            k += 1
        if tabela and _tabela_de_campos(tabela[0]):
            stats["ocr_descartado_campos"] = stats.get("ocr_descartado_campos", 0) + 1
            i = j
            continue
        palavras = set(_palavras("\n".join(l[1:] for l in linhas[i + 1:j])))
        if tabela and len(palavras) >= 5 and \
                len(palavras & set(_palavras("\n".join(tabela)))) / len(palavras) >= LIM_OCR_TABELA:
            stats["ocr_descartado_tabela"] = stats.get("ocr_descartado_tabela", 0) + 1
        else:
            saida.extend(linhas[i:j])
        i = j
    return "\n".join(saida)


# ------------------------------------------------------------------ tipo e peso do artefato (RAG)
# Tipo = pasta + sigla (nunca só a sigla): "PT" em glossario/mensagens não é plano de teste e "UC" aparece
# dentro de pastas de user story. Siglas de story variam: US (User Story), HU (História do Usuário),
# EU (Estória do Usuário); as pastas também (historias_de_usuario, estoria_de_usuario, user_story...).
PESO_TIPO = {"caso_de_uso": 1.0, "user_story": 0.85, "plano_de_teste": 0.4, "regras_de_negocio": 0.8,
             "glossario": 0.8, "mensagens": 0.8, "eventos": 0.8, "documento": 0.6, "indice": 0.3}
_RE_SIGLA = re.compile(r"^(UC|US|HU|EU|PT)\s?\d", re.I)


def tipo_artefato(id_vault):
    ps = id_vault.split("/")
    nome, pastas = ps[-1], ps[:-1]
    nn = nome.lower().replace("_", " ")
    if not _RE_SIGLA.match(nome):   # documentos de conteúdo que ficam na raiz do sistema ("gai - regras de negocio")
        for chave, tipo in (("regras de negocio", "regras_de_negocio"), ("mensagens", "mensagens"),
                            ("glossario", "glossario")):
            if nn.endswith(" - " + chave) or nn == chave:
                return tipo
    if "documentacao_tecnica" not in pastas:
        return "indice" if len(pastas) <= 4 else "documento"   # nota do sistema ou página de outra seção
    sub = pastas[pastas.index("documentacao_tecnica") + 1] if pastas[-1] != "documentacao_tecnica" else ""
    m = _RE_SIGLA.match(nome)
    sigla = m.group(1).upper() if m else ""
    n = nome.lower()
    if not sigla and re.search(r" - [^\d]+$", n) and (not sub or "documentacao tecnica" in n or n.endswith(
            " - " + sub.replace("_", " "))):
        return "indice"   # nota de pasta ("gai - plano de teste", "x - documentacao tecnica")
    if sub in PASTAS_TESTE:
        return "plano_de_teste"
    if sigla == "UC" or sub == "caso_de_uso":
        return "caso_de_uso"
    if any(t in sub for t in ("stor", "estoria", "hist")):
        return "user_story" if sigla in ("US", "HU", "EU") or not sigla else "documento"
    if sub in ("glossario", "mensagens", "eventos"):
        return sub
    if sub == "regras_de_negocio" or "regras de negocio" in n:
        return "regras_de_negocio"
    if "mensagens" in n and not sub:
        return "mensagens"
    if "glossario" in n and not sub:
        return "glossario"
    return "documento"


# ------------------------------------------------------------------ âncoras de wikilink
def _norm_ancora(t):
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def _codigo_ancora(t):
    m = re.match(r"\s*\[?\s*(RN|FE|FA|MN|Tela|UC|US|HU|EU|RF)\s*0*(\d+)", t, re.I)
    return (m.group(1).upper(), int(m.group(2))) if m else None


RE_WIKILINK_ANC = re.compile(r"\[\[([^\]|#]*)#([^\]|\\]+?)((?:\\?\|.*?)?)\]\](?!\])")


def corrige_ancoras(arquivos, stats):
    """Pós-passo: faz a âncora [[pagina#titulo]] casar com o título real da seção (texto normalizado, prefixo ou
    código RN/FE/FA/...); sem correspondência, tira a âncora (link para a página) ou deixa só o rótulo."""
    paginas, titulos = {}, {}
    for p in arquivos:
        with open(p, encoding="utf-8") as fh:
            linhas = fh.read().split("\n")
        paginas[p] = linhas
        chave = os.path.relpath(p, SAIDA).replace(os.sep, "/")[:-3]
        titulos[chave] = [re.sub(r"\s*#+\s*$", "", l.lstrip("#").strip()) for l in linhas if re.match(r"#{1,6}\s", l)]
    for p, linhas in paginas.items():
        proprio = os.path.relpath(p, SAIDA).replace(os.sep, "/")[:-3]
        mudou = False

        def sub(m):
            nonlocal mudou
            alvo, anc, resto = m.group(1), m.group(2).strip(), m.group(3)
            pagina = alvo or proprio
            hs = titulos.get(pagina)
            if hs is None:      # destino fora do vault ou arquivo não convertido: não mexe
                return m.group(0)
            na = _norm_ancora(anc)
            achado = next((h for h in hs if _norm_ancora(h) == na), None)
            if achado is None and na:
                achado = next((h for h in hs if min(len(na), len(_norm_ancora(h))) >= 8 and
                               (_norm_ancora(h).startswith(na) or na.startswith(_norm_ancora(h)))), None)
            cod = _codigo_ancora(anc)
            if achado is None and cod:
                achado = next((h for h in hs if _codigo_ancora(h) == cod), None)
            if achado is not None:
                nova = ancora_segura(achado)
                if nova != anc:
                    stats["ancoras_corrigidas"] += 1
                    mudou = True
                return f"[[{alvo}#{nova}{resto}]]"
            stats["ancoras_removidas"] += 1
            mudou = True
            if alvo:
                return f"[[{alvo}{resto}]]"
            rotulo = resto.lstrip("\\").lstrip("|")
            return rotulo or anc
        novas = [RE_WIKILINK_ANC.sub(sub, l) for l in linhas]
        if mudou:
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(novas))


# ------------------------------------------------------------------ OCR de código ou de baixa qualidade
LIM_QUALIDADE_OCR = 0.35  # fração de palavras do OCR que existem no vocabulário do próprio vault


def descarta_ocr_ruim(arquivos, stats):
    """Depois de gravado o vault: remove blocos [!ocr] de código (SQL/VBA) ou de qualidade baixa. Usa as mesmas
    funções do triagem_ocr.py (vocabulário = palavras com freq >= 3 fora dos blocos de OCR)."""
    import collections
    import triagem_ocr as tr
    paginas, freq = {}, collections.Counter()
    for p in arquivos:
        with open(p, encoding="utf-8") as fh:
            linhas = fh.read().split("\n")
        paginas[p] = linhas
        freq.update(t for l in linhas if not l.startswith(">") for t in tr.tokens(l))
    vocab = {t for t, n in freq.items() if n >= 3}
    for p, linhas in paginas.items():
        fora = []
        for k, midia, ocr, depois in tr.blocos_ocr(linhas):
            q, cod, kw, sob, n = tr.analisa("\n".join(ocr), None, vocab)
            if cod or (q < LIM_QUALIDADE_OCR and n >= 5):
                fora.append((depois - len(ocr) - 1, depois))
                stats["ocr_descartado_codigo" if cod else "ocr_descartado_qualidade"] += 1
        if fora:
            for ini, fim in reversed(fora):
                del linhas[ini:fim]
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(linhas))


# False = o vault não leva nenhum bloco [!ocr] (decisão: OCR de prints é ruído para o RAG; cache intacto)
OCR_NO_VAULT = False
PASTAS_TESTE = {"plano_de_teste", "listas_dos_planos_de_testes", "requisito_teste"}


def converte_pagina(txt, namespace_atual, indice_media, indice_paginas, pasta_pagina_saida, cache_ocr,
                     sem_ocr, stats):
    txt = txt.replace(ZW, "")
    # algumas páginas têm embeds colados por engano, ex. "{{{{:ns:img.png?400|}}|}}" (chave dupla dentro
    # de chave dupla) -- normaliza para "{{{{" -> "{{" e "}}}}" -> "}}" antes do parser de mídia
    txt = re.sub(r"\{{3,}", "{{", txt)
    txt = re.sub(r"(\{\{[^{}\n]*\}\})\|\}\}", r"\1", txt)  # sobra "|}}" do embed duplo
    txt = txt.replace("{{|}}", "")
    txt = re.sub(r"\}{3,}", "}}", txt)
    txt = _conv_blocos_codigo(txt)
    txt = _conv_del_multilinha(txt)
    txt = _conv_cabecalhos(txt)
    txt = _conv_inline(txt)
    # links primeiro: converte_midia gera sintaxe ![[...]] nova, que não deve passar de novo pelo
    # resolvedor de links (senão a barra do caminho do arquivo é tratada como separador de namespace)
    txt = converte_links(txt, namespace_atual, indice_paginas)
    # plano de teste: o OCR vem de prints de execução (evidência), repete o que está nos casos de uso/protótipos
    # e só polui a recuperação; a imagem continua na página e o cache de OCR não é alterado
    sem_ocr_pagina = sem_ocr or not OCR_NO_VAULT or any(p in PASTAS_TESTE for p in namespace_atual)
    txt = converte_midia(txt, namespace_atual, indice_media, pasta_pagina_saida, cache_ocr, sem_ocr_pagina, stats)
    txt = _conv_listas(txt)
    txt = _conv_tabelas(txt)
    txt = remove_links_mortos(txt)
    txt = remove_ocr_redundante(txt, stats)
    txt = re.sub(r"(?m)^----+$", "---", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip() + "\n"
    return txt


def extrai_titulo(md):
    m = re.search(r"(?m)^#{1,5}\s+(.+)$", md)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------ execução
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int)
    ap.add_argument("--sem-ocr", action="store_true")
    ap.add_argument("--refazer-ocr", action="store_true")
    args = ap.parse_args()

    cache_ocr = {} if args.refazer_ocr else _carrega_cache()
    indice_media = indexar_media()
    indice_paginas = indexar_paginas()
    realoc, nomes = calcula_realocacoes(indice_paginas), calcula_nomes(indice_paginas)  # ambos com os dicts ainda vazios
    REALOC.update(realoc)
    NOMES.update(nomes)
    calcula_notas(indexar_paginas(incluir_excluidas=False))
    CATEGORIAS.update(calcula_categorias())
    paginas = sorted(listar_paginas())
    if args.limite:
        paginas = paginas[:args.limite]

    stats = {"paginas": 0, "erros": 0, "imagens": 0, "ocr_novo": 0, "ocr_cache": 0,
              "ocr_descartado_tabela": 0, "ocr_descartado_campos": 0, "ocr_descartado_codigo": 0, "ocr_descartado_qualidade": 0, "ancoras_corrigidas": 0, "ancoras_removidas": 0, "videos": 0, "anexos": 0, "midia_nao_encontrada": 0}
    t0 = time.time()
    gravados = []
    tipos = collections.Counter()
    for i, caminho_txt in enumerate(paginas, 1):
        rel = os.path.relpath(caminho_txt, PAGINAS_DIR).replace(ZW, "")
        namespace_atual, _ = namespace_da_pagina(caminho_txt)
        destino_rel = os.path.splitext(rel)[0] + ".md"
        # id limpo (cleanid), o mesmo que os links usam; sem ".md", porque as chaves não têm
        id_pagina = "sistemas/" + "/".join(cleanid_segmento(p) for p in
                                           os.path.splitext(destino_rel)[0].split(os.sep))
        id_vault = com_categoria(nota_de_pasta(pre_id(id_pagina)))
        destino_abs = os.path.join(SAIDA, id_vault.replace("/", os.sep) + ".md")
        os.makedirs(os.path.dirname(destino_abs), exist_ok=True)
        try:
            with open(caminho_txt, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
            md = converte_pagina(original, namespace_atual, indice_media, indice_paginas,
                                  os.path.dirname(destino_abs), cache_ocr, args.sem_ocr, stats)
            titulo = extrai_titulo(md) or os.path.splitext(os.path.basename(rel))[0]
            origem_id = "sistemas:" + ":".join(namespace_atual + [os.path.splitext(os.path.basename(rel))[0]])
            extra = ""
            ps = id_vault.split("/")
            if len(ps) > 2 and ps[1] in ("ATIVOS", "INATIVOS"):
                extra = f"situacao: {ps[1][:-1]}\n"  # ATIVO / INATIVO
                if ps[1] == "ATIVOS":
                    extra += f"categoria: {ps[2]}\n"
            tipo = tipo_artefato(id_vault)
            tipos[tipo] += 1
            extra += f"tipo: {tipo}\npeso: {PESO_TIPO[tipo]}\n"
            frontmatter = f"---\ntitle: {titulo}\norigem_dokuwiki: {origem_id}\n{extra}---\n\n"
            with open(destino_abs, "w", encoding="utf-8", newline="\n") as f:
                f.write(frontmatter + md)
            gravados.append(destino_abs)
            stats["paginas"] += 1
        except Exception as e:
            stats["erros"] += 1
            print(f"ERRO em {caminho_txt}: {type(e).__name__}: {e}", flush=True)
        if i % 200 == 0:
            _salva_cache(cache_ocr)
            print(f"  {i}/{len(paginas)} ({time.time()-t0:.0f}s) {stats}", flush=True)
    _salva_cache(cache_ocr)
    if not args.limite:
        descarta_ocr_ruim(gravados, stats)
        corrige_ancoras(gravados, stats)
    print("tipos:", dict(tipos))
    print(f"FIM em {time.time()-t0:.0f}s: {stats}")


if __name__ == "__main__":
    main()
