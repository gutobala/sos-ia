"""Conversão DokuWiki → Markdown do vault, em memória (porta de migracao/referencia_wiki/converte_dokuwiki.py, 10/11/2026).

A lógica é a do laboratório, linha a linha, para os trechos saírem iguais aos do `sosia` (critério de paridade do pedido
docs/pedido-sincronizacao-wiki.md). O que muda é só a origem dos dados:
  - páginas: dicionário {id DokuWiki: texto} (vindo da API XML-RPC, ou do cache em apf.wiki_pagina_sync), em vez do dump em disco;
  - mídia: lista dos ids da wiki (wiki.getAttachments), só para saber se o arquivo existe. Não baixa nem copia nada;
  - sem OCR (no laboratório, OCR_NO_VAULT = False: o vault não leva nenhum bloco [!ocr]), sem gravar arquivos.
A ordem em que o laboratório percorria o disco (os.walk no NTFS) é reproduzida em `ordem_walk`, porque decide empates.

Uso: `Conversor(paginas, midias).converte()` → {id_vault: Nota}. `id_vault` = caminho da nota no vault, sem ".md"
("sistemas/ATIVOS/PHP/gr/documentacao_tecnica/estoria_de_usuario/EU010 - Visualizar Categoria").
"""
import collections
import os
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

ZW = "\u200b"
EXT_IMAGEM = {".png", ".jpg", ".jpeg", ".gif"}
EXT_VIDEO = {".mp4", ".webm", ".avi", ".mov", ".wmv", ".mkv"}
RE_MEDIA = re.compile(r"\{\{\s*([^}|?]+?)\s*(?:\?([^}|]*))?(?:\|([^}]*))?\s*\}\}")
# como o DokuWiki, o link termina no primeiro "]]"; um "]" isolado é permitido no alvo (âncora "#[Tela 01] Título") e no rótulo
RE_LINK = re.compile(r"(?<!!)\[\[\s*((?:[^\]|]|\](?!\]))+?)\s*(?:\|((?:[^\]]|\](?!\]))*))?\s*\]\]")
RE_URL_WIKI = re.compile(r"https?://terrawiki\.terracap\.df\.gov\.br/doku\.php\?id=([^&#\s]+)(#.*)?$", re.I)
ARQ_EXCLUSOES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "config", "wiki_exclusoes.txt")
PASTA_AUX = "_auxiliares"
FUSOES = {"glpweb": "glpv2"}  # a documentação do GLPv2 está em sistemas:glpweb:*, o glpv2 só tem o inicio
RE_SIGLA = re.compile(r"^(uc|hu|us|eu|pt|rn|msg)(\d+)$")
RE_ROTULO = re.compile(r"^\W*(uc|hu|us|eu|pt|rn|msg)\s*0*(\d+)\s*[-_:.–—]*\s*(.*)$", re.I)
RE_ARTEFATO = re.compile(r"^(uc|hu|us|eu|pt|rn|msg)\d+", re.I)
PESO_TIPO = {"caso_de_uso": 1.0, "user_story": 0.85, "plano_de_teste": 0.4, "regras_de_negocio": 0.8,
             "glossario": 0.8, "mensagens": 0.8, "eventos": 0.8, "documento": 0.6, "indice": 0.3}
_RE_SIGLA = re.compile(r"^(UC|US|HU|EU|PT)\s?\d", re.I)
PASTAS_TESTE = {"plano_de_teste", "listas_dos_planos_de_testes", "requisito_teste"}
MORTO_INI, MORTO_FIM = "\x00M", "\x00"
RE_ITEM_MORTO = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*\x00M[^\x00]*\x00\s*$")
RE_WIKILINK_ANC = re.compile(r"\[\[([^\]|#]*)#([^\]|\\]+?)((?:\\?\|.*?)?)\]\](?!\])")


@dataclass
class Nota:
    """Uma nota do vault: o frontmatter que o indexador lê e o corpo em Markdown."""
    origem_dokuwiki: str
    titulo: str
    situacao: str | None
    categoria: str | None
    tipo: str
    peso: float
    md: str


# ------------------------------------------------------------------ funções puras (cópia do laboratório)
def ancora_segura(a):
    """Âncora de seção p/ wikilink do Obsidian: [ ] | # ^ quebram o link, então viram espaço."""
    return re.sub(r"\s+", " ", re.sub(r"[\[\]|#^]", " ", a)).strip()


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


