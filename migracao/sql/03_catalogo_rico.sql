-- Candidatos de funcionalidade melhorados (decisão de 05/11/2026; experimento em dados\melhora_catalogo.txt, migracao\melhora_catalogo.py).
-- 1) texto enriquecido de cada função (nome + arquivos AR/TR + campos TD da contagem que a criou) e o vetor 3-large dele;
-- 2) vetor 3-large do texto anonimizado de cada chamado SOS, para achar chamados parecidos e as funções que eles alteraram.
-- Idempotente. Aplicada por nucleo.rag.catalogo.garante_rico (e por migracao\embeda_3large.py).
ALTER TABLE apf.funcao_sistema_emb ADD COLUMN IF NOT EXISTS texto_rico text;
ALTER TABLE apf.funcao_sistema_emb ADD COLUMN IF NOT EXISTS embedding_rico vector(1024);
CREATE TABLE IF NOT EXISTS apf.chamado_emb (
    chamado_id   int PRIMARY KEY REFERENCES apf.chamado(id) ON DELETE CASCADE,
    texto        text NOT NULL,
    embedding_3l vector(1024)
);
CREATE INDEX IF NOT EXISTS ix_chamado_emb_3l ON apf.chamado_emb USING hnsw (embedding_3l vector_cosine_ops);
