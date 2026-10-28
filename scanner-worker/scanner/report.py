"""report/v1 (contracts/report.schema.json) + report.md."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .checkpoint import atomic_write_json, atomic_write_text
from .tools import SEV_ORDER


def sev_key(f: dict):
    return (SEV_ORDER.index(f["severity"]), f["file"], f["line"] or 0, f["id"])


def top_findings(findings: list[dict], n: int) -> list[dict]:
    return sorted((f for f in findings if f["tool"] != "ai"), key=sev_key)[:n]


def build_report(*, review_id, repo_url, commit_sha, ref, depth, attempt, budget_no, started_at, finished_at,
                 findings, provider=None, executive_summary=None, files_scanned=0, tool_versions=None) -> dict:
    findings = sorted(findings, key=sev_key)
    counts = Counter(f["severity"] for f in findings)
    rep = {"schema": "report/v1", "review_id": review_id,
           "repo": {"url": repo_url, "commit_sha": commit_sha, "ref": ref},
           "depth": depth, "provider": provider, "started_at": started_at, "finished_at": finished_at,
           "attempt": attempt, "budget_no": budget_no,
           "summary": {**{s: counts.get(s, 0) for s in SEV_ORDER}, "files_scanned": files_scanned},
           "executive_summary": executive_summary, "findings": findings}
    if tool_versions:
        rep["tool_versions"] = tool_versions
    return rep


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(rep: dict) -> str:
    prov = rep.get("provider") or {}
    s = rep["summary"]
    lines = [f"# Security review: {rep['repo']['url']}", "",
             f"- Review: `{rep['review_id']}` | depth: **{rep['depth']}** | commit: `{rep['repo'].get('commit_sha') or '-'}`"
             f" | attempt {rep['budget_no']}-{rep['attempt']} | finished: {rep['finished_at']}",
             f"- AI provider: **{prov.get('name') or 'none'}**"
             + (" ⚠️ cloud AI — code/findings were sent outside your network" if prov.get("kind") == "cloud"
                else " (on-prem / no code left the network)"),
             "", "## Summary", "",
             "| critical | high | medium | low | info | files scanned |", "|---|---|---|---|---|---|",
             f"| {s['critical']} | {s['high']} | {s['medium']} | {s['low']} | {s['info']} | {s.get('files_scanned', 0)} |",
             "", rep.get("executive_summary") or "_AI step skipped or unavailable._"]
    scan = [f for f in rep["findings"] if f["tool"] != "ai"]
    if scan:
        lines += ["", "## Findings", "", "| Severity | Tool | Rule | Location | Title | Description |", "|---|---|---|---|---|---|"]
        lines += [f"| {f['severity']} | {f['tool']} | {_cell(f['rule'])} | {_cell(f['file'])}"
                  f"{':' + str(f['line']) if f.get('line') else ''} | {_cell(f['title'])} | {_cell(f['description'][:300])} |" for f in scan]
    for f in (f for f in scan if f.get("ai_explanation")):
        lines += ["", f"### {f['severity']}: {f['title']} ({f['file']})", "", f["ai_explanation"]]
    for f in (f for f in rep["findings"] if f["tool"] == "ai"):
        lines += ["", f"### AI review: {f['file']}", "", f["description"]]
    return "\n".join(lines) + "\n"


def write_report(review_dir: Path, rep: dict) -> Path:
    review_dir = Path(review_dir)
    atomic_write_text(review_dir / "report.md", render_markdown(rep))
    atomic_write_json(review_dir / "report.json", rep)  # written last: its presence == report complete
    return review_dir / "report.json"
