"""Atualiza o dump da DokuWiki (D:\\Projetos\\dump_wiki) pela API XML-RPC, só com o que mudou. Precisa da VPN.

Compara a lista de páginas da wiki (wiki.getAllPages, só "sistemas:*") com o dump local:
  - página nova                      -> baixa o texto e cria um .meta mínimo (created/modified)
  - data mais nova ou tamanho diferente -> baixa; só grava se o texto mudou de fato (a data do .meta é atualizada sempre)
  - página que sumiu da wiki         -> move o .txt e o .meta para D:\\Projetos\\dump_wiki_removidas
  - mídia citada ({{...}}) nas páginas baixadas -> baixa se falta no dump ou se o tamanho mudou (vídeos ficam de fora)
Antes de sobrescrever, copia o arquivo antigo para D:\\Projetos\\backups\\dump_wiki_<data>. Simulação por padrão.

Depois de aplicar, para o vault do Obsidian:
  python converte_dokuwiki.py            (OCR só das imagens novas; o resto vem do ocr_cache.json)
  python quarentena_exclusoes.py --aplicar
  python atualiza_dump.py --orfaos-vault [--aplicar]   (tira do vault as notas de páginas removidas da wiki)
A base apf (RAG do laboratório) NÃO muda: reindexar (indexa_wiki.py) é decisão separada.

Uso:  python atualiza_dump.py [--aplicar] [--url URL]
      python atualiza_dump.py --orfaos-vault [--aplicar]
"""
import argparse
import base64
import datetime
import json
import os
import re
import shutil
import sys
import xmlrpc.client
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = r"D:\Projetos"
DUMP = os.path.join(RAIZ, "dump_wiki")
REMOVIDAS = os.path.join(RAIZ, "dump_wiki_removidas")
VAULT = os.path.join(RAIZ, "Terrawiki")
VAULT_REMOVIDAS = os.path.join(RAIZ, "Terrawiki_removidas")
URL = os.environ.get("TERRAWIKI_URL", "http://terrawiki.terracapnet.local/lib/exe/xmlrpc.php")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados", "atualiza_dump.jsonl")
VIDEO = {".mp4", ".avi", ".wmv", ".mov", ".mkv", ".webm", ".flv", ".mpg", ".mpeg"}
RE_MEDIA = re.compile(r"\{\{\s*([^}|?]+?)\s*(?:\?[^}|]*)?(?:\|[^}]*)?\}\}")
RE_MODIFIED = re.compile(r'"modified";i:(\d+)')


