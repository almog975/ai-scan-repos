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
    r = client.get("/readyz").json()
    assert r["status"] in ("ready", "degraded") and r["checks"]["db"] == "ok" and r["checks"]["kube"] == "ok"
    body = client.get("/metrics").text
    for m in ("reviews_total", "scan_attempts_total", "job_launch_errors_total", "http_request_duration_seconds"):
        assert m in body
    assert 'route="/healthz"' in body


def _kube_down(client):
    def ready():
        raise RuntimeError("Connection refused: 10.43.0.1:443")
    client.app.state.deps["launcher"].inner.ready = ready


def test_readyz_stays_ready_when_k8s_api_down(client):
    _kube_down(client)
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded" and r.json()["checks"]["kube"] == "unavailable"


def test_readyz_can_require_kube(client):
    _kube_down(client)
    client.app.state.settings.readyz_require_kube = True
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["error"]["code"] == "INTERNAL"


def test_readyz_fails_when_db_down(client):
    from app import db as dbmod
    class Bad:
        def execute(self, *a, **k):
            raise RuntimeError("db down")
    client.app.dependency_overrides[dbmod.get_db] = lambda: Bad()
    try:
        assert client.get("/readyz").status_code == 503
    finally:
        client.app.dependency_overrides.pop(dbmod.get_db)


def test_kubernetes_launcher_ready_gets_a_job():
    from app.config import Settings
    from app.launcher import KubernetesJobLauncher
    calls = []

    class Batch:
        def read_namespaced_job(self, name, namespace):
            calls.append((namespace, name))
            raise type("E", (Exception,), {"status": 404})()
    KubernetesJobLauncher(Settings(k8s_namespace="ns"), batch_api=Batch(), core_api=object()).ready()
    assert calls == [("ns", "readyz-probe")]


def test_review_counters_move(client, H):
    from tests.test_control import beat
    before, run_before = _val("reviews_total", state="queued"), _val("reviews_total", state="running")
    r = client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "quick"}, headers=H)
    assert r.status_code == 201
    assert _val("reviews_total", state="queued") == before + 1
    beat(client, r.json()["id"])  # first heartbeat: queued -> running (test no longer depends on test order)
    assert _val("reviews_total", state="running") == run_before + 1


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


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize not installed")
@pytest.mark.parametrize("overlay", ["local", "k3s", "eks"])
def test_migrations_job_uses_deployment_image(overlay):
    out = subprocess.run(["kustomize", "build", str(DEPLOY / "review-api" / "overlays" / overlay)],
                         capture_output=True, text=True, check=True).stdout
    docs = {d["kind"]: d for d in yaml.safe_load_all(out) if d}
    job, dep = docs["Job"], docs["Deployment"]
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    (jc,), (dc,) = job["spec"]["template"]["spec"]["containers"], dep["spec"]["template"]["spec"]["containers"]
    assert jc["image"] == dc["image"] and jc["args"] == ["alembic", "upgrade", "head"]
    assert "alembic" not in " ".join(dc.get("args", []))


def test_dockerfile_does_not_migrate():
    cmd = [l for l in (DEPLOY.parent / "review-api" / "Dockerfile").read_text().splitlines() if l.startswith("CMD")]
    assert cmd and "alembic" not in cmd[0]


def test_rbac_is_least_privilege_and_launcher_stays_within_it():
    """Role allows jobs create/get/delete and secrets create/update/delete; the launcher must not need more."""
    import re
    docs = [d for d in yaml.safe_load_all((DEPLOY / "review-api" / "base" / "rbac.yaml").read_text()) if d]
    (role,) = [d for d in docs if d["kind"] == "Role"]
    rules = {(r["apiGroups"][0], tuple(r["resources"])): set(r["verbs"]) for r in role["rules"]}
    assert rules == {("batch", ("jobs",)): {"create", "get", "delete"},
                     ("", ("secrets",)): {"create", "update", "delete"}}
    src = (Path(__file__).resolve().parents[1] / "app" / "launcher.py").read_text()
    calls = set(re.findall(r"self\.(?:api|core)\.(\w+)\(", src))
    assert calls == {"create_namespaced_job", "read_namespaced_job", "delete_namespaced_job",
                     "create_namespaced_secret", "replace_namespaced_secret", "delete_namespaced_secret"}, calls
