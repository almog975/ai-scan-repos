# Frontend e2e

Playwright (Python) against the real built SPA served by `mock_stack.py`, a contract-shaped
mock gateway (login cookies + CSRF, reviews, pause/resume/cancel/retry 409 rules, reports
incl. partial, SSE progress) with a test-only `/__mock/set/<id>` control API.
`test_nginx_headers.py` statically checks `services/frontend/nginx.conf` + Traefik `security-headers`.

    pip install -r requirements-e2e.txt && playwright install chromium
    cd services/frontend/e2e && python -m pytest -q

Strict xfail = UI/header feature not landed yet (SSE, partial report, cancel, retry, 409 error UI,
CSP style unsafe-inline, base-uri/form-action/object-src, Permissions-Policy, X-XSS-Protection).

Full stack (opt-in): `AISCAN_FULLSTACK=1 AISCAN_WORKER_PYTHON=$(which python) python -m pytest test_fullstack.py` runs the SPA under the
nginx.conf headers -> real gateway (fake GitHub OAuth) -> review-api (uvicorn+sqlite, LocalLauncher) -> worker with phase3 shims.
