# services/scanner-worker/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../../README.md) and [ADR-0001](../../docs/adr/0001-repo-layout.md).

## Purpose
Scanner Job image: clone → Semgrep, Gitleaks, Trivy → AI (quick / brief = top 10 findings / deep) → `report.json` + `report.md`, with checkpoint/pause support.

## Contents
`scanner/` (run, tools, ai_client, report, checkpoint, api_client, config), `tests/` + fixtures, `Dockerfile`, `Makefile`.

## Build/Run
`python -m scanner.run --review-id <id>` (normally launched by review-api); `make test`.

## CI/CD
TODO: Jenkins stage with path filter `services/scanner-worker/**` (Test → Build → Scan → Push); image tag bump in `infra/k8s/review-api/overlays/<env>`; Argo CD syncs. TODO: confirm where the worker image tag lives.

## Config
See [architecture.md › Configuration](../../docs/diagrams/architecture.md#configuration).

## Owners
AI Scan Coder; Tester (golden/chaos tests).
