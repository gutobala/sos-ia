"""Completa os vetores text-embedding-3-large (coluna embedding_3l) da wiki e do catálogo que estiverem vazios. Incremental.

Quando rodar:
  - depois de recriar o `sosia` com exporta_nucleo.py (a exportação só leva os vetores do e5): ~US$ 1,20 para o corpus todo;
  - depois de qualquer ingestão nova da wiki ou do catálogo (os jobs da Fase B chamam nucleo.embeddings.preenche_3l).
Cria as colunas e os índices se faltarem (migracao/sql/02_embedding_3l.sql e 03_catalogo_rico.sql) e completa também o catálogo
enriquecido e os vetores dos chamados SOS (candidatos de funcionalidade; nucleo.rag.catalogo.preenche_rico, ~US$ 0,10 do zero).

Uso (na raiz, com a chave no .env):  .venv\\Scripts\\python migracao\\embeda_3large.py [--simula] [--teto 3.0]
"""
import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
from nucleo import config, db, embeddings, precos  # noqa: E402
from nucleo.rag import catalogo  # noqa: E402

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--simula", action="store_true", help="só mostra quanto falta e o custo estimado")
    ap.add_argument("--teto", type=float, default=3.0)
    a = ap.parse_args()
    with db.db() as c:
        embeddings.garante_3l(c)
        car = c.execute("""SELECT (SELECT count(*) FROM apf.conhecimento_trecho WHERE embedding_3l IS NULL) nw,
                                  (SELECT coalesce(sum(length(texto_indexado)), 0) FROM apf.conhecimento_trecho WHERE embedding_3l IS NULL) cw,
                                  (SELECT count(*) FROM apf.funcao_sistema_emb WHERE embedding_3l IS NULL) nc,
                                  (SELECT coalesce(sum(length(texto)), 0) FROM apf.funcao_sistema_emb WHERE embedding_3l IS NULL) cc""").fetchone()
        tok = (car["cw"] + car["cc"]) / 3.2          # medido na geração de 05/11/2026: ~3,2 caracteres por token
        print(f"faltam {car['nw']} trechos da wiki e {car['nc']} funções do catálogo ≈ {tok / 1e6:.2f} mi tokens "
              f"≈ US$ {precos.custo_embedding_usd(config.MODELO_EMBEDDING, tok):.2f}")
        if not a.simula and (car["nw"] or car["nc"]):
            print(f"custo real: US$ {embeddings.preenche_3l(c, a.teto):.3f}")
        if not a.simula:
            print(f"catálogo enriquecido e chamados: US$ {catalogo.preenche_rico(c, a.teto):.3f}")
