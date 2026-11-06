"""Preços por 1 milhão de tokens (US$), modalidade Standard: (entrada, entrada em cache, saída).

Fonte: preços oficiais informados pelo analista em 05/11/2026 (gpt-5.4 e gpt-5.4-mini); demais da tabela da pesquisa
(PRE-PROJETO/app/llm.py). Modelo fora da tabela: custo None (não estimar sem preço). Na Azure, o preço do contrato pode diferir.
"""
PRECOS = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-4.1": (2.00, None, 8.00),
    "gpt-4.1-mini": (0.40, None, 1.60),
    "gpt-4o-mini": (0.15, None, 0.60),
    "gpt-4o": (2.50, None, 10.00),
}

# embeddings: US$ por 1 milhão de tokens de entrada (informado pelo analista em 05/11/2026, preços de nov/2026)
PRECOS_EMBEDDING = {
    "text-embedding-3-large": 0.13,
    "text-embedding-3-small": 0.02,
}


def custo_embedding_usd(modelo, tokens):
    p = PRECOS_EMBEDDING.get(modelo)
    return None if p is None else tokens * p / 1e6


def custo_usd(modelo, entrada, saida, entrada_cache=0):
    """Tokens de raciocínio já vêm contados na saída (cobrados como saída)."""
    p = PRECOS.get(modelo)
    if not p:
        return None
    ent, cache, sai = p
    cache = cache if cache is not None else ent
    return ((entrada - entrada_cache) * ent + entrada_cache * cache + saida * sai) / 1e6
