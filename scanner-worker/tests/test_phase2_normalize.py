"""Phase 2: golden normalization of semgrep / gitleaks / trivy output (raw fixtures trimmed from real
runs against the pinned repos in fixtures/phase2/vulnerable_repos.json)."""
import json

import pytest

from scanner.tools import finding_id, make_finding, parse_gitleaks, parse_semgrep, parse_trivy, run_scanner
from scanner.report import build_report
from tests.contract import check
from tests.phase2_util import p2

PARSERS = {"semgrep": parse_semgrep, "gitleaks": parse_gitleaks, "trivy": parse_trivy}
INFO_GAP = "semgrep INFO must map to low (agreed mapping); tools._norm_sev maps it to info"


def dedupe(fs):
    out = {}
    for f in fs:
        out[f["id"]] = f  # same rule as Checkpoint.findings(): last record per id wins
    return sorted(out.values(), key=lambda f: f["id"])


def _cmp(tool, skip_info):
    got = dedupe(PARSERS[tool](p2(f"raw/{tool}.json")))
    want = p2(f"golden/{tool}.json")
    if skip_info:
        got = [f for f in got if f["rule"] != "synthetic.info-rule"]
        want = [f for f in want if f["rule"] != "synthetic.info-rule"]
    return got, want


@pytest.mark.parametrize("tool", list(PARSERS))
def test_golden_matches(tool):
    got, want = _cmp(tool, skip_info=True)
    assert got == want


@pytest.mark.xfail(strict=True, reason=INFO_GAP)
def test_golden_semgrep_info_record():
    got, want = _cmp("semgrep", skip_info=False)
    assert got == want


@pytest.mark.parametrize("tool", list(PARSERS))
def test_golden_validates_against_finding_schema(tool):
    for f in p2(f"golden/{tool}.json"):
        check("finding.schema.json", f)
    for f in PARSERS[tool](p2(f"raw/{tool}.json")):
        check("finding.schema.json", f)


@pytest.mark.parametrize("tool", list(PARSERS))
def test_ids_stable_and_follow_recipe(tool):
    a = PARSERS[tool](p2(f"raw/{tool}.json"))
    b = PARSERS[tool](json.loads(json.dumps(p2(f"raw/{tool}.json"))))
    assert [f["id"] for f in a] == [f["id"] for f in b]
    for f in a:
        assert f["id"] == finding_id(f["tool"], f["rule"], f["file"], f["line"])


@pytest.mark.parametrize("tool", list(PARSERS))
def test_raw_duplicates_collapse(tool):
    raw = PARSERS[tool](p2(f"raw/{tool}.json"))
    assert len(raw) == len(p2(f"golden/{tool}.json")) + 1  # one injected exact duplicate per tool
    assert len(dedupe(raw)) == len(p2(f"golden/{tool}.json"))


# ---- severity mapping (golden table) ---------------------------------------------------------
SEMGREP_CASES = [("ERROR", "high"), ("WARNING", "medium"), ("INFO", "low"), ("CRITICAL", "critical"),
                 ("HIGH", "high"), ("MEDIUM", "medium"), ("LOW", "low"), ("", "info"), (None, "info")]
TRIVY_CASES = [("CRITICAL", "critical"), ("HIGH", "high"), ("MEDIUM", "medium"), ("LOW", "low"),
               ("UNKNOWN", "info"), ("bogus", "info")]


@pytest.mark.parametrize("raw,want", [pytest.param(r, w, marks=pytest.mark.xfail(strict=True, reason=INFO_GAP))
                                      if r == "INFO" else (r, w) for r, w in SEMGREP_CASES])
def test_semgrep_severity_mapping(raw, want):
    data = {"results": [{"check_id": "r", "path": "a.py", "start": {"line": 1}, "end": {"line": 1},
                         "extra": {"severity": raw, "message": "m"}}]}
    assert parse_semgrep(data)[0]["severity"] == want


@pytest.mark.parametrize("raw,want", TRIVY_CASES)
def test_trivy_severity_mapping(raw, want):
    data = {"Results": [{"Target": "requirements.txt", "Vulnerabilities": [
        {"VulnerabilityID": "CVE-1-1", "PkgName": "p", "Severity": raw}],
        "Misconfigurations": [{"ID": "DS001", "Title": "t", "Severity": raw}]}]}
    assert {f["severity"] for f in parse_trivy(data)} == {want}


