# contracts/

Part of the AI Scan Repos monorepo (`almog975/FinalProject` → `ai-scan-repos/`). See the
[top-level README](../README.md) and [ADR-0001](../docs/adr/0001-repo-layout.md).

## Purpose
**Source of truth** for APIs and data shapes. Code must follow these; the one adopted exception is Brief depth = top 10 findings.

## Contents
`gateway.openapi.yaml`, `review-api.openapi.yaml`, `finding.schema.json`, `report.schema.json`, `checkpoint.schema.json`. `progress.schema.json`.

## Build/Run
`make contracts` (from `ai-scan-repos/`): openapi-spec-validator + JSON Schema meta-validation.

## CI/CD
TODO: run `make contracts` + contract tests (schemathesis/jsonschema) on every PR.

## Config
N/A

## Owners
AI Scan Planner (A/R); Coder, Tester, UI Designer consulted. Changes only via PR.
