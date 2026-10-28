# report.md specification

`report.md` is the human-readable rendering of `report.json` (`contracts/report.schema.json`, `report/v1`).
Renderer: `scanner-worker/scanner/report.py` → `render_markdown()` (no separate template file). `write_report()` writes `report.md` first and
`report.json` last — the presence of `report.json` means the report is complete.

Status: everything below is implemented on `feat/phase-2-scanner` (✅), tests in `scanner-worker/tests/test_report_md.py`.
Contract fields `ai_severity`, `snippet`, `repo.private` and the required `provider` object are in `contracts/`.

## Section order

### 1. Title ✅
`# Security review: <repo.url>`

### 2. Header bullets ✅
| Shown | report.json field |
|---|---|
| Review id | `review_id` |
| depth | `depth` |
| commit (`-` if null) | `repo.commit_sha` |
| attempt `<budget_no>-<attempt>` | `budget_no`, `attempt` |
| finished | `finished_at` |
| AI provider name (`none` if null) | `provider.name` |
| model | `provider.model` |
| ref, started | `repo.ref`, `started_at` |
| visibility: `private`/`public` (omitted when null) | `repo.private` |

### 3. Cloud banner ✅
If `provider.kind == "cloud"` (the DB provider `kind`, passed as `AI_KIND`): a standalone blockquote line directly
under the title — `CLOUD_BANNER` = **`> ⚠️ cloud AI — code/findings were sent outside your network`**, or when
`repo.private` is true `CLOUD_BANNER_PRIVATE` = `> ⚠️ cloud AI — code/findings from this private repository were sent outside your network`
(wording kept in one constant in `scanner/report.py`). Private wording: **confirmed (Planner)** — already implemented in `render_markdown()` (`report.py` L46/L61), so no implementation is pending.
Grok (x.ai) runs as `openai_compatible` with `kind: cloud`, so it gets the cloud banner (see
[admin-ai-providers.md](runbooks/admin-ai-providers.md)).
On-prem: the provider bullet ends with `ONPREM_NOTE` = `(on-prem / no code left the network)`.

### 4. Summary ✅
`## Summary`, one table: `critical | high | medium | low | info | files scanned` ← `summary.{critical,high,medium,low,info,files_scanned}`
(counts include `tool: ai` file-review findings, which are always `info`). Then the executive summary
paragraph ← `executive_summary`, or `_AI step skipped or unavailable._` when null.

### 5. Tool versions ✅
`## Tool versions` table `tool | version` ← `tool_versions` (map, filled by `tools.tool_versions()` in `run.py`); omitted when empty.

### 6. Findings table ✅
`## Findings` (omitted when there are no scanner findings), excluding `tool == "ai"`, sorted by severity →
file → line → id (`sev_key`). Columns ← finding fields:

| Column | Field | Notes |
|---|---|---|
| Severity | `severity` | normalized, see [severity-mapping.md](severity-mapping.md) |
| Tool | `tool` | |
| Rule | `rule` | `|` escaped, newlines removed |
| Location | `file`:`line` | `:line` only if line set |
| Title | `title` | |
| Description | `description` | truncated to 300 chars |
| AI severity | `ai_severity` | rendered |
| Confidence | `confidence` | 0–1, shown as % |
| AI fix | `ai_fix` | `yes` when present; full text in section 7 |

Not rendered (spec-optional): `category`, `line_end`, `cwe`, `cve`, `package`, `fixed_version`.

### 7. Per-finding AI detail ✅
For each scanner finding with `ai_explanation`: `### <severity>: <title> (<file>)`, a line
`AI severity: <ai_severity> · confidence: <confidence %>`, `ai_explanation`, a fenced code block ← `snippet`
(≤ 10 lines; Gitleaks secrets masked `********`) and a `**Suggested fix**` block ← `ai_fix`. Filled at Brief and Deep.
Snippet code block next to `ai_fix`: **confirmed (Planner)** — already implemented in `render_markdown()` (`report.py` L90–95).

### 8. AI file reviews (Deep) ✅
For each `tool == "ai"` finding: `### AI review: <file>` then `description`.

## Known differences
1. Summary counts include AI file-review `info` findings, while the Findings table excludes them.
2. Gitleaks rows show no explicit redaction marker even though `secret_redacted: true` is set (snippet is masked).
