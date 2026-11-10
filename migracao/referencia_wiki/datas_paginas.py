"""Preenche apf.conhecimento_trecho.dt_pagina_criada com a data de criação de cada página (campo `created` do
.meta do dump DokuWiki). `origem_dokuwiki` ("sistemas:gai:...:us027:inicio") aponta o .meta: meta/<com / no lugar de :>.meta.

Correção pelo histórico: a data do .meta às vezes é de recriação/migração (ex.: boleto_gai UC002: .meta de 13/08/2019, mas o
Histórico de Mudanças da própria página começa em 09/02/2017). A data usada no filtro anti-vazamento é a MENOR entre o .meta e a
primeira data do histórico (apf.gabarito_chamado.dt_mudanca, entre 2010 e hoje). A data do .meta fica em dt_pagina_meta.

Uso:  python datas_paginas.py            (idempotente; refazer após reindexar páginas novas)
"""
import datetime
import os
import re
import sys

DUMP_META = r"D:\Projetos\dump_wiki\meta"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "importador"))
from comum import conectar  # noqa: E402

RE_CRIADA = re.compile(r'"created";i:(\d+)')


def main():
    conn = conectar()
    origens = [r[0] for r in conn.execute("SELECT DISTINCT origem_dokuwiki FROM apf.conhecimento_trecho").fetchall()]
    ok, sem = [], []
    for o in origens:
        caminho = os.path.join(DUMP_META, *o.split(":")) + ".meta"
        try:
            with open(caminho, encoding="utf-8") as f:
                m = RE_CRIADA.search(f.read())
        except OSError:
            m = None
        if m:
            ok.append((datetime.datetime.fromtimestamp(int(m.group(1)), datetime.timezone.utc), o))
        else:
            sem.append(o)
    with conn.cursor() as cur:
        cur.executemany("UPDATE apf.conhecimento_trecho SET dt_pagina_criada = %s WHERE origem_dokuwiki = %s", ok)
    conn.execute("ALTER TABLE apf.conhecimento_trecho ADD COLUMN IF NOT EXISTS dt_pagina_meta timestamptz")
    conn.execute("UPDATE apf.conhecimento_trecho SET dt_pagina_meta = dt_pagina_criada")
    n = conn.execute(
        """WITH h AS (SELECT origem_dokuwiki, min(dt_mudanca) dh FROM apf.gabarito_chamado
                      WHERE dt_mudanca BETWEEN DATE '2010-01-01' AND current_date GROUP BY 1)
           UPDATE apf.conhecimento_trecho t SET dt_pagina_criada = h.dh::timestamptz
           FROM h WHERE t.origem_dokuwiki = h.origem_dokuwiki AND (t.dt_pagina_criada IS NULL OR h.dh < t.dt_pagina_criada::date)""").rowcount
    conn.commit()
    print(f"{n} trechos tiveram a data antecipada pelo histórico da página")
    print(f"{len(ok)} páginas com data de criação; {len(sem)} sem .meta (ficam sem filtro por data)")
    for o in sem[:10]:
        print("  sem data:", o)


if __name__ == "__main__":
    main()
