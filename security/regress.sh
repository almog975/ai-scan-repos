#!/bin/bash
# clean-env regression for PR #10
R=/workspace/aiscan-pr14; cd $R
B=$HOME/.local/bin:/workspace/aiscan-venv-r2/bin:/workspace/pg16/client/usr/lib/postgresql/17/bin:/usr/local/bin:/usr/bin:/bin
C="env -i HOME=$HOME PATH=$B LD_LIBRARY_PATH=/workspace/pg16/client/usr/lib/x86_64-linux-gnu AISCAN_WORKER_PYTHON=/workspace/aiscan-venv-r2/bin/python"
s(){ printf '%s: ' "$1"; }
s contracts; (cd services/contracts && $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= tests 2>&1 | tail -1)
for c in gateway review-api scanner-worker; do s $c; (cd services/$c && $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= 2>&1 | tail -1); done
s phase3-recovery; (cd services/review-api && $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= tests/test_phase3_recovery.py tests/test_phase3_dod.py 2>&1 | tail -1)
s worker-recovery; (cd services/scanner-worker && $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= tests/test_recovery.py 2>&1 | tail -1)
s frontend; (cd services/frontend && $C npx vitest run 2>&1 | grep -E "Tests ")
s tsc; (cd services/frontend && $C npx tsc --noEmit >/dev/null 2>&1; echo rc=$?)
for i in 1 2; do s ci-clean-$i; $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= ci/tests 2>&1 | tail -1; done
s ci-env; $C GRAFANA_ADMIN_PASSWORD=S3CRET_g GITHUB_CLIENT_ID=cid GITHUB_CLIENT_SECRET=S3CRET_c POSTGRES_PASSWORD=S3CRET_p REGISTRY_PUSH_PASSWORD=S3CRET_r NODE_NAME=n KUBECONFIG=/nonexistent DATABASE_URL=sqlite:////tmp/x.db INTERNAL_API_TOKEN=zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz AWS_REGION=eu-west-1 python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= ci/tests 2>&1 | tail -1
s gateway-env; (cd services/gateway && $C GITHUB_CLIENT_ID=cid GITHUB_CLIENT_SECRET=x PUBLIC_URL=http://x python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= 2>&1 | tail -1)
s ci-parity-pg; $C PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= ci/tests/test_db_parity.py ci/tests/test_r2_regressions.py 2>&1 | tail -1
s schemathesis; $C bash ci/schemathesis.sh 25 > /workspace/verify-r2/p14/st.log 2>&1; echo "rc=$? $(grep -E 'passed|failed|errors' /workspace/verify-r2/p14/st.log | tail -2 | tr '\n' ' ')"
s kubeconform; $C bash ci/kubeconform.sh > /workspace/verify-r2/p14/kc.log 2>&1; echo "rc=$? $(tail -1 /workspace/verify-r2/p14/kc.log)"
s shellcheck; $C shellcheck $(git ls-files '*.sh') 2>&1 | grep -c "^In " ; s bashn; for f in $(git ls-files '*.sh'); do bash -n $f || echo BAD $f; done; echo ok
s e2e-mock; (cd services/frontend/e2e && $C python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= 2>&1 | tail -1)
s e2e-full; (cd services/frontend/e2e && $C AISCAN_FULLSTACK=1 python -m pytest -q -p no:cacheprovider -p no:warnings -o addopts= . 2>&1 | tail -1)
echo DONE
