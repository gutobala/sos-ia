"""Laboratório do ponto de partida (contrato 1.5.0): quanto o caminho do menu na busca melhora a documentação achada, e quanto se
perde com UM ponto de partida em vez de vários.

Simulação: o usuário escolhe o menu CERTO. O "certo" vem do gabarito por insumo (apf.gabarito_insumo: páginas de caso de uso /
user story citadas como insumo na contagem detalhada do chamado). O caminho de cada página é o que a wiki cita ("Menu: …",
"CAMINHO DE ACESSO"), extraído com nucleo.menus.wiki como no carrega_menus.py.

Condições (mesma busca do motor: busca.busca(n=5, max_por_pagina=1, sem plano de teste, sem expansão)):
  A    só o relato (resumo + descrição anonimizados)
  B1   "Menu: <caminho da página principal>." + relato   (principal = a página com mais funções no insumo)
  BK   um "Menu: …" por página do gabarito com caminho (até 5); uma busca por ponto; junta os resultados por rodízio e mostra 5
  BKu  igual ao BK, mas mostra 5 por ponto (união)
Métricas: revocação das páginas do gabarito entre as mostradas; revocação só das OUTRAS páginas (sem a principal); acerto (≥1).
Sem LLM e sem API: SOSIA_EMB=e5 (vetor local). Uso: python migracao/mede_ponto_partida.py [--limite N]
Saída: dados/ponto_partida.txt e dados/ponto_partida.json
"""
import argparse
import collections
import json
import os
import statistics
import sys

os.environ["SOSIA_EMB"] = "e5"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from nucleo import config, db  # noqa: E402
from nucleo.menus import wiki as mwiki  # noqa: E402
from nucleo.rag import busca  # noqa: E402

DSN_APF = "host=127.0.0.1 port=5433 dbname=apf user=apf password=apf_local sslmode=disable gssencmode=disable"
DADOS = os.path.join(config.RAIZ, "dados")
MAX_PONTOS = 5


def gabarito(apf):
    rows = apf.execute("""
        SELECT g.chamado_numero, g.sistema, g.origem_dokuwiki, g.n_funcoes,
               coalesce(c.resumo_anon, '') || E'\n' || coalesce(c.descricao_anon, '') AS relato
          FROM apf.gabarito_insumo g
          JOIN apf.chamado c ON c.origem = 'SOS' AND c.numero = g.chamado_numero
         WHERE g.tipo_artefato IN ('caso_de_uso', 'user_story') AND c.descricao_anon IS NOT NULL""").fetchall()
    por = collections.defaultdict(lambda: dict(paginas={}, relato="", sistema=None))
    for r in rows:
        x = por[r["chamado_numero"]]
        x["relato"], x["sistema"] = r["relato"].strip(), r["sistema"]
        x["paginas"][r["origem_dokuwiki"]] = max(x["paginas"].get(r["origem_dokuwiki"], 0), r["n_funcoes"] or 0)
    return por


def caminhos(conn, paginas):
    """{página: caminho} para as páginas que citam um menu (o caminho mais longo citado na página)."""
    rows = conn.execute("""SELECT DISTINCT ON (origem_dokuwiki, secao, ordem) sistema, tipo, secao, texto, origem_dokuwiki
                           FROM apf.conhecimento_trecho WHERE origem_dokuwiki = ANY(%s) AND tipo IN ('caso_de_uso', 'user_story')
                           ORDER BY origem_dokuwiki, secao, ordem, data_versao DESC""", (list(paginas),)).fetchall()
    out = {}
    for r in rows:
        for segs in mwiki.caminhos_do_trecho(r["tipo"], r["secao"], r["texto"], r["sistema"]):
            c = " > ".join(segs)
            if len(c) > len(out.get(r["origem_dokuwiki"], "")):
                out[r["origem_dokuwiki"]] = c
    return out


def paginas_achadas(conn, texto, sistema):
    res = busca.busca(conn, texto[:4000], sistema, n=5, incluir_teste=False, expandir=False, max_por_pagina=1)["resultados"]
    vistas = []
    for x in res:
        if x["origem_dokuwiki"] not in vistas:
            vistas.append(x["origem_dokuwiki"])
    return vistas


def rodizio(listas, n):
    out = []
    for i in range(max(map(len, listas), default=0)):
        for lst in listas:
            if i < len(lst) and lst[i] not in out:
                out.append(lst[i])
    return out[:n] if n else out