def test_gitleaks_always_high_and_sarif_style_levels():
    assert {f["severity"] for f in parse_gitleaks(p2("raw/gitleaks.json"))} == {"high"}
    for raw, want in (("error", "high"), ("warning", "medium"), ("note", "low"), ("unknown", "info")):
        assert make_finding("semgrep", "sast", "r", raw, "a", 1, "t", "d")["severity"] == want


# ---- gitleaks masking -------------------------------------------------------------------------
FAKE = "AKIAZZZZFAKESECRET0001"


def test_gitleaks_secret_masked_in_findings_and_report(tmp_path):
    raw = p2("raw/gitleaks.json")
    assert FAKE in json.dumps(raw)  # the fixture really contains an unredacted secret
    fs = parse_gitleaks(raw)
    assert FAKE not in json.dumps(fs)
    assert all(f["secret_redacted"] is True for f in fs)
    rep = build_report(review_id="0b8f3a52-2c1e-4c55-9a77-3f4e0c1d2a10", repo_url="https://github.com/a/b",
                       commit_sha="a" * 40, ref="HEAD", depth="quick", attempt=1, budget_no=1,
                       started_at="2026-10-28T00:00:00Z", finished_at="2026-10-28T00:01:00Z", findings=fs)
    from scanner.report import render_markdown
    assert FAKE not in json.dumps(rep) and FAKE not in render_markdown(rep)


# ---- report summary == counts from findings ----------------------------------------------------
def test_report_summary_counts_match_findings():
    fs = dedupe(sum((PARSERS[t](p2(f"raw/{t}.json")) for t in PARSERS), []))
    rep = build_report(review_id="0b8f3a52-2c1e-4c55-9a77-3f4e0c1d2a10", repo_url="https://github.com/a/b",
                       commit_sha="a" * 40, ref="HEAD", depth="quick", attempt=1, budget_no=1,
                       started_at="2026-10-28T00:00:00Z", finished_at="2026-10-28T00:01:00Z", findings=fs)
    check("report.schema.json", rep)
    for s in ("critical", "high", "medium", "low", "info"):
        assert rep["summary"][s] == sum(1 for f in rep["findings"] if f["severity"] == s), s
    assert sum(rep["summary"][s] for s in ("critical", "high", "medium", "low", "info")) == len(rep["findings"])


# ---- product gaps found while running the real tools --------------------------------------------
def _capture(name, src, stdout="{}"):
    seen = {}

    class P:
        pass

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        if name == "gitleaks":
            rp = cmd[cmd.index("--report-path") + 1]
            open(rp, "w").write(json.dumps([{"RuleID": "k", "File": f"{src}/conf/x.env", "StartLine": 2,
                                              "Description": "d"}]))
        p = P()
        p.stdout = stdout
        return p
    return runner, seen


@pytest.mark.xfail(strict=True, reason="semgrep exits 2 'Cannot create auto config when metrics are off' "
                                       "(verified with semgrep 1.182.1): tools.py passes --config auto + --metrics off, "
                                       "so real semgrep never yields findings")
def test_semgrep_cmd_is_runnable(tmp_path):
    runner, seen = _capture("semgrep", tmp_path)
    run_scanner("semgrep", tmp_path, 10, runner=runner, which=lambda n: True)
    c = seen["cmd"]
    assert not ("auto" in c and "off" in c)


@pytest.mark.parametrize("tool", [
    pytest.param("semgrep", marks=pytest.mark.xfail(strict=True, reason="semgrep/gitleaks report paths under the "
                 "absolute temp clone dir; finding.file (and thus id) is not repo-relative -> unstable across attempts")),
    pytest.param("gitleaks", marks=pytest.mark.xfail(strict=True, reason="same: gitleaks File is absolute")),
    "trivy"])
def test_finding_file_is_repo_relative(tmp_path, tool):
    src = tmp_path / "src-x" / "repo"
    out = {"semgrep": {"results": [{"check_id": "r", "path": f"{src}/app/a.py", "start": {"line": 1},
                                    "extra": {"severity": "ERROR", "message": "m"}}]},
           "trivy": {"Results": [{"Target": "app/requirements.txt", "Vulnerabilities": [
               {"VulnerabilityID": "CVE-1-1", "PkgName": "p", "Severity": "HIGH"}]}]}}.get(tool, {})
    runner, _ = _capture(tool, src, json.dumps(out))
    res = run_scanner(tool, src, 10, runner=runner, which=lambda n: True)
    assert res["status"] == "ok"
    f = res["findings"][0]
    assert not f["file"].startswith("/") and str(tmp_path) not in f["file"]
