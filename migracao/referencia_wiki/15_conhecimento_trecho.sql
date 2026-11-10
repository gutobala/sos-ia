-- F1b: trechos da base de conhecimento (wiki; depois normas e contratos) com embedding local (pgvector) e
-- as referências entre eles (UC -> RN/FE/FA/MN/Tela). Fonte: vault Obsidian D:\Projetos\Terrawiki (ver
-- wiki\LEIAME_WIKI.md); gerado por wiki\indexa_wiki.py. Idempotente.
SET search_path TO apf, public;

CREATE TABLE IF NOT EXISTS conhecimento_trecho (
    id               serial PRIMARY KEY,
    fonte            text NOT NULL DEFAULT 'wiki' CHECK (fonte IN ('wiki', 'norma', 'contrato')),
    sistema          text NOT NULL,            -- slug da pasta do sistema no vault (gai, glpv2, ...)
    origem_dokuwiki  text NOT NULL,            -- identidade da página (id original da DokuWiki)
    arquivo_vault    text NOT NULL,            -- caminho relativo ao vault (informativo; a identidade é origem_dokuwiki)
    tipo             text NOT NULL,            -- caso_de_uso, user_story, plano_de_teste, regras_de_negocio, ...
    peso             numeric(4,2) NOT NULL,    -- peso do tipo no ranqueamento (frontmatter do vault)
    situacao         text,                     -- ATIVO / INATIVO
    categoria        text,
    titulo_pagina    text NOT NULL,
    secao            text NOT NULL,            -- caminho de títulos: "UC002 > FLUXO PRINCIPAL"
    ordem            int NOT NULL,             -- posição do trecho na página (0-based)
    definicoes       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"RN:1": "texto da RN", "MN:3": "linha da mensagem", "P:3": ...}
    refs             text[] NOT NULL DEFAULT '{}',        -- chaves citadas no trecho ("RN:1", "FE:1", "Tela:2")
    texto            text NOT NULL,            -- exibido ao usuário / enviado ao LLM
    texto_indexado   text NOT NULL,            -- cabeçalho de contexto + texto (é o que vira embedding e tsvector)
    n_caracteres     int NOT NULL,
    hash             text NOT NULL,            -- sha256 do texto_indexado: reindexa só o que mudou
    modelo_embedding text,
    embedding        vector(1024),
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', texto_indexado)) STORED,
    data_versao      timestamptz NOT NULL DEFAULT 'epoch',  -- versão histórica (wiki_sync / backtest); epoch = atual
    dt_criacao       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (origem_dokuwiki, secao, ordem, data_versao)
);
CREATE INDEX IF NOT EXISTS ix_conh_trecho_sistema ON conhecimento_trecho(sistema, tipo);
CREATE INDEX IF NOT EXISTS ix_conh_trecho_pagina ON conhecimento_trecho(origem_dokuwiki);
CREATE INDEX IF NOT EXISTS ix_conh_trecho_tsv ON conhecimento_trecho USING gin (tsv);
CREATE INDEX IF NOT EXISTS ix_conh_trecho_emb ON conhecimento_trecho USING hnsw (embedding vector_cosine_ops);

-- referência: o trecho de origem cita um código (RN01, FE01, ...); a resolução é feita pelo CÓDIGO, nunca pela
-- âncora do Obsidian. destino_trecho_id NULL = não resolvida (vai para o relatório de cobertura).
CREATE TABLE IF NOT EXISTS conhecimento_referencia (
    id                serial PRIMARY KEY,
    origem_trecho_id  int NOT NULL REFERENCES conhecimento_trecho(id) ON DELETE CASCADE,
    tipo_ref          text NOT NULL,           -- RN, FE, FA, MN, Tela, RF ...
    chave             text NOT NULL,           -- "RN:1"
    codigo            text NOT NULL,           -- como aparece no texto ("RN01")
    destino_trecho_id int REFERENCES conhecimento_trecho(id) ON DELETE CASCADE,
    texto_destino     text,                    -- trecho do destino que responde ao código (linha da MN, seção da RN...)
    resolvido         boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_conh_ref_origem ON conhecimento_referencia(origem_trecho_id);
CREATE INDEX IF NOT EXISTS ix_conh_ref_destino ON conhecimento_referencia(destino_trecho_id);

-- sistemas de onde a página é acessível: o próprio `sistema` e os ATIVOS/INATIVOS que apontam para a sua pasta em
-- _auxiliares (preenchido por indexa_wiki.py). A busca por sistema usa `sis = ANY(sistemas_acesso)`.
ALTER TABLE apf.conhecimento_trecho ADD COLUMN IF NOT EXISTS sistemas_acesso text[];
CREATE INDEX IF NOT EXISTS ix_conhecimento_trecho_acesso ON apf.conhecimento_trecho USING gin (sistemas_acesso);

-- data de criação da página na DokuWiki (campo "created" do .meta do dump). Serve para buscar "como a documentação era
-- na data do chamado", sem as páginas criadas depois dele (preenchido por wiki\datas_paginas.py).
ALTER TABLE apf.conhecimento_trecho ADD COLUMN IF NOT EXISTS dt_pagina_criada timestamptz;
