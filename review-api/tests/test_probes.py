from pathlib import Path

import yaml
from prometheus_client import REGISTRY

import shutil
import subprocess

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def _val(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_healthz_readyz_metrics(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    body = client.get("/metrics").text
    for m in ("reviews_total", "scan_attempts_total", "job_launch_errors_total", "http_request_duration_seconds"):
        assert m in body
    assert 'route="/healthz"' in body


def test_readyz_fails_when_k8s_api_down(client):
    class Down:
        def ready(self):
            raise RuntimeError("no api")
    client.app.state.deps["launcher"].inner.ready = Down().ready
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["error"]["code"] == "INTERNAL"


def test_kubernetes_launcher_ready_lists_jobs():
    from app.config import Settings
    from app.launcher import KubernetesJobLauncher
    calls = []

    class Batch:
        def list_namespaced_job(self, namespace, limit):
            calls.append((namespace, limit))
    KubernetesJobLauncher(Settings(k8s_namespace="ns"), batch_api=Batch(), core_api=object()).ready()
    assert calls == [("ns", 1)]


def test_review_counters_move(client, H):
    before = _val("reviews_total", state="queued")
    assert client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "quick"}, headers=H).status_code == 201
    assert _val("reviews_total", state="queued") == before + 1
    assert _val("reviews_total", state="running") >= 1


def test_launch_error_counted():
    from app.jobs import ReviewJobs

    class Boom:
        def launch(self, *a, **k):
            raise RuntimeError("x")
    before = _val("job_launch_errors_total")
    r = type("R", (), {"id": "x", "attempt": 1, "budget_no": 1, "depth": "quick", "repo_url": "u", "ref": None,
                       "commit_sha": "a" * 40, "provider_id": None})()
    try:
        ReviewJobs(Boom(), None).launch(r)
    except RuntimeError:
        pass
    assert _val("job_launch_errors_total") == before + 1


def _job_container():
    cm = yaml.safe_load((DEPLOY / "scanner-worker" / "base" / "scanner-job-template.yaml").read_text())
    return yaml.safe_load(cm["data"]["job.yaml"])["spec"]["template"]["spec"]["containers"][0]


def _check(c, port):
    assert c["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert c["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert {p["containerPort"] for p in c["ports"]} == {port}


def test_base_probe_paths_and_ports():
    for svc, port in {"gateway": 8000, "review-api": 8001}.items():
        d = yaml.safe_load((DEPLOY / svc / "base" / "deployment.yaml").read_text())
        _check(d["spec"]["template"]["spec"]["containers"][0], port)
    _check(_job_container(), 8002)


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize not installed")
@pytest.mark.parametrize("overlay", ["local", "k3s", "eks"])
@pytest.mark.parametrize("svc,port", [("gateway", 8000), ("review-api", 8001)])
def test_overlay_probe_paths(svc, port, overlay):
    path = DEPLOY / svc / "overlays" / overlay
    if not path.exists():
        pytest.skip("no overlay")
    out = subprocess.run(["kustomize", "build", str(path)], capture_output=True, text=True, check=True).stdout
    deps = [d for d in yaml.safe_load_all(out) if d and d["kind"] == "Deployment"]
    assert deps
    for d in deps:
        _check(d["spec"]["template"]["spec"]["containers"][0], port)
