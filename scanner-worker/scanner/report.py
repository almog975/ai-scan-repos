"""report.json + report.md writer."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .checkpoint import atomic_write_json
from .tools import SEV_ORDER


def build_report(*, review_id, repo_url, depth, scanners: dict, ai: dict) -> dict:
    findings = [f for s in scanners.values() for f in s.get("findings", [])]
    counts = Counter(f["severity"] for f in findings)
    return {
        "review_id": review_id, "repo_url": repo_url, "depth": depth,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "severity_counts": {s: counts.get(s, 0) for s in SEV_ORDER if counts.get(s)},
        "total_findings": len(findings),
        "scanners": {n: {"status": s["status"], "count": len(s.get("findings", [])), "error": s.get("error")}
                     for n, s in scanners.items()},
        "findings": findings,
        "ai": ai,  # {provider, model, is_cloud, code_left_network, summary, file_reviews, recommendations}
    }


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(rep: dict) -> str:
    ai = rep.get("ai") or {}
    lines = [f"# Security review: {rep['repo_url']}", "",
             f"- Review: `{rep['review_id']}` | depth: **{rep['depth']}** | generated: {rep['generated_at']}",
             f"- AI provider: **{ai.get('provider') or 'none'}**"
             + (" ⚠️ cloud AI — code/findings were sent outside your network" if ai.get("code_left_network") else
                " (on-prem / no code left the network)"), "", "## Summary", ""]
    sc = rep["severity_counts"]
    lines.append(f"Total findings: **{rep['total_findings']}** — " +
                 (", ".join(f"{k}: {v}" for k, v in sc.items()) if sc else "none"))
    lines += ["", "| Scanner | Status | Findings |", "|---|---|---|"]
    lines += [f"| {n} | {s['status']}{' (' + _cell(s['error']) + ')' if s.get('error') else ''} | {s['count']} |"
              for n, s in rep["scanners"].items()]
    for name in rep["scanners"]:
        fs = [f for f in rep["findings"] if f["scanner"] == name]
        if not fs:
            continue
        lines += ["", f"## {name} findings", "", "| Severity | Rule | Location | Message |", "|---|---|---|---|"]
        fs.sort(key=lambda f: SEV_ORDER.index(f["severity"]) if f["severity"] in SEV_ORDER else 99)
        lines += [f"| {f['severity']} | {_cell(f['rule'])} | {_cell(f['file'])}{':' + str(f['line']) if f.get('line') else ''} | {_cell(f['message'])} |"
                  for f in fs]
    lines += ["", "## AI recommendations", ""]
    lines.append(ai.get("summary") or "_AI step skipped or unavailable._")
    for path, txt in (ai.get("file_reviews") or {}).items():
        lines += ["", f"### {path}", "", txt]
    return "\n".join(lines) + "\n"


def write_report(review_dir: Path, rep: dict) -> Path:
    review_dir = Path(review_dir)
    atomic_write_json(review_dir / "report.json", rep)
    md = review_dir / "report.md"
    tmp = review_dir / ".report.md.tmp"
    tmp.write_text(render_markdown(rep))
    tmp.replace(md)
    return review_dir / "report.json"
