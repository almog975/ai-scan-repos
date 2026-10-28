# report.md specification

`report.md` is the human-readable rendering of `report.json` (`contracts/report.schema.json`, `report/v1`).
Renderer: `scanner-worker/scanner/report.py` → `render_markdown()` (identical on `feat/phase-1-backend` and
`feat/phase-2-scanner`; there is no separate template file). `write_report()` writes `report.md` first and
`report.json` last — the presence of `report.json` means the report is complete.

Status markers: ✅ = renderer does it today; ⏳ = **decided, implementation pending (Phase 2)**; 🟢 = **confirmed (Planner), implementation pending in `render_markdown()`**.
Contract fields marked *coming in the contract (owned by Coder)* are being added to `contracts/` by Coder (`ai_severity`, `snippet`, `repo.private`, newly required fields); this doc does not edit `contracts/`.

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
| model 🟢 — **confirmed (Planner), implementation pending in `render_markdown()`** | `provider.model` |
| ref, started 🟢 — **confirmed (Planner), implementation pending in `render_markdown()`** | `repo.ref`, `started_at` |
| visibility: `private`/`public` ⏳ | `repo.private` (coming in the contract (owned by Coder)) |

### 3. Cloud banner ✅ (source of `kind` ⏳)
On the provider line: if `provider.kind == "cloud"` →
**`⚠️ cloud AI — code/findings were sent outside your network`**; otherwise `(on-prem / no code left the network)`.
Decided: `provider.kind` must be the DB provider `kind`; the worker currently derives it from the adapter name
(`run.py`) — ⏳. Banner on its own blockquote line directly under the title — **confirmed (Planner), implementation pending in `render_markdown()`** (renderer still appends it
to the provider bullet). When `repo.private` (coming in the contract (owned by Coder)) is true and `kind` is cloud, the banner should say the
private repository's code was sent out ⏳.

### 4. Summary ✅
`## Summary`, one table: `critical | high | medium | low | info | files scanned` ← `summary.{critical,high,medium,low,info,files_scanned}`
(counts include `tool: ai` file-review findings, which are always `info`). Then the executive summary
paragraph ← `executive_summary`, or `_AI step skipped or unavailable._` when null.

### 5. Tool versions 🟢
**confirmed (Planner), implementation pending in `render_markdown()`**. `## Tool versions` table `tool | version` ← `tool_versions` (map). The renderer has no such section, and
`run.py` never passes `tool_versions` to `build_report()`, so it is absent from report.json too.

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
| AI severity ⏳ | `ai_severity` | coming in the contract (owned by Coder); not rendered yet |
| Confidence ⏳ | `confidence` | spec column (0–1, show as %); not rendered |

Not rendered (spec-optional): `category`, `line_end`, `cwe`, `cve`, `package`, `fixed_version`.

### 7. Per-finding AI detail ✅ / ⏳
For each scanner finding with `ai_explanation`: `### <severity>: <title> (<file>)` then `ai_explanation` ✅.
Spec additions ⏳: a line `AI severity: <ai_severity> · confidence: <confidence>` and a `**Suggested fix**`
block ← `ai_fix`, and a fenced code block ← `snippet` (coming in the contract (owned by Coder)) ⏳. Decided: at Brief and Deep the AI fills `ai_fix`, `confidence`, `ai_severity` — ⏳.

### 8. AI file reviews (Deep) ✅
For each `tool == "ai"` finding: `### AI review: <file>` then `description`.

## Disagreements between renderer and spec
1. `ai_fix`, `confidence`, `ai_severity` are not rendered (and not produced by the worker).
2. `ai_severity`, `snippet` and `repo.private` are coming in the contract (owned by Coder); the renderer must use them once they land.
3. No tool-versions section (confirmed, pending); `tool_versions` is never populated by `run.py` although the schema allows it.
4. Cloud banner is inline on the provider bullet, not a standalone line (confirmed change pending); and `kind` comes from the adapter, not the DB.
5. Header omits `provider.model`, `repo.ref`, `started_at` (confirmed, pending), and `repo.private`.
6. Summary counts include AI file-review `info` findings, while the Findings table excludes them.
7. Gitleaks rows show no redaction marker even though `secret_redacted: true` is set (minor).
