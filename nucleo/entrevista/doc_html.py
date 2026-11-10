"""Texto da wiki (markdown convertido da DokuWiki) → HTML seguro para o usuário ler no SOS (1.5.4).

Determinístico e sem dependência: escapa TODO o texto e só gera as próprias tags (p, br, table, thead, tbody, tr, th, td, ul, ol,
li, del, strong, span, div), com classes `sosia-*` para o SOS estilizar. Nada da wiki passa como HTML: `<ano de referência>`
aparece como texto. O que trata, pelo que aparece nos 27 mil trechos de UC/US/RN:
  - tabelas `| a | b |` (17 mil trechos), com a linha `| --- |` como separador do cabeçalho;
  - listas `- item` / `* item` (com recuo) e numeradas `1. item` (a wiki numera tudo com "1."; o navegador renumera);
  - `~~revogado~~` → riscado; `**negrito**`;
  - links `[texto](url)` e `[[página|texto]]` → só o texto (a wiki não abre para o usuário);
  - referências soltas uma por linha (RN01, Tela02, FE01, MN013…) → juntas na linha do passo, em destaque discreto;
  - passos "P1.", "FE01.", "FA02.1." e rótulos "BREVE DESCRIÇÃO:" no começo da linha → negrito;
  - sobras da conversão ("---", "Voltar", "#####" vazio) → somem.
"""
import html
import re

RX_SOBRA = re.compile(r"^\s*(?:-{3,}|voltar|#+)\s*$", re.I)
RX_TITULO = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
REF = r"(?:~~)?(?:RNF?|Tela|FE|FA|MN|MSG|UC|US|RF)[\s.\-]?\d+[\w.]*(?:~~)?"
RX_SO_REFS = re.compile(rf"^\s*{REF}(?:\s*[,;]?\s*{REF})*\s*[,;.]?\s*$", re.I)
RX_TABELA = re.compile(r"^\s*\|")
RX_SEPARADOR = re.compile(r"^:?-{3,}:?$")
RX_LISTA = re.compile(r"^(\s*)[-*](?:\s+(.*))?$")
RX_NUMERADA = re.compile(r"^(\s*)\d{1,2}[.)]\s+(.*)$")     # a wiki numera tudo com "1." (o navegador renumera)


def _item(l):
    return RX_LISTA.match(l) or RX_NUMERADA.match(l)


def _continua(l, mesmo):
    """A linha continua a lista: item do mesmo tipo, ou texto recuado que não é item (continuação do anterior)."""
    if not l.strip() or RX_TABELA.match(l):
        return False
    return bool(mesmo.match(l)) or (l.startswith(" ") and not _item(l))
RX_PASSO = re.compile(r"^((?:P|FE|FA|FS)\d+(?:\.\d+)*\.?)(\s)")
RX_ROTULO = re.compile(r"^([A-ZÀ-Ý][A-ZÀ-Ý \-/]{3,40}:)(\s|$)")
RX_RISCO = re.compile(r"~~(.+?)~~")
RX_NEGRITO = re.compile(r"\*\*(.+?)\*\*")
RX_LINK_MD = re.compile(r"\[([^\[\]]*)\]\([^)\s]*\)")
RX_LINK_DOKU = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
RX_REFS_FIM = re.compile(rf"((?:\s*[,;]?\s*{REF})+)\s*[,;.]?\s*$", re.I)


def _inline(t):
    """Uma linha (ou célula): links viram texto, depois escapa e aplica riscado/negrito."""
    t = RX_LINK_DOKU.sub(lambda m: (m.group(2) or m.group(1).split(":")[-1]).strip(), t)
    t = RX_LINK_MD.sub(lambda m: m.group(1), t)
    t = html.escape(t, quote=False)
    t = RX_RISCO.sub(r"<del>\1</del>", t)
    return RX_NEGRITO.sub(r"<strong>\1</strong>", t)


