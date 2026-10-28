# Frontend e2e (Phase 4)

Playwright (Python) against the real built SPA served by `mock_stack.py`, a contract-shaped
mock gateway (login cookies + CSRF, reviews, pause/resume/cancel/retry 409 rules, reports
incl. partial, SSE progress) with a test-only `/__mock/set/<id>` control API.
`test_nginx_headers.py` statically checks `frontend/nginx.conf` + Traefik `security-headers`.

    pip install -r requirements-e2e.txt && playwright install chromium
    cd frontend/e2e && python -m pytest -q

Strict xfail = UI/header feature not landed yet (SSE, partial report, cancel, retry, 409 error UI,
CSP style unsafe-inline, base-uri/form-action/object-src, Permissions-Policy, X-XSS-Protection).
