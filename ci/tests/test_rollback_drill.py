"""Rollback drill: reproduce the Deploy stage's tag bump in a scratch clone, git revert it, and check the overlay
is back on the previous image and still builds."""
import re, shutil, subprocess
import pytest, yaml
from conftest import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize not on PATH")
BOT = ["-c", "user.name=ai-scan-ci-bot", "-c", "user.email=ci-bot@ai-scan.local"]
HOST = "registry.registry:5000"


def sh(*a, cwd):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def images(ov):
    return {i["name"]: i for i in yaml.safe_load((ov / "kustomization.yaml").read_text()).get("images", [])}


def scanner_image(ov):
    out = sh("kustomize", "build", ".", cwd=ov)
    return re.search(r"SCANNER_IMAGE: (\S+)", out).group(1)


def bump(clone, svc, sha):
    ov = clone / f"deploy/{svc}/overlays/k3s"
    if svc == "scanner-worker":
        sh("kustomize", "edit", "set", "configmap", "scanner-image", f"--from-literal=SCANNER_IMAGE={HOST}/ai-scan/{svc}:{sha}", cwd=ov)
    else:
        sh("kustomize", "edit", "set", "image", f"ai-scan/{svc}={HOST}/ai-scan/{svc}:{sha}", cwd=ov)
    sh("git", "add", "deploy", cwd=clone)
    sh("git", *BOT, "commit", "-qm", f"ci(k3s): {svc} -> {sha} [skip ci]", cwd=clone)


@pytest.fixture
def clone(tmp_path):
    c = tmp_path / "clone"
    sh("git", "clone", "-q", str(ROOT), str(c), cwd=tmp_path)
    sh("git", "checkout", "-q", sh("git", "rev-parse", "HEAD", cwd=ROOT).strip(), cwd=c)
    return c


@pytest.mark.parametrize("svc", ["gateway", "frontend", "review-api"])
def test_revert_restores_previous_tag(clone, svc):
    ov = clone / f"deploy/{svc}/overlays/k3s"
    bump(clone, svc, "aaaaaaa")
    bump(clone, svc, "bbbbbbb")
    assert images(ov)[f"ai-scan/{svc}"]["newTag"] == "bbbbbbb"
    sh("git", *BOT, "revert", "--no-edit", "HEAD", cwd=clone)
    assert images(ov)[f"ai-scan/{svc}"]["newTag"] == "aaaaaaa"
    out = sh("kustomize", "build", ".", cwd=ov)
    assert f"{HOST}/ai-scan/{svc}:aaaaaaa" in out and "bbbbbbb" not in out
    assert sh("git", "status", "--porcelain", cwd=clone) == ""


def test_revert_scanner_image(clone):
    ov = clone / "deploy/scanner-worker/overlays/k3s"
    bump(clone, "scanner-worker", "aaaaaaa")
    bump(clone, "scanner-worker", "bbbbbbb")
    assert scanner_image(ov).endswith(":bbbbbbb")
    sh("git", *BOT, "revert", "--no-edit", "HEAD", cwd=clone)
    assert scanner_image(ov) == f"{HOST}/ai-scan/scanner-worker:aaaaaaa"
