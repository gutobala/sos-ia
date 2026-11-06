"""Recalibra o casamento por HISTÓRICO do estimador (nucleo/estimador/historico.py) no text-embedding-3-large (06/11/2026).

Hoje o histórico usa o e5 local com cosseno >= 0,95 (+ mesmo verbo e mesmo objeto). Aqui se procura o corte equivalente no 3-large,
para o núcleo ficar sem modelo local.

Etapas:
  embeda   vetores 3-large (dim 1024) do universo de nomes (dados/cache_universo_3l.pkl) e dos nomes de consulta dos dois conjuntos
           (dados/cache_consultas_3l.pkl); ~200 mil tokens ≈ US$ 0,03. Nomes de consulta no e5 são gerados localmente.
  avalia   A) funções de ALTERAÇÃO/EXCLUSÃO das contagens DETALHADA de chamados SOS (fora os 83 do conjunto B): busca no histórico
              anterior à abertura, sem o próprio chamado; mede cobertura (achou) e acerto da complexidade (mesma BAIXA/MÉDIA/ALTA da
              contagem real). Escolha do corte: o MENOR corte do 3-large com acerto >= o do e5 a 0,95 (mesma precisão, mais cobertura).
           B) os 83 chamados da paridade (listas de funções guardadas da pesquisa): PF pelo histórico × PF aceito (MAE, Σ, PRED25),
              e5 0,95 × 3-large no corte escolhido; Wilcoxon pareado nos erros absolutos. Saída: dados/calibra_historico.txt

Uso (na raiz):  .venv\\Scripts\\python migracao\\calibra_historico.py embeda|avalia
"""
import argparse
import os
import pickle
import sys

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "migracao"))

from nucleo import db, embeddings, precos, config  # noqa: E402
from nucleo.estimador import historico as hist  # noqa: E402
from nucleo.estimador import regras as cc  # noqa: E402
from nucleo.estimador import universo as uf  # noqa: E402

U3 = os.path.join(RAIZ, "dados", "cache_universo_3l.pkl")
Q3 = os.path.join(RAIZ, "dados", "cache_consultas_3l.pkl")
QE5 = os.path.join(RAIZ, "dados", "cache_consultas_e5.pkl")
SAIDA = os.path.join(RAIZ, "dados", "calibra_historico.txt")
CORTES = [round(x, 2) for x in np.arange(0.60, 0.981, 0.01)]


def texto(tipo, nome):
    return f"{tipo} {nome.lower()}"


def nb(tipo, nome):
    return hist.nome_base(hist.nome_dados(nome) if tipo in ("ALI", "AIE") else nome)


def conjunto_b():
    import confere_paridade as cp
    return cp.listas()


def conjunto_a(c, excluir):
    rows = c.execute(
        """SELECT c.numero, c.sistema_id, c.dt_abertura::date ab, cf.tipo_funcao tipo, cf.nome_funcao nome, cf.complexidade cx,
                  cf.qtd_td td, cf.qtd_ar_tr ar
           FROM apf.contagem_funcao cf JOIN apf.contagem ct ON ct.id = cf.contagem_id JOIN apf.chamado c ON c.id = ct.chamado_id
           WHERE ct.tipo = 'DETALHADA' AND c.origem = 'SOS' AND c.dt_abertura IS NOT NULL AND upper(cf.acao) IN ('A', 'E', 'A75')
             AND cf.tipo_funcao = ANY(%s) AND NOT (c.numero = ANY(%s)) ORDER BY c.numero, cf.id""",
        (list(uf.TIPOS), list(map(str, excluir)))).fetchall()
    out = []
    for r in rows:
        k = hist.CX.get(r["cx"])
        if k is None:
            if not (r["td"] and 1 <= r["td"] <= 98 and r["ar"] is not None):
                continue
            k = cc.complexidade(r["tipo"], r["td"], r["ar"])
        out.append(dict(r, k=k))
    return out


