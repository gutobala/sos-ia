"""Experimento: melhorar os candidatos do catálogo (funcionalidades) além do vetor 3-large puro.

Gabarito e consultas iguais ao piloto de embeddings (migracao/piloto_embeddings.py --todos): chamados SOS cujo texto
anonimizado é a consulta e cujas funções existentes alteradas/excluídas na detalhada aceita são o alvo.
Sem vazamento (modo estrito): só funções vigentes antes da abertura E cuja contagem de criação é de outro chamado e anterior à
abertura (181 funções-alvo tinham sido cadastradas pela contagem do próprio chamado); o texto enriquecido só vale quando a contagem
de criação tem data anterior à abertura (senão a função entra com o texto simples).

Canais (cada um devolve uma lista ordenada de funções do sistema):
  V0  vetor 3-large do texto atual "<tipo> <nome> (<módulo>)"                         (produção hoje)
  V1  vetor 3-large do texto enriquecido: + arquivos (AR/TR) e campos (TD) da contagem que CRIOU a função (sempre anterior)
  L   palavra-chave (BM25) sobre o texto enriquecido
  H   histórico: chamados anteriores do mesmo sistema parecidos com o pedido (vetor 3-large) → funções que eles alteraram
      (contagem com data anterior à abertura); pontuação = soma da similaridade dos vizinhos
Fusões por RRF (k=60, sem ajuste fino para não sobreajustar ao gabarito). Reordenação: gpt-5.4-mini escolhe as 10 mais
prováveis entre as 30 primeiras de uma fusão.

Etapas (na raiz do sos-ia, chave no .env):
  prepara   cria o schema `exp` no sosia e gera os vetores do texto enriquecido e dos chamados (~US$ 0,10)
  avalia    tabela dos canais e fusões (sem custo além das consultas já cacheadas)
  reordena  --base F3 [--teto 1.5]  LLM sobre a fusão escolhida (~US$ 0,5–1); cache em dados/reordena_catalogo.json
Saída: dados/melhora_catalogo.txt
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import unicodedata

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SOSIA_DSN", "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable")
from nucleo import config, db, embeddings, llm, precos  # noqa: E402

APF = "host=127.0.0.1 port=5433 dbname=apf user=apf password=apf_local sslmode=disable gssencmode=disable"
SAIDA = os.path.join(RAIZ, "dados", "melhora_catalogo.txt")
CACHE_LLM = os.path.join(RAIZ, "dados", "reordena_catalogo.json")
N = 30            # profundidade de cada canal
RRF = 60
VIZINHOS = 15
MAX_TD = 30
STOP = set("""para pela pelo pelas pelos com sem que nos nas dos das uma uns umas por como mais mas foi ser sao esta este esse essa
isso nao sim ate seu sua seus suas ele ela eles elas tem ter ter quando onde qual quais todo toda todos todas apos sobre entre
deve devem favor solicito solicitamos solicitacao sistema tela campo""".split())


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def tokens(s):
    return [w for w in re.findall(r"[a-z0-9]{3,}", sem_acento(s or "")) if w not in STOP]


# ------------------------------------------------------------------------------------------------------------------ prepara
def texto_rico(c):
    """{fid: texto enriquecido} com AR/TR e TD só da contagem que criou a função."""
    base = {r["id"]: r["t"] for r in c.execute("SELECT funcao_sistema_id id, texto t FROM apf.funcao_sistema_emb")}
    ar, td = collections.defaultdict(list), collections.defaultdict(list)
    for r in c.execute("""SELECT f.id, a.nome FROM apf.funcao_sistema f
                          JOIN apf.contagem_funcao cf ON cf.contagem_id = f.contagem_criacao_id AND cf.funcao_sistema_id = f.id
                          JOIN apf.contagem_funcao_ar_tr a ON a.contagem_funcao_id = cf.id ORDER BY a.id"""):
        n = r["nome"].strip()
        if n and n.lower() not in [x.lower() for x in ar[r["id"]]]:
            ar[r["id"]].append(n)
    for r in c.execute("""SELECT f.id, t.nome_td nome FROM apf.funcao_sistema f
                          JOIN apf.contagem_funcao cf ON cf.contagem_id = f.contagem_criacao_id AND cf.funcao_sistema_id = f.id
                          JOIN apf.contagem_funcao_td t ON t.contagem_funcao_id = cf.id ORDER BY t.id"""):
        n = r["nome"].strip()[:40]
        if n and len(td[r["id"]]) < MAX_TD and n.lower() not in [x.lower() for x in td[r["id"]]]:
            td[r["id"]].append(n)
    out = {}
    for fid, t in base.items():
        partes = [t]
        if ar[fid]:
            partes.append("arquivos: " + ", ".join(ar[fid]))
        if td[fid]:
            partes.append("campos: " + ", ".join(td[fid]))
        out[fid] = " | ".join(partes)
    return out


def _gera(c, tabela, chave, linhas, teto, gasto):
    for i in range(0, len(linhas), 96):
        if gasto >= teto:
            print("teto atingido")
            break
        lote = linhas[i:i + 96]
        m, tok = embeddings.api_textos([t for _, t in lote])
        gasto += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok) or 0
        with c.cursor() as cur:
            cur.executemany(f"UPDATE exp.{tabela} SET emb = %s::vector WHERE {chave} = %s",
                            [(embeddings.vetor_sql(v), k) for v, (k, _) in zip(m, lote)])
        c.commit()
        print(f"  {tabela}: {i + len(lote)}/{len(linhas)}  US$ {gasto:.3f}", flush=True)
    return gasto


def prepara(teto):
    config.EMB = "azure"
    with db.db() as c:
        c.execute("""CREATE SCHEMA IF NOT EXISTS exp;
                     CREATE TABLE IF NOT EXISTS exp.funcao_rica (funcao_sistema_id int PRIMARY KEY, texto text, emb vector(1024));
                     CREATE TABLE IF NOT EXISTS exp.chamado_emb (chamado_id int PRIMARY KEY, texto text, emb vector(1024));""")
        rico = texto_rico(c)
        with c.cursor() as cur:
            cur.executemany("""INSERT INTO exp.funcao_rica (funcao_sistema_id, texto) VALUES (%s, %s)
                               ON CONFLICT (funcao_sistema_id) DO UPDATE SET texto = excluded.texto,
                                 emb = CASE WHEN exp.funcao_rica.texto = excluded.texto THEN exp.funcao_rica.emb END""", list(rico.items()))
        c.execute("""INSERT INTO exp.chamado_emb (chamado_id, texto)
                     SELECT id, left(coalesce(resumo_anon,'') || E'\\n' || coalesce(descricao_anon,''), 4000) FROM apf.chamado
                     WHERE origem = 'SOS' AND descricao_anon IS NOT NULL ON CONFLICT DO NOTHING""")
        c.commit()
        f = [(r["k"], r["t"]) for r in c.execute("SELECT funcao_sistema_id k, texto t FROM exp.funcao_rica WHERE emb IS NULL ORDER BY 1")]
        h = [(r["k"], r["t"]) for r in c.execute("SELECT chamado_id k, texto t FROM exp.chamado_emb WHERE emb IS NULL ORDER BY 1")]
        car = sum(len(t) for _, t in f + h)
        print(f"a gerar: {len(f)} funções enriquecidas + {len(h)} chamados; ≈ US$ "
              f"{precos.custo_embedding_usd(config.MODELO_EMBEDDING, car / 3.2):.3f}")
        g = _gera(c, "funcao_rica", "funcao_sistema_id", f, teto, 0.0)
        g = _gera(c, "chamado_emb", "chamado_id", h, teto, g)
        print(f"custo: US$ {g:.3f}")


# ------------------------------------------------------------------------------------------------------------------ avalia
def casos(c):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(APF, row_factory=dict_row) as a:
        nums = [r["ch"] for r in a.execute("SELECT DISTINCT chamado_numero ch FROM apf.gabarito_chamado WHERE momento = 'ALTERADA'")]
    func = {}
    for r in c.execute("""SELECT x.chamado_id, cf.funcao_sistema_id fid FROM apf.contagem x JOIN apf.contagem_funcao cf ON cf.contagem_id = x.id
                          WHERE x.tipo = 'DETALHADA' AND x.papel = 'FABRICA' AND cf.funcao_sistema_id IS NOT NULL AND cf.acao IN ('A','E')
                            AND x.id = (SELECT y.id FROM apf.contagem y JOIN apf.chamado z ON z.id = y.chamado_id WHERE y.chamado_id = x.chamado_id
                                        AND y.tipo = 'DETALHADA' AND y.papel = 'FABRICA'
                                        ORDER BY abs(coalesce(y.pf_liquido_calculado, y.pf_liquido_declarado, 0) - z.pf_detalhado_origem), y.id DESC LIMIT 1)"""):
        func.setdefault(r["chamado_id"], set()).add(r["fid"])
    rows = c.execute("""SELECT c.id, c.numero, c.sistema_id, c.dt_abertura::date ab, e.texto tx, e.emb::text v
                        FROM apf.chamado c JOIN exp.chamado_emb e ON e.chamado_id = c.id
                        WHERE c.origem = 'SOS' AND c.numero = ANY(%s) AND c.descricao_anon IS NOT NULL""", (nums,)).fetchall()
    out = []
    for r in rows:
        alvo = func.get(r["id"], set())
        if alvo:
            ok = {x["id"] for x in c.execute(
                """SELECT f.id FROM apf.funcao_sistema f JOIN apf.contagem x ON x.id = f.contagem_criacao_id
                   WHERE f.id = ANY(%(f)s) AND f.vigente_desde < %(a)s AND (f.vigente_ate IS NULL OR f.vigente_ate >= %(a)s)
                     AND x.chamado_id IS DISTINCT FROM %(i)s AND (x.dt_contagem IS NULL OR x.dt_contagem < %(a)s)""",
                dict(f=list(alvo), a=r["ab"], i=r["id"]))}
            if ok:
                out.append(dict(r, alvo=ok))
    return out


def vigentes(c, caso):
    """Funções candidatas no modo estrito, com as distâncias ao pedido: d0 (texto simples) e d1 (enriquecido, se permitido)."""
    rows = c.execute(
        """SELECT f.id, f.tipo_funcao, f.nome_canonico, f.modulo, e.texto simples, r.texto rico,
                  (x.dt_contagem IS NOT NULL AND x.dt_contagem < %(a)s) rico_ok,
                  e.embedding_3l <=> %(v)s::vector d0, r.emb <=> %(v)s::vector d1
           FROM apf.funcao_sistema f JOIN apf.funcao_sistema_emb e ON e.funcao_sistema_id = f.id
           JOIN exp.funcao_rica r ON r.funcao_sistema_id = f.id JOIN apf.contagem x ON x.id = f.contagem_criacao_id
           WHERE f.sistema_id = %(s)s AND f.vigente_desde < %(a)s AND (f.vigente_ate IS NULL OR f.vigente_ate >= %(a)s)
             AND x.chamado_id IS DISTINCT FROM %(i)s AND (x.dt_contagem IS NULL OR x.dt_contagem < %(a)s)""",
        dict(s=caso["sistema_id"], a=caso["ab"], v=caso["v"], i=caso["id"])).fetchall()
    out = {}
    for r in rows:
        r = dict(r)
        if not r["rico_ok"]:
            r["rico"], r["d1"] = r["simples"], r["d0"]
        out[r["id"]] = r
    return out


def vetor(vig, chave, k):
    return [f for f, _ in sorted(((f, r[chave]) for f, r in vig.items()), key=lambda x: x[1])[:k]]


def bm25(vig, consulta, k, k1=1.2, b=0.75):
    docs = {fid: tokens(r["rico"]) for fid, r in vig.items()}
    if not docs:
        return []
    df = collections.Counter(w for d in docs.values() for w in set(d))
    n, med = len(docs), sum(len(d) for d in docs.values()) / len(docs)
    q = set(tokens(consulta))
    pont = {}
    for fid, d in docs.items():
        tf = collections.Counter(d)
        s = sum(math.log(1 + (n - df[w] + .5) / (df[w] + .5)) * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / med))
                for w in q if w in tf)
        if s > 0:
            pont[fid] = s
    return [f for f, _ in sorted(pont.items(), key=lambda x: -x[1])[:k]]


def historico(c, caso, vig, k):
    viz = c.execute(
        """SELECT h.id, 1 - (e.emb <=> %(v)s::vector) cos FROM exp.chamado_emb e JOIN apf.chamado h ON h.id = e.chamado_id
           WHERE h.sistema_id = %(s)s AND h.id <> %(i)s AND h.dt_abertura::date < %(a)s
             AND EXISTS (SELECT 1 FROM apf.contagem x WHERE x.chamado_id = h.id AND x.tipo = 'DETALHADA' AND x.dt_contagem < %(a)s)
           ORDER BY e.emb <=> %(v)s::vector LIMIT %(n)s""", dict(v=caso["v"], s=caso["sistema_id"], i=caso["id"], a=caso["ab"], n=VIZINHOS)).fetchall()
    if not viz:
        return []
    cos = {r["id"]: float(r["cos"]) for r in viz}
    pont = collections.defaultdict(float)
    for r in c.execute("""SELECT DISTINCT x.chamado_id, cf.funcao_sistema_id fid FROM apf.contagem x JOIN apf.contagem_funcao cf ON cf.contagem_id = x.id
                          WHERE x.chamado_id = ANY(%s) AND x.tipo = 'DETALHADA' AND x.dt_contagem < %s AND cf.funcao_sistema_id IS NOT NULL""",
                       (list(cos), caso["ab"])):
        if r["fid"] in vig:
            pont[r["fid"]] += cos[r["chamado_id"]]
    return [f for f, _ in sorted(pont.items(), key=lambda x: -x[1])[:k]]


def rrf(*listas):
    s = collections.defaultdict(float)
    for li in listas:
        for i, f in enumerate(li, 1):
            s[f] += 1 / (RRF + i)
    return [f for f, _ in sorted(s.items(), key=lambda x: -x[1])]


FUSOES = {"F1 V0+L": ("V0", "L"), "F2 V1+L": ("V1", "L"), "F3 V1+L+H": ("V1", "L", "H"), "F4 V0+H": ("V0", "H"),
          "F5 V1+H": ("V1", "H"), "F6 V0+V1+L+H": ("V0", "V1", "L", "H")}


def listas_do_caso(c, caso):
    vig = vigentes(c, caso)
    L = dict(V0=vetor(vig, "d0", N), V1=vetor(vig, "d1", N), L=bm25(vig, caso["tx"], N), H=historico(c, caso, vig, N))
    for nome, partes in FUSOES.items():
        L[nome] = rrf(*(L[p] for p in partes))[:N]
    return L, vig


def metricas(lista, alvo):
    out = {f"@{k}": len(set(lista[:k]) & alvo) / len(alvo) for k in (5, 10, 20)}
    out["MRR"] = next((1 / i for i, x in enumerate(lista[:20], 1) if x in alvo), 0.0)
    return out


def tabela(res, ref="V0", titulo=""):
    from scipy.stats import wilcoxon
    linhas = [titulo, f"{'variante':16} {'@5':>7} {'@10':>7} {'@20':>7} {'MRR':>7}   @10 × {ref} (melhor / pior / empate, p)"]
    for nome in res[next(iter(res))]:
        m = {k: np.mean([r[nome][k] for r in res.values()]) * 100 for k in ("@5", "@10", "@20", "MRR")}
        a = np.array([r[ref]["@10"] for r in res.values()])
        b = np.array([r[nome]["@10"] for r in res.values()])
        cmp = f"{(b > a).sum()} / {(b < a).sum()} / {(b == a).sum()}" + (f", p={wilcoxon(a, b).pvalue:.3f}" if (a != b).any() else "")
        linhas.append(f"{nome:16} {m['@5']:6.1f}% {m['@10']:6.1f}% {m['@20']:6.1f}% {m['MRR']:6.1f}%   {'' if nome == ref else cmp}")
    return linhas


def avalia():
    with db.db() as c:
        cs = casos(c)
        res = {}
        for i, caso in enumerate(cs, 1):
            L, _ = listas_do_caso(c, caso)
            res[caso["numero"]] = {k: metricas(v, caso["alvo"]) for k, v in L.items()}
            if i % 50 == 0:
                print(f"  {i}/{len(cs)}", flush=True)
    out = tabela(res, titulo=f"Catálogo: {len(res)} chamados com gabarito de funções (alvo = funções existentes alteradas/excluídas)")
    out += ["", "V0 vetor atual | V1 vetor do texto enriquecido (AR/TR + TD da contagem de criação) | L palavra-chave (BM25) | "
            "H chamados anteriores parecidos → funções que alteraram | F* fusão RRF k=60"]
    print("\n".join(out))
    open(SAIDA, "w", encoding="utf-8").write("\n".join(out) + "\n")


# ------------------------------------------------------------------------------------------------------------------ reordena
PROMPT = """Você ajuda a abrir chamados de manutenção do sistema {sigla}. Abaixo está o pedido do usuário e uma lista de funcionalidades
EXISTENTES do sistema (funções de Ponto de Função: EE = entrada/gravação, CE = consulta, SE = relatório/saída calculada,
ALI/AIE = arquivo/cadastro lógico).

