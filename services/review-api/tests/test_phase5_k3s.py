"""Phase 5 (K3s): config only from env with a production guard, probes, and the rendered overlays."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "t" * 40
KEY = "00" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Settings read the process env: a shell with e.g. INTERNAL_API_TOKEN set must not change these tests."""
    from app.config import Settings
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_dev_defaults_are_fine_outside_production():
    from app.config import Settings
    assert Settings().problems() == []


def test_production_rejects_dev_defaults():
    from app.config import Settings
    bad = Settings(app_env="production").problems()
    text = " ".join(bad)
    for k in ("DATABASE_URL", "INTERNAL_API_TOKEN", "AI_KEYS_ENCRYPTION_KEY", "JOB_LAUNCHER"):
        assert k in text


def test_production_ok_and_pinned_image(monkeypatch):
    from app.config import Settings
    good = dict(app_env="production", database_url="postgresql+psycopg://u:p@postgres/aiscan", internal_api_token=TOKEN,
                ai_keys_encryption_key=KEY, job_launcher="kubernetes", scanner_image="reg:5000/ai-scan/scanner-worker:abc123")
    assert Settings(**good).problems() == []
    assert "SCANNER_IMAGE" in " ".join(Settings(**{**good, "scanner_image": "reg/ai-scan/scanner-worker:latest"}).problems())
    assert "SCANNER_IMAGE" in " ".join(Settings(**{**good, "scanner_image": "reg:5000/ai-scan/scanner-worker"}).problems())


def test_settings_read_from_env(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    s = Settings()
    assert s.app_env == "production" and s.internal_api_token == TOKEN


def test_get_app_exits_on_bad_production_config(monkeypatch):
    from app import config, main
    monkeypatch.setenv("APP_ENV", "production")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="invalid production config"):
            main.get_app()
    finally:
        config.get_settings.cache_clear()


def _render(component, overlay="k3s"):
    if not shutil.which("kustomize"):
        pytest.skip("kustomize not installed")
    out = subprocess.run(["kustomize", "build", str(ROOT / "deploy" / component / "overlays" / overlay)],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _container(docs, name):
    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == name)
    return dep["spec"]["template"]["spec"], dep["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("overlay", ["k3s", "local", "eks"])
@pytest.mark.parametrize("name", ["review-api", "gateway"])
def test_backend_pods_run_in_production_mode_with_probes(name, overlay):
    _, c = _container(_render(name, overlay), name)
    env = {e["name"]: e for e in c["env"]}
    assert env["APP_ENV"]["value"] == "production"
    assert env["INTERNAL_API_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "scanner-secrets", "key": "INTERNAL_API_TOKEN"}
    # least privilege: the AI_* defaults in scanner-secrets are never loaded wholesale into the API pods
    assert {"secretRef": {"name": "scanner-secrets"}} not in c.get("envFrom", [])
    assert c["startupProbe"]["httpGet"]["path"] == "/healthz"
    assert c["livenessProbe"]["httpGet"]["path"] == "/healthz"   # liveness never depends on the DB
    assert c["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert ":latest" not in c["image"]


def test_gateway_env_matches_settings():
    spec, c = _container(_render("gateway"), "gateway")
    env = {e["name"]: e for e in c["env"]}
    assert env["PUBLIC_URL"]["value"].startswith("https://") and env["COOKIE_SECURE"]["value"] == "true"
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "db-secrets"
    assert spec["automountServiceAccountToken"] is False
    _, eks = _container(_render("gateway", "eks"), "gateway")
    assert {e["name"]: e.get("value") for e in eks["env"]}["PUBLIC_URL"] == "https://ai-scan.example.com"


def test_frontend_probes_hit_nginx_health():
    spec, c = _container(_render("frontend"), "frontend")
    assert c["livenessProbe"]["httpGet"]["path"] == c["readinessProbe"]["httpGet"]["path"] == "/nginx-health"
    assert "location = /nginx-health" in (ROOT / "frontend" / "nginx.conf").read_text()
    assert spec["automountServiceAccountToken"] is False


def test_rendered_overlays_pass_kubeconform():
    if not (shutil.which("kustomize") and shutil.which("kubeconform")):
        pytest.skip("kustomize/kubeconform not installed")
    r = subprocess.run([str(ROOT / "ci" / "kubeconform.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
