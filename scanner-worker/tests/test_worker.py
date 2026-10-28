import json
from pathlib import Path

import httpx
import pytest

from scanner import run as runmod
from scanner.api_client import ReviewAPI
from scanner.checkpoint import PARTIAL, Checkpoint, atomic_write_json, attempt_dir
from scanner.config import WorkerConfig
from scanner.run import Worker, list_code_files, make_ai
from scanner.tools import make_finding
from tests.contract import check

RID = "0b8f3a52-2c1e-4c55-9a77-3f4e0c1d2a10"
SHA = "b" * 40


class FakeAPI:
    def __init__(self, supersede_after=None):
        self.calls, self.supersede_after = [], supersede_after

    def report(self, rid, body):
        self.calls.append(body)
        if self.supersede_after and len(self.calls) >= self.supersede_after:
            from scanner.api_client import Superseded
            raise Superseded("stale")


class FakeAI:
    provider, model, is_cloud = "openai", "m", True

    def __init__(self, fail=False):
        self.prompts, self.fail = [], fail

    def complete(self, p):
        self.prompts.append(p)
        if self.fail:
            raise RuntimeError("provider down")
        return f"ai#{len(self.prompts)}"


def fake_clone(url, dest, timeout, ref="", sha=""):
    dest.mkdir(parents=True)
    (dest / "a.py").write_text("x = eval(input())\n" * 3)
    (dest / "b.js").write_text("console.log(1)\n")
    (dest / "node_modules").mkdir()
    (dest / "node_modules" / "c.js").write_text("skip")
    return sha or SHA


SEVS = ["critical", "high", "medium", "low", "info"]


def fake_scanner(name, src, t):
    # 6 findings per tool so Brief has to pick a top-10 out of 18
    return {"status": "ok", "findings": [make_finding(name, "sast", f"{name}.r{i}", SEVS[i % 5], "a.py", i + 1, "t", "d")
                                         for i in range(6)]}


class Kill(Exception):
    pass


def killing_scanner(after):
    n = {"c": 0}

    def fn(name, src, t):
        n["c"] += 1
        if n["c"] > after:
            raise Kill()
        return fake_scanner(name, src, t)
    return fn


def cfg(tmp_path, depth="quick", attempt=1, budget=1, sha=""):
    return WorkerConfig(review_id=RID, repo_url="https://github.com/a/b", depth=depth, attempt=attempt,
                        budget_no=budget, commit_sha=sha, reports_dir=str(tmp_path / "reports"),
                        work_dir=str(tmp_path / "work"), ai_provider="none")


def rdir(tmp_path):
    return tmp_path / "reports" / RID


def report(tmp_path):
    return json.loads((rdir(tmp_path) / "report.json").read_text())


def test_layout_and_contract_outputs(tmp_path):
    api = FakeAPI()
    assert Worker(cfg(tmp_path, "brief"), api, FakeAI(), fake_scanner, fake_clone).run() == "completed"
    a = attempt_dir(tmp_path / "reports", RID, 1, 1)
    assert a == rdir(tmp_path) / "attempts" / "1-1"
    assert (a / "checkpoint.json").is_file() and (a / PARTIAL).is_file() and (a / "logs").is_dir()
    cp = json.loads((a / "checkpoint.json").read_text())
    check("checkpoint.schema.json", cp)
    assert cp["stage"] == "done" and cp["commit_sha"] == SHA and cp["cursor"]["scanners_done"] == ["semgrep", "gitleaks", "trivy"]
    for line in (a / PARTIAL).read_text().splitlines():
        check("finding.schema.json", json.loads(line))
    prog = json.loads((rdir(tmp_path) / "progress.json").read_text())
    check("progress.schema.json", prog)
    assert prog["state"] == "completed" and prog["totals"]["files"] == 2 and prog["totals"]["done"] == 2
    rep = report(tmp_path)
    check("report.schema.json", rep)
    assert rep["attempt"] == 1 and rep["budget_no"] == 1 and rep["repo"]["commit_sha"] == SHA
    assert rep["provider"] == {"name": "openai", "model": "m", "kind": "cloud"}
    assert (rdir(tmp_path) / "report.md").is_file()
    assert api.calls[-1]["outcome"] == "success" and api.calls[-1]["progress"] == 100
    assert not list((tmp_path / "work").iterdir())  # source deleted


