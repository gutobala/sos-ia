"""Piloto: text-embedding-3-large (o do ANIA) × e5-large (atual) no corpus do núcleo, antes de decidir o provedor do RAG.

Escopo: 5 sistemas com mais gabarito (GRH, GAI, GCF, GIU, GOP). Custo estimado ~US$ 0,20 (US$ 0,13 / 1M tokens).
  gera     cria as colunas paralelas `embedding_3l vector(1024)` (conhecimento_trecho e funcao_sistema_emb) no `sosia` e gera os
           vetores 3-large só desses sistemas (retoma de onde parou; para em --teto US$)
  avalia   compara os dois modelos, por chamado do gabarito (texto anonimizado do pedido como consulta, só páginas/funções que
           existiam antes da abertura):
             wiki-vetor   recall@1/5/10 das páginas que o chamado alterou (gabarito_chamado, momento ALTERADA), só vetor
             wiki-híbrida o mesmo pela busca de produção (vetor + texto completo + pesos), nucleo.rag.busca
             catálogo     recall@10/20 das funções existentes que a detalhada aceita alterou/excluiu (funcao_sistema_id)
           + teste pareado (Wilcoxon) por chamado. Gabarito lido da base de pesquisa `apf` (fica fora do núcleo).
Sem chave: --simula mostra o volume e o custo estimado. O histórico de complexidade NÃO entra aqui (fica no e5: limiar calibrado).

Uso (na raiz, com OPENAI_API_KEY no .env):  .venv\\Scripts\\python migracao\\piloto_embeddings.py gera [--simula] [--teto 1.0]
                                             .venv\\Scripts\\python migracao\\piloto_embeddings.py avalia
Saída: dados\\piloto_embeddings.txt
"""
import argparse
import os
import sys
import time

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SOSIA_DSN", "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable")
from nucleo import config, db, embeddings, precos  # noqa: E402

SISTEMAS = ("grh", "gai", "gcf", "giu", "gop")
TODOS = False
APF = "host=127.0.0.1 port=5433 dbname=apf user=apf password=apf_local sslmode=disable gssencmode=disable"
LOTE = 96
SAIDA = os.path.join(RAIZ, "dados", "piloto_embeddings.txt")


def tokens_aprox(chars):
    return chars / 3.6          # português no tokenizador cl100k: ~3,5–3,8 caracteres por token


def _filtro_sis():
    """(SQL, parâmetro) do filtro de sistemas: os 5 do piloto ou todos (--todos)."""
    return ("true", None) if TODOS else ("sistemas_acesso && %(s)s", list(SISTEMAS))


def gera(simula, teto):
    with db.db() as c:
        if not simula:
            c.execute("ALTER TABLE apf.conhecimento_trecho ADD COLUMN IF NOT EXISTS embedding_3l vector(1024)")
            c.execute("ALTER TABLE apf.funcao_sistema_emb ADD COLUMN IF NOT EXISTS embedding_3l vector(1024)")
            c.commit()
        tem = c.execute("SELECT count(*) n FROM information_schema.columns WHERE table_schema='apf' AND "
                        "table_name='conhecimento_trecho' AND column_name='embedding_3l'").fetchone()["n"]
        falta = "embedding_3l IS NULL AND " if tem else ""
        fw, ps = _filtro_sis()
        wiki = c.execute(f"SELECT id, texto_indexado t FROM apf.conhecimento_trecho WHERE {falta} {fw} ORDER BY id", dict(s=ps)).fetchall()
        fc = "true" if TODOS else "lower(s.sigla) = ANY(%(s)s)"
        cat = c.execute(f"""SELECT e.funcao_sistema_id id, e.texto t FROM apf.funcao_sistema_emb e JOIN apf.funcao_sistema f ON f.id = e.funcao_sistema_id
                            JOIN apf.sistema s ON s.id = f.sistema_id WHERE {falta.replace('embedding_3l', 'e.embedding_3l')}
                            {fc} ORDER BY 1""", dict(s=ps)).fetchall()
        car = sum(len(r["t"]) for r in wiki) + sum(len(r["t"]) for r in cat)
        est = precos.custo_embedding_usd(config.MODELO_EMBEDDING, tokens_aprox(car))
        print(f"a gerar: {len(wiki)} trechos da wiki + {len(cat)} funções do catálogo; {car / 1e6:.2f} mi caracteres "
              f"≈ {tokens_aprox(car) / 1e6:.2f} mi tokens ≈ US$ {est:.2f}")
        if simula:
            return
        gasto = 0.0
        for nome, linhas, tabela, chave in (("wiki", wiki, "conhecimento_trecho", "id"), ("catálogo", cat, "funcao_sistema_emb", "funcao_sistema_id")):
            for i in range(0, len(linhas), LOTE):
                if gasto >= teto:
                    print(f"parado no teto de US$ {teto:.2f} (rode de novo para continuar)")
                    return
                lote = linhas[i:i + LOTE]
                m, tok = embeddings.api_textos([r["t"] for r in lote])
                gasto += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok)
                with c.cursor() as cur:
                    cur.executemany(f"UPDATE apf.{tabela} SET embedding_3l = %s::vector WHERE {chave} = %s",
                                    [(embeddings.vetor_sql(v), r["id"]) for v, r in zip(m, lote)])
                c.commit()
                print(f"  {nome}: {i + len(lote)}/{len(linhas)}  (US$ {gasto:.3f})", flush=True)
        print(f"custo real: US$ {gasto:.3f}")


