"""Paridade da conversão da wiki: converte um dump da DokuWiki com o código do núcleo (nucleo.wiki) e compara com os trechos
gravados no banco. É o critério 1 do pedido docs/pedido-sincronizacao-wiki.md: a busca calibrada só continua valendo se a
conversão nova gerar os mesmos trechos (mesmo `hash`) que a do laboratório.

O dump é o de 23/10/2026 (D:\\Projetos\\dump_wiki), a mesma origem dos trechos do `sosia`. Só leitura: nada é gravado.
Rode de novo sempre que mexer em nucleo/wiki/conversao.py ou trechos.py.

Uso (na raiz; no container, monte o dump em /dump):
  python -m migracao.confere_paridade_wiki [--dump D:\\Projetos\\dump_wiki] [--exemplos 15] [--mostra sistemas:gr:...]
"""
import argparse
import collections
import os
import sys
from urllib.parse import unquote

from nucleo import db
from nucleo.wiki import conversao, trechos as tr


def le_dump(raiz):
    """({id da página: texto}, [ids de mídia]) de um dump em disco. Os nomes no disco são os ids com percent-encoding."""
    paginas, midias = {}, []
    base = os.path.join(raiz, "sistemas")
    for d, _, fs in os.walk(base):
        for f in fs:
            if f.endswith(".txt"):
                rel = os.path.relpath(os.path.join(d, f[:-4]), base)
                partes = [unquote(p) for p in rel.replace("\\", "/").split("/")]
                with open(os.path.join(d, f), "r", encoding="utf-8", errors="replace") as fh:
                    paginas["sistemas:" + ":".join(partes)] = fh.read()
    base = os.path.join(raiz, "media")
    for d, _, fs in os.walk(base):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), base)
            midias.append("sistemas:" + ":".join(unquote(p) for p in rel.replace("\\", "/").split("/")))
    return paginas, midias


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="/dump" if os.path.isdir("/dump") else r"D:\Projetos\dump_wiki")
    ap.add_argument("--exemplos", type=int, default=15)
    ap.add_argument("--mostra", help="id de uma página: imprime os trechos novos e os do banco, lado a lado")
    a = ap.parse_args()

    paginas, midias = le_dump(a.dump)
    conv = conversao.Conversor(paginas, midias)
    notas = conv.converte()
    novos = tr.carrega(notas)
    refs = tr.resolve(novos)
    cruz, sem_dest = tr.acesso_e_cruzadas(novos, refs, notas)
    print(f"dump: {len(paginas)} páginas, {len(midias)} mídias -> {len(notas)} notas, {len(novos)} trechos, {len(refs)} referências "
          f"({sum(1 for r in refs if r['destino'] is not None)} resolvidas, {cruz} em outro sistema)")
    if conv.stats.get("erros"):
        print("ERROS na conversão:", conv.stats["erros_paginas"][:10])

    conn = db.conectar(timeout=10)
    banco = conn.execute("""SELECT id, origem_dokuwiki, secao, ordem, hash, sistema, tipo, peso, situacao, categoria, titulo_pagina,
                                   arquivo_vault, sistemas_acesso, left(texto, 160) ini FROM apf.conhecimento_trecho
                            WHERE fonte = 'wiki'""").fetchall()
    b = {(r["origem_dokuwiki"], r["ordem"]): r for r in banco}
    n = {(t["origem_dokuwiki"], t["ordem"]): t for t in novos}
    pb, pn = {k[0] for k in b}, {k[0] for k in n}
    print(f"\n## Páginas com trechos: banco {len(pb)}, conversão nova {len(pn)}; só no banco {len(pb - pn)}, só na nova {len(pn - pb)}")
    for p in sorted(pb - pn)[:a.exemplos]:
        print("   só no banco:", p)
    for p in sorted(pn - pb)[:a.exemplos]:
        print("   só na nova: ", p)

    iguais = sum(1 for k, r in b.items() if k in n and n[k]["hash"] == r["hash"] and n[k]["secao"] == r["secao"])
    print(f"\n## Trechos: banco {len(b)}, nova {len(n)}; iguais (página, ordem, seção e hash) {iguais} = {100 * iguais / len(b):.2f}% do banco")
    dif_pag = collections.Counter()
    for k, r in b.items():
        if k not in n or n[k]["hash"] != r["hash"] or n[k]["secao"] != r["secao"]:
            dif_pag[k[0]] += 1
    for k in n:
        if k not in b:
            dif_pag[k[0]] += 1
    print(f"   páginas com alguma diferença de texto: {len(dif_pag)}")
    por_sis = collections.Counter(p.split(":")[1] for p in dif_pag)
    print("   por sistema:", dict(por_sis.most_common(12)))
    for p, q in dif_pag.most_common(a.exemplos):
        ks = sorted(k for k in set(b) | set(n) if k[0] == p)
        k1 = next(k for k in ks if k not in b or k not in n or n[k]["hash"] != b[k]["hash"] or n[k]["secao"] != b[k]["secao"])
        print(f"   {p}: {q} trechos; 1ª diferença na ordem {k1[1]}")
        print(f"      banco: {(b[k1]['secao'] + ' | ' + b[k1]['ini'][:110]) if k1 in b else '(não existe)'!r}")
        print(f"      nova:  {(n[k1]['secao'] + ' | ' + n[k1]['texto'][:110]) if k1 in n else '(não existe)'!r}")

    print("\n## Metadados dos trechos com o mesmo hash")
    for campo in ("sistema", "tipo", "peso", "situacao", "categoria", "titulo_pagina", "arquivo_vault", "sistemas_acesso"):
        dif = []
        for k, r in b.items():
            if k in n and n[k]["hash"] == r["hash"]:
                x, y = r[campo], n[k][campo]
                if campo == "peso":
                    x, y = float(x), float(y)
                if campo == "sistemas_acesso":
                    x, y = set(x or []), set(y or [])
                if x != y:
                    dif.append((k, x, y))
        print(f"   {campo:16} {len(dif)} diferentes" + (f"   ex.: {dif[0][0][0]} banco={dif[0][1]!r} nova={dif[0][2]!r}" if dif else ""))

    # referências: (origem, chave) -> destino
    ids = {r["id"]: (r["origem_dokuwiki"], r["ordem"]) for r in banco}
    rb = {}
    for r in conn.execute("SELECT origem_trecho_id o, chave, destino_trecho_id d, resolvido FROM apf.conhecimento_referencia").fetchall():
        rb[(ids[r["o"]], r["chave"])] = ids.get(r["d"]) if r["d"] else None
    rn = {}
    for r in refs:
        o = novos[r["origem"]]
        d = novos[r["destino"]] if r["destino"] is not None else None
        rn[((o["origem_dokuwiki"], o["ordem"]), r["chave"])] = (d["origem_dokuwiki"], d["ordem"]) if d else None
    comuns = set(rb) & set(rn)
    ig = sum(1 for k in comuns if rb[k] == rn[k])
    print(f"\n## Referências: banco {len(rb)}, nova {len(rn)}; mesma origem e código {len(comuns)}; mesmo destino {ig} "
          f"= {100 * ig / max(len(rb), 1):.2f}% do banco")
    difs = [k for k in comuns if rb[k] != rn[k]]
    for k in difs[:a.exemplos]:
        print(f"   {k[0][0]} [{k[0][1]}] {k[1]}: banco -> {rb[k]}  nova -> {rn[k]}")

    if a.mostra:
        print(f"\n## {a.mostra}")
        for k in sorted(k for k in set(b) | set(n) if k[0] == a.mostra):
            print(f"--- ordem {k[1]}")
            print("  banco:", (b[k]["secao"], b[k]["hash"][:10], b[k]["ini"]) if k in b else None)
            print("  nova: ", (n[k]["secao"], n[k]["hash"][:10], n[k]["texto"][:160]) if k in n else None)
    conn.close()


if __name__ == "__main__":
    main()
