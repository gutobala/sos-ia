-- 11/11/2026, decisão do usuário: o alias SERVICOS ONLINE é do GSO (Gestão de Serviços Online), não do GAC-GEATE.
-- Veio do PRE-PROJETO (modelo_bd/07_ajustes_etapa2.sql), provavelmente porque a área gestora do GSO é a GEATE.
-- Só o alias muda: a contagem com esse nome na planilha continua no GAC-GEATE (a base de contagens v1.1 não muda).
-- Idempotente.
UPDATE apf.sistema_alias
   SET sistema_id = (SELECT id FROM apf.sistema WHERE sigla = 'GSO'), fonte = 'USUARIO'
 WHERE alias = 'SERVICOS ONLINE'
   AND sistema_id IS DISTINCT FROM (SELECT id FROM apf.sistema WHERE sigla = 'GSO');