def carrega_exclusoes(arquivo=ARQ_EXCLUSOES):
    """-> (namespaces, paginas). Linha 'sistemas:c:' (com ':' no fim) exclui o namespace inteiro; sem ':' no fim exclui só
    aquela página. '#' começa comentário."""
    namespaces, paginas = set(), set()
    if os.path.exists(arquivo):
        with open(arquivo, "r", encoding="utf-8") as f:
            for linha in f:
                item = linha.split("#", 1)[0].strip().lower()
                if not item:
                    continue
                (namespaces if item.endswith(":") else paginas).add(item)
    return namespaces, paginas


def nome_seguro(s):
    s = s.replace(":", " - ").replace("_", " ")
    s = re.sub(r"[<>\"/\\|?*#^\[\]]+", " ", s)  # inválidos no Windows ou que quebram [[links]] do Obsidian
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    return s[:90].rstrip(" .-_")


def _conv_del_multilinha(texto):
    """<del> que atravessa linhas/parágrafos (ex. regras de negócio revogadas do GAI): risca linha a linha; título riscado vira
    cabeçalho."""
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
        nivel = 7 - min(len(m.group(1)), len(m.group(3)))  # ====== (6) -> H1 ... == (2) -> H5
        titulo = m.group(2).strip()
        if "[[" in titulo and "]]" not in titulo:
            titulo = titulo.replace("[[", "[")  # "[[Tela02] Título": colchete dobrado por engano, sem fechar
        return f"\n{'#' * nivel} {titulo}\n"
    #  : espaço não separável depois do "====", comum em texto colado do Word
    texto = re.sub(r"(?m)^[ \t ]*(={2,6})([^=\n]+?)(={2,6})[ \t ]*$", sub, texto)
    # "**Item Homologado..:** ===X===": título no meio da linha não é cabeçalho; vira negrito
    return re.sub(r"(?m)^(\*\*[^*\n]+\*\*[ \t]*)(={2,6})([^=\n]+?)(={2,6})[ \t]*$", r"\1**\3**", texto)


def _conv_inline(texto):
    texto = re.sub(r"<del>(.*?)</del>", r"~~\1~~", texto, flags=re.S)
    # "//" precedido de ":" é de URL (https://...), não itálico
    texto = re.sub(r"(?<!:)//\*\*(.*?)\*\*//", lambda m: "***" + m.group(1).strip() + "***", texto, flags=re.S)
    texto = re.sub(r"(?<!:)//(.*?)(?<!:)//", r"*\1*", texto)
    texto = re.sub(r"''(.*?)''", r"`\1`", texto)
    # <br> em vez de quebra "dura": "\\" aparece dentro de célula de tabela com frequência
    texto = texto.replace("\\\\", "<br>")
    return texto


def _conv_blocos_codigo(texto):
    def sub(m):
        lang, corpo = m.group(2) or "", m.group(3)
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
            while i < len(linhas) and re.match(r"^\s*[|^]", linhas[i]):
                # o "|" de [[link|rótulo]] e ![[img|400]] não pode separar células
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


def remove_links_mortos(texto):
    """Item de lista que é só um link morto some; link morto no meio do texto vira o próprio rótulo."""
    linhas = [ln for ln in texto.split("\n") if not RE_ITEM_MORTO.match(ln)]
    return re.sub(r"\x00M([^\x00]*)\x00", r"\1", "\n".join(linhas))


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
        return "indice" if len(pastas) <= 4 else "documento"
    sub = pastas[pastas.index("documentacao_tecnica") + 1] if pastas[-1] != "documentacao_tecnica" else ""
    m = _RE_SIGLA.match(nome)
    sigla = m.group(1).upper() if m else ""
    n = nome.lower()
    if not sigla and re.search(r" - [^\d]+$", n) and (not sub or "documentacao tecnica" in n or n.endswith(
            " - " + sub.replace("_", " "))):
        return "indice"
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


def _norm_ancora(t):
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def _codigo_ancora(t):
    m = re.match(r"\s*\[?\s*(RN|FE|FA|MN|Tela|UC|US|HU|EU|RF)\s*0*(\d+)", t, re.I)
    return (m.group(1).upper(), int(m.group(2))) if m else None


