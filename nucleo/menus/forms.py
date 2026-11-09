"""Menu de um sistema Oracle Forms lido direto do `.mmb` (fonte binária), sem Oracle Forms instalado.

Formato observado no `gai_menu.mmb` (ROS.60050, Forms 6i) em 08/11/2026:
  - o arquivo é paginado em 4 KB; as páginas marcadas com FF FF FF D4 nos bytes 4..7 têm 16 bytes de cabeçalho, que cortam
    as strings no meio e precisam sair antes de tudo;
  - na tabela de objetos, cada string tem 4 bytes de tamanho antes, termina em nulo e ocupa `4 + arred4(len + 2)` bytes;
  - item de menu = NOME, NOME, rótulo visível ("&Cadastro") e, quando abre um submenu, o NOME do menu filho no campo seguinte
    (ou "DEFAULT", que é o atributo visual);
  - cabeçalho de menu = NOME, NOME e nada de rótulo no campo seguinte;
  - o comando do item fica no bloco de código: o `call_Form('gai217', ...)` vem antes do nome "MENU.ITEM".
A raiz é o MAIN_MENU (o menu principal do módulo); objetos fora dele (MSG_ALERT, bibliotecas) não são alcançados.
"""
import re

PAG = 4096
MARCA_PAGINA = b"\xff\xff\xff\xd4"
LETRAS = "A-Z0-9_À-Ý"
IDENT = re.compile(rf"^[{LETRAS}]+$")
CALL = re.compile(r"call_form\s*\(\s*'([^']+)'", re.I)
NOME_CMD = re.compile(rf"([{LETRAS}]+_MENU)\.([{LETRAS}]+)$")     # "CADATRO_NUNEG_MENU.PARCELAMENTO_RESÍDUO"
DESCARTA = {"sair", "janela", "ajuda", "window", "help", "exit"}      # itens de sistema
RAIZ = "MAIN_MENU"


def sem_cabecalhos(dados: bytes) -> bytes:
    out = bytearray()
    for p in range(0, len(dados), PAG):
        pag = dados[p:p + PAG]
        out += pag[16:] if pag[4:8] == MARCA_PAGINA else pag
    return bytes(out)


def _proxima(off, s):
    """Offset da string seguinte na tabela de objetos."""
    return off + 4 + (len(s) + 2 + 3) // 4 * 4


def _comandos(dados):
    """{(MENU, ITEM): form chamado} pelo bloco de código de cada item."""
    soltas = [m.group().decode("latin-1") for m in re.finditer(rb"[\x20-\x7e\xa0-\xff]{3,}", dados)]
    out = {}
    for i, s in enumerate(soltas):
        if len(s) > 1 and ord(s[0]) == len(s) - 1:          # byte de tamanho na frente ("1" = 49 caracteres) colado no nome
            s = s[1:]
        m = NOME_CMD.search(s)
        if not m:
            continue
        for j in range(i - 1, max(i - 40, -1), -1):
            t = soltas[j]
            if NOME_CMD.search(t):
                break
            f = CALL.search(t)
            if f and not t.lstrip().startswith("--"):
                out.setdefault((m.group(1), m.group(2)), f.group(1))
                break
    return out


def _menus(dados):
    """{MENU: [dict(nome, rotulo, sub)]} na ordem do arquivo."""
    ss = [(m.start(), m.group().decode("latin-1")) for m in re.finditer(rb"[\x20-\x7e\xa0-\xff]{2,}(?=\x00)", dados)]
    menus, atual, i, n = {}, None, 0, len(ss)
    while i < n - 2:
        off, s = ss[i]
        if IDENT.match(s) and ss[i + 1][1] == s and ss[i + 1][0] == _proxima(off, s):
            if ss[i + 2][0] != _proxima(ss[i + 1][0], s):          # sem rótulo no campo seguinte: cabeçalho de menu
                atual = menus.setdefault(s, [])
                i += 2
                continue
            if atual is not None:
                rot = ss[i + 2][1]
                sub = None
                if i + 3 < n and ss[i + 3][0] == _proxima(ss[i + 2][0], rot) and IDENT.match(ss[i + 3][1]) and ss[i + 3][1] != "DEFAULT":
                    sub = ss[i + 3][1]
                if not any(it["nome"] == s for it in atual):        # a tabela aparece repetida no fim do arquivo
                    atual.append(dict(nome=s, rotulo=rot, sub=sub))
                i += 3
                continue
        i += 1
    return menus


def rotulo(texto):
    """'&Cadastro de  Prestamistas' -> 'Cadastro de Prestamistas' (o & marca a tecla de atalho)."""
    return " ".join(texto.replace("&&", "\0").replace("&", "").replace("\0", "&").split())


def le_mmb(dados: bytes):
    """Itens do menu na ordem da árvore: dict(id, pai, nome, folha, caminho, ordem, comando).
    `id` = "MENU.ITEM" (nome interno, estável entre compilações); submenu repetido ganha o id do pai como prefixo."""
    dados = sem_cabecalhos(dados)
    menus, cmd = _menus(dados), _comandos(dados)
    if RAIZ in menus:
        raiz = RAIZ
    else:
        filhos = {it["sub"] for its in menus.values() for it in its if it["sub"]}
        raizes = [m for m in menus if m not in filhos]
        if not raizes:
            return []
        raiz = max(raizes, key=lambda m: len(menus[m]))
    out, usados = [], set()

    def desce(menu, pai, caminho, vistos):
        ordem = 0
        for it in menus.get(menu, []):
            nome = rotulo(it["rotulo"])
            if not nome or set(nome) <= set("-_ ") or nome.lower() in DESCARTA:
                continue
            sub = it["sub"] if it["sub"] in menus and it["sub"] not in vistos else None
            ident = f"{menu}.{it['nome']}"
            if ident in usados:
                ident = f"{pai}/{ident}"
            usados.add(ident)
            ordem += 1
            cam = caminho + [nome]
            out.append(dict(id=ident, pai=pai, nome=nome, folha=sub is None, caminho=" > ".join(cam), ordem=ordem,
                            comando=cmd.get((menu, it["nome"]))))
            if sub:
                desce(sub, ident, cam, vistos | {sub})

    desce(raiz, None, [], {raiz})
    # submenu vazio (só itens descartados) vira folha
    com_filho = {x["pai"] for x in out if x["pai"]}
    for x in out:
        x["folha"] = x["id"] not in com_filho
    return out


def le_arquivo(caminho):
    with open(caminho, "rb") as f:
        return le_mmb(f.read())
