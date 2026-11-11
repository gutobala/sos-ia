-- Sincronização da wiki no produto (pedido docs/pedido-sincronizacao-wiki.md, 10/11/2026). Idempotente.
-- Aplicada por nucleo.wiki.sincroniza na 1ª execução com --aplicar.

-- Controle por página da wiki: data e tamanho que a API informou, e o texto DokuWiki MASCARADO (cache, para não baixar as ~4.900
-- páginas a cada execução; a conversão precisa de todas, porque o índice de uma página dá o nome de outra).
CREATE TABLE IF NOT EXISTS apf.wiki_pagina_sync (
    origem_dokuwiki  text PRIMARY KEY,            -- id da página na DokuWiki
    last_modified    timestamptz NOT NULL,        -- data da última alteração na wiki
    tamanho          int NOT NULL,                -- tamanho informado pela API (bytes)
    hash_texto       text NOT NULL,               -- sha256 do texto guardado (DokuWiki, mascarado)
    texto_wiki       text NOT NULL,               -- texto DokuWiki mascarado (CPF, e-mail, telefone e nomes conhecidos)
    dt_primeira_versao timestamptz,               -- versão mais antiga do histórico (só calculada para páginas novas no RAG)
    sincronizada_em  timestamptz NOT NULL DEFAULT now(),
    removida_em      timestamptz                  -- quando a página saiu da wiki, se saiu
);

-- Uma linha por execução (simulação ou aplicação), com o relatório.
CREATE TABLE IF NOT EXISTS apf.wiki_sync_execucao (
    id          serial PRIMARY KEY,
    inicio      timestamptz NOT NULL DEFAULT now(),
    fim         timestamptz,
    aplicado    boolean NOT NULL,
    ok          boolean,
    origem      text,                             -- manual | agenda
    resumo      jsonb,
    erro        text
);

-- Trechos que saíram do RAG (página alterada ou removida), sem os vetores: para dar para voltar atrás. A busca nunca lê esta
-- tabela (duas versões da mesma página na conhecimento_trecho repetiriam a página no rag()).
CREATE TABLE IF NOT EXISTS apf.wiki_trecho_arquivo (
    id              serial PRIMARY KEY,
    execucao_id     int REFERENCES apf.wiki_sync_execucao(id),
    motivo          text NOT NULL CHECK (motivo IN ('alterada', 'removida')),
    origem_dokuwiki text NOT NULL,
    linha           jsonb NOT NULL,               -- a linha de apf.conhecimento_trecho, menos embedding, embedding_3l e tsv
    arquivado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wiki_trecho_arquivo_pagina ON apf.wiki_trecho_arquivo (origem_dokuwiki);
