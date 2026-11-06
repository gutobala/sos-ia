"""Gera dados/nomes_anonimizacao.txt (nomes de pessoas conhecidos, um por linha) a partir da base de pesquisa `apf`, para a camada
de nomes da anonimização local (nucleo.pii). O arquivo é DADO PESSOAL: fica só na máquina, fora do git (dados/) e do dump.
Mesmas fontes e normalização de PRE-PROJETO/importador/anonimiza.carrega_nomes.

Uso (na raiz):  .venv\\Scripts\\python migracao\\exporta_nomes.py
"""
import os
import re
import sys

import psycopg

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DSN = os.environ.get("APF_DSN", "host=127.0.0.1 port=5433 dbname=apf user=apf password=apf_local sslmode=disable gssencmode=disable")
FONTES = [("contagem", "responsavel_nome"), ("contagem", "revisor_nome"), ("contagem", "validador_nome"),
          ("contagem_vinculo", "revisado_por"), ("contagem_correcao", "autor"),
          ("chamado_tratamento_divergencia", "autor"), ("profissional", "nome")]


def normaliza(v):
    v = re.sub(r",?\s*CFPS.*$", "", v, flags=re.I)
    v = re.sub(r"\(.*?\)", "", v, flags=re.S)
    v = re.sub(r",?\s*ID:?\s*\d+", "", v, flags=re.I)
    v = re.sub(r"[()]", "", v)
    return re.sub(r"\s+", " ", v).strip()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    nomes = set()
    with psycopg.connect(DSN) as c:
        for t, col in FONTES:
            for (v,) in c.execute(f"SELECT DISTINCT {col} FROM apf.{t} WHERE {col} IS NOT NULL"):
                for parte in normaliza(v).split("/"):
                    parte = parte.strip()
                    if " " in parte and len(parte) >= 5:
                        nomes.add(parte)
    os.makedirs(os.path.join(RAIZ, "dados"), exist_ok=True)
    arq = os.path.join(RAIZ, "dados", "nomes_anonimizacao.txt")
    with open(arq, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(nomes)) + "\n")
    print(f"{len(nomes)} nomes em {arq} (dado pessoal: não versionar, não distribuir)")
