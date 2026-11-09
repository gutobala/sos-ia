-- Catálogo de menus dos sistemas (contrato 1.5.0; pedido do SOS em docs/sosia-1.5.0-menu-e-documentacao.md).
-- Uma linha por item de menu, num formato único para todas as fontes. Carregada por migracao/carrega_menus.py
-- (que gera dados/menus.sql; o exporta_nucleo.py aplica os dois antes do pg_dump). Idempotente.
--   sistema   chave do sistema como na wiki: sigla minúscula, '_' no lugar de '-' (a mesma de motor.resolve_sistema)
--   id / pai  identificador estável do item dentro do sistema e da fonte (FORMS: "MENU.ITEM", o nome interno); pai nulo na raiz
--   caminho   "Pai > Filho > Item", sem a sigla do sistema
--   comando   form ou rotina que o item chama (ex.: gai217); só para a Sustentação, fora da API
--   paginas   páginas da wiki que citam o caminho (fonte wiki)
CREATE TABLE IF NOT EXISTS apf.menu_sistema (
    sistema      text NOT NULL,
    sistema_id   int,
    fonte        text NOT NULL CHECK (fonte IN ('forms', 'benner', 'hope', 'agendamento', 'wiki')),
    id           text NOT NULL,
    pai          text,
    nome         text NOT NULL,
    folha        boolean NOT NULL,
    caminho      text NOT NULL,
    detalhe      text,
    modulo       text,
    ordem        int NOT NULL,
    comando      text,
    paginas      text[] NOT NULL DEFAULT '{}',
    carregado_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sistema, fonte, id)
);
