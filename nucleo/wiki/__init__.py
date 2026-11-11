"""Sincronização da wiki (DokuWiki) com o RAG do núcleo. Pedido: docs/pedido-sincronizacao-wiki.md.

  conversao.py   DokuWiki → Markdown do vault (em memória)
  trechos.py     notas → trechos, referências e acesso por sistema
  mascara.py     CPF, e-mail, telefone e nomes conhecidos, antes de gravar e de gerar o vetor
  cliente.py     API XML-RPC da wiki (só leitura)
  sincroniza.py  compara com o banco e grava só o que mudou (simula por padrão)
Uso:  python -m nucleo.wiki [--aplicar] [--sistema gr] [--tudo]
"""
