"""Grok (xAI, OpenAI-compatible) as a *cloud* provider through the openai_compatible adapter, end to end:
review-api (ack_cloud gate, cloud_warning, encrypted key, AI_* env) -> real worker -> mock xAI endpoint."""
import importlib.util
import json
from pathlib import Path

import pytest

from app.providers import adapter_name
from contract import check
from phase3_harness import ROOT, URL, WORKER_PY, Stack

pytestmark = pytest.mark.skipif(not WORKER_PY or not Path(WORKER_PY).exists(), reason="AISCAN_WORKER_PYTHON not set")
KEY = "xai-TEST-key-0123456789abcdefSECRET"

_spec = importlib.util.spec_from_file_location("mock_ai", ROOT / "scanner-worker" / "tests" / "mock_ai.py")
mock_ai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mock_ai)


def test_xai_host_maps_to_openai_compatible():
    assert adapter_name("https://api.x.ai/v1", "cloud") == "openai_compatible"


@pytest.mark.parametrize("depth", ["quick", "brief"])
def test_grok_cloud_via_openai_compatible_end_to_end(tmp_path, monkeypatch, depth):
    s = Stack(tmp_path, monkeypatch)
    try:
        with mock_ai.MockAI() as m:
            p = s.http.post("/admin/ai-providers", headers=s.H, json={
                "name": "grok", "kind": "cloud", "base_url": f"{m.url}/v1", "model": "grok-4", "api_key": KEY})
            assert p.status_code == 201, p.text
            check("AIProvider", p.json())
            pid = p.json()["id"]
            assert KEY not in p.text
            pub = {x["id"]: x for x in s.http.get("/ai-providers", headers=s.H).json()}
            assert pub[pid]["cloud_warning"] is True and pub[pid]["kind"] == "cloud"

            body = {"repo_url": URL, "depth": depth, "provider_id": pid}
            r = s.http.post("/reviews", json=body, headers=s.H)
            assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"
            check("Error", r.json())
            assert s.launcher.launched == []  # nothing ran without the acknowledgement
            r = s.http.post("/reviews", json={**body, "ack_cloud": True}, headers=s.H)
            assert r.status_code == 201, r.text
            rev = r.json()
            check("Review", rev)
            assert rev["provider"] == {"id": pid, "kind": "cloud", "cloud_warning": True}
            s.track()
            done = s.wait_state(rev["id"], "completed")
            assert done["provider"]["cloud_warning"] is True

        assert m.requests, "worker never called the AI endpoint"
        for req in m.requests:
            assert req["path"] == "/v1/chat/completions"
            assert req["headers"]["authorization"] == f"Bearer {KEY}"
            assert KEY not in req["path"] and KEY not in req["body"]
            b = json.loads(req["body"])
            assert b["model"] == "grok-4"
            assert [x["role"] for x in b["messages"]] == ["system", "user"]
        rep = s.report(rev["id"])
        # quick: one summary call; brief: one call per finding (6 < top 10) + summary
        assert len(m.requests) == (1 if depth == "quick" else len(rep["findings"]) + 1)
        check("report.schema.json", rep)
        assert rep["provider"]["kind"] == "cloud" and rep["provider"]["name"] == "grok"
        md = s.http.get(f"/reviews/{rev['id']}/report.md", headers=s.H).text
        assert "cloud" in md.lower()
        # the key never lands on disk in clear: reports volume, DB file, shim logs
        for f in tmp_path.rglob("*"):
            if f.is_file():
                assert KEY.encode() not in f.read_bytes(), f"key leaked into {f}"
    finally:
        s.close()