def faixa(k):
    return "1" if k == 1 else "2" if k == 2 else "3-5" if k <= 5 else "6+"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=0)
    a = ap.parse_args()
    apf = psycopg.connect(DSN_APF, row_factory=dict_row)
    conn = db.conectar(timeout=10)
    gab = gabarito(apf)
    todas = {p for x in gab.values() for p in x["paginas"]}
    cam = caminhos(conn, todas)
    print(f"chamados com gabarito: {len(gab)}; páginas: {len(todas)}; com caminho de menu: {len(cam)}", flush=True)

    linhas = []
    itens = sorted(gab.items())
    if a.limite:
        itens = itens[:a.limite]
    for i, (num, x) in enumerate(itens, 1):
        gold = set(x["paginas"])
        com_cam = [p for p in sorted(gold, key=lambda p: -x["paginas"][p]) if p in cam]
        principal = max(gold, key=lambda p: (p in cam, x["paginas"][p]))
        reg = dict(chamado=num, sistema=x["sistema"], n_gold=len(gold), n_com_caminho=len(com_cam), principal=principal,
                   gold=sorted(gold))
        reg["A"] = paginas_achadas(conn, x["relato"], x["sistema"])
        if com_cam:
            pontos = com_cam[:MAX_PONTOS]
            p1 = principal if principal in cam else pontos[0]
            reg["principal"] = p1
            reg["B1"] = paginas_achadas(conn, f"Menu: {cam[p1]}.\n{x['relato']}", x["sistema"])
            por_ponto = [reg["B1"] if p == p1 else paginas_achadas(conn, f"Menu: {cam[p]}.\n{x['relato']}", x["sistema"])
                         for p in pontos]
            reg["BK"] = rodizio(por_ponto, 5)
            reg["BKu"] = rodizio(por_ponto, 0)
            reg["caminhos"] = {p: cam[p] for p in pontos}
        linhas.append(reg)
        if i % 50 == 0:
            print(f"  {i}/{len(itens)}", flush=True)

    os.makedirs(DADOS, exist_ok=True)
    json.dump(linhas, open(os.path.join(DADOS, "ponto_partida.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    relatorio(linhas)


def metricas(regs, cond):
    rec, rec_out, hit, mostradas = [], [], [], []
    for r in regs:
        gold, achou = set(r["gold"]), set(r.get(cond) or [])
        rec.append(len(gold & achou) / len(gold))
        hit.append(1.0 if gold & achou else 0.0)
        mostradas.append(len(achou))
        outras = gold - {r["principal"]}
        if outras:
            rec_out.append(len(outras & achou) / len(outras))
    m = lambda v: f"{100 * statistics.mean(v):5.1f}%" if v else "   — "  # noqa: E731
    return dict(rec=m(rec), outras=m(rec_out), hit=m(hit), mostradas=f"{statistics.mean(mostradas):4.1f}" if mostradas else "—",
                n=len(regs), n_outras=len(rec_out))


def relatorio(linhas):
    out = []
    p = lambda s="": out.append(s)  # noqa: E731
    total = len(linhas)
    com = [r for r in linhas if "B1" in r]
    p("Laboratório do ponto de partida (contrato 1.5.0): caminho do menu na busca × um ponto × vários")
    p("Gabarito: páginas de caso de uso/user story citadas no insumo da contagem detalhada. Busca do motor, vetor e5 local.")
    p(f"Chamados: {total}; com ao menos 1 página do gabarito que cita caminho de menu: {len(com)} ({100 * len(com) / total:.0f}%).")
    pags = collections.Counter(r["n_gold"] for r in linhas)
    p(f"Páginas do gabarito por chamado: " + ", ".join(f"{k}: {v}" for k, v in sorted(pags.items())[:8]) + " …")
    p()
    p("Revocação = % das páginas do gabarito entre as mostradas; 'outras' = sem a página do menu escolhido (B1).")
    p("Só os chamados com caminho (mesmo conjunto nas 4 condições).")
    p()
    hdr = f"{'faixa (págs.)':14} {'n':>4} | " + " | ".join(f"{c:>3}: rev  outras  acerto  mostr." for c in ("A", "B1", "BK", "BKu"))
    p(hdr)
    grupos = [("todas", com)] + [(f, [r for r in com if faixa(r["n_gold"]) == f]) for f in ("1", "2", "3-5", "6+")]
    for nome, regs in grupos:
        if not regs:
            continue
        cells = []
        for c in ("A", "B1", "BK", "BKu"):
            m = metricas(regs, c)
            cells.append(f"{c:>3}: {m['rec']} {m['outras']} {m['hit']}  {m['mostradas']}")
        p(f"{nome:14} {len(regs):>4} | " + " | ".join(cells))
    p()
    sem = [r for r in linhas if "B1" not in r]
    if sem:
        m = metricas(sem, "A")
        p(f"Chamados SEM caminho de menu no gabarito ({len(sem)}): só o relato → revocação {m['rec']}, acerto {m['hit']}.")
    txt = "\n".join(out)
    open(os.path.join(DADOS, "ponto_partida.txt"), "w", encoding="utf-8").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
