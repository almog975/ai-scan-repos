"""Phase 8: infra/installer/aiscan-install.sh — dry-run plan, no secret leakage, idempotency, image rewrite."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import ROOT

SCRIPT = ROOT / "infra/installer/aiscan-install.sh"
SECRETS = {"GITHUB_CLIENT_ID": "ghid-123", "GITHUB_CLIENT_SECRET": "fake-oauth-SENTINEL-1",
           "REGISTRY_PUSH_USER": "pusher", "REGISTRY_PUSH_PASSWORD": "push-SENTINEL-2",
           "REGISTRY_PULL_USER": "puller", "REGISTRY_PULL_PASSWORD": "pull-SENTINEL-3",
           "AI_API_KEY": "sk-SENTINEL-4", "NODE_NAME": "node-1"}
SENTINELS = [v for v in SECRETS.values() if "SENTINEL" in v]

FAKE_KUBECTL = r"""#!/usr/bin/env bash
# records calls; `get` succeeds for objects listed in $FAKE_STATE; secrets created via apply are remembered
echo "kubectl $*" >> "$FAKE_LOG"
if [ "$1" = -n ] && [ "$3" = get ]; then grep -qx "$2/$4/$5" "$FAKE_STATE" 2>/dev/null && exit 0; r=$4s; [ "$4" = deploy ] && r=deployments.apps; echo "Error from server (NotFound): $r \"$5\" not found" >&2; exit 1; fi
if [ "$1" = -n ] && [ "$3" = create ] && [ "$4" = secret ]; then
  echo "$2/secret/$6" >> "$FAKE_STATE"
  for a in "$@"; do case "$a" in --from-env-file=*|--from-file=*) f="${a#*=}"; f="${f#*=}"; cat "$f" >> "$FAKE_SECRETS";; esac; done
  echo "apiVersion: v1"; exit 0
