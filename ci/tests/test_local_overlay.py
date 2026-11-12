"""infra/k8s/overlays/local: one-shot kubectl apply -k for Docker Desktop / k3d (no registry, no NetworkPolicies)."""
import pathlib, shutil, subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "infra/k8s/overlays/local"
BASE_NP = ROOT / "infra/k8s/platform/base/networkpolicies.yaml"


def build(path=OVERLAY):
    return [d for d in yaml.safe_load_all(
        subprocess.run(["kustomize", "build", str(path)], capture_output=True, text=True, check=True).stdout
    ) if d]


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize")
def test_local_overlay_renders_clean():
    docs = build()
    text = subprocess.run(["kustomize", "build", str(OVERLAY)], capture_output=True, text=True, check=True).stdout
    assert "GIT_SHA_PLACEHOLDER" not in text
    assert "registry.registry:5000" not in text
    assert "k3d-ai-scan-registry" not in text
    assert not any(d["kind"] == "NetworkPolicy" for d in docs)

    secrets = {d["metadata"]["name"] for d in docs if d["kind"] == "Secret"}
    assert {"db-secrets", "ai-keys-master", "gateway-secrets", "scanner-secrets"} <= secrets
    for d in docs:
        if d["kind"] == "Secret":
            assert d["metadata"].get("annotations", {}).get("ai-scan.local/not-for-production") == "true"

    imgs = []
    for d in docs:
        if d["kind"] in ("Deployment", "Job"):
            for c in d["spec"]["template"]["spec"].get("initContainers", []) + d["spec"]["template"]["spec"]["containers"]:
                if c["image"].startswith("ai-scan-repos-"):
                    imgs.append(c["image"])
                    assert c.get("imagePullPolicy") == "IfNotPresent", (d["metadata"]["name"], c["name"], c)
    assert "ai-scan-repos-review-api:latest" in imgs
    assert "ai-scan-repos-gateway:latest" in imgs
    assert "ai-scan-repos-frontend:latest" in imgs

    cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "scanner-image")
    assert cm["data"]["SCANNER_IMAGE"] == "ai-scan-repos-scanner-worker:latest"

    job = next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"] == "review-api-migrate")
    assert job["metadata"]["labels"]["app"] == "review-api-migrate"
    assert job["spec"]["template"]["metadata"]["labels"]["app"] == "review-api-migrate"
    assert job["spec"]["template"]["spec"]["containers"][0]["args"] == ["alembic", "upgrade", "head"]
    assert any(r.get("secretRef", {}).get("name") == "db-secrets"
               for r in job["spec"]["template"]["spec"]["containers"][0].get("envFrom", []))

    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "review-api")
    assert any(c["name"] == "migrate" for c in dep["spec"]["template"]["spec"].get("initContainers", []))


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize")
def test_base_postgres_netpol_covers_migrate():
    """Strict base/prod path: review-api-migrate may reach postgres (egress + ingress)."""
    docs = build(ROOT / "infra/k8s/platform/overlays/k3s")
    # also need a workload with migrate labels from review-api overlay
    docs += build(ROOT / "infra/k8s/review-api/overlays/k3s")
    nps = [d for d in docs if d["kind"] == "NetworkPolicy"]
    ingress = next(p for p in nps if p["metadata"]["name"] == "postgres-ingress")
    egress = next(p for p in nps if p["metadata"]["name"] == "postgres-clients-egress")
    dns = next(p for p in nps if p["metadata"]["name"] == "allow-dns-egress")

    from_labels = []
    for rule in ingress["spec"]["ingress"]:
        for peer in rule.get("from", []):
            if "podSelector" in peer and "matchLabels" in peer["podSelector"]:
                from_labels.append(peer["podSelector"]["matchLabels"])
    assert {"app": "review-api-migrate"} in from_labels
    assert {"app": "review-api"} in from_labels

    expr = egress["spec"]["podSelector"]["matchExpressions"][0]
    assert expr["key"] == "app" and "review-api-migrate" in expr["values"] and "review-api" in expr["values"]

    assert dns["spec"]["podSelector"] == {}
    ports = {(p["protocol"], p["port"]) for r in dns["spec"]["egress"] for p in r["ports"]}
    assert ("UDP", 53) in ports and ("TCP", 53) in ports

    job = next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"] == "review-api-migrate")
    assert job["spec"]["template"]["metadata"]["labels"]["app"] == "review-api-migrate"


def test_gitattributes_and_installer_crlf():
    ga = (ROOT / ".gitattributes").read_text()
    assert "eol=lf" in ga and "*.sh" in ga and "*.yaml" in ga and "Makefile" in ga
    sh = (ROOT / "infra/bootstrap/installer/aiscan-install.sh").read_text()
    assert r'grep -qE "^  - name: ai-scan/$c\r?$"' in sh


def test_secret_placeholders_marked_not_for_production():
    readme = (OVERLAY / "secrets/README.md").read_text()
    assert "NOT FOR PRODUCTION" in readme
    db = (OVERLAY / "secrets/db-secrets.env").read_text()
    assert "aiscan-local-dev-only" in db
    assert "postgresql+psycopg://aiscan:aiscan-local-dev-only@postgres:5432/aiscan" in db
