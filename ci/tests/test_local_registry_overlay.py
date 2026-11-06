"""deploy/overlays/local-registry: same as local, but images pull from ghcr.io."""
import pathlib, shutil, subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "deploy/overlays/local-registry"
WORKFLOW = ROOT / ".github/workflows/images.yml"

GHCR = {
    "ai-scan-repos-review-api": "ghcr.io/almog975/ai-scan-repos-review-api:latest",
    "ai-scan-repos-gateway": "ghcr.io/almog975/ai-scan-repos-gateway:latest",
    "ai-scan-repos-frontend": "ghcr.io/almog975/ai-scan-repos-frontend:latest",
    "scanner": "ghcr.io/almog975/ai-scan-repos-scanner-worker:latest",
}


def build(path=OVERLAY):
    return [d for d in yaml.safe_load_all(
        subprocess.run(["kustomize", "build", str(path)], capture_output=True, text=True, check=True).stdout
    ) if d]


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize")
def test_local_registry_overlay_uses_ghcr_images():
    docs = build()
    text = subprocess.run(["kustomize", "build", str(OVERLAY)], capture_output=True, text=True, check=True).stdout
    assert "GIT_SHA_PLACEHOLDER" not in text
    assert "registry.registry:5000" not in text
    assert "k3d-ai-scan-registry" not in text
    # still the lightweight local stack: no NetworkPolicies, no Traefik IngressRoute
    assert not any(d["kind"] == "NetworkPolicy" for d in docs)
    assert not any(d["kind"] == "IngressRoute" for d in docs)

    imgs = []
    for d in docs:
        if d["kind"] in ("Deployment", "Job"):
            for c in d["spec"]["template"]["spec"].get("initContainers", []) + d["spec"]["template"]["spec"]["containers"]:
                if "ai-scan-repos-" in c["image"]:
                    imgs.append(c["image"])
                    assert c["image"].startswith("ghcr.io/almog975/"), (d["metadata"]["name"], c["name"], c["image"])
                    assert c.get("imagePullPolicy") == "Always", (d["metadata"]["name"], c["name"], c)
    assert GHCR["ai-scan-repos-review-api"] in imgs
    assert GHCR["ai-scan-repos-gateway"] in imgs
    assert GHCR["ai-scan-repos-frontend"] in imgs
    # no bare local tags left on the app images
    assert not any(i.startswith("ai-scan-repos-") and not i.startswith("ghcr.io/") for i in imgs)

    cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "scanner-image")
    assert cm["data"]["SCANNER_IMAGE"] == GHCR["scanner"]

    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "review-api")
    env = {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    assert env.get("SCANNER_IMAGE_PULL_POLICY") == "Always"
    assert env.get("APP_ENV") == "local"  # inherited from ../local


def test_images_workflow_publishes_to_ghcr():
    assert WORKFLOW.is_file()
    doc = yaml.safe_load(WORKFLOW.read_text())
    assert doc["name"] == "Images"
    perms = doc["permissions"]
    assert perms.get("packages") == "write"
    assert perms.get("contents") == "read"

    # PyYAML turns the key "on" into True
    on = doc.get(True) or doc.get("on")
    assert on is not None
    assert "workflow_dispatch" in on
    assert "main" in on["push"]["branches"]
    assert "pull_request" in on
    for key in ("frontend/**", "gateway/**", "review-api/**", "scanner-worker/**", ".github/workflows/images.yml"):
        assert key in on["push"]["paths"]
        assert key in on["pull_request"]["paths"]

    job = doc["jobs"]["build"]
    services = {row["service"] for row in job["strategy"]["matrix"]["include"]}
    assert services == {"review-api", "gateway", "frontend", "scanner-worker"}
    # contexts are the service dirs (same as Makefiles / Jenkins kaniko), not repo root
    by_svc = {row["service"]: row["context"] for row in job["strategy"]["matrix"]["include"]}
    for svc, ctx in by_svc.items():
        assert ctx == f"./{svc}"

    steps = job["steps"]
    meta = next(s for s in steps if s.get("uses", "").startswith("docker/metadata-action@"))
    assert "ghcr.io/almog975/ai-scan-repos-${{ matrix.service }}" in meta["with"]["images"]
    tags = meta["with"]["tags"]
    assert "type=sha,prefix=sha-" in tags
    assert "type=raw,value=latest" in tags
    assert "org.opencontainers.image.source=https://github.com/almog975/ai-scan-repos" in meta["with"]["labels"]

    build = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["platforms"] == "linux/amd64"
    # PRs build only; push is gated off for pull_request
    assert "pull_request" in str(build["with"]["push"])

    login = next(s for s in steps if s.get("uses", "").startswith("docker/login-action@"))
    assert login["with"]["registry"] == "ghcr.io"
    assert "pull_request" in str(login.get("if", ""))


def test_docs_mention_ghcr_and_public_packages():
    local = (ROOT / "docs/local-k3d.md").read_text()
    assert "deploy/overlays/local-registry" in local
    assert "ghcr.io/almog975/ai-scan-repos-" in local
    assert "Change visibility" in local or "Package settings" in local
    readme = (ROOT / "README.md").read_text()
    assert "deploy/overlays/local-registry" in readme