fi
[ "$1" = apply ] && { cat > /dev/null; exit 0; }
exit 0
"""


def sh(*args, env=None, cwd=None, check=True):
    # clean env: nothing from the developer's shell (KUBECONFIG, AWS_*, real secrets) leaks into the run
    e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **(env or {})}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=e, cwd=cwd)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.fixture
def fake(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    k = b / "kubectl"
    k.write_text(FAKE_KUBECTL)
    k.chmod(0o755)
    for tool in ("helm", "aws", "htpasswd", "kustomize"):
        if tool == "kustomize" and shutil.which("kustomize"):
            continue
        t = b / tool
        t.write_text('#!/usr/bin/env bash\necho "%s $*" >> "$FAKE_LOG"\n[ "$1" = -iBn ] && { cat >/dev/null; echo "$2:\\$2y\\$hash"; }\n'
                     '[ "$2" = get-login-password ] && echo ecr-token-SENTINEL-5\nexit 0\n' % tool)
        t.chmod(0o755)
    env = {"PATH": f"{b}:{os.environ['PATH']}", "FAKE_LOG": str(tmp_path / "log"),
           "FAKE_STATE": str(tmp_path / "state"), "FAKE_SECRETS": str(tmp_path / "secrets"), **SECRETS}
    return env, tmp_path


def test_shellcheck():
    sc = shutil.which("shellcheck")
    if not sc:
        pytest.skip("shellcheck not installed")
    r = subprocess.run([sc, str(SCRIPT), str(ROOT / "infra/installer/scripts/install-argocd.sh")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_dry_run_plan_touches_nothing(fake):
    env, tmp = fake
    before = subprocess.run(["git", "status", "--porcelain", "deploy"], cwd=ROOT, capture_output=True, text=True).stdout
    out = sh("--dry-run", "install", env=env).stdout
    assert not (tmp / "log").exists(), "dry-run must not call kubectl/helm/aws"
    assert subprocess.run(["git", "status", "--porcelain", "deploy"], cwd=ROOT, capture_output=True,
                          text=True).stdout == before
    for step in ("preflight", "bootstrap", "registry", "secrets", "images", "deploy", "verify"):
        assert f"==> {step}" in out
    for name in ("gateway-secrets", "db-secrets", "scanner-secrets", "ai-keys-master", "registry-htpasswd",
                 "regcred", "registry-push"):
        assert name in out
    assert "--version 10.11.6" in out and "root-app.yaml" in out and "(dry-run: nothing was changed)" in out
    for s in SENTINELS:
        assert s not in out


def test_ecr_option(fake):
    env, _ = fake
    env = {**env, "AWS_ACCOUNT_ID": "123456789012", "AWS_REGION": "eu-central-1"}
    out = sh("--dry-run", "--registry", "ecr", "--tag", "abc123", "install", env=env).stdout
    host = "123456789012.dkr.ecr.eu-central-1.amazonaws.com"
    assert f"ai-scan/review-api={host}/ai-scan/review-api:abc123" in out
    assert f"SCANNER_IMAGE={host}/ai-scan/scanner-worker:abc123" in out
    assert "create-repository" in out and "get-login-password" in out and "registry-htpasswd" not in out
    assert "ecr-token" not in out
    bad = sh("--dry-run", "--registry", "ecr", "install", env={**env, "AWS_ACCOUNT_ID": "12"}, check=False)
    assert bad.returncode != 0 and "12 digits" in bad.stderr


def test_ecr_login_refresh_writes_regcred_without_printing_token(fake):
    env, tmp = fake
    env = {**env, "AWS_ACCOUNT_ID": "123456789012", "AWS_REGION": "eu-central-1"}
    out = sh("--registry", "ecr", "ecr-login", env=env)
    assert "SENTINEL" not in out.stdout + out.stderr
    assert "ecr-token-SENTINEL-5" in (tmp / "secrets").read_text()  # reached the cluster, not the terminal
    assert "regcred" in (tmp / "log").read_text()


def test_flags_validated(fake):
    env, _ = fake
    assert sh("--dry-run", "--overlay", "eks", "install", env=env, check=False).returncode != 0  # gitops is k3s only
    assert sh("--dry-run", "--tag", "x;rm", "images", env=env, check=False).returncode != 0
    r = sh("uninstall", env=env, check=False)
    assert r.returncode != 0 and "--yes" in r.stderr
    out = sh("--dry-run", "--yes", "--purge", "uninstall", env=env).stdout
    assert "helm uninstall argocd" in out and "delete namespace ai-scan-repos" in out
    keep = sh("--dry-run", "--yes", "uninstall", env=env).stdout
    assert "delete namespace" not in keep and "finalizers" in keep  # data kept without --purge


def test_config_file(fake, tmp_path):
    env, _ = fake
    cfg = tmp_path / "i.env"
    cfg.write_text("# c\nOVERLAY=local\nMODE=direct\nIMAGE_TAG=cfgtag\n")
    out = sh("--config", str(cfg), "--dry-run", "images", env=env).stdout
    assert "overlays/local" in out and ":cfgtag" in out
    assert ":flagtag" in sh("--config", str(cfg), "--dry-run", "--tag", "flagtag", "images", env=env).stdout
    cfg.write_text("rm -rf /\n")
    assert sh("--config", str(cfg), "--dry-run", "images", env=env, check=False).returncode != 0


def test_secrets_idempotent_and_never_logged(fake):
    env, tmp = fake
    first = sh("secrets", env=env)
    log1 = (tmp / "log").read_text()
    for s in SENTINELS:
        assert s not in first.stdout + first.stderr + log1
    stored = (tmp / "secrets").read_text()
    assert "fake-oauth-SENTINEL-1" in stored and "sk-SENTINEL-4" in stored
    assert "INTERNAL_API_TOKEN=" in stored and len(stored.split("INTERNAL_API_TOKEN=")[1].split()[0]) >= 32
    second = sh("secrets", env=env).stdout
    assert second.count("exists: kept") == 4  # gateway, db, scanner, ai-keys-master
    rot = sh("--rotate-secrets", "secrets", env=env).stdout
    assert "ai-keys-master exists: kept (never rotated" in rot and rot.count("exists: kept") == 1


def test_images_rewrite_idempotent_and_valid(fake, tmp_path):
    if not shutil.which("kustomize"):
        pytest.skip("kustomize not on PATH")
    env, _ = fake
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", root / "deploy")
    env = {**env, "ROOT": str(root), "MODE": "direct"}
    sh("--tag", "deadbeef", "--overlay", "local", "images", env=env)
    snap = {p: p.read_text() for p in root.glob("deploy/*/overlays/local/kustomization.yaml")}
    sh("--tag", "deadbeef", "--overlay", "local", "images", env=env)
    assert snap == {p: p.read_text() for p in root.glob("deploy/*/overlays/local/kustomization.yaml")}
    imgs = set()
    for c in ("review-api", "gateway", "frontend", "scanner-worker"):
        docs = [d for d in yaml.safe_load_all(subprocess.run(
            ["kustomize", "build", str(root / f"deploy/{c}/overlays/local")], capture_output=True, text=True,
            check=True).stdout) if d]
        text = yaml.safe_dump_all(docs)
        imgs |= {w for w in text.split() if "k3d-ai-scan-registry.localhost:5111/ai-scan/" in w}
    assert any(i.endswith("review-api:deadbeef") for i in imgs)
    assert any("scanner-worker:deadbeef" in i for i in imgs)
    assert not any("GIT_SHA_PLACEHOLDER" in i for i in imgs)


def test_direct_install_calls_in_order(fake):
    env, tmp = fake
    env = {**env, "MODE": "direct", "OVERLAY": "local", "SKIP_VERIFY": "1"}
    sh("deploy", env=env)
    calls = [l for l in (tmp / "log").read_text().splitlines() if "apply -k" in l]
    order = [l.split("deploy/")[1].split("/")[0] for l in calls]
    assert order == ["platform", "postgres", "scanner-worker", "review-api", "gateway", "frontend"]


def test_example_config_has_placeholders_only_and_real_config_ignored():
    ex = (ROOT / "infra/installer/installer.env.example").read_text()
    for line in ex.splitlines():
        if line and not line.startswith("#") and ("PASSWORD" in line or "SECRET" in line):
            assert line.split("=", 1)[1].split()[0] == "REPLACE_ME", line
    r = subprocess.run(["git", "check-ignore", "-q", "infra/installer/installer.env"], cwd=ROOT)
    assert r.returncode == 0
