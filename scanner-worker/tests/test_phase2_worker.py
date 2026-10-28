"""Phase 2: worker against a real mock AI HTTP server, clone-dir cleanup, Quick/Brief/Deep depth behaviour."""
import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from scanner import run as runmod
from scanner.checkpoint import attempt_dir
from scanner.run import Worker, make_ai
from tests.contract import check
from tests.mock_ai import MockAI
from tests.phase2_util import all_text
from tests.test_worker import RID, FakeAPI, cfg, fake_clone, fake_scanner

KEY = "sk-test-SUPERSECRET-0123456789abcdef"
PROVIDERS = [("openai_compatible", "/chat/completions"), ("ollama", "/chat/completions"),
             ("azure_openai", "/chat/completions"), ("openai", "/chat/completions"),
             ("groq", "/chat/completions"), ("mistral", "/chat/completions"), ("anthropic", "/messages"),
             ("gemini", ":generateContent")]


ONPREM_ROWS = ("openai_compatible", "ollama")  # kind of the (simulated) DB provider row, sent as AI_KIND


def ai_cfg(tmp_path, mock, provider="openai_compatible", depth="quick", **kw):
    kw.setdefault("ai_kind", "onprem" if provider in ONPREM_ROWS else "cloud")
    return dataclasses.replace(cfg(tmp_path, depth), ai_provider=provider, ai_model="m", ai_base_url=mock.url,
                               ai_api_key=KEY, **kw)


def rdir(c):
    return Path(c.reports_dir) / RID


def report(c):
    return json.loads((rdir(c) / "report.json").read_text())


def run(c, scanner=fake_scanner, clone=fake_clone, api=None):
    w = Worker(c, api or FakeAPI(), make_ai(c), scanner_fn=scanner, clone_fn=clone)
    return w, w.run()


def assert_no_key(c, api):
    assert KEY not in all_text(rdir(c)), "API key leaked into reports dir"
    assert KEY not in json.dumps(api.calls), "API key leaked into progress callbacks"


# ---- mock AI server ------------------------------------------------------------------------------
@pytest.mark.parametrize("provider,suffix", PROVIDERS)
def test_worker_uses_mock_ai_for_every_provider_kind(tmp_path, provider, suffix):
    with MockAI() as m:
        c, api = ai_cfg(tmp_path, m, provider), FakeAPI()
        _, out = run(c, api=api)
    assert out == "completed" and len(m.requests) == 1 and m.requests[0]["path"].split("?")[0].endswith(suffix)
    rep = report(c)
    check("report.schema.json", rep)
    assert rep["executive_summary"] == "mock answer #1"
    assert rep["provider"]["kind"] == ("onprem" if provider in ("openai_compatible", "ollama") else "cloud")
    h = m.requests[0]["headers"]
    sent = {"anthropic": h.get("x-api-key"), "gemini": h.get("x-goog-api-key"),
            "azure_openai": h.get("api-key")}.get(provider, h.get("authorization"))
    assert sent in (KEY, f"Bearer {KEY}")
    assert KEY not in m.requests[0]["path"] and KEY not in m.requests[0]["body"]  # only in the auth header
    assert_no_key(c, api)


@pytest.mark.parametrize("code", [400, 401, 429, 500, 503])
def test_ai_http_error_still_reports_and_logs_without_key(tmp_path, code):
    with MockAI(plan=[code] * 50) as m:
        c, api = ai_cfg(tmp_path, m, "openai_compatible", "brief"), FakeAPI()
        _, out = run(c, api=api)
    assert out == "completed"
    rep = report(c)
    assert rep["executive_summary"] is None and not any(f.get("ai_explanation") for f in rep["findings"])
    log = (attempt_dir(c.reports_dir, RID, 1, 1) / "logs" / "ai.log").read_text()
    assert str(code) in log
    assert api.calls[-1]["outcome"] == "success"
    assert_no_key(c, api)


def test_ai_unreachable_server_still_reports(tmp_path):
    with MockAI() as m:
        url = m.url
    c = dataclasses.replace(ai_cfg(tmp_path, m), ai_base_url=url)  # server already closed
    _, out = run(c)
    assert out == "completed" and report(c)["executive_summary"] is None


@pytest.mark.parametrize("code", [429, 503])
def test_ai_transient_error_is_retried(tmp_path, code):
    with MockAI(plan=[code]) as m:
        c = ai_cfg(tmp_path, m)
        run(c)
    assert report(c)["executive_summary"] is not None


def test_key_not_leaked_on_attempt_error(tmp_path):
    def boom(name, src, t):
        raise RuntimeError("scanner crashed")
    with MockAI() as m:
        c, api = ai_cfg(tmp_path, m), FakeAPI()
        with pytest.raises(RuntimeError):
            run(c, scanner=boom, api=api)
    assert_no_key(c, api)


# ---- clone dir cleanup ---------------------------------------------------------------------------
@pytest.fixture
def local_repo(tmp_path):
    r = tmp_path / "origin"
    r.mkdir()
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(g[:3] + ["init", "-q"], check=True)
    (r / "app.py").write_text("import os\nos.system(input())\n")
    subprocess.run(g[:3] + ["add", "."], check=True)
    subprocess.run(g + ["commit", "-qm", "init"], check=True)
    return f"file://{r}"


def _work_empty(c):
    w = Path(c.work_dir)
    return not w.exists() or list(w.iterdir()) == []


def test_real_clone_dir_removed_after_success(tmp_path, local_repo):
    c = dataclasses.replace(cfg(tmp_path), repo_url=local_repo)
    seen = {}

    def scanner(name, src, t):
        seen["src"] = src
        assert (src / "app.py").is_file()  # the real clone happened
        return fake_scanner(name, src, t)
    w = Worker(c, FakeAPI(), None, scanner_fn=scanner)  # real runmod.clone
    assert w.run() == "completed"
    assert not seen["src"].exists() and _work_empty(c)