def extrai_titulo(md):
    m = re.search(r"(?m)^#{1,5}\s+(.+)$", md)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------ ordem do disco do laboratório
def _chave_ntfs(nome):
    """O NTFS lista os nomes em ordem de maiúsculas (comparação ordinal depois de passar para maiúsculas)."""
    return nome.upper()


def ordem_walk(caminhos):
    """Caminhos 'a/b/c' na ordem do os.walk no Windows: os arquivos da pasta (pela ordem do NTFS) e depois cada subpasta,
    recursivamente. Os caminhos são de arquivos; o último segmento é o nome do arquivo (com a extensão)."""
    arvore = {}
    for c in caminhos:
        no = arvore
        partes = c.split("/")
        for p in partes[:-1]:
            no = no.setdefault(p, {})
        no.setdefault(None, []).append((partes[-1], c))
    saida = []

    def desce(no):
        for _, c in sorted(no.get(None, []), key=lambda x: _chave_ntfs(x[0])):
            saida.append(c)
        for nome in sorted((k for k in no if k is not None), key=_chave_ntfs):
            desce(no[nome])
    desce(arvore)
    return saida


def _partes(id_pagina):
    """'sistemas:gai:x:inicio' -> ['gai', 'x', 'inicio'] (caminho relativo à pasta sistemas/ do dump)."""
    return id_pagina.split(":")[1:]


