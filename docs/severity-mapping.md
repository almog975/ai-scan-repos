# Severity mapping

Every finding carries a normalized `severity` from `contracts/common.schema.json#/$defs/severity`:
`critical | high | medium | low | info` (lowercase). Normalization happens in
`scanner-worker/scanner/tools.py` → `_norm_sev()`: lowercase the value, map `error→high`, `warning→medium`,
`note→low`, `unknown→info`; anything else is kept if it's a valid severity, otherwise **`info`**.
Reports sort by `SEV_ORDER = [critical, high, medium, low, info]` (`scanner/report.py`).

| Tool | Native level | Normalized | Source | Matches plan? |
|---|---|---|---|---|
| semgrep | `ERROR` | `high` | `tools.py` `parse_semgrep` → `_norm_sev` | yes |
| semgrep | `WARNING` | `medium` | same | yes |
| semgrep | `INFO` | **`info`** | same (passes through) | **no — plan says INFO→low** |
| semgrep | other/missing | `info` | `_norm_sev` fallback | not specified |
| gitleaks | any (no native level) | `high` (hard-coded) | `tools.py` `parse_gitleaks` | yes; secret value never stored (`secret_redacted: true`) |
| trivy (vulns + misconfig) | `CRITICAL` | `critical` | `tools.py` `parse_trivy` → `_norm_sev` | yes |
| trivy | `HIGH` | `high` | same | yes |
| trivy | `MEDIUM` | `medium` | same | yes |
| trivy | `LOW` | `low` | same | yes |
| trivy | `UNKNOWN` | `info` | `_norm_sev` (`unknown→info`) | plan: "keeps its own levels" — UNKNOWN isn't in the contract enum, so `info` is the code's choice |
| (SARIF-style) | `NOTE` | `low` | `_norm_sev` | not used by current parsers |
| AI (Deep, per-file review) | — | `info` (fixed), tool `ai`, rule `ai.file_review` | `scanner/run.py` `_ai_phase` | plan has no AI findings severity |
| AI enrichment of a scanner finding | — | unchanged (only `ai_explanation` added) | `scanner/run.py` `_ai_phase` | yes |

## AI-adjusted severity
None. The AI never changes a severity; the contract's Finding has `ai_explanation`, `ai_fix`, `confidence`
but no AI severity field. TODO: `ai_fix` and `confidence` are not populated by the worker yet.

## Code vs contract/plan disagreements
1. **semgrep INFO**: code → `info`, plan → `low`. Decide and align (TODO).
2. **trivy UNKNOWN**: plan says Trivy keeps its own levels, but the contract enum has no `unknown`; code maps it to `info`.
3. Unknown/garbage levels silently become `info` (not surfaced as an error).