@pytest.mark.parametrize("failure", ["scanner_raises", "ai_raises", "report_raises", "superseded", "paused"])
def test_clone_dir_removed_after_failure(tmp_path, local_repo, failure, monkeypatch):
    c = dataclasses.replace(cfg(tmp_path, "brief"), repo_url=local_repo)
    api, ai, scanner = FakeAPI(), None, fake_scanner
    if failure == "scanner_raises":
        def scanner(name, src, t):
            raise RuntimeError("boom")
    elif failure == "ai_raises":
        class AI:
            provider, model = "openai", "m"

            def complete(self, p):
                raise KeyboardInterrupt  # not caught by _ask's `except Exception`
        ai = AI()
    elif failure == "report_raises":
        monkeypatch.setattr(runmod, "write_report", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    elif failure == "superseded":
        api = FakeAPI(supersede_after=2)
    elif failure == "paused":
        rdir(c).mkdir(parents=True)
        (rdir(c) / "PAUSE").write_text("")
    w = Worker(c, api, ai, scanner_fn=scanner)
    try:
        w.run()
    except BaseException:  # noqa: BLE001
        pass
    assert _work_empty(c)


# ---- depth ---------------------------------------------------------------------------------------
def stages(api):
    out = []
    for b in api.calls:
        if not out or out[-1] != b["stage"]:
            out.append(b["stage"])
    return out


def run_depth(tmp_path, depth, **kw):
    with MockAI() as m:
        c, api = ai_cfg(tmp_path, m, "openai_compatible", depth, **kw), FakeAPI()
        _, out = run(c, api=api)
    assert out == "completed"
    rep = report(c)
    check("report.schema.json", rep)
    return m, api, rep


@pytest.mark.parametrize("depth", ["quick", "brief", "deep"])
def test_every_depth_runs_all_scanners_in_order(tmp_path, depth):
    _, api, rep = run_depth(tmp_path, depth)
    assert stages(api) == ["clone", "semgrep", "gitleaks", "trivy", "ai", "report", "done"]
    assert {f["tool"] for f in rep["findings"]} >= {"semgrep", "gitleaks", "trivy"}
    assert rep["depth"] == depth


def test_quick_one_summary_call_top30_no_enrichment(tmp_path):
    m, _, rep = run_depth(tmp_path, "quick")
    assert len(m.requests) == 1
    prompt = json.loads(m.requests[0]["body"])["messages"][-1]["content"]
    assert "summary" in prompt
    assert all(f["tool"] != "ai" and not f.get("ai_explanation") for f in rep["findings"])
    assert rep["executive_summary"]


def test_brief_enriches_top10_then_summarises(tmp_path):
    m, _, rep = run_depth(tmp_path, "brief")
    assert len(m.requests) == 10 + 1  # 18 scanner findings -> top 10 + summary
    enriched = [f for f in rep["findings"] if f.get("ai_explanation")]
    assert len(enriched) == 10 and not any(f["tool"] == "ai" for f in rep["findings"])
    order = ["critical", "high", "medium", "low", "info"]
    worst_plain = min((order.index(f["severity"]) for f in rep["findings"] if not f.get("ai_explanation")))
    assert max(order.index(f["severity"]) for f in enriched) <= worst_plain


def test_deep_reviews_each_code_file_within_limits(tmp_path):
    def clone(url, dest, timeout, ref="", sha=""):
        fake_clone(url, dest, timeout, ref, sha)
        (dest / "big.py").write_text("x = 1\n" * 20000)  # > deep_max_file_bytes
        for i in range(5):
            (dest / f"m{i}.py").write_text("pass\n")
        return sha or "b" * 40
    with MockAI() as m:
        c = ai_cfg(tmp_path, m, "openai_compatible", "deep", deep_max_files=6, deep_max_file_bytes=60000)
        _, out = run(c, clone=clone)
    rep = report(c)
    ai = [f for f in rep["findings"] if f["tool"] == "ai"]
    files = {f["file"] for f in ai}
    assert "big.py" not in files and "node_modules/c.js" not in files
    assert 1 <= len(ai) <= 6 and all(f["category"] == "ai_review" for f in ai)
    assert len(m.requests) == len(ai) + 1  # per-file + summary
    assert rep["executive_summary"]


def test_depth_output_differs_but_scanner_findings_identical(tmp_path):
    reps = {d: run_depth(tmp_path / d, d)[2] for d in ("quick", "brief", "deep")}
    scan = {d: sorted(f["id"] for f in r["findings"] if f["tool"] != "ai") for d, r in reps.items()}
    assert scan["quick"] == scan["brief"] == scan["deep"]
    sig = {d: (sum(1 for f in r["findings"] if f["tool"] == "ai"),
               sum(1 for f in r["findings"] if f.get("ai_explanation") and f["tool"] != "ai")) for d, r in reps.items()}
    assert sig["quick"] == (0, 0) and sig["brief"] == (0, 10) and sig["deep"][0] > 0
    for r in reps.values():
        for s in ("critical", "high", "medium", "low", "info"):
            assert r["summary"][s] == sum(1 for f in r["findings"] if f["severity"] == s)


@pytest.mark.parametrize("depth", ["quick", "brief", "deep"])
def test_no_ai_provider_same_scanner_output_any_depth(tmp_path, depth):
    c = cfg(tmp_path, depth)
    _, out = run(c)
    rep = report(c)
    assert out == "completed" and rep["executive_summary"] is None
    assert not any(f["tool"] == "ai" or f.get("ai_explanation") for f in rep["findings"])
