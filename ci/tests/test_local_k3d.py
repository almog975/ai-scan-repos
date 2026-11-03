"""infra/local/k3d.sh (make local-k8s): dry-run plan, no secret leaks, overlays/local + deploy/argocd/local wiring."""
import pathlib, shutil, subprocess
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/local/k3d.sh"
REG = "k3d-ai-scan-registry.localhost:5111/ai-scan/"
SECRETS = {"GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "S3CRET_gh",
           "INTERNAL_API_TOKEN": "S3CRET_tok_0123456789abcdef0123456789", "POSTGRES_PASSWORD": "S3CRET_pg",
           "AI_KEYS_ENCRYPTION_KEY": "S3CRET" + "a" * 58}


def run(*args, env_file=None, extra=None):
    env = {"PATH": "/usr/bin:/bin:" + str(pathlib.Path(shutil.which("kustomize") or "/x").parent), "HOME": "/tmp"}
    env.update(extra or {})
    a = [str(SCRIPT)] + (["--env", str(env_file)] if env_file else []) + list(args)
    return subprocess.run(a, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)


@pytest.fixture
def env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("".join(f"{k}={v}\n" for k, v in SECRETS.items()) + "AI_PROVIDER=none\n")
    return f


def test_dry_run_up_plan_order_and_no_secrets(env_file):
    before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    r = run("--dry-run", "up", env_file=env_file, extra={"GITHUB_REPO_TOKEN": "S3CRET_repo"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "S3CRET" not in out
    marks = ["k3d registry create", "k3d cluster create ai-scan", "-p 80:80@loadbalancer", "docker push localhost:5111/ai-scan/scanner-worker:dev",
             "monitoring.coreos.com_servicemonitors.yaml", "create secret tls ai-scan-tls", "KUBE_API_ENDPOINT=",
             "secret generic db-secrets", "delete job review-api-migrate", "repo/deploy/platform/overlays/local", "wait --for=condition=complete job/review-api-migrate",
             "rollout status deploy/gateway", "helm upgrade --install argocd", "repo-ai-scan-repos", "deploy/argocd/local/root-app.yaml",
             "https://ai-scan.localhost/auth/github/callback"]
    pos = [r.stdout.find(m) for m in marks]
    assert all(p >= 0 for p in pos), [m for m, p in zip(marks, pos) if p < 0]
    assert pos == sorted(pos), out
    order = [l.split("deploy/")[1].split("/")[0] for l in out.splitlines() if "apply -k" in l and "/deploy/" in l]
    assert order == ["platform", "postgres", "scanner-worker", "review-api", "gateway", "frontend"]
    after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    assert before == after, "k3d.sh must not edit the working tree"


def test_no_argocd_and_down_needs_yes(env_file):
    r = run("--dry-run", "--no-argocd", "up", env_file=env_file)
    assert r.returncode == 0 and "helm upgrade" not in r.stdout
    r = run("down")   # stdin is not a tty and no --yes
    assert r.returncode != 0
    assert "--yes" in r.stderr or "missing tools" in r.stderr
    r = run("--dry-run", "--yes", "down")
    assert "k3d cluster delete ai-scan" in r.stdout and "k3d registry delete" in r.stdout


def test_bad_args():
    assert run("bogus").returncode != 0
    assert run().returncode != 0


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize")
def test_local_overlays_use_k3d_registry_and_localhost():
    imgs, text_all = set(), ""
    for c in ("platform", "postgres", "scanner-worker", "review-api", "gateway", "frontend"):
        out = subprocess.run(["kustomize", "build", str(ROOT / f"deploy/{c}/overlays/local")],
                             capture_output=True, text=True, check=True).stdout
        text_all += out
        for d in yaml.safe_load_all(out):
            if d and d["kind"] in ("Deployment", "Job"):
                for ct in d["spec"]["template"]["spec"]["containers"]:
                    if "ai-scan/" in ct["image"]:
                        imgs.add(ct["image"]); assert ct.get("imagePullPolicy") == "Always", ct["image"]
    assert {i.split(REG)[-1] for i in imgs} == {"review-api:dev", "gateway:dev", "frontend:dev"}
    assert all(i.startswith(REG) for i in imgs)
    assert f"SCANNER_IMAGE: {REG}scanner-worker:dev" in text_all
    assert "Host(`ai-scan.localhost`)" in text_all and "Host(`ai-scan.local`)" not in text_all
    assert "https://ai-scan.localhost" in text_all and "GIT_SHA_PLACEHOLDER" not in text_all


def test_argocd_local_apps_point_to_local_overlays_only():
    apps = sorted((ROOT / "deploy/argocd/local/apps").glob("*.yaml"))
    assert {a.stem for a in apps} == {"platform", "postgres", "scanner-worker", "review-api", "gateway", "frontend"}
    for a in apps:
        d = yaml.safe_load(a.read_text())
        assert d["spec"]["source"]["path"] == f"deploy/{a.stem}/overlays/local"
        assert d["spec"]["source"]["targetRevision"] == "main"
    root = yaml.safe_load((ROOT / "deploy/argocd/local/root-app.yaml").read_text())
    assert root["spec"]["source"]["path"] == "deploy/argocd/local/apps"
    for a in (ROOT / "deploy/argocd/apps").glob("*.yaml"):
        assert "overlays/local" not in a.read_text()


def test_make_targets():
    if not shutil.which("make"):
        pytest.skip("make")
    for t, sub in (("local-k8s", "up"), ("local-k8s-down", "down"), ("local-k8s-status", "status")):
        out = subprocess.run(["make", "-n", t], cwd=ROOT, capture_output=True, text=True).stdout
        assert f"infra/local/k3d.sh  {sub}" in out or f"infra/local/k3d.sh {sub}" in out, out
