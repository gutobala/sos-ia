"""Revalida o holdout da dissertação (texto original × reescrito) com os modelos do núcleo (gpt-5.4-mini / gpt-5.4).

A pesquisa validou com gpt-4.1 (PRE-PROJETO/app/testa_reescrita.py, prompt congelado sha 5e86e97ae4149ec8): 6 chamados nunca usados
no ajuste, PF pelo histórico (com reserva da complexidade pelos campos). Mesmo procedimento aqui, com o código do núcleo e o banco
`sosia`: prévia (3 execuções, fica a mediana) → complexidade pelos campos (reserva) → PF pelo histórico, sem o próprio chamado e só
com contagens anteriores à abertura. Textos reescritos lidos de PRE-PROJETO/testes/reescrito_<n>.txt (anonimizados antes do envio).
Obs.: o reescrito do 3871 hoje é o v2 (o v1 foi sobrescrito); a referência gpt-4.1 dele é a da rodada v2.

Uso (na raiz):  .venv\\Scripts\\python migracao\\revalida_holdout.py [--papel chat|raciocinio] [--teto 1.5]
Saída: dados\\holdout_<modelo>.txt
"""
import argparse
import json
import os
import sys

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("SOSIA_DSN", "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable")
from nucleo import config, db, llm  # noqa: E402
from nucleo.estimador import campos, historico, previa  # noqa: E402

TESTES = r"D:\Projetos\PRE-PROJETO\testes"
# chamado: (PF aceito, PF pelo histórico com gpt-4.1 no ORIGINAL, no REESCRITO) — testes/holdout_*_saida.txt
REF_41 = {"2778": (12.3, 4.5, 14.5), "2507": (16.5, 9.0, 11.8), "3871": (41.0, 8.2, 27.0),
          "4681": (9.3, 4.5, 9.0), "3910": (64.0, 6.8, 64.2), "5327": (42.7, 3.0, 40.0)}


def roda(c, H, ch, texto, papel):
    pv, fs = previa.calcula(c, ch["sigla"], texto, data_ref=ch["ab"], excluir=[ch["numero"]], papel=papel)
    pfc, lin, cst = campos.estima(c, texto, fs, papel)
    reserva = {i: ("BAIXA", "MEDIA", "ALTA").index(x["cx"]) for i, x in enumerate(lin, 1) if x["cx"] != "NESMA"}
    pfh, linh, achou = H.pf(fs, ch["sistema_id"], ch["ab"], excluir=ch["numero"], reserva=reserva, texto=texto)
    return dict(previa=pv["pf"], campos=pfc, hist=pfh, n=pv["n_funcoes"], execucoes=pv["pf_execucoes"], achou=achou,
                funcoes=linh, custo=(pv["custo_usd"] or 0) + cst, modelo=pv["modelo"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--papel", default="chat", choices=["chat", "raciocinio", "previa"])
    ap.add_argument("--teto", type=float, default=1.5)
    a = ap.parse_args()
    saida = []

    def p(t=""):
        print(t, flush=True)
        saida.append(t)
    parcial = os.path.join(RAIZ, "dados", f"holdout_parcial_{a.papel}.json")
    res = json.load(open(parcial, encoding="utf-8")) if os.path.exists(parcial) else {}
    gasto = sum(r["custo"] for d in res.values() for r in d.values())
    with db.db() as c:
        H = historico.Historico(c)
        for n, (aceito, o41, r41) in REF_41.items():
            if len(res.get(n, {})) == 2:
                p(f"\nChamado {n}: já rodado (parcial) — histórico orig {res[n]['ORIGINAL']['hist']:.1f}, reesc {res[n]['REESCRITO']['hist']:.1f}")
                continue
            if gasto >= a.teto:
                p(f"parado no teto de US$ {a.teto:.2f}")
                break
            ch = c.execute("""SELECT c.numero, c.sistema_id, s.sigla, c.dt_abertura::date ab,
                                     coalesce(c.resumo_anon,'') || E'\\n' || coalesce(c.descricao_anon,'') tx
                              FROM apf.chamado c JOIN apf.sistema s ON s.id = c.sistema_id
                              WHERE c.origem = 'SOS' AND c.numero = %s""", (n,)).fetchone()
            reesc = open(os.path.join(TESTES, f"reescrito_{n}.txt"), encoding="utf-8-sig").read().strip()
            p(f"\nChamado {n} ({ch['sigla']}, aberto em {ch['ab']}), PF aceito {aceito}")
            res[n] = {}
            for cond, tx in (("ORIGINAL", ch["tx"].strip()), ("REESCRITO", reesc)):
                r = roda(c, H, ch, tx, a.papel)
                res[n][cond] = r
                gasto += r["custo"]
                p(f"  {cond:9} prévia {r['previa']:5.1f}  campos {r['campos']:5.1f}  histórico {r['hist']:5.1f}  "
                  f"({r['n']} funções, {r['achou']} no histórico; execuções {r['execucoes']})  US$ {r['custo']:.3f}")
                for x in r["funcoes"]:
                    p(f"       {x['tipo_ifpug']:3} {x['cx']:5} {x['pf']:5.2f}  {x['nome_funcao'][:70]}")
                r["funcoes"] = [{k: x[k] for k in ("tipo_ifpug", "movimento", "nome_funcao", "cx", "pf") if k in x} for x in r["funcoes"]]
            json.dump(res, open(parcial, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    modelo = next((r["modelo"] for d in res.values() for r in d.values()), llm.modelo(a.papel))
    ok = [n for n in res if len(res[n]) == 2]
    p(f"\nResumo — PF pelo histórico, {modelo} × gpt-4.1 (pesquisa), {len(ok)} chamados")
    p(f"{'chamado':8} {'aceito':>7} {'orig 4.1':>9} {'orig novo':>10} {'reesc 4.1':>10} {'reesc novo':>11}")
    acc = np.array([REF_41[n][0] for n in ok])
    cols = {"o41": [REF_41[n][1] for n in ok], "on": [res[n]["ORIGINAL"]["hist"] for n in ok],
            "r41": [REF_41[n][2] for n in ok], "rn": [res[n]["REESCRITO"]["hist"] for n in ok]}
    for i, n in enumerate(ok):
        p(f"{n:8} {acc[i]:7.1f} {cols['o41'][i]:9.1f} {cols['on'][i]:10.1f} {cols['r41'][i]:10.1f} {cols['rn'][i]:11.1f}")
    for nome, k in (("orig 4.1", "o41"), ("orig novo", "on"), ("reesc 4.1", "r41"), ("reesc novo", "rn")):
        v = np.array(cols[k])
        rel = np.abs(v - acc) / acc
        p(f"  {nome:10} Σ {v.sum() / acc.sum():.2f}  MAE {np.mean(np.abs(v - acc)):5.1f}  erro rel. mediano {np.median(rel) * 100:4.0f}%"
          f"  PRED25 {int((rel <= 0.25).sum())}/{len(v)}")
    try:
        from scipy.stats import wilcoxon
        on, rn = np.abs(np.array(cols["on"]) - acc), np.abs(np.array(cols["rn"]) - acc)
        p(f"  reescrito melhora em {int((rn < on).sum())}/{len(ok)} (Wilcoxon p={wilcoxon(on, rn).pvalue:.3f})")
    except Exception:  # noqa: BLE001
        pass
    p(f"\ncusto da API: US$ {gasto:.3f}")
    arq = os.path.join(RAIZ, "dados", f"holdout_{modelo}.txt")
    open(arq, "w", encoding="utf-8").write("\n".join(saida) + "\n")
    print(f"gravado em {arq}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
