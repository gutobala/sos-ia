"""Paridade do núcleo com a pesquisa (passo 4 do plano), sem LLM.

1. Histórico: PF pelo histórico dos 83 chamados do benchmark com as listas de funções guardadas na pesquisa
   (PRE-PROJETO/testes/anexos_resultados.json [ORIG+ANEXOS] e previa_1x_resultados.json), com o código do núcleo:
     a) sobre a base de pesquisa `apf`  (o código portado reproduz a pesquisa?)  alvo: MAE 19,87 e Σ 0,411
     b) sobre o banco do núcleo `sosia` (a exportação preservou o que o estimador usa?)
2. RAG: top-5 da busca híbrida da wiki para consultas fixas — pesquisa (PRE-PROJETO, base apf) × núcleo (base sosia).

Uso (na raiz):  .venv\\Scripts\\python migracao\\confere_paridade.py
"""
import json
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
PESQ = r"D:\Projetos\PRE-PROJETO"
DSN = {"apf": "host=127.0.0.1 port=5433 dbname=apf user=apf password=apf_local sslmode=disable gssencmode=disable",
       "sosia": "host=127.0.0.1 port=5433 dbname=sosia user=apf password=apf_local sslmode=disable gssencmode=disable"}
MOV = {"INCL": "INCLUSAO", "ALTE": "ALTERACAO", "EXCL": "EXCLUSAO", "INDE": "INDETERMINADO", "NAO_": "NAO_MENSURAVEL",
       "SEM_": "SEM_ALTERACAO"}
CONSULTAS = [("GAI", "relatório de conferência de valores pagos da alienação com o Benner"),
             ("GRH", "reembolso do programa de línguas estrangeiras limite por acordo coletivo"),
             ("GOP", "controle de operação de venda direta com desconto por edital"),
             ("GLP", "edital de licitação devolução de caução"),
             ("GPE", "erro ao gerar a guia de pagamento do processo")]


def listas():
    T = os.path.join(PESQ, "testes")
    antigos = json.load(open(os.path.join(T, "anexos_resultados.json"), encoding="utf-8"))
    novos = json.load(open(os.path.join(T, "previa_1x_resultados.json"), encoding="utf-8"))
    out = {ch: v["ORIG+ANEXOS"]["funcoes"] for ch, v in antigos.items()}
    out.update({ch: v["funcoes"] for ch, v in novos.items() if ch not in out})
    conv = {}
    for ch, strs in out.items():
        fs = []
        for s in strs:
            t, m, n = s.split(" ", 2)
            fs.append(dict(tipo_ifpug=t, movimento=MOV.get(m, m), nome_funcao=n))
        conv[ch] = fs
    return conv


def historico(base, fs_por_chamado):
    os.environ["SOSIA_DSN"] = DSN[base]
    import importlib
    from nucleo import config
    importlib.reload(config)
    from nucleo import db
    importlib.reload(db)
    from nucleo.estimador import historico as hist
    import numpy as np
    with db.db() as c:
        H = hist.Historico(c)
        sel = {r["numero"]: r for r in c.execute(
            "SELECT numero, sistema_id, dt_abertura::date ab, pf_detalhado_origem::float pf FROM apf.chamado WHERE origem = 'SOS' "
            "AND numero = ANY(%s)", (list(fs_por_chamado),)).fetchall()}
        prev, acc = [], []
        for ch, fs in fs_por_chamado.items():
            r = sel[ch]
            prev.append(H.pf(fs, r["sistema_id"], r["ab"], excluir=ch)[0])
            acc.append(r["pf"])
    prev, acc = np.array(prev), np.array(acc)
    return dict(n=len(prev), mae=float(np.mean(np.abs(prev - acc))), soma=float(prev.sum() / acc.sum()),
                pred25=float(np.mean(np.abs(prev - acc) / acc <= 0.25)), por=dict(zip(fs_por_chamado, prev.round(2).tolist())))


def busca_pesquisa():
    cod = ("import sys,json; sys.path.insert(0,r'%s\\app'); sys.path.insert(0,r'%s\\importador'); import busca_docs as bd; "
           "from comum import conectar; from psycopg.rows import dict_row; c=conectar(); c.row_factory=dict_row; "
           "print(json.dumps([[r['id'] for r in bd.busca(c,q,s,n=5,expandir=False)['resultados']] for s,q in %r]))") % (PESQ, PESQ, CONSULTAS)
    r = subprocess.run([os.path.join(PESQ, ".venv", "Scripts", "python.exe"), "-c", cod], capture_output=True, text=True, encoding="utf-8")
    return json.loads(r.stdout.strip().splitlines()[-1])


def busca_nucleo():
    os.environ["SOSIA_DSN"] = DSN["sosia"]
    import importlib
    from nucleo import config, db
    importlib.reload(config)
    importlib.reload(db)
    from nucleo.rag import busca
    with db.db() as c:
        return [[r["id"] for r in busca.busca(c, q, s, n=5, expandir=False)["resultados"]] for s, q in CONSULTAS]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    fs = listas()
    print(f"listas de funções guardadas: {len(fs)} chamados")
    res = {}
    for base in ("apf", "sosia"):
        res[base] = historico(base, fs)
        r = res[base]
        print(f"histórico [{base:5}] n={r['n']}  MAE {r['mae']:.2f}  Σprévia/Σaceito {r['soma']:.3f}  PRED25 {r['pred25'] * 100:.1f}%"
              "   (pesquisa: MAE 19,87 · Σ 0,411)")
    dif = [(ch, res["apf"]["por"][ch], res["sosia"]["por"][ch]) for ch in fs if abs(res["apf"]["por"][ch] - res["sosia"]["por"][ch]) > 0.01]
    print(f"chamados com PF diferente entre apf e sosia: {len(dif)}" + (f"  ex.: {dif[:5]}" if dif else ""))
    p, n = busca_pesquisa(), busca_nucleo()
    print("\nRAG top-5 (pesquisa/apf × núcleo/sosia):")
    for (s, q), a, b in zip(CONSULTAS, p, n):
        print(f"  {s:4} {'IGUAL' if a == b else 'mesmos ids' if set(a) == set(b) else f'{len(set(a) & set(b))}/5 em comum'}  {q[:60]}")