def test_brief_enriches_top10_by_severity(tmp_path):
    ai = FakeAI()
    Worker(cfg(tmp_path, "brief"), FakeAPI(), ai, fake_scanner, fake_clone).run()
    rep = report(tmp_path)
    enriched = [f for f in rep["findings"] if f.get("ai_explanation")]
    assert len(enriched) == 10 and len(rep["findings"]) == 18
    order = {s: i for i, s in enumerate(SEVS)}
    worst_plain = min(order[f["severity"]] for f in rep["findings"] if not f.get("ai_explanation"))
    assert max(order[f["severity"]] for f in enriched) <= worst_plain
    assert "x = eval" in ai.prompts[0]  # code context
    assert rep["executive_summary"] == "ai#11"
    assert len({f["id"] for f in rep["findings"]}) == len(rep["findings"])


def test_quick_summary_only(tmp_path):
    ai = FakeAI()
    Worker(cfg(tmp_path), FakeAPI(), ai, fake_scanner, fake_clone).run()
    assert len(ai.prompts) == 1 and report(tmp_path)["executive_summary"] == "ai#1"


def test_kill_and_resume_same_findings_no_duplicates(tmp_path):
    Worker(cfg(tmp_path, "quick", attempt=1, sha=SHA), FakeAPI(), None, fake_scanner, fake_clone).run()
    baseline = sorted(f["id"] for f in report(tmp_path)["findings"])
    (rdir(tmp_path) / "report.json").unlink()

    other = tmp_path / "other"
    c1 = cfg(other, "quick", attempt=1, sha=SHA)
    with pytest.raises(Kill):  # Job killed during gitleaks
        Worker(c1, FakeAPI(), None, killing_scanner(1), fake_clone).run()
    a1 = attempt_dir(other / "reports", RID, 1, 1)
    cp1 = json.loads((a1 / "checkpoint.json").read_text())
    assert cp1["cursor"]["scanners_done"] == ["semgrep"] and cp1["cursor"]["findings_offset"] == 6
    # torn write after the last committed offset must be ignored on resume
    with (a1 / PARTIAL).open("a") as f:
        f.write(json.dumps(fake_scanner("gitleaks", None, 0)["findings"][0]) + "\n{\"trunc")
    seen = []

    def counting(name, src, t):
        seen.append(name)
        return fake_scanner(name, src, t)
    assert Worker(cfg(other, "quick", attempt=2, sha=SHA), FakeAPI(), None, counting, fake_clone).run() == "completed"
    assert seen == ["gitleaks", "trivy"]  # semgrep not rerun
    ids = [f["id"] for f in json.loads((other / "reports" / RID / "report.json").read_text())["findings"]]
    assert sorted(ids) == baseline and len(ids) == len(set(ids))


def test_resume_requires_same_commit(tmp_path):
    with pytest.raises(Kill):
        Worker(cfg(tmp_path, attempt=1, sha=SHA), FakeAPI(), None, killing_scanner(1), fake_clone).run()
    seen = []

    def counting(name, src, t):
        seen.append(name)
        return fake_scanner(name, src, t)
    Worker(cfg(tmp_path, attempt=2, sha="c" * 40), FakeAPI(), None, counting, fake_clone).run()
    assert seen == ["semgrep", "gitleaks", "trivy"]


def test_resume_across_budget_and_skips_finished_stages(tmp_path):
    Worker(cfg(tmp_path, attempt=1, sha=SHA), FakeAPI(), None, fake_scanner, fake_clone).run()
    seen = []
    Worker(cfg(tmp_path, attempt=1, budget=2, sha=SHA), FakeAPI(), None,
           lambda n, s, t: seen.append(n), fake_clone).run()
    assert seen == [] and report(tmp_path)["budget_no"] == 2


