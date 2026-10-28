#!/usr/bin/env bash
# Postgres parity: schema from `alembic upgrade head` must equal infra/db/schema.sql (pg_dump --schema-only diff).
# Needs docker. Usage: ci/db-parity.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME=aiscan-parity-$$
PW=$(openssl rand -hex 12)
docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD="$PW" -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
PORT=$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')
for _ in $(seq 60); do docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
psql_() { docker exec -i "$NAME" psql -v ON_ERROR_STOP=1 -qU postgres "$@"; }
psql_ -c "CREATE DATABASE via_alembic" -c "CREATE DATABASE via_sql"
(cd "$ROOT/review-api" && DATABASE_URL="postgresql+psycopg://postgres:$PW@127.0.0.1:$PORT/via_alembic" alembic upgrade head)
psql_ -d via_sql < "$ROOT/infra/db/schema.sql"
dump() { docker exec "$NAME" pg_dump -U postgres --schema-only --no-owner --no-privileges \
           --exclude-table=alembic_version "$1" | grep -vE '^(--|SET |SELECT pg_catalog|\\restrict|\\unrestrict)' | sed '/^$/d'; }
diff -u <(dump via_sql) <(dump via_alembic) && echo "db parity ok"
# Round trip: downgrade to 0001 and back must succeed on Postgres too.
(cd "$ROOT/review-api" && export DATABASE_URL="postgresql+psycopg://postgres:$PW@127.0.0.1:$PORT/via_alembic" \
   && alembic downgrade 0001 && alembic upgrade head) && echo "migration round trip ok"
