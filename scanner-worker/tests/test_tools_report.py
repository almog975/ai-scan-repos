import json
import subprocess
from pathlib import Path

from scanner.report import build_report, render_markdown, write_report
from scanner.tools import parse_trivy, run_scanner
from tests.conftest import FIX, load


def test_parsers(fixture_scans):
    s = fixture_scans["semgrep"]["findings"]
    assert [f["severity"] for f in s] == ["HIGH", "MEDIUM"]
    g = fixture_scans["gitleaks"]["findings"][0]
    assert "REDACTED" not in json.dumps(g) and g["severity"] == "HIGH"
    t = parse_trivy(load("trivy.json"))
    assert {f["rule"] for f in t} == {"CVE-2023-0001", "DS002"} and t[0]["severity"] == "CRITICAL"


def test_run_scanner_missing_tool():
    r = run_scanner("semgrep", Path("."), 5, which=lambda n: None)
    assert r["status"] == "skipped"


def test_run_scanner_with_fake_runner(tmp_path):
    class P:
        stdout = (FIX / "semgrep.json").read_text()
    assert len(run_scanner("semgrep", tmp_path, 5, runner=lambda c, t: P, which=lambda n: "/x")["findings"]) == 2
    P.stdout = (FIX / "trivy.json").read_text()
    assert run_scanner("trivy", tmp_path, 5, runner=lambda c, t: P, which=lambda n: "/x")["status"] == "ok"

    def gl_runner(cmd, t):
        Path(cmd[cmd.index("--report-path") + 1]).write_text((FIX / "gitleaks.json").read_text())
    assert len(run_scanner("gitleaks", tmp_path, 5, runner=gl_runner, which=lambda n: "/x")["findings"]) == 1

    def boom(cmd, t):
        raise subprocess.TimeoutExpired(cmd, t)
    assert "timed out" in run_scanner("trivy", tmp_path, 5, runner=boom, which=lambda n: "/x")["error"]

    def err(cmd, t):
        raise RuntimeError("exit 2")
    assert run_scanner("trivy", tmp_path, 5, runner=err, which=lambda n: "/x")["status"] == "error"


def test_report_render(tmp_path, fixture_scans):
    rep = build_report(review_id="r1", repo_url="https://github.com/a/b", depth="brief", scanners=fixture_scans,
                       ai={"provider": "openai", "is_cloud": True, "code_left_network": True,
                           "summary": "Fix eval.", "file_reviews": {"app.py": "use ast.literal_eval"}})
    assert rep["severity_counts"] == {"HIGH": 2, "MEDIUM": 1} and rep["total_findings"] == 3
    md = render_markdown(rep)
    assert "⚠️ cloud AI" in md and "## semgrep findings" in md and "## gitleaks findings" in md
    assert "trivy | skipped (trivy not installed) | 0" in md
    assert "eval \\| dangerous" in md and "### app.py" in md
    assert md.index("| HIGH |") < md.index("| MEDIUM |")
    write_report(tmp_path, rep)
    assert json.loads((tmp_path / "report.json").read_text())["review_id"] == "r1"
    assert (tmp_path / "report.md").read_text().startswith("# Security review")


def test_report_onprem_no_ai(fixture_scans):
    md = render_markdown(build_report(review_id="r", repo_url="u", depth="quick", scanners={}, ai={}))
    assert "no code left the network" in md and "AI step skipped" in md