def chaves_consulta(c, B):
    ks = {(f["tipo_ifpug"], uf.norm(nb(f["tipo_ifpug"], f["nome_funcao"]))) for fs in B.values() for f in fs if f["tipo_ifpug"] in uf.TIPOS}
    ks |= {(a["tipo"], uf.norm(nb(a["tipo"], a["nome"]))) for a in conjunto_a(c, list(B))}
    return sorted(ks)


def _api(textos, teto=0.5):
    out, gasto = [], 0.0
    for i in range(0, len(textos), 512):
        m, tok = embeddings.api_textos(textos[i:i + 512])
        out.append(m)
        gasto += precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok) or 0
        print(f"  {i + len(m)}/{len(textos)}  US$ {gasto:.4f}", flush=True)
        if gasto > teto:
            raise SystemExit("teto de custo atingido")
    return np.vstack(out), gasto


def embeda():
    with db.db() as c:
        oc, e5 = uf.carrega(c)
        B = conjunto_b()
        qs = chaves_consulta(c, B)
    u3 = pickle.load(open(U3, "rb")) if os.path.exists(U3) else {}
    falta = [k for k in e5 if k not in u3]
    print(f"universo: {len(e5)} nomes, faltam {len(falta)} no 3-large")
    total = 0.0
    if falta:
        m, g = _api([texto(t, n) for _, t, n in falta])
        total += g
        u3.update({k: v for k, v in zip(falta, m)})
        pickle.dump(u3, open(U3, "wb"))
    q3 = pickle.load(open(Q3, "rb")) if os.path.exists(Q3) else {}
    falta = [k for k in qs if k not in q3]
    print(f"consultas: {len(qs)}, faltam {len(falta)} no 3-large")
    if falta:
        m, g = _api([texto(t, n) for t, n in falta])
        total += g
        q3.update({k: v for k, v in zip(falta, m)})
        pickle.dump(q3, open(Q3, "wb"))
    qe = pickle.load(open(QE5, "rb")) if os.path.exists(QE5) else {}
    falta = [k for k in qs if k not in qe]
    if falta:
        print(f"consultas no e5 (local): {len(falta)}")
        m = embeddings._e5().encode([f"passage: {texto(t, n)}" for t, n in falta], normalize_embeddings=True, batch_size=128,
                                    show_progress_bar=False)
        qe.update({k: v.astype(np.float32) for k, v in zip(falta, m)})
        pickle.dump(qe, open(QE5, "wb"))
    print(f"custo total: US$ {total:.4f}")


def historico(c, modelo):
    """Historico com os vetores do modelo pedido (universo e consultas em cache, sem gerar nada)."""
    univ = pickle.load(open(U3 if modelo == "3l" else uf.CACHE, "rb"))
    qc = pickle.load(open(Q3 if modelo == "3l" else QE5, "rb"))
    orig = uf.carrega
    uf.carrega = lambda conn: (None, univ)
    try:
        H = hist.Historico(c)
    finally:
        uf.carrega = orig
    H._vq = lambda tipo, nome: qc[(tipo, uf.norm(nome))]
    return H


def mede_a(H, A, corte):
    hist.LIMIAR = corte
    achou = certo = 0
    for a in A:
        h = H.busca(a["sistema_id"], a["ab"], a["numero"], a["tipo"], a["nome"])
        if h:
            achou += 1
            r = h[0]
            k = hist.CX.get(r["cx"], cc.complexidade(a["tipo"], r["td"], r["ar"]))
            certo += k == a["k"]
    return achou, certo


