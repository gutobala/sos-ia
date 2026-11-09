"""Caminhos de menu citados na wiki (reserva para os sistemas sem catálogo próprio).

De onde saem:
  - texto dos casos de uso: "Menu: GAI > Relatório > NUGEC > Demonstrativo …", "opção de menu Cadastro -> NUGIV -> …",
    "Caminho de acesso à funcionalidade: - Menu: …";
  - 1ª linha das seções "CAMINHO DE ACESSO" das user stories ("Matrizes de Risco → Visualizar Matriz de Risco → …").
Normalização: `>`, `->`, `→`, `»` (e ` - ` quando não há outro separador) viram " > "; sai a sigla do começo e os passos de
entrada ("Login no Sistema …", "Perfil …", "Menu Lateral"); corta nos passos de ação ("localizar", "Ações", "Botão …");
repetidos (sem caixa e sem acento) viram um só, com as páginas de origem. Prefixo de outro caminho sai (fica a folha).
"""
import hashlib
import re
import unicodedata

SEP = r"\s*(?:[-–]+>|→|»|>)\s*"
SEG = r"[^\n>→»;\"“”|]{1,90}?"
CAMINHO = rf"{SEG}(?:{SEP}{SEG})+"
FIM = r"(?=\s*(?:[.;\"”'|\n]|\)\s*(?:[.;\n]|$)|$))"
RX_MENU = re.compile(rf"(?i)\b(?:op[çc][ãa]o\s+de\s+)?menu(?:\s+de\s+acesso)?(?:[^:\n]{{0,40}}?\s+[ée](?=[\s:]))?\s*[:\-–]?\s*[\"“']?\s*"
                     rf"({CAMINHO}){FIM}")
RX_SEP = re.compile(SEP)
RX_SECAO_CAMINHO = re.compile(r"(?i)(^|> )[0-9. ]*caminho de acesso\s*$")
RX_ENTRADA = re.compile(r"(?i)^(login\b|acessar o sistema|acesso ao sistema|entrar no sistema|sistema\b|perfil\b|usu[aá]rio\b|"
                        r"(menu )?(lateral|principal)$|menu$|tela (principal|inicial)|p[aá]gina (principal|inicial)|home$|in[ií]cio$)")
RX_ACAO = re.compile(r"(?i)^(localizar|a[çc][õo]es$|a[çc][ãa]o\b|bot[ãa]o|clicar|clique|acionar|selecionar|preencher|informar|"
                     r"digitar|marcar|confirmar|aba\b|link\b|[íi]cone|acessar|abrir|expandir|resultado d|"
                     r"(novo|nova|editar|visualizar|imprimir|filtrar|aprovar|rejeitar|excluir|salvar|detalhar|excel|pdf)$)")
MAX_SEGMENTOS = 6
MAX_SEG = 80


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def chave(segs):
    return " > ".join(sem_acento(s).lower() for s in segs)


def _limpa_seg(s):
    s = re.sub(r"^[\s\-–:*\"“”'(]+|[\s\"“”'.,:;*]+$", "", s)
    s = " ".join(s.split())
    if s.count("(") > s.count(")"):                   # "Cadastrar Alienações (Venda" -> cortado no parêntese aberto
        s = s[:s.rfind("(")].strip()
    return s


def _compacta(t):
    return re.sub(r"[^a-z0-9]", "", sem_acento(t).lower())


def normaliza(texto, sigla, outras=()):
    """'GAI > Cadastro -> NUGIV → Parcelamento Resíduo.' -> ['Cadastro', 'NUGIV', 'Parcelamento Resíduo'] (ou []).
    `sigla` e `outras` (aliases) saem do começo, comparadas sem acento, caixa nem pontuação ("e_social" = "eSocial")."""
    t = texto.strip()
    if RX_SEP.search(t):
        partes = RX_SEP.split(t)
    else:
        partes = re.split(r"\s+[-–]\s+", t)
    segs = []
    for p in partes:
        p = _limpa_seg(p)
        if not p:
            continue
        if RX_ACAO.match(p) or (segs and p[0].islower()):    # passo de tela ou texto corrido: o menu acabou antes
            break
        if len(p) > MAX_SEG or len(p.split()) > 10:      # prosa, não rótulo de menu
            return []
        segs.append(p)
    sigs = {_compacta(s) for s in (sigla, *outras) if s}
    sigs |= {"sistema" + s for s in sigs} | {"menu" + s for s in sigs}
    while segs and (RX_ENTRADA.match(segs[0]) or _compacta(segs[0]) in sigs):
        segs.pop(0)
    if segs and sem_acento(segs[0]).lower().startswith("menu "):
        segs[0] = segs[0][5:].strip()
    return segs[:MAX_SEGMENTOS] if len(segs) >= 2 else []


def caminhos_do_trecho(tipo, secao, texto, sigla, outras=()):
    """Listas de segmentos citadas num trecho da wiki."""
    out = []
    if tipo in ("caso_de_uso", "user_story"):
        for m in RX_MENU.finditer(texto or ""):
            s = normaliza(m.group(1), sigla, outras)
            if s:
                out.append(s)
    if tipo == "user_story" and RX_SECAO_CAMINHO.search(secao or ""):
        linha = next((x for x in (texto or "").split("\n") if x.strip() and not x.strip().startswith("|")), "")
        s = normaliza(linha, sigla, outras)
        if s:
            out.append(s)
    return out


def extrai(trechos, sigla, outras=()):
    """trechos: dicts com tipo, secao, texto, origem_dokuwiki. -> itens no formato do catálogo (só folhas, sem pai)."""
    achados = {}                                      # chave -> dict(segs, paginas, n)
    for t in trechos:
        for segs in caminhos_do_trecho(t["tipo"], t["secao"], t["texto"], sigla, outras):
            k = chave(segs)
            a = achados.setdefault(k, dict(segs=segs, paginas=[], n=0))
            a["n"] += 1
            if t["origem_dokuwiki"] not in a["paginas"]:
                a["paginas"].append(t["origem_dokuwiki"])
    chaves = set(achados)
    out = []
    for k, a in sorted(achados.items(), key=lambda x: x[0]):
        if any(o != k and o.startswith(k + " > ") for o in chaves):   # "Cadastro > NUGIV" some se há "Cadastro > NUGIV > …"
            continue
        out.append(dict(id="w" + hashlib.md5(k.encode("utf-8")).hexdigest()[:12], pai=None, nome=a["segs"][-1], folha=True,
                        caminho=" > ".join(a["segs"]), ordem=0, comando=None, paginas=a["paginas"][:20]))
    for i, x in enumerate(out, 1):
        x["ordem"] = i
    return out
