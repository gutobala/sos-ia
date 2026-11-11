"""Sincroniza o RAG com a wiki (atalho para `python -m nucleo.wiki`, que é o que roda no container).

Uso (na raiz, com o SOSIA_DSN apontando para o banco do núcleo):
  python -m migracao.sincroniza_wiki [--aplicar] [--sistema gr] [--tudo]
"""
import sys

from nucleo.wiki.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
