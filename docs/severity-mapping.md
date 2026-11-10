# Severity mapping

Every finding carries a normalized `severity` from `services/contracts/common.schema.json#/$defs/severity`:
`critical | high | medium | low | info` (lowercase). Normalization: `services/scanner-worker/scanner/tools.py` →
`SEVERITY_MAP` / `map_severity()`; reports sort by `SEV_ORDER` (`scanner/report.py`). Implemented on
`feat/phase-2-scanner` (tests: `services/scanner-worker/tests/test_phase2_*.py`, `test_tools.py`).

| Tool | Native level | Normalized |
|---|---|---|
| semgrep | `CRITICAL` | `critical` |
| semgrep | `ERROR` / `HIGH` | `high` |
| semgrep | `WARNING` / `MEDIUM` | `medium` |
| semgrep | `INFO` / `NOTE` / `LOW` | `low` |
| semgrep | `INVENTORY` / `EXPERIMENT` / `UNKNOWN` | `info` |
| gitleaks | any (no native level) | `high` (always); `secret_redacted: true`, `snippet` has the secret masked (`********`) |
| trivy (vulns + misconfig) | `CRITICAL` / `HIGH` / `MEDIUM` / `LOW` | same, lowercase |
| trivy | `UNKNOWN` | `info` |
| any | unrecognised / missing | `info` + **warning logged** (`unknown <tool> severity ... -> info`) |
| AI (Deep, per-file review) | — | `info`, tool `ai`, rule `ai.file_review` |

semgrep runs with `--config p/default --metrics off --disable-version-check`; `finding.file` is repo-relative.

## AI-adjusted severity
At **Brief** (top 10 findings by severity) and **Deep** (every finding in a reviewed file) the AI fills, per
finding: `ai_explanation`, `ai_fix` (markdown), `confidence` (0–1) and `ai_severity` (same enum).
`severity` always stays the scanner's normalized value; `ai_severity` is the AI's opinion next to it.
The worker asks for a trailing JSON line `{"ai_severity","confidence","ai_fix"}` (`run.py` `parse_ai_json`).
If the answer has no parsable JSON: `ai_explanation` = `ai_fix` = the answer text, `confidence` = `0.5`,
`ai_severity` = the scanner `severity`. Gitleaks findings are sent to the AI with the masked snippet only.
AI calls retry 429/5xx up to 3 tries with backoff (honouring `Retry-After`, capped at 30 s).
