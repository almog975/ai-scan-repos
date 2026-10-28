import json
from pathlib import Path

import httpx

from scanner import run as runmod
from scanner.api_client import ReviewAPI
from scanner.checkpoint import Checkpoint, atomic_write_json
from scanner.config import WorkerConfig
from scanner.run import Worker, list_code_files, make_ai


class FakeAPI:
    def __init__(self):
        self.calls = []

    def progress(self, rid, pct, **kw):
        self.calls.append((pct, kw))


class FakeAI:
    provider, model, is_cloud = "openai", "m", True

    def __init__(self):
        self.prompts = []

    def complete(self, p):
        self.prompts.append(p)
        return f"ai#{len(self.prompts)}"


def fake_clone(url, dest, timeout):
    dest.mkdir(parents=True)
    (dest / "a.py").write_text("x = eval(input())\n")
    (dest / "b.js").write_text("console.log(1)\n")
    (dest / "node_modules").mkdir()
    (dest / "node_modules" / "c.js").write_text("skip")


def fake_scanner(name, src, t):
    return {"status": "ok", "findings": [{"scanner": name, "rule": "r", "severity": "HIGH",
                                          "file": "a.py", "line": 1, "message": "m"}]}


def cfg(tmp_path, depth="quick"):
    return WorkerConfig(review_id="rev1", repo_url="https://github.com/a/b", depth=depth,
                        reports_dir=str(tmp_path / "reports"), work_dir=str(tmp_path / "work"))


def test_atomic_and_checkpoint(tmp_path):
    atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": 1}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    cp = Checkpoint(tmp_path)
    assert cp.load() is False
    cp.state["done_files"] = ["a.py"]
    cp.save()
    cp2 = Checkpoint(tmp_path)
    assert cp2.load() and cp2.state["done_files"] == ["a.py"]
    (tmp_path / "checkpoint.json").write_text("{bad")
    assert Checkpoint(tmp_path).load() is False


def test_quick_run_completes_and_deletes_source(tmp_path):
    api, ai = FakeAPI(), FakeAI()
    w = Worker(cfg(tmp_path), api, ai, scanner_fn=fake_scanner, clone_fn=fake_clone)
    assert w.run() == "completed"
    assert list((tmp_path / "work").iterdir()) == []  # source deleted
    rep = json.loads((tmp_path / "reports/rev1/report.json").read_text())
    assert rep["total_findings"] == 3 and rep["ai"]["code_left_network"] and rep["ai"]["summary"] == "ai#1"
    assert len(ai.prompts) == 1 and api.calls[-1][1]["status"] == "completed"


def test_brief_includes_code_context(tmp_path):
    ai = FakeAI()
    Worker(cfg(tmp_path, "brief"), FakeAPI(), ai, scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    assert "eval(input())" in ai.prompts[0]


def test_deep_pause_then_resume_from_checkpoint(tmp_path):
    c = cfg(tmp_path, "deep")
    ai = FakeAI()
    w = Worker(c, FakeAPI(), ai, scanner_fn=fake_scanner, clone_fn=fake_clone)
    orig = w._beat

    def beat(pct, msg):
        if msg == "AI reviewed a.py":
            (w.cp.dir / "PAUSE").write_text("1")
        orig(pct, msg)
    w._beat = beat
    assert w.run() == "paused"
    assert list((tmp_path / "work").iterdir()) == []
    st = json.loads((tmp_path / "reports/rev1/checkpoint.json").read_text())
    assert st["done_files"] == ["a.py"] and set(st["scanners"]) == {"semgrep", "gitleaks", "trivy"}
    assert json.loads((tmp_path / "reports/rev1/progress.json").read_text())["progress"] > 50

    (tmp_path / "reports/rev1/PAUSE").unlink()
    scans = []
    ai2 = FakeAI()
    w2 = Worker(c, FakeAPI(), ai2, scanner_fn=lambda *a: scans.append(a) or fake_scanner(*a), clone_fn=fake_clone)
    assert w2.run() == "completed"
    assert scans == []  # scanners not re-run
    assert len(ai2.prompts) == 2  # only b.js + summary
    rep = json.loads((tmp_path / "reports/rev1/report.json").read_text())
    assert set(rep["ai"]["file_reviews"]) == {"a.py", "b.js"}


def test_ai_failure_still_reports(tmp_path):
    class Bad(FakeAI):
        def complete(self, p):
            raise RuntimeError("down")
    Worker(cfg(tmp_path), FakeAPI(), Bad(), scanner_fn=fake_scanner, clone_fn=fake_clone).run()
    rep = json.loads((tmp_path / "reports/rev1/report.json").read_text())
    assert rep["ai"]["error"] == "down" and rep["ai"]["summary"] is None


def test_clone_failure_cleans_up(tmp_path):
    def bad_clone(u, d, t):
        d.mkdir(parents=True)
        (d / "secret.py").write_text("x")
        raise RuntimeError("clone failed")
    try:
        Worker(cfg(tmp_path), FakeAPI(), None, scanner_fn=fake_scanner, clone_fn=bad_clone).run()
    except RuntimeError:
        pass
    assert list((tmp_path / "work").iterdir()) == []


def test_list_code_files(tmp_path):
    fake_clone("", tmp_path / "r", 0)
    (tmp_path / "r" / "big.py").write_text("x" * 100)
    assert list_code_files(tmp_path / "r", 10, 50) == ["a.py", "b.js"]
    assert list_code_files(tmp_path / "r", 1, 50) == ["a.py"]


def test_make_ai(tmp_path):
    c = cfg(tmp_path)
    c.ai_provider = "none"
    assert make_ai(c) is None
    c.ai_provider, c.ai_base_url = "openai_compatible", ""
    assert make_ai(c) is None
    c.ai_base_url = "http://x/v1"
    assert make_ai(c).is_cloud is False


def test_api_client_and_main(tmp_path, monkeypatch):
    posts = []

    def handler(req):
        assert req.headers["x-internal-token"] == "tok"
        if req.method == "GET":
            return httpx.Response(200, json={"repo_url": "https://github.com/a/b", "depth": "quick",
                                             "ai_provider": "none", "progress": 0})
        posts.append(json.loads(req.content))
        return httpx.Response(200, json={})
    t = httpx.MockTransport(handler)
    real = ReviewAPI
    monkeypatch.setattr(runmod, "ReviewAPI", lambda u, tok: real(u, tok, transport=t))
    monkeypatch.setattr(runmod, "clone", fake_clone)
    monkeypatch.setattr(runmod.Worker.__init__, "__defaults__", (fake_scanner, fake_clone))
    monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "rep"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "w"))
    assert runmod.main(["--review-id", "r9"]) == 0
    assert posts[-1]["status"] == "completed"
    # heartbeat failures are swallowed
    bad = real("http://x", "tok", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    bad.progress("r", 1)
