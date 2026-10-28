"""Phase 2 contract additions (7c00f77): finding.snippet / ai_severity / ai_fix / confidence,
report.provider.kind required, summary.files_scanned required, repo.private."""
import dataclasses
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scanner.run import Worker, make_ai
from scanner.tools import make_finding, run_scanner
from tests.contract import _registry, check
from tests.mock_ai import MockAI
from tests.phase2_util import all_text, p2
from tests.test_phase2_worker import ai_cfg, report
from tests.test_worker import RID, FakeAPI, cfg, fake_clone, fake_scanner

FAKE = "AKIAZZZZFAKESECRET0001"


def valid(name, payload):
    return not list(Draft202012Validator({"$ref": name}, registry=_registry()).iter_errors(payload))


def base():
    return make_finding("semgrep", "sast", "r", "ERROR", "a.py", 1, "t", "d")


# ---- schema-level expectations the worker must respect -----------------------------------------
def test_snippet_at_most_10_lines():
    assert valid("finding.schema.json", {**base(), "snippet": "\n".join(["x"] * 10)})
    assert not valid("finding.schema.json", {**base(), "snippet": "\n".join(["x"] * 11)})
    assert valid("finding.schema.json", {**base(), "snippet": None})


def test_ai_severity_enum_and_independent_of_severity():
    for s in ("critical", "high", "medium", "low", "info", None):
        assert valid("finding.schema.json", {**base(), "ai_severity": s})
    assert not valid("finding.schema.json", {**base(), "ai_severity": "unknown"})


def test_report_provider_requires_kind_and_is_closed():
    from scanner.report import build_report
    rep = build_report(review_id=RID, repo_url="https://github.com/a/b", commit_sha="a" * 40, ref="HEAD", depth="quick",
                       attempt=1, budget_no=1, started_at="2026-10-28T00:00:00Z", finished_at="2026-10-28T00:01:00Z",
                       findings=[base()], provider={"name": "x", "model": "m", "kind": "cloud"})
    check("report.schema.json", rep)
    assert not valid("report.schema.json", {**rep, "provider": {"name": "x", "model": "m"}})
    assert not valid("report.schema.json", {**rep, "provider": {**rep["provider"], "extra": 1}})
    s = dict(rep["summary"]); s.pop("files_scanned")
    assert not valid("report.schema.json", {**rep, "summary": s})


# ---- worker output vs new contract ---------------------------------------------------------------
def _gitleaks_runner():
    raw = p2("raw/gitleaks.json")

    def runner(cmd, timeout):
        rp = cmd[cmd.index("--report-path") + 1]
        Path(rp).write_text(json.dumps(raw))

        class P:
            stdout = "{}"
        return P()
    return runner


def test_gitleaks_secret_never_in_worker_outputs(tmp_path):
    def scanner(name, src, t):
        if name == "gitleaks":
            return run_scanner(name, src, t, runner=_gitleaks_runner(), which=lambda n: True)
        return {"status": "ok", "findings": []}
    with MockAI() as m:
        c, api = ai_cfg(tmp_path, m, "openai_compatible", "brief"), FakeAPI()
        assert Worker(c, api, make_ai(c),
                      scanner_fn=scanner, clone_fn=fake_clone).run() == "completed"
    assert FAKE not in all_text(Path(c.reports_dir))
    assert all(FAKE not in r["body"] for r in m.requests), "unmasked secret sent to the AI provider"
    rep = report(c)
    for f in rep["findings"]:
        if f["tool"] == "gitleaks":
            assert f["secret_redacted"] is True
            assert FAKE not in (f.get("snippet") or "")


@pytest.mark.parametrize("depth", ["quick", "brief", "deep"])
def test_worker_report_matches_phase2_contract(tmp_path, depth):
    with MockAI() as m:
        c = ai_cfg(tmp_path, m, "openai_compatible", depth)
        Worker(c, FakeAPI(), make_ai(c),
               scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    rep = report(c)
    check("report.schema.json", rep)
    assert rep["provider"]["kind"] == "onprem"
    assert set(rep["provider"]) <= {"name", "model", "kind"}
    assert rep["summary"]["files_scanned"] == 2  # a.py, b.js (node_modules skipped)
    for f in rep["findings"]:
        if f.get("snippet") is not None:
            assert f["snippet"].count("\n") <= 9
        if depth == "quick":
            assert f.get("ai_severity") is None and f.get("ai_fix") is None and f.get("confidence") is None


def test_no_ai_report_provider_null_or_kind(tmp_path):
    c = cfg(tmp_path, "brief")
    Worker(c, FakeAPI(), None, scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    rep = report(c)
    check("report.schema.json", rep)
    assert rep["provider"] is None or "kind" in rep["provider"]


GAP_AI_FIELDS = ("brief/deep AI enrichment only fills ai_explanation; ai_fix / confidence / ai_severity "
                 "(contract 7c00f77, docs/severity-mapping.md TODO) are never populated")


@pytest.mark.parametrize("field", ["ai_fix", "confidence", "ai_severity"])
def test_brief_enrichment_populates_ai_fields(tmp_path, field):
    with MockAI() as m:
        c = ai_cfg(tmp_path, m, "openai_compatible", "brief")
        Worker(c, FakeAPI(), make_ai(c),
               scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    enriched = [f for f in report(c)["findings"] if f.get("ai_explanation")]
    assert enriched and all(f.get(field) is not None for f in enriched)


def test_ai_never_changes_original_severity(tmp_path):
    with MockAI() as m:
        c = ai_cfg(tmp_path, m, "openai_compatible", "brief")
        Worker(c, FakeAPI(), make_ai(c),
               scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    orig = {f["id"]: f["severity"] for t in ("semgrep", "gitleaks", "trivy") for f in fake_scanner(t, None, 0)["findings"]}
    for f in report(c)["findings"]:
        if f["id"] in orig:
            assert f["severity"] == orig[f["id"]]


def test_report_repo_private_present(tmp_path):
    c = cfg(tmp_path)
    Worker(c, FakeAPI(), None, scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    assert isinstance(report(c)["repo"].get("private"), bool)
