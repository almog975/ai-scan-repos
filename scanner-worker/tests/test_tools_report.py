import hashlib
import json
import subprocess
from pathlib import Path

from scanner.report import build_report, render_markdown, top_findings, write_report
from scanner.tools import finding_id, make_finding, parse_trivy, run_scanner
from tests.conftest import FIX, load
from tests.contract import check

RID = "0b8f3a52-2c1e-4c55-9a77-3f4e0c1d2a10"
SHA = "a" * 40


def test_finding_id_recipe():
    want = hashlib.sha1(b"semgrep\npython.lang.security.audit.eval\nsrc/app.py\n12").hexdigest()
    assert finding_id("semgrep", "python.lang.security.audit.eval", "src/app.py", 12) == want
    assert finding_id("trivy", "r", "req.txt", None) == hashlib.sha1(b"trivy\nr\nreq.txt\n").hexdigest()


def test_parsers_emit_contract_findings(fixture_scans):
    s = fixture_scans["semgrep"]["findings"]
    assert [f["severity"] for f in s] == ["high", "medium"]
    g = fixture_scans["gitleaks"]["findings"][0]
    assert "REDACTED" not in json.dumps(g) and g["severity"] == "high" and g["secret_redacted"] is True
    t = parse_trivy(load("trivy.json"))
    assert {f["category"] for f in t} == {"dependency", "misconfig"} and t[0]["severity"] == "critical"
    assert t[0]["cve"] == "CVE-2023-0001" and t[0]["line"] is None
    for f in s + [g] + t:
        check("finding.schema.json", f)
    assert make_finding("semgrep", "sast", "r", "weird", "f", 0, "", "")["severity"] == "info"


def test_run_scanner_missing_tool():
    assert run_scanner("semgrep", Path("."), 5, which=lambda n: None)["status"] == "skipped"


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


def _rep(findings, provider=None, summary=None):
    return build_report(review_id=RID, repo_url="https://github.com/a/b", commit_sha=SHA, ref="main", depth="brief",
                        attempt=2, budget_no=1, started_at="2026-10-28T10:00:00Z", finished_at="2026-10-28T10:05:00Z",
                        findings=findings, provider=provider, executive_summary=summary, files_scanned=3)


def test_report_contract_and_markdown(tmp_path, fixture_scans):
    fs = [f for s in fixture_scans.values() for f in s["findings"]]
    fs[0] = {**fs[0], "ai_explanation": "use ast.literal_eval"}
    rep = _rep(fs, {"name": "openai", "model": "m", "kind": "cloud"}, "Fix eval.")
    check("report.schema.json", rep)
    assert rep["summary"] == {"critical": 0, "high": 2, "medium": 1, "low": 0, "info": 0, "files_scanned": 3}
    assert rep["attempt"] == 2 and rep["budget_no"] == 1 and rep["repo"]["commit_sha"] == SHA
    md = render_markdown(rep)
    assert "⚠️ cloud AI" in md and "Fix eval." in md and "use ast.literal_eval" in md
    assert md.index("| high | semgrep") < md.index("| medium | semgrep") and "eval \\| dangerous" in md
    write_report(tmp_path, rep)
    assert json.loads((tmp_path / "report.json").read_text())["review_id"] == RID
    assert (tmp_path / "report.md").read_text().startswith("# Security review")


def test_report_onprem_no_ai():
    rep = _rep([])
    check("report.schema.json", rep)
    md = render_markdown(rep)
    assert "no code left the network" in md and "AI step skipped" in md


def test_top_findings_is_severity_ordered():
    fs = [make_finding("semgrep", "sast", f"r{i}", sev, "a.py", i + 1, "t", "d")
          for i, sev in enumerate(["low", "critical", "info", "high", "medium"] * 4)]
    top = top_findings(fs, 10)
    assert len(top) == 10 and [f["severity"] for f in top[:4]] == ["critical"] * 4
    assert [f["severity"] for f in top[4:8]] == ["high"] * 4