def mede_b(H, c, B, corte):
    hist.LIMIAR = corte
    sel = {r["numero"]: r for r in c.execute(
        "SELECT numero, sistema_id, dt_abertura::date ab, pf_detalhado_origem::float pf FROM apf.chamado WHERE origem = 'SOS' "
        "AND numero = ANY(%s)", (list(B),)).fetchall()}
    prev, acc, ach, n = [], [], 0, 0
    for ch, fs in B.items():
        r = sel[ch]
        pf, linhas, a = H.pf(fs, r["sistema_id"], r["ab"], excluir=ch)
        prev.append(pf)
        acc.append(r["pf"])
        ach += a
        n += sum(1 for x in linhas if x["movimento"] != "INCLUSAO")
    prev, acc = np.array(prev), np.array(acc)
    err = np.abs(prev - acc)
    return dict(mae=float(err.mean()), soma=float(prev.sum() / acc.sum()), pred25=float(np.mean(err / acc <= 0.25)), err=err,
                achou=ach, n=n)


def avalia():
    from scipy.stats import wilcoxon
    sys.stdout.reconfigure(encoding="utf-8")
    B = conjunto_b()
    L = []
    pr = lambda s="": (print(s, flush=True), L.append(s))  # noqa: E731
    with db.db() as c:
        A = conjunto_a(c, list(B))
        He5, H3 = historico(c, "e5"), historico(c, "3l")
        pr(f"Recalibração do histórico do estimador no 3-large. Conjunto A: {len(A)} funções de alteração/exclusão "
           f"(contagens DETALHADA do SOS, sem os {len(B)} chamados do B). Conjunto B: {len(B)} chamados da paridade.\n")
        ae, ce = mede_a(He5, A, 0.95)
        pr(f"A  e5 0,95 (produção):  achou {ae} ({ae / len(A):.1%})  complexidade certa {ce}/{ae} ({ce / ae:.1%})  "
           f"certas no total {ce / len(A):.1%}")
        for t in (0.93, 0.97):
            a, cc_ = mede_a(He5, A, t)
            pr(f"   e5 {t:.2f}:              achou {a} ({a / len(A):.1%})  certa {cc_ / max(a, 1):.1%}")
        pr("\nA  3-large por corte:")
        pr("corte  achou   cobertura  acerto  certas/total")
        res = {}
        for t in CORTES:
            a, k = mede_a(H3, A, t)
            res[t] = (a, k)
            pr(f"{t:.2f}  {a:6d}   {a / len(A):6.1%}   {k / max(a, 1):6.1%}   {k / len(A):6.1%}")
        prec_e5 = ce / ae
        ok = [t for t in CORTES if res[t][0] and res[t][1] / res[t][0] >= prec_e5]
        escolhido = min(ok) if ok else max(CORTES)
        a, k = res[escolhido]
        pr(f"\nCorte escolhido no 3-large: {escolhido:.2f} (menor corte com acerto >= {prec_e5:.1%} do e5): "
           f"achou {a / len(A):.1%} × {ae / len(A):.1%} no e5, acerto {k / a:.1%}")
        be5 = mede_b(He5, c, B, 0.95)
        pr("\nB  83 chamados, PF pelo histórico × PF aceito:")
        pr(f"   e5 0,95        MAE {be5['mae']:.2f}  Σ {be5['soma']:.3f}  PRED25 {be5['pred25']:.1%}  achadas {be5['achou']}/{be5['n']}")
        for t in sorted({escolhido, round(escolhido - 0.02, 2), round(escolhido + 0.02, 2)}):
            b = mede_b(H3, c, B, t)
            d = be5["err"] - b["err"]
            p = wilcoxon(be5["err"], b["err"]).pvalue if np.any(d != 0) else 1.0
            pr(f"   3-large {t:.2f}   MAE {b['mae']:.2f}  Σ {b['soma']:.3f}  PRED25 {b['pred25']:.1%}  achadas {b['achou']}/{b['n']}"
               f"   melhor/pior/igual {int((d > 1e-9).sum())}/{int((d < -1e-9).sum())}/{int((np.abs(d) <= 1e-9).sum())}  p={p:.3f}"
               + ("   <- escolhido" if t == escolhido else ""))
    open(SAIDA, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n-> {SAIDA}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("etapa", choices=["embeda", "avalia"])
    a = ap.parse_args()
    {"embeda": embeda, "avalia": avalia}[a.etapa]()
