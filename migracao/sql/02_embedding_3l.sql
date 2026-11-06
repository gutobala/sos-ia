-- Adoção do text-embedding-3-large no RAG e no catálogo (decisão de 05/11/2026; comparação em dados\avalia_todos.log).
-- Colunas paralelas às do e5 (que continuam: o histórico de complexidade e o universo usam o e5 calibrado).
-- Idempotente. Aplicada por nucleo.embeddings.garante_3l (e por migracao\embeda_3large.py).
ALTER TABLE apf.conhecimento_trecho ADD COLUMN IF NOT EXISTS embedding_3l vector(1024);
ALTER TABLE apf.funcao_sistema_emb  ADD COLUMN IF NOT EXISTS embedding_3l vector(1024);
-- função nova cadastrada num servidor sem o e5 (SOSIA_EMB=azure) só tem o vetor 3-large
ALTER TABLE apf.funcao_sistema_emb  ALTER COLUMN embedding DROP NOT NULL;
CREATE INDEX IF NOT EXISTS ix_conh_trecho_emb3l    ON apf.conhecimento_trecho USING hnsw (embedding_3l vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_funcao_sistema_emb3l ON apf.funcao_sistema_emb  USING hnsw (embedding_3l vector_cosine_ops);