def test_deep_pause_then_resume_per_file(tmp_path):
    ai = FakeAI()
    api = FakeAPI()
    c = cfg(tmp_path, "deep", sha=SHA)
    w = Worker(c, api, ai, fake_scanner, fake_clone)
    orig = w._beat

    def beat(stage, pct=None, current=None, **kw):
        orig(stage, pct, current, **kw)
        if stage == "ai" and w.cp.cursor["files_done"] == ["a.py"]:
            (rdir(tmp_path) / "PAUSE").touch()
    w._beat = beat
    assert w.run() == "paused"
    (rdir(tmp_path) / "PAUSE").unlink()
    ai2 = FakeAI()
    assert Worker(cfg(tmp_path, "deep", attempt=2, sha=SHA), FakeAPI(), ai2, fake_scanner, fake_clone).run() == "completed"
    assert "b.js" in ai2.prompts[0] and len(ai2.prompts) == 2  # only b.js + summary
    rep = report(tmp_path)
    check("report.schema.json", rep)
    assert {f["file"] for f in rep["findings"] if f["tool"] == "ai"} == {"a.py", "b.js"}


def test_progress_snapshot_mid_run(tmp_path):
    snaps = []
    w = Worker(cfg(tmp_path, "deep", sha=SHA), FakeAPI(), FakeAI(), fake_scanner, fake_clone)
    orig = w._beat

    def beat(stage, pct=None, current=None, **kw):
        orig(stage, pct, current, **kw)
        snaps.append(json.loads((rdir(tmp_path) / "progress.json").read_text()))
    w._beat = beat
    w.run()
    for s in snaps:
        check("progress.schema.json", s)
    scanning = [s for s in snaps if s["current_file"] == "a.py"]
    assert scanning and {f["path"]: f["status"] for f in scanning[0]["files"]}["a.py"] == "scanning"
    pcts = [s for s in [json.loads(json.dumps(x)) for x in snaps]]
    assert pcts[-1]["state"] == "completed"


def test_ai_failure_still_reports(tmp_path):
    assert Worker(cfg(tmp_path, "brief"), FakeAPI(), FakeAI(fail=True), fake_scanner, fake_clone).run() == "completed"
    rep = report(tmp_path)
    assert rep["executive_summary"] is None and not any(f.get("ai_explanation") for f in rep["findings"])
    assert (attempt_dir(tmp_path / "reports", RID, 1, 1) / "logs" / "ai.log").read_text()


def test_superseded_stops_and_cleans(tmp_path):
    assert Worker(cfg(tmp_path), FakeAPI(supersede_after=2), None, fake_scanner, fake_clone).run() == "superseded"
    assert not (rdir(tmp_path) / "report.json").exists() and not list((tmp_path / "work").iterdir())


def test_clone_failure_cleans_up(tmp_path):
    def bad_clone(*a):
        raise RuntimeError("no repo")
    with pytest.raises(RuntimeError):
        Worker(cfg(tmp_path), FakeAPI(), None, fake_scanner, bad_clone).run()
    assert not list((tmp_path / "work").iterdir())


def test_list_code_files(tmp_path):
    fake_clone("", tmp_path / "r", 1)
    assert list_code_files(tmp_path / "r", 10, 1000) == ["a.py", "b.js"]
    assert list_code_files(tmp_path / "r", 1, 1000) == ["a.py"]


def test_make_ai(tmp_path):
    c = cfg(tmp_path)
    assert make_ai(c) is None
    c.ai_provider, c.ai_model, c.ai_base_url = "ollama", "llama3", "http://ollama:11434/v1"
    ai = make_ai(c)
    assert ai.base_url == "http://ollama:11434/v1" and ai.is_cloud  # no AI_KIND -> cloud (safe side)
    c.ai_kind = "onprem"
    assert not make_ai(c).is_cloud
    c.ai_provider, c.ai_model, c.ai_base_url, c.ai_kind = "anthropic", "", "", "cloud"
    assert make_ai(c).model and make_ai(c).is_cloud
    c.ai_provider = "bedrock"  # removed adapter -> AI skipped, scan continues
    assert make_ai(c) is None