Escolha as até 10 funcionalidades que mais provavelmente precisarão ser corrigidas ou alteradas para atender o pedido, da mais
provável para a menos provável. Considere a tela/operação citada, os dados envolvidos e os arquivos lógicos que a operação grava.
Use só ids da lista.

PEDIDO:
{pedido}

FUNCIONALIDADES:
{lista}
"""
SCHEMA = {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}, "required": ["ids"],
          "additionalProperties": False}


def reordena(base, teto):
    if config.MODO_PII != "local":
        print("aviso: modo PII não é local; o texto já é anonimizado (resumo_anon/descricao_anon), seguindo")
    cache = json.load(open(CACHE_LLM, encoding="utf-8")) if os.path.exists(CACHE_LLM) else {}
    gasto = sum(v.get("custo", 0) or 0 for v in cache.values())
    with db.db() as c:
        cs = casos(c)
        sig = {r["id"]: r["sigla"] for r in c.execute("SELECT id, sigla FROM apf.sistema")}
        res = {}
        for i, caso in enumerate(cs, 1):
            L, vig = listas_do_caso(c, caso)
            cand = L[base][:N]
            ch = f"{base}|{caso['numero']}"
            if ch not in cache:
                if gasto >= teto:
                    print(f"teto de US$ {teto} atingido em {i - 1} chamados; rode de novo para continuar")
                    break
                lst = "\n".join(f"{f} | {vig[f]['rico'][:300]}" for f in cand if f in vig)
                try:
                    r, info = llm.json_estruturado(PROMPT.format(sigla=sig.get(caso["sistema_id"], ""), pedido=caso["tx"][:4000], lista=lst),
                                                   SCHEMA, nome="reordena", papel="chat", reasoning_effort="low")
                    cache[ch] = dict(ids=r["ids"], custo=info["custo_usd"])
                    gasto += info["custo_usd"] or 0
                except llm.LLMIndisponivel as e:
                    print(f"  {caso['numero']}: {e}")
                    cache[ch] = dict(ids=[], custo=0)
                json.dump(cache, open(CACHE_LLM, "w", encoding="utf-8"))
            vistos, nova = set(), []
            for f in cache[ch]["ids"] + cand:
                if f in set(cand) and f not in vistos:
                    vistos.add(f)
                    nova.append(f)
            res[caso["numero"]] = {k: metricas(L[k], caso["alvo"]) for k in ("V0", base)}
            res[caso["numero"]][f"LLM({base.split()[0]})"] = metricas(nova, caso["alvo"])
            if i % 25 == 0:
                print(f"  {i}/{len(cs)}  US$ {gasto:.3f}", flush=True)
    out = tabela(res, titulo=f"\nReordenação por {llm.modelo('chat')} das {N} primeiras de {base} ({len(res)} chamados; custo total US$ {gasto:.3f})")
    print("\n".join(out))
    open(SAIDA, "a", encoding="utf-8").write("\n".join(out) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("etapa", choices=["prepara", "avalia", "reordena"])
    ap.add_argument("--teto", type=float, default=1.5)
    ap.add_argument("--base", default="F3 V1+L+H")
    a = ap.parse_args()
    config.EMB = "azure"
    {"prepara": lambda: prepara(a.teto), "avalia": avalia, "reordena": lambda: reordena(a.base, a.teto)}[a.etapa]()
