"""render_markdown() vs docs/report-md-spec.md; tool_versions in report.json."""
import json
from pathlib import Path

from scanner import report as R
from scanner.tools import tool_versions



def _rep(**kw):
    f = {"id": "a" * 40, "tool": "semgrep", "category": "sast", "rule": "py.eval", "severity": "high",
         "file": "app.py", "line": 3, "title": "eval use", "description": "Use of eval",
         "snippet": "x = eval(u)", "ai_severity": "critical", "confidence": 0.82,
         "ai_fix": "Use `ast.literal_eval`.", "ai_explanation": "User input reaches eval."}
    rep = R.build_report(review_id="3f1c2a9e-7b4d-4e8a-9c1f-2d5e6a7b8c90", repo_url="https://github.com/o/r",
                         commit_sha="b" * 40, ref="main", depth="brief", attempt=1, budget_no=2,
                         started_at="2026-10-28T10:00:00Z", finished_at="2026-10-28T10:05:00Z", findings=[f],
                         provider=kw.get("provider", {"name": "openai", "model": "gpt-x", "kind": "cloud"}),
                         files_scanned=4, tool_versions={"semgrep": "1.101.0", "trivy": "0.76.1"})
    rep["repo"]["private"] = kw.get("private", False)
    return rep


def test_header_banner_columns_snippet_versions():
    md = R.render_markdown(_rep())
    lines = md.splitlines()
    assert lines[2] == R.CLOUD_BANNER  # own line right under the title
    assert "model: `gpt-x`" in md and "ref: `main`" in md and "started: 2026-10-28T10:00:00Z" in md
    assert "| AI severity | Confidence | AI fix |" in md and "| critical | 82% | yes |" in md
    assert "## Tool versions" in md and "| trivy | 0.76.1 |" in md
    assert "```\nx = eval(u)\n```" in md and "**Suggested fix**" in md
    assert "AI severity: critical · confidence: 82%" in md
    assert R.ONPREM_NOTE not in md


def test_private_banner_and_onprem():
    assert R.CLOUD_BANNER_PRIVATE in R.render_markdown(_rep(private=True))
    md = R.render_markdown(_rep(provider={"name": "ollama", "model": "llama3", "kind": "onprem"}))
    assert R.CLOUD_BANNER not in md and R.ONPREM_NOTE in md


def test_report_valid_against_schema():
    from tests.contract import check as v
    v("report.schema.json", _rep())


class P:
    def __init__(self, out, rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


def test_tool_versions_parses_and_skips_missing():
    outs = {"semgrep": P("1.101.0\n"), "gitleaks": P("8.21.2"), "trivy": P("Version: 0.76.1\n"), "git": P("x", 1)}
    tv = tool_versions(runner=lambda c: outs[c[0]], which=lambda n: n != "gitleaks")
    assert tv == {"semgrep": "1.101.0", "trivy": "0.76.1"}
