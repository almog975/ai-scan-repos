"""Opt-in end-to-end runs against the pinned vulnerable repos (real git clone + real semgrep/gitleaks/trivy).
Skipped unless AISCAN_NETWORK_TESTS=1; tools missing on PATH are skipped per tool."""
import dataclasses
import json
import os
import shutil
from pathlib import Path

import pytest

from scanner.run import Worker
from tests.contract import check
from tests.phase2_util import p2
from tests.test_worker import RID, FakeAPI, cfg

pytestmark = [pytest.mark.network,
              pytest.mark.skipif(os.environ.get("AISCAN_NETWORK_TESTS") != "1", reason="set AISCAN_NETWORK_TESTS=1")]
REPOS = p2("vulnerable_repos.json")["repos"]
SEMGREP_GAP = pytest.mark.xfail(strict=True, reason="tools.py runs semgrep with --config auto --metrics off -> exit 2")


@pytest.fixture(scope="module")
def scans(tmp_path_factory):
    out = {}
    for r in REPOS:
        tmp = tmp_path_factory.mktemp(r["name"])
        c = dataclasses.replace(cfg(tmp, "quick"), repo_url=r["url"], commit_sha=r["commit_sha"], scanner_timeout=900)
        assert Worker(c, FakeAPI(), None).run() == "completed"
        rep = json.loads((Path(c.reports_dir) / RID / "report.json").read_text())
        out[r["name"]] = (c, rep)
    return out


def cases():
    for r in REPOS:
        for k, n in r["expect"].items():
            tool = k.split("_")[0]
            marks = [pytest.mark.skipif(not shutil.which(tool), reason=f"{tool} not installed")]
            if tool == "semgrep":
                marks.append(SEMGREP_GAP)
            yield pytest.param(r["name"], k, n, marks=marks, id=f"{r['name']}-{k}")


@pytest.mark.parametrize("repo,key,n", list(cases()))
def test_expected_findings(scans, repo, key, n):
    _, rep = scans[repo]
    tool = key.split("_")[0]
    fs = [f for f in rep["findings"] if f["tool"] == tool]
    if key == "trivy_vuln_min":
        fs = [f for f in fs if f["category"] == "dependency"]
    if key == "trivy_misconfig_min":
        fs = [f for f in fs if f["category"] == "misconfig"]
    assert len(fs) >= n, f"{repo}: {len(fs)} {key}"


@pytest.mark.parametrize("repo", [r["name"] for r in REPOS])
def test_report_valid_pinned_and_clone_removed(scans, repo):
    c, rep = scans[repo]
    check("report.schema.json", rep)
    want = next(r["commit_sha"] for r in REPOS if r["name"] == repo)
    assert rep["repo"]["commit_sha"] == want
    assert list(Path(c.work_dir).iterdir()) == []
    for s in ("critical", "high", "medium", "low", "info"):
        assert rep["summary"][s] == sum(1 for f in rep["findings"] if f["severity"] == s)


@pytest.mark.parametrize("repo", [pytest.param(r["name"], marks=pytest.mark.xfail(
    strict=True, reason="gitleaks findings carry the absolute temp clone path in `file` (semgrep too, once it runs)"))
    if "gitleaks_min" in r["expect"] else r["name"] for r in REPOS])
def test_real_findings_are_repo_relative(scans, repo):
    _, rep = scans[repo]
    bad = [f["file"] for f in rep["findings"] if f["file"].startswith("/")]
    assert not bad, bad[:3]
