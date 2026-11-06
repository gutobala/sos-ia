"""Recalibração dos pesos da busca da wiki (nucleo/rag/busca.py) no gabarito objetivo: páginas que a fábrica ALTEROU por chamado.

Por quê (05/11/2026): os pesos de 27/10 (regras de negócio/mensagens = 0,2) foram calibrados em outro gabarito (relevância julgada por
LLM) e rebaixam justamente 28% das páginas alteradas. Pesos neutros já sobem o top-5 de 41% para 57%.

Como:
  1. candidatos de cada chamado (60 trechos do vetor 3-large + 60 da palavra-chave), uma vez só, na configuração da entrevista
     (sem plano de teste, só páginas criadas antes da abertura); cache em dados/calibra_wiki_cache.pkl;
  2. grade de parâmetros: peso da palavra-chave no RRF, peso de cada tipo de página, desconto de seção de tela;
  3. validação cruzada em 5 partes: escolhe a melhor configuração (MRR) em 4/5 e mede no 1/5 que ficou de fora, em rodízio;
  4. compara as previsões fora da amostra com os pesos neutros e com a produção (Wilcoxon pareado por chamado).
Regra: adota a configuração escolhida no conjunto todo só se ganhar dos neutros fora da amostra com p < 0,05; senão, neutros.

Uso (na raiz):  .venv\\Scripts\\python migracao\\calibra_wiki.py        Saída: dados\\calibra_wiki.txt
Custo: só os vetores das 556 consultas (centavos) na 1ª vez; sem LLM.
"""
import itertools
import os
import pickle
import sys

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "migracao"))
os.environ.setdefault("SOSIA_DSN", "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable")
import piloto_embeddings as P  # noqa: E402
from nucleo import config, db  # noqa: E402
from nucleo.rag import busca as B  # noqa: E402

CACHE = os.path.join(RAIZ, "dados", "calibra_wiki_cache.pkl")
SAIDA = os.path.join(RAIZ, "dados", "calibra_wiki.txt")
TIPOS = ["caso_de_uso", "user_story", "regras_de_negocio", "mensagens", "glossario", "eventos", "documento", "indice"]
N = 10


def coleta():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    P.TODOS = True
    config.EMB = "azure"
    gab = P._gabarito()
    casos = []
    with db.db() as c:
        ch = {r["numero"]: r for r in c.execute(
            """SELECT numero, dt_abertura::date ab, coalesce(resumo_anon,'') || E'\\n' || coalesce(descricao_anon,'') tx FROM apf.chamado
               WHERE origem = 'SOS' AND numero = ANY(%s) AND descricao_anon IS NOT NULL""", (list(gab),))}
        for i, n in enumerate(sorted(n for n in gab if n in ch), 1):
            g, r = gab[n], ch[n]
            cd = B._candidatos(c, r["tx"][:4000], g["sistema"], False, False, r["ab"])
            linhas = []
            for tid, x in cd["linhas"].items():
                base = B.PESO_BASE.get(x["tipo"])
                linhas.append(dict(pg=x["origem_dokuwiki"], tipo=x["tipo"], peso=float(x["peso"]),
                                   tela=base is not None and float(x["peso"]) < base * 0.99,
                                   rv=cd["rank_v"].get(tid), rt=cd["rank_t"].get(tid)))
            casos.append(dict(numero=n, paginas=g["paginas"], linhas=linhas))
            if i % 100 == 0:
                print(f"  {i}", flush=True)
    pickle.dump(casos, open(CACHE, "wb"))
    return casos


def prepara(casos):
    """Arrays por chamado para pontuar rápido."""
    out = []
    for k in casos:
        L = k["linhas"]
        if not L:
            out.append(None)
            continue
        out.append(dict(
            bv=np.array([1 / (B.RRF_K + x["rv"]) if x["rv"] else 0.0 for x in L]),
            bt=np.array([1 / (B.RRF_K + x["rt"]) if x["rt"] else 0.0 for x in L]),
            ti=np.array([TIPOS.index(x["tipo"]) if x["tipo"] in TIPOS else -1 for x in L]),
            p=np.array([x["peso"] for x in L]), tela=np.array([x["tela"] for x in L]),
            pg=np.array([x["pg"] for x in L], dtype=object), alvo=k["paginas"]))
    return out


def mede(a, cfg):
    """(rr, @1, @5, @10) de um chamado sob a configuração."""
    if a is None:
        return (0.0, 0.0, 0.0, 0.0)
    w = np.array([cfg["pesos"][t] for t in TIPOS])
    mult = np.where(a["ti"] >= 0, w[np.maximum(a["ti"], 0)], a["p"]) * np.where(a["tela"], cfg["fator_tela"], 1.0)
    s = (a["bv"] + cfg["peso_texto"] * a["bt"]) * mult
    ordem = a["pg"][np.argsort(-s, kind="stable")]
    pgs, vistos = [], set()
    for p in ordem:
        if p not in vistos:
            vistos.add(p)
            pgs.append(p)
            if len(pgs) == N:
                break
    alvo = a["alvo"]
    rr = next((1 / i for i, x in enumerate(pgs, 1) if x in alvo), 0.0)
    return (rr,) + tuple(len(set(pgs[:k]) & alvo) / len(alvo) for k in (1, 5, 10))


