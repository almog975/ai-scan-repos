#!/usr/bin/env bash
# Contract fuzzing: runs review-api (sqlite, no-op worker) and checks it against services/contracts/review-api.openapi.yaml.
# Undocumented 5xx fail status_code_conformance; documented ones (e.g. 502 on provider test) are allowed.
# All modes (positive + negative): every operation that accepts input documents 400 (Error schema).
# Usage: ci/schemathesis.sh [max-examples]   (needs: pip install -r services/review-api/requirements.txt schemathesis)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=${PORT:-18001}
export DATABASE_URL="sqlite:///$WORK/st.db" REPORTS_DIR="$WORK/reports" JOB_LAUNCHER=local LOCAL_WORKER_CMD=true \
       WATCHDOG_ENABLED=false INTERNAL_API_TOKEN="st-$(openssl rand -hex 12)" \
       AI_KEYS_ENCRYPTION_KEY="$(openssl rand -hex 32)"
cd "$ROOT/review-api"
alembic upgrade head >/dev/null
USER_ID=$(python3 -c "
from app import db as d; from app.config import get_settings; from app.auth import ensure_user
d.init_engine(get_settings().database_url)
with d.session_factory()() as s:
    u = ensure_user(s, 4242, 'schemathesis'); u.role = 'admin'; s.commit(); print(u.id)")
uvicorn --factory app.main:get_app --port "$PORT" --log-level warning &
PID=$!; trap 'kill $PID 2>/dev/null; rm -rf "$WORK"' EXIT
for _ in $(seq 50); do curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 0.2; done
schemathesis run "$ROOT/services/contracts/review-api.openapi.yaml" --url "http://127.0.0.1:$PORT" \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" -H "X-User-Id: $USER_ID" \
  --checks status_code_conformance,content_type_conformance,response_schema_conformance \
  --mode all --exclude-path-regex '/progress$' --max-examples "${1:-25}" --workers 1
