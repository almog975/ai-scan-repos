# frontend/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../README.md) and [ADR-0001](../docs/adr/0001-repo-layout.md).

## Purpose
React+Vite SPA: new review (repo URL + depth), review list/progress, report view, AI key settings.

## Contents
`src/App.tsx`, `src/api.ts`, `src/components/{NewReviewForm,ReviewList,ReportView,AIKeySettings}.tsx`, `src/test/` (vitest), `nginx.conf`, `Dockerfile`, `Makefile`.

## Build/Run
`npm ci && npm run dev`; `make test` (vitest); `docker build -t ai-scan-repos/frontend frontend`; full stack: `docker compose up --build` → http://localhost:8080.

## CI/CD
TODO: Jenkins stage with path filter `frontend/**` (Test → Build → Scan → Push); image tag bump in `deploy/frontend/overlays/<env>`; Argo CD syncs.

## Config
Calls the gateway under `/api` (via nginx/Traefik). TODO: document build-time vars.

## Owners
AI Scan Coder (code), UI Designer (design), Tester (e2e TODO: Playwright).
