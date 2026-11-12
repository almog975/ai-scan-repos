# services/scanner-worker/

Part of the AI Scan Repos repo (`almog975/ai-scan-repos`). See the
[top-level README](../../README.md).

## Purpose
Scanner Job image: clone → Semgrep, Gitleaks, Trivy → AI (quick / brief = top 10 findings / deep) → `report.json` + `report.md`, with checkpoint/pause support.

## Contents
`scanner/` (run, tools, ai_client, report, checkpoint, api_client, config), `tests/` + fixtures, `Dockerfile`, `Makefile`.

## Build/Run
`python -m scanner.run --review-id <id>` (normally launched by review-api); `make test`.
