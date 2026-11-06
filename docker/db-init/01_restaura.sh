#!/bin/bash
# Roda uma única vez, na criação do volume: restaura o núcleo de /dados/sosia.dump (gerado por migracao/exporta_nucleo.py).
set -euo pipefail
DUMP=/dados/sosia.dump
if [ ! -f "$DUMP" ]; then
  echo "sos-ia: $DUMP não encontrado; o banco sobe vazio (rode migracao/exporta_nucleo.py --aplicar e recrie o volume)"
  touch "$PGDATA/.sosia_restaurado"
  exit 0
fi
echo "sos-ia: restaurando $DUMP ..."
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
pg_restore --no-owner --role="$POSTGRES_USER" -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error -j 2 "$DUMP" || {
  echo "sos-ia: pg_restore falhou"; exit 1; }
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER DATABASE $POSTGRES_DB SET search_path TO apf, public; ANALYZE;"
touch "$PGDATA/.sosia_restaurado"
echo "sos-ia: núcleo restaurado"
