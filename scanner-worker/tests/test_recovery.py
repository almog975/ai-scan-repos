"""Phase 3: atomic checkpoints, heartbeat thread, dedupe by finding.id on resume."""
import dataclasses
import json
import os
import time

from scanner.api_client import Superseded
from scanner.checkpoint import PARTIAL, Checkpoint, atomic_write_json, attempt_dir
from scanner.run import Worker
from scanner.tools import make_finding
from tests.contract import check
from tests.test_worker import RID, SHA, FakeAPI, FakeAI, cfg, fake_clone, fake_scanner, rdir


def test_atomic_write_fsyncs_file_and_dir(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real(fd))
    atomic_write_json(tmp_path / "c.json", {"a": 1})
    assert len(synced) == 2 and json.loads((tmp_path / "c.json").read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]  # no tmp left


def test_append_dedupes_by_id_but_keeps_enrichment(tmp_path):
    cp = Checkpoint(tmp_path / "a", review_id=RID, attempt=1, budget_no=1, depth="deep")
    f = make_finding("semgrep", "sast", "r", "high", "a.py", 1, "t", "d")
    cp.append_findings([f, f])
    cp.append_findings([f])  # resume re-runs the scanner
    assert cp.cursor["findings_offset"] == 1
    cp.append_findings([{**f, "ai_explanation": "x"}])  # enrichment: same id, new content -> wins
    assert cp.cursor["findings_offset"] == 2 and cp.findings() == [{**f, "ai_explanation": "x"}]


def test_heartbeat_thread_posts_while_a_long_scanner_runs(tmp_path):
    api = FakeAPI()

    def slow(name, src, t):
        time.sleep(0.35)
        return fake_scanner(name, src, t)
    c = dataclasses.replace(cfg(tmp_path, sha=SHA), heartbeat_seconds=0.1)
    assert Worker(c, api, None, slow, fake_clone).run() == "completed"
    beats = [b for b in api.calls if "outcome" not in b and b["stage"] in ("semgrep", "gitleaks", "trivy")]
    assert len(beats) > len({"semgrep", "gitleaks", "trivy"})  # more than the per-stage beats
    check("report.schema.json", json.loads((rdir(tmp_path) / "report.json").read_text()))


def test_heartbeat_superseded_stops_the_attempt(tmp_path):
    class API(FakeAPI):
        def report(self, rid, body):
            self.calls.append(body)
            if len(self.calls) > 2:
                raise Superseded("stale")

    def slow(name, src, t):
        time.sleep(0.3)
        return fake_scanner(name, src, t)
    c = dataclasses.replace(cfg(tmp_path, sha=SHA), heartbeat_seconds=0.05)
    assert Worker(c, API(), None, slow, fake_clone).run() == "superseded"
    assert not list((tmp_path / "work").iterdir())


def test_deep_resume_no_duplicate_file_reviews(tmp_path):
    w = Worker(cfg(tmp_path, "deep", sha=SHA), FakeAPI(), FakeAI(), fake_scanner, fake_clone)
    orig = w._beat

    def beat(stage, pct=None, current=None, **kw):
        orig(stage, pct, current, **kw)
        if stage == "ai" and w.cp.cursor["files_done"] == ["a.py"]:
            raise KeyboardInterrupt  # killed after file 1
    w._beat = beat
    try:
        w.run()
    except KeyboardInterrupt:
        pass
    ai2 = FakeAI()
    assert Worker(cfg(tmp_path, "deep", attempt=2, sha=SHA), FakeAPI(), ai2, fake_scanner, fake_clone).run() == "completed"
    assert "a.py" not in ai2.prompts[0]
    lines = [json.loads(x) for x in (attempt_dir(tmp_path / "reports", RID, 1, 2) / PARTIAL).read_text().splitlines()]
    reviews = [x["file"] for x in lines if x["rule"] == "ai.file_review"]
    assert sorted(reviews) == ["a.py", "b.js"]
    rep = json.loads((rdir(tmp_path) / "report.json").read_text())
    ids = [f["id"] for f in rep["findings"]]
    assert len(ids) == len(set(ids))