# ------------------------------------------------------------------ conversor (estado global do laboratório numa instância)
class Conversor:
    def __init__(self, paginas, midias, exclusoes=None):
        """paginas: {id DokuWiki 'sistemas:...': texto}. Ids com o espaço invisível (rascunho antigo do gcf) ficam de fora.
        midias: ids de mídia da wiki ('sistemas:gai:x:tela.png' ou 'gai:x:tela.png')."""
        # o laboratório lia os arquivos em modo texto: "\r\n" e "\r" viravam "\n"
        self.paginas = {i: t.replace("\r\n", "\n").replace("\r", "\n") for i, t in paginas.items()
                        if i.startswith("sistemas:") and ZW not in i}
        self.exclusoes = exclusoes if exclusoes is not None else carrega_exclusoes()
        self.indice_media = {}
        for mid in midias:
            # o dump do laboratório só tinha a mídia do namespace "sistemas:", guardada sem esse prefixo (não existe
            # media/sistemas/ no disco). Mídia de outro namespace continua "não encontrada", como lá.
            # A pasta com o espaço invisível (rascunho antigo do gcf) fica de fora, como as páginas.
            if not mid.lower().startswith("sistemas:") or ZW in mid:
                continue
            rel = re.sub(r"^sistemas:", "", mid, flags=re.I)
            pasta, _, nome = rel.rpartition(":")
            base, ext = os.path.splitext(nome)
            chave = cleanid(pasta + ":" + base) + ext.lower() if pasta else cleanid_segmento(base) + ext.lower()
            self.indice_media.setdefault(chave, rel.replace(":", "/"))
        self.nomes, self.realoc, self.final, self.categorias = {}, set(), {}, {}
        self._auxiliares = None
        self.stats = collections.Counter()

    # ---- exclusões
    def excluida(self, id_pagina):
        id_pagina = id_pagina.lower()
        namespaces, paginas = self.exclusoes
        return id_pagina in paginas or any(id_pagina.startswith(ns) for ns in namespaces)

    # ---- descoberta (no lugar do disco)
    def listar_paginas(self, incluir_excluidas=False):
        """Ids das páginas, na ordem do os.walk do laboratório."""
        por_caminho = {"/".join(_partes(i)) + ".txt": i for i in self.paginas}
        for c in ordem_walk(por_caminho):
            i = por_caminho[c]
            if not incluir_excluidas and self.excluida(i):
                continue
            yield i

    def pastas_raiz(self):
        """Nomes das pastas de 1º nível em sistemas/ (as que têm alguma página dentro)."""
        return sorted({_partes(i)[0] for i in self.paginas if len(_partes(i)) >= 2})

    def indexar_paginas(self, incluir_excluidas=True):
        return {"sistemas/" + "/".join(cleanid_segmento(p) for p in _partes(i))
                for i in self.listar_paginas(incluir_excluidas=incluir_excluidas)}

    @staticmethod
    def namespace_da_pagina(id_pagina):
        partes = _partes(id_pagina)
        return partes[:-1], cleanid_segmento(partes[-1]) or "inicio"

    def texto(self, id_pagina):
        t = self.paginas.get(id_pagina)
        return t.replace(ZW, "") if t is not None else None

    # ---- auxiliares
    def calcula_auxiliares(self):
        txt = self.texto("sistemas:inicio")
        if txt is None:
            return set()
        linkados = set()
        for m in RE_LINK.finditer(txt):
            partes = cleanid(m.group(1).split("#")[0]).split("/")
            if len(partes) >= 2 and partes[0] == "sistemas":
                linkados.add(partes[1])
        aux = set()
        for nome in self.pastas_raiz():
            ns = cleanid_segmento(nome)
            if ns not in linkados and ns not in FUSOES and not self.excluida(f"sistemas:{ns}:inicio"):
                aux.add(ns)
        return aux

    def para_vault(self, id_barra):
        if self._auxiliares is None:
            self._auxiliares = self.calcula_auxiliares()
        partes = id_barra.split("/")
        if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in FUSOES:
            partes[1] = FUSOES[partes[1]]
            return "/".join(partes)
        if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in self._auxiliares:
            return "/".join([partes[0], PASTA_AUX] + partes[1:])
        return id_barra

    # ---- nomes dos artefatos
    def calcula_nomes(self, indice_paginas):
        from collections import Counter
        candidatos, outro_tipo, soltos, titulos_proprios, proprios = {}, {}, {}, {}, {}
        for id_pagina in self.listar_paginas():
            namespace_atual, _ = self.namespace_da_pagina(id_pagina)
            txt = self.texto(id_pagina)
            segs = [cleanid_segmento(s) for s in _partes(id_pagina)]
            if segs[-1] == "inicio":
                segs = segs[:-1]
            if segs and RE_SIGLA.match(segs[-1]):
                for h in re.finditer(r"(?m)^[ 	]*=+[ 	]*(.+?)[ 	]*=+[ 	]*$", txt):
                    rt = RE_ROTULO.match(re.sub(r"[*/']{2,}", "", h.group(1)).strip())
                    chave_p = "/".join(x for x in ("sistemas/" + "/".join(segs)).split("/") if x != PASTA_AUX)
                    if rt:
                        if rt.group(1).lower() == RE_SIGLA.match(segs[-1]).group(1):
                            proprios[chave_p] = int(rt.group(2))
                            if rt.group(1).lower() != "pt":
                                titulos_proprios[chave_p] = nome_seguro(rt.group(3))
                        elif RE_SIGLA.match(segs[-1]).group(1) != "pt" and rt.group(1).lower() != "pt":
                            titulos_proprios[chave_p] = nome_seguro(rt.group(3))
                        break
                    rs = re.search(r"\b(uc|hu|us|eu)\s*0*\d+\s*[-_:.–—]+\s*(.+)$", re.sub(r"[*/']{2,}", "", h.group(1)), re.I)
                    if rs and not RE_SIGLA.match(segs[-1]).group(1) == "pt":
                        titulos_proprios[chave_p] = nome_seguro(rs.group(2))
                        break
            for m in RE_LINK.finditer(txt):
                if not m.group(2):
                    continue
                destino = self.resolve_link(m.group(1), namespace_atual, indice_paginas)
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
                if rot.group(1).lower() != alvo.group(1):
                    outro_tipo.setdefault("/".join(partes), Counter())[nome] += 1
                    continue
                if int(rot.group(2)) == int(alvo.group(2)):
                    candidatos.setdefault("/".join(partes), Counter())[nome] += 1
                else:
                    soltos.setdefault("/".join(partes), []).append((int(rot.group(2)), nome))
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

    def aplica_nomes(self, id_barra):
        if not self.nomes:
            return id_barra
        partes = id_barra.split("/")
        saida, chave = [], []
        for seg in partes:
            if seg == PASTA_AUX:
                saida.append(seg)
                continue
            chave.append(seg.lower())
            saida.append(self.nomes.get("/".join(chave), seg))
        return "/".join(saida)

    # ---- realocação para dentro de documentacao_tecnica
    def calcula_realocacoes(self, indice_paginas):
        achados = set()
        for nome in self.pastas_raiz():
            txt = self.texto(f"sistemas:{nome}:documentacao_tecnica:inicio")
            if txt is None:
                continue
            sis = cleanid_segmento(nome)
            for m in RE_LINK.finditer(txt):
                destino = self.resolve_link(m.group(1), [nome, "documentacao_tecnica"], indice_paginas)
                if destino is None:
                    continue
                partes = [p for p in destino.split("#")[0].split("/") if p != PASTA_AUX]
                if len(partes) < 3 or partes[0] != "sistemas" or partes[1] != sis \
                        or partes[2] in ("inicio", "documentacao_tecnica", "manual_do_usuario"):
                    continue
                base = "/".join(partes[:3])
                existe = not self.excluida(base.replace("/", ":")) and \
                    any(i == base or i.startswith(base + "/") for i in indice_paginas)
                destino_ocupado = any(i.startswith(f"sistemas/{sis}/documentacao_tecnica/{partes[2]}")
                                      for i in indice_paginas)
                if existe and not destino_ocupado:
                    achados.add(base)
        return achados

    def aplica_realoc(self, id_barra):
        if not self.realoc:
            return id_barra
        partes = id_barra.split("/")
        i0 = 2 if len(partes) > 1 and partes[1] == PASTA_AUX else 1
        if partes[0] != "sistemas" or len(partes) < i0 + 2:
            return id_barra
        if f"sistemas/{partes[i0].lower()}/{partes[i0 + 1].lower()}" in self.realoc:
            partes.insert(i0 + 1, "documentacao_tecnica")
        return "/".join(partes)

    # ---- nome do arquivo no lugar de "inicio"
    def titulos_sistemas(self):
        titulos = {}
        for nome in self.pastas_raiz():
            txt = self.texto(f"sistemas:{nome}:inicio")
            if txt is None:
                continue
            m = re.search(r"(?m)^\s*={2,6}\s*(.+?)\s*={2,6}\s*$", txt)
            if m:
                t = nome_seguro(re.sub(r"[*/']{2,}|<[^>]*>|\[|\]", "", m.group(1)))
                if t:
                    if len(t) > 80:
                        t = t[:80].rsplit(" ", 1)[0]
                    titulos[cleanid_segmento(nome)] = t.rstrip(" .,-_")
        return titulos

    def pre_id(self, id_dump):
        return self.para_vault(self.aplica_realoc(self.aplica_nomes(id_dump)))

    def calcula_notas(self, ids_dump):
        titulos = self.titulos_sistemas()
        pre = {i: self.pre_id(i) for i in ids_dump}
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
            if len(partes) == 1:
                return "Sistemas"
            if len(partes) == 2:
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
            if achatado and usados[final.lower()] > 1:
                final = monta(p, False)
                ultimo = sem_aux(p[:-len("/inicio")])[-1]
                if RE_ARTEFATO.match(ultimo):
                    final = f"{p[:-len('/inicio')]}/{RE_ARTEFATO.match(ultimo).group(0).upper()}"
            self.final[p] = final
        return pre

    def nota_de_pasta(self, id_pre):
        return self.final.get(id_pre, id_pre)

    # ---- categorias (ATIVOS/INATIVOS e classificação)
    def calcula_categorias(self):
        txt = self.texto("sistemas:inicio")
        if txt is None:
            return {}
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
                    niveis = [pilha[1], pilha[-1]] if len(pilha) > 3 else pilha[1:]
                    cats[partes[1]] = "/".join(niveis)
        return {k: v for k, v in cats.items() if v}

    def com_categoria(self, id_vault):
        partes = id_vault.split("/")
        if len(partes) >= 3 and partes[0] == "sistemas" and partes[1] in self.categorias:
            return "/".join([partes[0], self.categorias[partes[1]]] + partes[1:])
        return id_vault

    # ---- links e mídia
    def resolve_media(self, alvo, namespace_atual):
        alvo = alvo.strip()
        if alvo.lower().startswith(("http://", "https://")):
            return None
        base, ext = os.path.splitext(alvo)
        ext = ext.lower()
        absoluto = alvo.startswith(":")
        base_sem_sistemas = re.sub(r"^:*sistemas:", "", base, flags=re.I)
        candidatos = [cleanid(base_sem_sistemas) + ext]
        if not absoluto and namespace_atual:
            candidatos.insert(0, cleanid(":".join(namespace_atual) + ":" + base.lstrip(":")) + ext)
        for c in candidatos:
            if c in self.indice_media:
                return self.indice_media[c]
        return None

    def resolve_link(self, alvo, namespace_atual, indice_paginas, cru=False):
        """[[alvo]] do DokuWiki -> caminho relativo ao vault (sem extensão), ou None se for link externo. Links relativos
        "escalam" os namespaces como o DokuWiki."""
        alvo = alvo.strip()
        if alvo.lower().startswith(("http://", "https://", "ftp://", "mailto:")):
            return None
        ancora = None
        if "#" in alvo:
            alvo, ancora = alvo.split("#", 1)
        absoluto = alvo.startswith(":")
        sufixo = cleanid(alvo.lstrip(":"))
        if absoluto:
            destino = sufixo
        else:
            candidatos = []
            ns = ["sistemas"] + list(namespace_atual)
            while True:
                candidatos.append("/".join(ns) + "/" + sufixo if ns else sufixo)
                if not ns:
                    break
                ns = ns[:-1]
            destino = next((c for c in candidatos if c in indice_paginas), candidatos[-1])
        if cru:
            return destino
        destino = self.com_categoria(self.nota_de_pasta(self.pre_id(destino)))
        if ancora and ancora_segura(ancora):
            destino += "#" + ancora_segura(ancora)
        return destino

    def converte_links(self, texto, namespace_atual, indice_paginas):
        def sub(m):
            alvo, label = m.group(1), m.group(2)
            if alvo.strip().startswith("#"):
                ancora = ancora_segura(alvo.strip()[1:])
                return f"[[#{ancora}|{label.strip()}]]" if ancora and label else (label or ancora or "").strip()
            wiki = RE_URL_WIKI.match(alvo.strip())
            if wiki:
                alvo = ":" + unquote(wiki.group(1)) + (wiki.group(2) or "")
            destino = self.resolve_link(alvo, namespace_atual, indice_paginas)
            if destino is None:
                return f"[{(label or alvo).strip()}]({alvo.strip()})"
            cru = self.resolve_link(alvo, namespace_atual, indice_paginas, cru=True)
            if alvo.strip().split("#")[0].strip() and \
                    (cru not in indice_paginas or cru.replace("/", ":").lower() in self.exclusoes[1]):
                return MORTO_INI + (label.strip() if label else alvo.strip().rstrip(":").split(":")[-1]) + MORTO_FIM
            texto_label = label.strip() if label else alvo.strip().rstrip(":").split(":")[-1]
            return f"[[{destino}|{texto_label}]]"
        return RE_LINK.sub(sub, texto)

    def converte_midia(self, texto, namespace_atual):
        stats = self.stats

        def sub(m):
            alvo, params, legenda = m.group(1), m.group(2) or "", m.group(3)
            legenda = (legenda or "").strip()
            base, ext = os.path.splitext(alvo.strip())
            ext = ext.lower()
            if ext in EXT_VIDEO:
                stats["videos"] += 1
                return f"*[vídeo: {legenda or os.path.basename(alvo.strip())}]*"
            origem = self.resolve_media(alvo, namespace_atual)
            if origem is None:
                stats["midia_nao_encontrada"] += 1
                return f"*(mídia não encontrada: {alvo.strip()})*"
            rel_media = "media/" + origem
            if ext in EXT_IMAGEM:
                largura_m = re.search(r"(\d+)", params)
                tag_img = f"![[{rel_media}|{largura_m.group(1)}]]" if largura_m else f"![[{rel_media}]]"
                partes = [tag_img]
                if legenda and not em_tabela[0]:
                    partes.append(f"*Legenda: {legenda}*")
                stats["imagens"] += 1
                return "\n".join(partes) + ("\n" if len(partes) > 1 and partes[-1].startswith("> ") else "")
            stats["anexos"] += 1
            rotulo = _escapa_pipe_em_legenda(legenda) or origem.rsplit("/", 1)[-1]
            return f"[[{rel_media}|{rotulo}]]"
        em_tabela = [False]
        saida = []
        for linha in texto.split("\n"):
            em_tabela[0] = bool(re.match(r"^\s*[|^]", linha))
            saida.append(RE_MEDIA.sub(sub, linha))
        return "\n".join(saida)

    def converte_pagina(self, txt, namespace_atual, indice_paginas):
        txt = txt.replace(ZW, "")
        # embeds colados por engano, ex. "{{{{:ns:img.png?400|}}|}}"
        txt = re.sub(r"\{{3,}", "{{", txt)
        txt = re.sub(r"(\{\{[^{}\n]*\}\})\|\}\}", r"\1", txt)
        txt = txt.replace("{{|}}", "")
        txt = re.sub(r"\}{3,}", "}}", txt)
        txt = _conv_blocos_codigo(txt)
        txt = _conv_del_multilinha(txt)
        txt = _conv_cabecalhos(txt)
        txt = _conv_inline(txt)
        # links primeiro: converte_midia gera ![[...]] novo, que não deve passar de novo pelo resolvedor de links
        txt = self.converte_links(txt, namespace_atual, indice_paginas)
        txt = self.converte_midia(txt, namespace_atual)
        txt = _conv_listas(txt)
        txt = _conv_tabelas(txt)
        txt = remove_links_mortos(txt)
        txt = re.sub(r"(?m)^----+$", "---", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip() + "\n"
        return txt

    # ---- pós-passo: âncoras de wikilink
    def corrige_ancoras(self, notas):
        titulos = {k: [re.sub(r"\s*#+\s*$", "", l.lstrip("#").strip()) for l in n.md.split("\n") if re.match(r"#{1,6}\s", l)]
                   for k, n in notas.items()}
        for proprio, nota in notas.items():
            def sub(m):
                alvo, anc, resto = m.group(1), m.group(2).strip(), m.group(3)
                pagina = alvo or proprio
                hs = titulos.get(pagina)
                if hs is None:
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
                        self.stats["ancoras_corrigidas"] += 1
                    return f"[[{alvo}#{nova}{resto}]]"
                self.stats["ancoras_removidas"] += 1
                if alvo:
                    return f"[[{alvo}{resto}]]"
                rotulo = resto.lstrip("\\").lstrip("|")
                return rotulo or anc
            nota.md = "\n".join(RE_WIKILINK_ANC.sub(sub, l) for l in nota.md.split("\n"))

    # ---- execução
    def converte(self):
        """{id_vault: Nota} de todas as páginas que entram no vault. Duas páginas no mesmo id_vault: vale a última na ordem
        do laboratório (sorted dos caminhos do Windows), como o arquivo sobrescrito no disco."""
        indice_paginas = self.indexar_paginas()
        realoc, nomes = self.calcula_realocacoes(indice_paginas), self.calcula_nomes(indice_paginas)
        self.realoc.update(realoc)
        self.nomes.update(nomes)
        self.calcula_notas(self.indexar_paginas(incluir_excluidas=False))
        self.categorias.update(self.calcula_categorias())
        ids = sorted(self.listar_paginas(), key=lambda i: "\\".join(_partes(i)) + ".txt")
        notas = {}
        for id_pagina in ids:
            namespace_atual, _ = self.namespace_da_pagina(id_pagina)
            id_dump = "sistemas/" + "/".join(cleanid_segmento(p) for p in _partes(id_pagina))
            id_vault = self.com_categoria(self.nota_de_pasta(self.pre_id(id_dump)))
            try:
                md = self.converte_pagina(self.texto(id_pagina), namespace_atual, indice_paginas)
            except Exception as e:  # noqa: BLE001  (uma página com erro não derruba a conversão)
                self.stats["erros"] += 1
                self.stats.setdefault("erros_paginas", []).append(f"{id_pagina}: {type(e).__name__}: {e}")
                continue
            titulo = extrai_titulo(md) or _partes(id_pagina)[-1]
            ps = id_vault.split("/")
            situacao = categoria = None
            if len(ps) > 2 and ps[1] in ("ATIVOS", "INATIVOS"):
                situacao = ps[1][:-1]
                if ps[1] == "ATIVOS":
                    categoria = ps[2]
            tipo = tipo_artefato(id_vault)
            notas.pop(id_vault, None)            # mesma ordem de inserção do arquivo sobrescrito
            notas[id_vault] = Nota(id_pagina, titulo, situacao, categoria, tipo, PESO_TIPO[tipo], md)
            self.stats["paginas"] += 1
        self.corrige_ancoras(notas)
        return notas
