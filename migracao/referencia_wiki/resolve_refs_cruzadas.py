"""Resolve referências RN/MN que apontam para o documento de regras de OUTRO sistema.

O indexador só resolve citações dentro do mesmo sistema. Mas uma página (ex.: boleto_gai UC002) pode linkar o documento
de regras de outro sistema (`[[sistemas/ATIVOS/FORMS/gai/documentacao_tecnica/gai - regras de negocio#RN145...]]`) e citar
RN145 sem que o `boleto_gai` tenha RN própria. Este passo lê o vault, descobre os sistemas linkados pela página e resolve
as citações ainda abertas pelo CÓDIGO nesses sistemas. Também acrescenta o sistema de origem a `sistemas_acesso` do
trecho de destino, para a busca de quem escolhe o `boleto_gai` alcançar a RN do `gai`.

Rode depois de `indexa_wiki.py` (a reindexação apaga as referências) e de `--so-acesso`:
    python resolve_refs_cruzadas.py [--simula]
Reversível: `indexa_wiki.py --so-acesso` refaz o acesso; a reindexação refaz as referências.
"""
import argparse
import collections
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "importador"))
from comum import conectar  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

VAULT = r"D:\Projetos\Terrawiki\sistemas"
RE_LINK_DOC = re.compile(r"\[\[sistemas/(?:ATIVOS|INATIVOS)/(.+?)/documentacao_tecnica/")
PREFERENCIA = {"regras_de_negocio": 0, "mensagens": 0, "glossario": 1, "caso_de_uso": 2, "user_story": 3}


def sistemas_linkados(caminho, cache={}):
    if caminho not in cache:
        try:
            with open(os.path.join(VAULT, caminho), encoding="utf-8") as f:
                cache[caminho] = {m.group(1).replace("\\", "/").split("/")[-1] for m in RE_LINK_DOC.finditer(f.read())}
        except OSError:
            cache[caminho] = set()
    return cache[caminho]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simula", action="store_true")
    a = ap.parse_args()
    conn = conectar()
    conn.row_factory = dict_row
    sistemas = {r["sistema"] for r in conn.execute("SELECT DISTINCT sistema FROM apf.conhecimento_trecho").fetchall()}
    abertas = conn.execute(
        """SELECT r.id, r.chave, r.tipo_ref, o.sistema, o.arquivo_vault FROM apf.conhecimento_referencia r
           JOIN apf.conhecimento_trecho o ON o.id = r.origem_trecho_id
           WHERE NOT r.resolvido AND r.tipo_ref IN ('RN', 'MN')""").fetchall()
    destinos = collections.defaultdict(list)           # (sistema, chave) -> [(preferência, id, texto)]
    for t in conn.execute("SELECT id, sistema, tipo, definicoes FROM apf.conhecimento_trecho "
                          "WHERE definicoes IS NOT NULL AND definicoes <> '{}'::jsonb").fetchall():
        for k, v in t["definicoes"].items():
            destinos[(t["sistema"], k)].append((PREFERENCIA.get(t["tipo"], 9), t["id"], v))
    resolvidas, acesso, sem_dest = [], collections.defaultdict(set), 0
    for r in abertas:
        alvos = (sistemas_linkados(r["arquivo_vault"]) & sistemas) - {r["sistema"]}
        cand = [(sis, d) for sis in sorted(alvos) for d in destinos.get((sis, r["chave"]), [])]
        if not cand:
            sem_dest += 1
            continue
        sis, (_, did, texto) = min(cand, key=lambda x: x[1][0])
        resolvidas.append((did, texto, r["id"]))
        acesso[did].add(r["sistema"])
    print(f"{len(abertas)} citações RN/MN abertas; {len(resolvidas)} resolvidas em outro sistema; {sem_dest} sem destino")
    print(f"{len(acesso)} trechos de destino ganham acesso de outros sistemas")
    for did, orig in list(acesso.items())[:5]:
        t = conn.execute("SELECT sistema, titulo_pagina, secao FROM apf.conhecimento_trecho WHERE id = %s", (did,)).fetchone()
        print(f"   {t['sistema']} | {t['secao'][-60:]}  <- {sorted(orig)}")
    if a.simula:
        return
    for did, texto, rid in resolvidas:
        conn.execute("UPDATE apf.conhecimento_referencia SET destino_trecho_id = %s, texto_destino = %s, resolvido = true "
                     "WHERE id = %s", (did, texto, rid))
    for did, orig in acesso.items():
        conn.execute("UPDATE apf.conhecimento_trecho SET sistemas_acesso = (SELECT array_agg(DISTINCT x) "
                     "FROM unnest(sistemas_acesso || %s::text[]) x) WHERE id = %s", (sorted(orig), did))
    conn.commit()
    print("gravado")


if __name__ == "__main__":
    main()
