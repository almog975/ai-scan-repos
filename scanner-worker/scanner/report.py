"""report/v1 (contracts/report.schema.json) + report.md."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .checkpoint import atomic_write_json, atomic_write_text
from .tools import SEV_ORDER


NO_AI_PROVIDER = {"name": "none", "kind": "onprem", "model": "none"}  # report.provider is required


def sev_key(f: dict):
    return (SEV_ORDER.index(f["severity"]), f["file"], f["line"] or 0, f["id"])


def top_findings(findings: list[dict], n: int) -> list[dict]:
    return sorted((f for f in findings if f["tool"] != "ai"), key=sev_key)[:n]


def build_report(*, review_id, repo_url, commit_sha, ref, depth, attempt, budget_no, started_at, finished_at,
                 findings, provider=None, executive_summary=None, files_scanned=0, tool_versions=None,
                 private=None, partial=False) -> dict:
    findings = sorted(findings, key=sev_key)
    counts = Counter(f["severity"] for f in findings)
    rep = {"schema": "report/v1", "review_id": review_id,
           "repo": {"url": repo_url, "commit_sha": commit_sha, "ref": ref},
           "depth": depth, "provider": provider or dict(NO_AI_PROVIDER), "started_at": started_at, "finished_at": finished_at,
           "attempt": attempt, "budget_no": budget_no,
           "summary": {**{s: counts.get(s, 0) for s in SEV_ORDER}, "files_scanned": files_scanned},
           "executive_summary": executive_summary, "findings": findings, "partial": bool(partial)}
    if private is not None:
        rep["repo"]["private"] = bool(private)
    if tool_versions:
        rep["tool_versions"] = tool_versions
    return rep


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


# Banner wording (docs/report-md-spec.md section 3). Private-repo wording is NOT confirmed yet: change it here only.
CLOUD_BANNER = "> ⚠️ cloud AI — code/findings were sent outside your network"
PARTIAL_BANNER = "> ⚠️ Partial report — the review stopped (failed or cancelled) before it finished; results cover {n} file(s) scanned so far."
CLOUD_BANNER_PRIVATE = "> ⚠️ cloud AI — code/findings from this private repository were sent outside your network"
ONPREM_NOTE = "(on-prem / no code left the network)"


def _pct(c) -> str:
    return f"{round(c * 100)}%" if isinstance(c, (int, float)) else ""


def render_markdown(rep: dict) -> str:
    prov = rep.get("provider") or {}
    repo = rep["repo"]
    s = rep["summary"]
    cloud = prov.get("kind") == "cloud"
    lines = [f"# Security review: {repo['url']}", ""]
    if rep.get("partial"):
        lines += [PARTIAL_BANNER.format(n=s["files_scanned"]), ""]
    if cloud:
        lines += [CLOUD_BANNER_PRIVATE if repo.get("private") else CLOUD_BANNER, ""]
    vis = "" if repo.get("private") is None else f" | visibility: {'private' if repo['private'] else 'public'}"
    lines += [f"- Review: `{rep['review_id']}` | depth: **{rep['depth']}** | ref: `{repo.get('ref') or '-'}`"
              f" | commit: `{repo.get('commit_sha') or '-'}`{vis}",
              f"- attempt {rep['budget_no']}-{rep['attempt']} | started: {rep['started_at']} | finished: {rep['finished_at']}",
              f"- AI provider: **{prov.get('name') or 'none'}**"
              + (f" | model: `{prov['model']}`" if prov.get("model") and prov.get("name") != "none" else "")
              + ("" if cloud else f" {ONPREM_NOTE}"),
              "", "## Summary", "",
              "| critical | high | medium | low | info | files scanned |", "|---|---|---|---|---|---|",
              f"| {s['critical']} | {s['high']} | {s['medium']} | {s['low']} | {s['info']} | {s.get('files_scanned', 0)} |",
              "", rep.get("executive_summary") or "_AI step skipped or unavailable._"]
    tv = rep.get("tool_versions") or {}
    if tv:
        lines += ["", "## Tool versions", "", "| tool | version |", "|---|---|"]
        lines += [f"| {_cell(k)} | {_cell(v)} |" for k, v in sorted(tv.items())]
    scan = [f for f in rep["findings"] if f["tool"] != "ai"]
    if scan:
        lines += ["", "## Findings", "",
                  "| Severity | Tool | Rule | Location | Title | Description | AI severity | Confidence | AI fix |",
                  "|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {f['severity']} | {f['tool']} | {_cell(f['rule'])} | {_cell(f['file'])}"
                  f"{':' + str(f['line']) if f.get('line') else ''} | {_cell(f['title'])} | {_cell(f['description'][:300])}"
                  f" | {_cell(f.get('ai_severity'))} | {_pct(f.get('confidence'))} | {'yes' if f.get('ai_fix') else ''} |"
                  for f in scan]
    for f in (f for f in scan if f.get("ai_explanation") or f.get("ai_fix") or f.get("snippet")):
        lines += ["", f"### {f['severity']}: {f['title']} ({f['file']})"]
        if f.get("ai_severity") or f.get("confidence") is not None:
            lines += ["", f"AI severity: {f.get('ai_severity') or '-'} · confidence: {_pct(f.get('confidence')) or '-'}"]
        if f.get("snippet"):
            lines += ["", "```", f["snippet"].replace("```", "ˋˋˋ"), "```"]
        if f.get("ai_explanation"):
            lines += ["", f["ai_explanation"]]
        if f.get("ai_fix"):
            lines += ["", "**Suggested fix**", "", f["ai_fix"]]
    for f in (f for f in rep["findings"] if f["tool"] == "ai"):
        lines += ["", f"### AI review: {f['file']}", "", f["description"]]
    return "\n".join(lines) + "\n"


PARTIAL_JSON, PARTIAL_MD = "report.partial.json", "report.partial.md"


def write_partial_report(review_dir: Path, rep: dict) -> Path:
    """Snapshot of the results so far (partial: true). review-api serves it once the review is failed/cancelled;
    report.json stays reserved for a completed review."""
    review_dir = Path(review_dir)
    rep = {**rep, "partial": True}
    atomic_write_text(review_dir / PARTIAL_MD, render_markdown(rep))
    atomic_write_json(review_dir / PARTIAL_JSON, rep)
    return review_dir / PARTIAL_JSON


def write_report(review_dir: Path, rep: dict) -> Path:
    review_dir = Path(review_dir)
    for n in (PARTIAL_JSON, PARTIAL_MD):
        (review_dir / n).unlink(missing_ok=True)
    atomic_write_text(review_dir / "report.md", render_markdown(rep))
    atomic_write_json(review_dir / "report.json", rep)  # written last: its presence == report complete
    return review_dir / "report.json"