def _linha(t):
    """Linha de parágrafo: passo/rótulo no começo em negrito; referências no fim em <span class="sosia-refs">."""
    t = t.strip()
    refs = ""
    m = RX_REFS_FIM.search(t)
    if m and m.start() > 0:
        refs = ", ".join(x.strip() for x in re.split(r"[,;]?\s+|[,;]", m.group(1)) if x.strip())
        t = t[:m.start()].rstrip(" ;,")
    for rx in (RX_PASSO, RX_ROTULO):
        mm = rx.match(t)
        if mm:
            t = f"\0{mm.group(1)}\1{t[mm.end(1):]}"
            break
    out = _inline(t).replace("\0", "<strong>").replace("\1", "</strong>")
    if refs:
        out += f' <span class="sosia-refs">{_inline(refs)}</span>'
    return out


def _junta_refs(linhas):
    """Referências soltas uma por linha (RN01,\\nRN68\\nTela02) vão para o fim da linha anterior com texto."""
    out = []
    for l in linhas:
        if RX_SO_REFS.match(l) and out and out[-1].strip() and not RX_TABELA.match(out[-1]):
            out[-1] = out[-1].rstrip(" ,;") + ", " + l.strip().rstrip(" ,;.")
        elif RX_SO_REFS.match(l) and out and not out[-1].strip() and len(out) > 1 and out[-2].strip() \
                and not RX_TABELA.match(out[-2]):
            out[-2] = out[-2].rstrip(" ,;") + ", " + l.strip().rstrip(" ,;.")   # "P1. …;\nTela01\n\nP2" (linha vazia antes)
        else:
            out.append(l)
    return out


def _tabela(linhas):
    rows = []
    for l in linhas:
        cel = l.strip()
        cel = cel[1:] if cel.startswith("|") else cel
        cel = cel[:-1] if cel.endswith("|") else cel
        rows.append([c.strip() for c in cel.split("|")])
    cab = None
    if len(rows) >= 2 and any(rows[1]) and all(not c or RX_SEPARADOR.match(c) for c in rows[1]):
        cab, rows = rows[0], rows[2:]
    rows = [r for r in rows if not all(RX_SEPARADOR.match(c) for c in r if c)]
    partes = ['<div class="sosia-tabela"><table>']
    if cab:
        partes.append("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cab) + "</tr></thead>")
    partes.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody>")
    partes.append("</table></div>")
    return "".join(partes)


def _lista(linhas):
    tag = "ol" if RX_NUMERADA.match(linhas[0]) else "ul"
    itens = []
    for l in linhas:
        m = _item(l)
        if m:
            nivel = min(len(m.group(1).expandtabs(2)) // 2, 3)
            if (m.group(2) or "").strip():
                itens.append((nivel, m.group(2)))
        elif itens:                                       # continuação do item anterior
            itens[-1] = (itens[-1][0], itens[-1][1] + " " + l.strip())
    if not itens:
        return ""
    base = min(n for n, _ in itens)
    return f'<{tag} class="sosia-lista">' + "".join(
        (f'<li class="sosia-nivel{n - base}">' if n > base else "<li>") + _linha(t) + "</li>" for n, t in itens) + f"</{tag}>"


def para_html(texto):
    """HTML seguro de um trecho da wiki. Texto vazio → ""."""
    linhas = [l.rstrip() for l in (texto or "").replace("\r\n", "\n").split("\n")]
    linhas = _junta_refs([l for l in linhas if not RX_SOBRA.match(l)])
    blocos, i, n = [], 0, len(linhas)
    while i < n:
        l = linhas[i]
        if not l.strip():
            i += 1
            continue
        if RX_TABELA.match(l):
            j = i
            while j < n and RX_TABELA.match(linhas[j]):
                j += 1
            blocos.append(_tabela(linhas[i:j]))
        elif _item(l):
            mesmo = RX_NUMERADA if RX_NUMERADA.match(l) else RX_LISTA
            j = i + 1
            while j < n and _continua(linhas[j], mesmo):
                j += 1
            blocos.append(_lista(linhas[i:j]))
        elif RX_TITULO.match(l):
            j = i + 1
            blocos.append(f'<p class="sosia-titulo"><strong>{_inline(RX_TITULO.match(l).group(1))}</strong></p>')
        else:
            j = i
            while j < n and linhas[j].strip() and not RX_TABELA.match(linhas[j]) and not _item(linhas[j]) \
                    and not RX_TITULO.match(linhas[j]):
                j += 1
            blocos.append("<p>" + "<br>".join(_linha(x) for x in linhas[i:j]) + "</p>")
        i = j
    return "".join(b for b in blocos if b)
