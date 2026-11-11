"""Carrega o catálogo de menus (apf.menu_sistema, contrato 1.5.0) no banco do núcleo.

Fontes, nesta ordem de prioridade na API: menu próprio do sistema (FORMS pelo `.mmb`; Benner, HOPE e AGENDAMENTO quando houver
extrator) e, como reserva para todos, os caminhos citados na wiki. Sempre simula (gera dados/menus.sql e o relatório de
cobertura); só grava no banco com --aplicar. O exporta_nucleo.py aplica migracao/sql/04_menu_sistema.sql e dados/menus.sql na
database nova antes do pg_dump, para o sosia.dump já sair com os menus.

Uso (no container da API, na raiz do projeto montada em /app; o SOSIA_DSN aponta para o banco do núcleo):
  python -m migracao.carrega_menus --mmb GAI=fontes/gai_menu.mmb --wiki             (simula)
  python -m migracao.carrega_menus --mmb GAI=fontes/gai_menu.mmb --wiki --aplicar
Saída: dados/menus.sql e dados/menus_cobertura.txt
"""
import argparse
import collections
import os
import sys

from nucleo import config, db
from nucleo.menus import catalogo, forms

DADOS = os.path.join(config.RAIZ, "dados")


# a leitura do catálogo e da wiki mora no núcleo (nucleo/menus/catalogo.py): a sincronização da wiki também refaz os menus
sistemas = catalogo.sistemas
trechos_wiki = catalogo.trechos_wiki


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mmb", action="append", default=[], metavar="SIGLA=ARQUIVO", help="menu FORMS (.mmb) de um sistema")
    ap.add_argument("--wiki", action="store_true", help="caminhos citados na wiki, para todos os sistemas")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    if not a.mmb and not a.wiki:
        ap.error("informe --mmb e/ou --wiki")
    conn = db.conectar(timeout=10)
    canon, alias = sistemas(conn)
    sis = {**alias, **canon}
    cargas = []                                   # (chave, fonte, itens, sistema_id)
    for par in a.mmb:
        sigla, _, arq = par.partition("=")
        k = catalogo.chave_sistema(sigla)
        if k in alias:
            k = catalogo.chave_sistema(alias[k][1])
        itens = forms.le_arquivo(arq)
        if not itens:
            sys.exit(f"{arq}: nenhum item de menu reconhecido")
        cargas.append((k, "forms", itens, sis.get(k, (None,))[0]))
    if a.wiki:
        cargas += catalogo.cargas_wiki(conn)

    sql = ["BEGIN;", open(catalogo.ARQ_DDL, encoding="utf-8").read()]
    if a.wiki:
        sql.append("DELETE FROM apf.menu_sistema WHERE fonte = 'wiki';")
    sql += [catalogo.sql_carga(k, f, itens, sid) for k, f, itens, sid in cargas]
    sql.append("COMMIT;")
    os.makedirs(DADOS, exist_ok=True)
    with open(os.path.join(DADOS, "menus.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(sql) + "\n")

    rel = ["# Cobertura do catálogo de menus (apf.menu_sistema)", ""]
    por = collections.defaultdict(dict)
    for k, f, itens, _ in cargas:
        por[k][f] = itens
    rel.append(f"{'sistema':30} {'fonte usada':12} {'itens':>6} {'folhas':>6}  outras fontes")
    for k in sorted(por):
        fs = por[k]
        usada = next((x for x in catalogo.FONTES_PROPRIAS if x in fs), "wiki")
        itens = fs[usada]
        outras = ", ".join(f"{x} ({len(v)})" for x, v in fs.items() if x != usada)
        nome = (canon[k][1] if k in canon else f"{alias[k][1]} (alias {k})" if k in alias else f"{k} (sem apf.sistema)")
        rel.append(f"{nome:30} {usada:12} {len(itens):6} {sum(1 for x in itens if x['folha']):6}  {outras}")
    cobertos = {sis[k][1] for k in por if k in sis}
    sem = sorted(s for _, s in canon.values() if s not in cobertos)
    rel += ["", f"## Sistemas de apf.sistema sem nenhum menu ({len(sem)}): o SOS mostra o campo livre", "  " + ", ".join(sem)]
    rel += ["", f"total: {sum(len(i) for *_, i, _ in cargas)} itens em {len(por)} sistemas"]
    with open(os.path.join(DADOS, "menus_cobertura.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(rel) + "\n")
    print("\n".join(rel))
    print("\ngerados: dados/menus.sql e dados/menus_cobertura.txt")

    if a.aplicar:
        catalogo.garante(conn)
        if a.wiki:
            conn.execute("DELETE FROM apf.menu_sistema WHERE fonte = 'wiki'")
        for k, f, itens, sid in cargas:
            conn.execute(catalogo.sql_carga(k, f, itens, sid))
        conn.commit()
        n = conn.execute("SELECT count(*) n FROM apf.menu_sistema").fetchone()["n"]
        print(f"aplicado: apf.menu_sistema com {n} itens")
    else:
        print("simulação: nada gravado (use --aplicar)")
    conn.close()


if __name__ == "__main__":
    main()