def test_checkpoint_atomic(tmp_path):
    atomic_write_json(tmp_path / "x.json", {"a": 1})
    assert json.loads((tmp_path / "x.json").read_text()) == {"a": 1} and len(list(tmp_path.iterdir())) == 1
    cp = Checkpoint(tmp_path / "a", review_id=RID, attempt=1, budget_no=1, depth="quick")
    cp.save()
    check("checkpoint.schema.json", json.loads(cp.path.read_text()))


def test_api_client(tmp_path):
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers["x-internal-token"], json.loads(req.content)))
        return httpx.Response(409 if len(seen) == 3 else 500 if len(seen) == 2 else 200, json={})
    api = ReviewAPI("http://ra:8001/", "tok", transport=httpx.MockTransport(handler))
    api.report(RID, {"attempt": 1})
    api.report(RID, {"attempt": 1})  # 500 is logged, not raised
    from scanner.api_client import Superseded
    with pytest.raises(Superseded):
        api.report(RID, {"attempt": 1})
    assert seen[0][:2] == (f"/internal/reviews/{RID}/progress", "tok")


def test_main_env_and_attempt_error(tmp_path, monkeypatch):
    for k, v in {"REVIEW_ID": RID, "ATTEMPT": "3", "BUDGET_NO": "2", "DEPTH": "quick", "COMMIT_SHA": SHA,
                 "REPO_URL": "https://github.com/a/b", "REPORTS_DIR": str(tmp_path / "r"),
                 "WORK_DIR": str(tmp_path / "w"), "AI_PROVIDER": "none"}.items():
        monkeypatch.setenv(k, v)
    calls = []
    monkeypatch.setattr(runmod, "ReviewAPI", lambda *a: type("A", (), {"report": lambda s, r, b: calls.append(b)})())
    monkeypatch.setattr("scanner.health.start_health_server", lambda port: None)
    monkeypatch.setattr(runmod, "clone", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(Worker.__init__, "__defaults__", (runmod.run_scanner, runmod.clone))
    assert runmod.main([]) == 1
    assert calls[-1]["outcome"] == "error" and calls[-1]["attempt"] == 3 and calls[-1]["budget_no"] == 2
    assert "boom" in calls[-1]["error"]


def test_pause_resume_same_attempt_dir(tmp_path):
    with pytest.raises(Kill):  # Job deleted by pause while running gitleaks
        Worker(cfg(tmp_path, attempt=1, sha=SHA), FakeAPI(), None, killing_scanner(1), fake_clone).run()
    seen = []

    def counting(name, src, t):
        seen.append(name)
        return fake_scanner(name, src, t)
    assert Worker(cfg(tmp_path, attempt=1, sha=SHA), FakeAPI(), None, counting, fake_clone).run() == "completed"
    assert seen == ["gitleaks", "trivy"] and len(report(tmp_path)["findings"]) == 18


@pytest.mark.parametrize("case", ["completed", "paused", "crashed", "superseded"])
def test_work_dir_empty_after_every_outcome(tmp_path, case):
    """Step 5: the clone under /work never outlives the run (success, pause, failure/cancel kill, supersede)."""
    api = FakeAPI(supersede_after=2 if case == "superseded" else None)
    scan = killing_scanner(1) if case == "crashed" else fake_scanner
    if case == "paused":
        rdir(tmp_path).mkdir(parents=True)
        (rdir(tmp_path) / "PAUSE").touch()
    w = Worker(cfg(tmp_path, "deep", sha=SHA), api, FakeAI(), scan, fake_clone)
    if case == "crashed":
        with pytest.raises(Kill):
            w.run()
    else:
        assert w.run() == case
    assert not list((tmp_path / "work").iterdir())


def test_sigterm_unwinds_and_cleans_work(tmp_path):
    def term_scanner(name, src, t):
        runmod._on_sigterm(15, None)
    with pytest.raises(SystemExit) as e:
        Worker(cfg(tmp_path, sha=SHA), FakeAPI(), None, term_scanner, fake_clone).run()
    assert e.value.code == 143 and not list((tmp_path / "work").iterdir())
