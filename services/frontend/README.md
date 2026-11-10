# services/frontend/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../../README.md) and [ADR-0001](../../docs/adr/0001-repo-layout.md).

## Purpose
React + Vite SPA (TypeScript): log in with GitHub, start a review (repo URL + depth), follow its progress live,
read the report, and (admins) manage AI providers. Dark theme from the UI design guide v1; English (LTR) and
Hebrew (RTL).

## Structure
```
src/
  main.tsx, App.tsx      app shell: sidebar nav, language switch, user chip, document title / focus on route change
  router.tsx             tiny history router (login, home, progress, report, admin, not found)
  api/client.ts          typed client (openapi-fetch) on src/api/schema.d.ts, CSRF header, ApiError, CAN state matrix
  api/schema.d.ts        generated from services/contracts/review-api.openapi.yaml (do not edit)
  live.ts                SSE progress stream with polling fallback
  version.ts             build version shown in the footer
  views/                 one file per screen: Login, Home (new review + list), Progress, Report, Admin (AI providers)
  ui/index.tsx           shared pieces: Ltr/LoginTag (bidi isolates), fmtTime (Intl, local time), State/Sev/Kind
                         badges, ErrorBox, ActionButton (disabled reason), Confirm dialog, SVG icons
  i18n/                  en.ts (keys), he.ts (same keys, typed), index.tsx (provider, format, <html lang dir>)
  styles.css             tokens + components; logical properties only, no inline styles (CSP)
  test/                  vitest unit tests
e2e/                     Playwright + axe (pytest) against a mock gateway serving the built SPA
```

## Build/Run
`npm ci && npm run dev`; `make test` (vitest); `docker build -t ai-scan-repos/frontend frontend`; full stack: `docker compose up --build` → http://localhost:8080.

## CI/CD
TODO: Jenkins stage with path filter `services/frontend/**` (Test → Build → Scan → Push); image tag bump in `infra/k8s/frontend/overlays/<env>`; Argo CD syncs.

## Config
Calls the gateway under `/api` (via nginx/Traefik). TODO: document build-time vars.

## Owners
AI Scan Coder (code), UI Designer (design), Tester (e2e TODO: Playwright).