def epoch(dt):
    """DateTime do xmlrpc ('2017-09-21T19:33:50+00:00') -> epoch."""
    v = dt.value if hasattr(dt, "value") else str(dt)
    try:
        return int(datetime.datetime.fromisoformat(v).timestamp())
    except ValueError:
        return int(datetime.datetime.strptime(v, "%Y%m%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())


def segs(id_):
    return [quote(s, safe="") for s in id_.split(":")]


def arq_pagina(id_):
    return os.path.join(DUMP, *segs(id_)) + ".txt"


def arq_meta(id_):
    return os.path.join(DUMP, "meta", *segs(id_)) + ".meta"


def arq_media(id_):
    return os.path.join(DUMP, "media", *segs(id_))


class Escritor:
    """Grava com backup do arquivo antigo; em simulação só conta."""

    def __init__(self, aplicar):
        self.aplicar = aplicar
        self.backup = os.path.join(RAIZ, "backups", "dump_wiki_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

    def _guarda(self, caminho):
        if self.aplicar and os.path.exists(caminho):
            dest = os.path.join(self.backup, os.path.relpath(caminho, DUMP))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(caminho, dest)

    def texto(self, caminho, conteudo):
        if not self.aplicar:
            return
        self._guarda(caminho)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8", newline="") as f:
            f.write(conteudo)

    def binario(self, caminho, dados):
        if not self.aplicar:
            return
        self._guarda(caminho)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "wb") as f:
            f.write(dados)

    def move(self, caminho, raiz_destino):
        if not self.aplicar or not os.path.exists(caminho):
            return
        dest = os.path.join(raiz_destino, os.path.relpath(caminho, DUMP))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(caminho, dest)


def le(caminho):
    if not os.path.exists(caminho):
        return None
    with open(caminho, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def meta_modified(id_):
    t = le(arq_meta(id_)) or ""
    m = RE_MODIFIED.search(t)
    return int(m.group(1)) if m else None


def grava_meta(w, wiki, id_, modificada):
    t = le(arq_meta(id_))
    if t and RE_MODIFIED.search(t):
        w.texto(arq_meta(id_), RE_MODIFIED.sub(f'"modified";i:{modificada}', t))
        return
    criada = modificada
    try:  # a versão mais antiga do histórico é a melhor estimativa da criação (datas_paginas.py usa "created")
        versoes = [v["version"] for v in wiki.wiki.getPageVersions(id_, 0) if v.get("version")]
        criada = min(versoes + [modificada])
    except Exception:
        pass
    w.texto(arq_meta(id_), 'a:1:{s:7:"current";a:1:{s:4:"date";a:2:{s:7:"created";i:%d;s:8:"modified";i:%d;}}}'
            % (criada, modificada))


def ids_media(texto, id_pagina):
    """Ids de mídia citados na página, resolvidos como a DokuWiki: ':' no começo = absoluto; '.' = relativo;
    sem ':' = namespace da página; com ':' = absoluto (tenta também relativo, se o absoluto não existir)."""
    ns = id_pagina.split(":")[:-1]
    out = []
    for m in RE_MEDIA.finditer(texto):
        alvo = m.group(1).strip()
        if alvo.lower().startswith(("http://", "https://")) or not alvo:
            continue
        if alvo.startswith(":"):
            cand = [alvo.lstrip(":")]
        elif alvo.startswith("."):
            partes = list(ns)
            for p in alvo.split(":"):
                if p == "..":
                    partes = partes[:-1]
                elif p not in (".", ""):
                    partes.append(p)
            cand = [":".join(partes)]
        elif ":" not in alvo:
            cand = [":".join(ns + [alvo])]
        else:
            cand = [alvo, ":".join(ns + [alvo])]
        out.append([c.lower().replace(" ", "_") for c in cand])
    return out


def atualiza(aplicar):
    wiki = xmlrpc.client.ServerProxy(URL, allow_none=True)
    w = Escritor(aplicar)
    remotas = {p["id"]: p for p in wiki.wiki.getAllPages() if p["id"].startswith("sistemas:")}
    locais = set()
    for d, _, fs in os.walk(os.path.join(DUMP, "sistemas")):
        for f in fs:
            if f.endswith(".txt"):
                rel = os.path.relpath(os.path.join(d, f[:-4]), DUMP)
                locais.add(":".join(rel.split(os.sep)))
    loc_quoted = {":".join(segs(i)): i for i in remotas}  # id remoto -> forma do disco

    res = dict(data=datetime.datetime.now().isoformat(timespec="seconds"), aplicado=aplicar, url=URL,
               novas=[], alteradas=[], iguais=0, removidas=[], midia_nova=[], midia_alterada=[], midia_nao_achada=[],
               erros=[])
    baixadas = {}
    for id_, p in sorted(remotas.items()):
        mod = epoch(p["lastModified"])
        local = le(arq_pagina(id_))
        if local is not None:
            mm = meta_modified(id_)
            tam_local = len(local.encode("utf-8"))
            if mm is not None and mod <= mm and tam_local == p["size"]:
                continue
        try:
            texto = wiki.wiki.getPage(id_)
        except Exception as e:
            res["erros"].append(f"{id_}: {type(e).__name__}: {e}")
            continue
        if local is None:
            res["novas"].append(id_)
        elif local.replace("\r\n", "\n") != texto.replace("\r\n", "\n"):
            res["alteradas"].append(id_)
        else:
            res["iguais"] += 1
            grava_meta(w, wiki, id_, mod)  # só a data estava defasada
            continue
        w.texto(arq_pagina(id_), texto)
        grava_meta(w, wiki, id_, mod)
        baixadas[id_] = texto

    for loc in sorted(locais - set(loc_quoted)):
        res["removidas"].append(loc)
        caminho = os.path.join(DUMP, *loc.split(":")) + ".txt"
        w.move(caminho, REMOVIDAS)
        w.move(os.path.join(DUMP, "meta", *loc.split(":")) + ".meta", REMOVIDAS)

    vistas = set()
    for id_, texto in baixadas.items():
        for cand in ids_media(texto, id_):
            if cand[0] in vistas:
                continue
            vistas.add(cand[0])
            if os.path.splitext(cand[0])[1].lower() in VIDEO:
                continue
            info, escolhido = None, None
            for c in cand:
                try:
                    i = wiki.wiki.getAttachmentInfo(c)
                    if i and i.get("size"):
                        info, escolhido = i, c
                        break
                except Exception:
                    pass
            if not info:
                res["midia_nao_achada"].append(f"{id_} -> {cand[0]}")
                continue
            caminho = arq_media(escolhido)
            if os.path.exists(caminho) and os.path.getsize(caminho) == info["size"]:
                continue
            (res["midia_alterada"] if os.path.exists(caminho) else res["midia_nova"]).append(escolhido)
            try:
                dados = wiki.wiki.getAttachment(escolhido)
                dados = dados.data if hasattr(dados, "data") else base64.b64decode(dados)
                w.binario(caminho, dados)
            except Exception as e:
                res["erros"].append(f"mídia {escolhido}: {type(e).__name__}: {e}")

    print(f"{'APLICADO' if aplicar else 'SIMULAÇÃO'} — {len(remotas)} páginas 'sistemas:' na wiki, {len(locais)} no dump")
    for k in ("novas", "alteradas", "removidas", "midia_nova", "midia_alterada", "midia_nao_achada", "erros"):
        print(f"  {k}: {len(res[k])}")
        for x in res[k][:200]:
            print(f"    {x}")
    print(f"  só a data defasada (texto igual): {res['iguais']}")
    if aplicar:
        print(f"backup do que foi sobrescrito: {w.backup}")
        print("Próximo: python converte_dokuwiki.py; python quarentena_exclusoes.py --aplicar; "
              "python atualiza_dump.py --orfaos-vault --aplicar")
    else:
        print("Simulação: nada foi gravado. Rode com --aplicar.")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")


def orfaos_vault(aplicar):
    """Notas do vault cuja página de origem (origem_dokuwiki) não existe mais no dump -> Terrawiki_removidas."""
    existentes = set()
    for d, dirs, fs in os.walk(os.path.join(DUMP, "sistemas")):
        for f in fs:
            if f.endswith(".txt"):
                rel = os.path.relpath(os.path.join(d, f[:-4]), DUMP).replace("%E2%80%8B", "")
                existentes.add(":".join(rel.split(os.sep)))
    orfas, por_origem = [], {}
    for d, _, fs in os.walk(VAULT):
        for f in fs:
            if not f.endswith(".md"):
                continue
            p = os.path.join(d, f)
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                cab = fh.read(800)
            m = re.search(r"^origem_dokuwiki:\s*(\S+)", cab, re.M)
            if not m:
                continue
            if m.group(1) not in existentes:
                orfas.append(p)
            else:
                por_origem.setdefault(m.group(1), []).append(p)
    # mesma página em duas notas (mudou o título ou o grupo/situação do sistema na página inicial da wiki): o conversor regrava
    # todas as notas a cada rodada, então a cópia com data mais antiga é a que ficou para trás
    duplicadas = 0
    for ps in por_origem.values():
        if len(ps) > 1:
            ps.sort(key=os.path.getmtime)
            orfas += ps[:-1]
            duplicadas += len(ps) - 1
    print(f"{'APLICADO' if aplicar else 'SIMULAÇÃO'} — {len(orfas)} notas do vault fora do dump "
          f"({len(orfas) - duplicadas} sem página; {duplicadas} cópias antigas de páginas que mudaram de pasta ou de título)")
    for p in orfas:
        print("  " + os.path.relpath(p, VAULT))
        if aplicar:
            dest = os.path.join(VAULT_REMOVIDAS, os.path.relpath(p, VAULT))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(p, dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--orfaos-vault", action="store_true")
    ap.add_argument("--url", help="endpoint XML-RPC (padrão: TERRAWIKI_URL ou terrawiki.terracapnet.local)")
    a = ap.parse_args()
    if a.url:
        global URL
        URL = a.url
    if a.orfaos_vault:
        orfaos_vault(a.aplicar)
    else:
        atualiza(a.aplicar)


if __name__ == "__main__":
    main()