def _gabarito():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(APF, row_factory=dict_row) as a:
        rows = a.execute("""SELECT chamado_numero ch, sistema, origem_dokuwiki pg FROM apf.gabarito_chamado
                            WHERE momento = 'ALTERADA' AND (%s OR sistema = ANY(%s))""", (TODOS, list(SISTEMAS))).fetchall()
    g = {}
    for r in rows:
        g.setdefault(r["ch"], dict(sistema=r["sistema"], paginas=set()))["paginas"].add(r["pg"])
    return g


def _recall(achados, esperados, k):
    return len(set(achados[:k]) & esperados) / len(esperados) if esperados else None


def _rr(achados, esperados):
    """Recíproco da posição do primeiro acerto (0 se não achou na lista)."""
    return next((1 / i for i, x in enumerate(achados, 1) if x in esperados), 0.0) if esperados else None


def avalia():
    from nucleo.rag import busca
    gab = _gabarito()
    linhas, saida, tempos = {}, [], {}

    def p(t=""):
        print(t, flush=True)
        saida.append(t)
    with db.db() as c:
        ch = {r["numero"]: r for r in c.execute(
            """SELECT c.id, c.numero, c.sistema_id, c.dt_abertura::date ab, coalesce(c.resumo_anon,'') || E'\\n' || coalesce(c.descricao_anon,'') tx
               FROM apf.chamado c WHERE c.origem = 'SOS' AND c.numero = ANY(%s) AND c.descricao_anon IS NOT NULL""", (list(gab),)).fetchall()}
        # funções existentes alteradas/excluídas pela detalhada aceita (versão com PF mais próximo do aceito)
        func = {}
        for r in c.execute("""SELECT x.chamado_id, cf.funcao_sistema_id fid FROM apf.contagem x JOIN apf.contagem_funcao cf ON cf.contagem_id = x.id
                              WHERE x.tipo = 'DETALHADA' AND x.papel = 'FABRICA' AND cf.funcao_sistema_id IS NOT NULL AND cf.acao IN ('A','E')
                                AND x.id = (SELECT y.id FROM apf.contagem y JOIN apf.chamado z ON z.id = y.chamado_id WHERE y.chamado_id = x.chamado_id
                                            AND y.tipo = 'DETALHADA' AND y.papel = 'FABRICA'
                                            ORDER BY abs(coalesce(y.pf_liquido_calculado, y.pf_liquido_declarado, 0) - z.pf_detalhado_origem), y.id DESC LIMIT 1)""").fetchall():
            func.setdefault(r["chamado_id"], set()).add(r["fid"])
        casos = [(n, gab[n], ch[n]) for n in gab if n in ch]
        p(f"Piloto de embeddings: {len(casos)} chamados com gabarito de páginas nos sistemas {'(todos)' if TODOS else ', '.join(SISTEMAS)}; "
          f"{sum(1 for _, _, r in casos if func.get(r['id']))} com gabarito de funções")
        tem_3l = c.execute("SELECT count(*) n FROM information_schema.columns WHERE table_schema='apf' AND "
                           "table_name='conhecimento_trecho' AND column_name='embedding_3l'").fetchone()["n"]
        modelos = (("e5", "embedding"), ("3-large", "embedding_3l")) if tem_3l else (("e5", "embedding"),)
        if not tem_3l:
            p("(sem a coluna embedding_3l: só o e5; rode 'gera' com a chave para comparar)")
        for modelo, col in modelos:
            config.EMB = "e5" if modelo == "e5" else "azure"
            embeddings.consulta_sql("aquecimento do modelo")      # carga do e5 / conexão da API fora da medida
            T = tempos.setdefault(modelo, dict(emb=[], busca=[]))
            for n, g, r in casos:
                t0 = time.perf_counter()
                v = embeddings.consulta_sql(r["tx"][:4000])
                t1 = time.perf_counter()
                ordem = [x["origem_dokuwiki"] for x in sorted(c.execute(
                    f"""SELECT origem_dokuwiki, min({col} <=> %(v)s::vector) d FROM apf.conhecimento_trecho
                        WHERE {col} IS NOT NULL AND %(s)s = ANY(sistemas_acesso) AND (dt_pagina_criada IS NULL OR dt_pagina_criada < %(a)s)
                        GROUP BY 1 ORDER BY 2 LIMIT 10""", dict(v=v, s=g["sistema"], a=r["ab"])).fetchall(), key=lambda x: x["d"])]
                T["emb"].append(t1 - t0)
                T["busca"].append(time.perf_counter() - t1)
                hib = [x["origem_dokuwiki"] for x in busca.busca(c, r["tx"][:4000], g["sistema"], n=10, expandir=False, max_por_pagina=1,
                                                                  ate_data=r["ab"])["resultados"]]
                L = linhas.setdefault(n, {})
                for k in (1, 5, 10):
                    L[f"{modelo}/wiki-vetor@{k}"] = _recall(ordem, g["paginas"], k)
                    L[f"{modelo}/wiki-hibrida@{k}"] = _recall(hib, g["paginas"], k)
                L[f"{modelo}/wiki-vetor MRR"] = _rr(ordem, g["paginas"])
                L[f"{modelo}/wiki-hibrida MRR"] = _rr(hib, g["paginas"])
                esp = func.get(r["id"])
                if esp:
                    fs = [x["fid"] for x in c.execute(
                        f"""SELECT f.id fid FROM apf.funcao_sistema_emb e JOIN apf.funcao_sistema f ON f.id = e.funcao_sistema_id
                            WHERE e.{col} IS NOT NULL AND f.sistema_id = %(s)s AND f.vigente_desde < %(a)s AND (f.vigente_ate IS NULL OR f.vigente_ate >= %(a)s)
                            ORDER BY e.{col} <=> %(v)s::vector LIMIT 20""", dict(v=v, s=r["sistema_id"], a=r["ab"])).fetchall()]
                    for k in (10, 20):
                        L[f"{modelo}/catalogo@{k}"] = _recall(fs, esp, k)
                    L[f"{modelo}/catalogo MRR"] = _rr(fs, esp)
    chaves = sorted({k.split("/", 1)[1] for L in linhas.values() for k in L})
    p(f"\n{'métrica':18} {'e5':>8} {'3-large':>9}   n   pareado (3-large melhor / pior / empate, p)")
    for m in chaves:
        par = [(L.get(f"e5/{m}"), L.get(f"3-large/{m}", L.get(f"e5/{m}"))) for L in linhas.values()]
        par = [(a, b) for a, b in par if a is not None and b is not None]
        if not par:
            continue
        a, b = np.array(par).T
        txt = f"{(b > a).sum()} / {(b < a).sum()} / {(b == a).sum()}"
        try:
            from scipy.stats import wilcoxon
            txt += f", p={wilcoxon(a, b).pvalue:.3f}" if (a != b).any() else ""
        except Exception:  # noqa: BLE001
            pass
        p(f"{m:18} {a.mean() * 100:7.1f}% {b.mean() * 100:8.1f}%  {len(par):3}   {txt}")
    p("\n(MRR: média de 1/posição do primeiro acerto; wiki até 10, catálogo até 20)")
    p("\nTempo por consulta (embedding da consulta / busca vetorial no banco), média e p95 em ms:")
    for modelo, T in tempos.items():
        e, b = np.array(T["emb"]) * 1000, np.array(T["busca"]) * 1000
        p(f"  {modelo:8} embedding {e.mean():6.0f} (p95 {np.percentile(e, 95):6.0f})   busca {b.mean():5.0f} (p95 {np.percentile(b, 95):5.0f})")
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    open(SAIDA, "w", encoding="utf-8").write("\n".join(saida) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("etapa", choices=["gera", "avalia"])
    ap.add_argument("--simula", action="store_true")
    ap.add_argument("--teto", type=float, default=1.0)
    ap.add_argument("--todos", action="store_true", help="todos os sistemas (não só os 5 do piloto)")
    a = ap.parse_args()
    TODOS = a.todos
    if TODOS:
        SAIDA = SAIDA.replace(".txt", "_todos.txt")
    gera(a.simula, a.teto) if a.etapa == "gera" else avalia()