def grade():
    for pt, us, rn, mn, aux, doc, ft in itertools.product((0.0, 0.25, 0.5, 1.0), (0.8, 1.0), (0.4, 0.6, 0.8, 1.0, 1.2),
                                                          (0.4, 0.6, 0.8, 1.0, 1.2), (0.4, 1.0), (0.6, 1.0), (0.6, 1.0)):
        yield dict(peso_texto=pt, fator_tela=ft, pesos=dict(caso_de_uso=1.0, user_story=us, regras_de_negocio=rn, mensagens=mn,
                                                           glossario=aux, eventos=aux, documento=doc, indice=0.3))


def nome_cfg(c):
    p = c["pesos"]
    return (f"texto={c['peso_texto']} US={p['user_story']} RN={p['regras_de_negocio']} MN={p['mensagens']} "
            f"gloss/eventos={p['glossario']} doc={p['documento']} tela={c['fator_tela']}")


def main():
    from scipy.stats import wilcoxon
    casos = coleta()
    arr = prepara(casos)
    n = len(arr)
    cfgs = list(grade())
    print(f"{n} chamados, {len(cfgs)} configurações", flush=True)
    M = np.array([[mede(a, c) for a in arr] for c in cfgs])          # (config, chamado, métrica)
    neutro = dict(peso_texto=1.0, fator_tela=1.0, pesos={t: 1.0 for t in TIPOS})
    prod = dict(peso_texto=1.0, fator_tela=B.CFG["fator_tela"], pesos={t: B.CFG["pesos"].get(t, B.PESO_BASE[t]) for t in TIPOS})
    Mn = np.array([mede(a, neutro) for a in arr])
    Mp = np.array([mede(a, prod) for a in arr])
    rng = np.random.default_rng(7)
    dobra = rng.permutation(n) % 5
    fora = np.zeros((n, 4))
    escolhidas = []
    for f in range(5):
        tr, te = dobra != f, dobra == f
        best = int(np.argmax(M[:, tr, 0].mean(axis=1)))
        escolhidas.append(best)
        fora[te] = M[best, te]
    final = int(np.argmax(M[:, :, 0].mean(axis=1)))
    linhas = [f"Recalibração da busca da wiki: {n} chamados (gabarito = páginas alteradas pela fábrica), {len(cfgs)} configurações,",
              "configuração da entrevista (sem plano de teste, 1 trecho por página, top 10). Validação cruzada em 5 partes.", "",
              f"{'variante':42} {'@1':>6} {'@5':>6} {'@10':>6} {'MRR':>6}   MRR × neutros (melhor/pior/empate, p)"]

    def lin(nome, X, ref=None):
        m = X.mean(axis=0) * 100
        cmp = ""
        if ref is not None and (X[:, 0] != ref[:, 0]).any():
            cmp = f"{(X[:, 0] > ref[:, 0]).sum()} / {(X[:, 0] < ref[:, 0]).sum()} / {(X[:, 0] == ref[:, 0]).sum()}, p={wilcoxon(X[:, 0], ref[:, 0]).pvalue:.3f}"
        linhas.append(f"{nome:42} {m[1]:5.1f}% {m[2]:5.1f}% {m[3]:5.1f}% {m[0]:5.1f}%   {cmp}")
    lin("produção (pesos de 27/10)", Mp, Mn)
    lin("pesos neutros", Mn)
    lin("calibrada, FORA da amostra (honesta)", fora, Mn)
    lin("calibrada no conjunto todo (otimista)", M[final], Mn)
    p_fora = wilcoxon(fora[:, 0], Mn[:, 0]).pvalue if (fora[:, 0] != Mn[:, 0]).any() else 1.0
    adota = fora[:, 0].mean() > Mn[:, 0].mean() and p_fora < 0.05
    linhas += ["", "Escolhida em cada parte:"] + [f"  parte {i + 1}: {nome_cfg(cfgs[b])}" for i, b in enumerate(escolhidas)]
    linhas += ["", f"Escolhida no conjunto todo: {nome_cfg(cfgs[final])}",
               f"DECISÃO: {'ADOTAR a calibrada' if adota else 'ADOTAR os pesos NEUTROS (a calibração não ganhou deles fora da amostra)'}"]
    print("\n".join(linhas))
    open(SAIDA, "w", encoding="utf-8").write("\n".join(linhas) + "\n")
    return cfgs[final] if adota else neutro


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main())
