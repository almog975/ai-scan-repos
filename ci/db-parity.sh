#!/usr/bin/env bash
# Postgres parity: schema from `alembic upgrade head` must equal infra/db/schema.sql (pg_dump --schema-only diff),
# plus a downgrade/upgrade round trip.
#
# Two modes:
#  - Sidecar / external server (Jenkins): set PGHOST (and optionally PGPORT, PGUSER, PGPASSWORD). The ai-scan-agent pod
#    template runs a postgres:16 sidecar on 127.0.0.1:5432 (deploy/jenkins/values.yaml). Needs psql + pg_dump >= server
#    major on PATH (ci/agent/Dockerfile installs postgresql-client-16). Two throwaway databases are created and dropped.
#  - Local fallback (no PGHOST): starts postgres:16-alpine with `docker run`.
# Usage: ci/db-parity.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SUF=$$_$RANDOM
A=parity_alembic_$SUF S=parity_sql_$SUF

if [ -n "${PGHOST:-}" ]; then
  export PGPORT=${PGPORT:-5432} PGUSER=${PGUSER:-postgres}
  for t in psql pg_dump pg_isready; do command -v $t >/dev/null || { echo "[db-parity] $t not found (postgresql-client)" >&2; exit 2; }; done
  echo "[db-parity] using server $PGHOST:$PGPORT"
  for _ in $(seq 60); do pg_isready -q && break; sleep 1; done
  pg_isready -q || { echo "[db-parity] $PGHOST:$PGPORT not ready" >&2; exit 2; }
  psql_() { psql -v ON_ERROR_STOP=1 -q "$@"; }
  pgdump_() { pg_dump "$@"; }
  trap 'psql -q -d postgres -c "DROP DATABASE IF EXISTS $A" -c "DROP DATABASE IF EXISTS $S" >/dev/null 2>&1 || true' EXIT
  # URL for alembic: password (if any) comes from PGPASSWORD via libpq, never from the URL.
  URL_BASE="postgresql+psycopg://${PGUSER}@${PGHOST}:${PGPORT}"
else
  command -v docker >/dev/null || { echo "[db-parity] set PGHOST (sidecar) or install docker" >&2; exit 2; }
  NAME=aiscan-parity-$$
  PW=$(openssl rand -hex 12)
  docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD="$PW" -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
  trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
  PORT=$(docker port "$NAME" 5432/tcp | head -1 | sed 's/.*://')
  for _ in $(seq 60); do docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
  psql_() { docker exec -i "$NAME" psql -v ON_ERROR_STOP=1 -qU postgres "$@"; }
  pgdump_() { docker exec "$NAME" pg_dump -U postgres "$@"; }
  URL_BASE="postgresql+psycopg://postgres:$PW@127.0.0.1:$PORT"
fi

psql_ -d postgres -c "CREATE DATABASE $A" -c "CREATE DATABASE $S"
(cd "$ROOT/review-api" && DATABASE_URL="$URL_BASE/$A" alembic upgrade head)
psql_ -d "$S" < "$ROOT/infra/db/schema.sql"
dump() { pgdump_ --schema-only --no-owner --no-privileges --exclude-table=alembic_version "$1" \
           | grep -vE '^(--|SET |SELECT pg_catalog|\\restrict|\\unrestrict)' | sed '/^$/d'; }
# Explicit if/exit: under `set -e` a failing command inside an && list does NOT abort the script.
if ! diff -u <(dump "$S") <(dump "$A"); then echo "[db-parity] FAIL: schema drift (alembic vs infra/db/schema.sql)" >&2; exit 1; fi
echo "db parity ok"
# Round trip: downgrade to 0001 and back must succeed on Postgres too.
if ! (cd "$ROOT/review-api" && export DATABASE_URL="$URL_BASE/$A" && alembic downgrade 0001 && alembic upgrade head); then
  echo "[db-parity] FAIL: migration round trip" >&2; exit 1
fi
echo "migration round trip ok"
# Gateway on real Postgres: OAuth login INSERT into users (role is enum user_role, sessions.ip is inet).
if ! (cd "$ROOT/gateway" && GATEWAY_PG_URL="$URL_BASE/$A" python -m pytest -q -p no:cacheprovider tests/test_db_pg_enum.py); then
  echo "[db-parity] FAIL: gateway insert on Postgres" >&2; exit 1
fi
echo "gateway postgres insert ok"
