# Severity mapping

Every finding carries a normalized `severity` from `contracts/common.schema.json#/$defs/severity`:
`critical | high | medium | low | info` (lowercase). Normalization is in
`scanner-worker/scanner/tools.py` → `_norm_sev()`; reports sort by `SEV_ORDER` (`scanner/report.py`).

This page describes the **decided** Phase 2 behavior. Where the code on `feat/phase-2-scanner` differs,
the row says **decided, implementation pending (Phase 2)**.

| Tool | Native level | Normalized (decided) | Code today | Source |
|---|---|---|---|---|
| semgrep | `ERROR` | `high` | `high` | `tools.py` `parse_semgrep` → `_norm_sev` |
| semgrep | `WARNING` | `medium` | `medium` | same |
| semgrep | `INFO` | **`low`** | `info` — **decided, implementation pending (Phase 2)** | same |
| gitleaks | any (no native level) | `high` (always) | `high`, secret never stored (`secret_redacted: true`) | `tools.py` `parse_gitleaks` |
| trivy (vulns + misconfig) | `CRITICAL` | `critical` | `critical` | `tools.py` `parse_trivy` → `_norm_sev` |
| trivy | `HIGH` | `high` | `high` | same |
| trivy | `MEDIUM` | `medium` | `medium` | same |
| trivy | `LOW` | `low` | `low` | same |
| trivy | `UNKNOWN` | `info` + **warning logged** | `info`, no warning — **decided, implementation pending (Phase 2)** | `_norm_sev` |
| any | unrecognised/missing | `info` + **warning logged** | `info`, no warning — **decided, implementation pending (Phase 2)** | `_norm_sev` fallback |
| AI (Deep, per-file review) | — | `info`, tool `ai`, rule `ai.file_review` | same | `scanner/run.py` `_ai_phase` |

## AI-adjusted severity (decided)
At **Brief and Deep** depth the AI fills, per finding: `ai_explanation`, `ai_fix`, `confidence` (0–1) and
`ai_severity`. `severity` stays the scanner's normalized value; `ai_severity` is the AI's opinion next to it,
using the same enum. Code today: only `ai_explanation` is set (`run.py` `_ai_phase`) — **decided, implementation pending (Phase 2)**.

## Disagreements
1. **`ai_severity`** is coming in the contract (owned by Coder) (`contracts/finding.schema.json`, alongside `snippet` and newly required fields);
   the worker must emit it once the contract lands.
2. semgrep INFO and the UNKNOWN/unrecognised warning: see table (code pending).
