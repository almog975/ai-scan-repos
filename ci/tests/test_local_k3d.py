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


def test_argocd_local_ignores_installer_owned_egress():
    """Argo CD self-heal must not revert the egress endpoints aiscan-install.sh fills (AI_EGRESS_CIDRS, KUBE_API_ENDPOINT)."""
    want = {"platform": {"review-api-ai-egress", "review-api-kube-api-egress"}, "scanner-worker": {"scanner-egress"}}
    for app, names in want.items():
        d = yaml.safe_load((ROOT / f"deploy/argocd/local/apps/{app}.yaml").read_text())
        assert "RespectIgnoreDifferences=true" in d["spec"]["syncPolicy"]["syncOptions"]
        got = {i["name"] for i in d["spec"]["ignoreDifferences"]
               if i["kind"] == "NetworkPolicy" and i["jsonPointers"] == ["/spec/egress"] and i["namespace"] == "ai-scan-repos"}
        assert got == names
    # every NetworkPolicy the installer's egress step rewrites is covered (names from the installer-marked overlays)
    plat = (ROOT / "deploy/platform/overlays/local/kustomization.yaml").read_text()
    for n in ("review-api-ai-egress", "review-api-kube-api-egress"):
        assert f"name: {n}" in plat
    assert "name: scanner-egress" in (ROOT / "deploy/scanner-worker/overlays/local/kustomization.yaml").read_text()


@pytest.mark.skipif(not shutil.which("kustomize"), reason="kustomize")
def test_local_scanner_pull_always_and_ingress_hosts():
    out = subprocess.run(["kustomize", "build", str(ROOT / "deploy/review-api/overlays/local")],
                         capture_output=True, text=True, check=True).stdout
    dep = next(d for d in yaml.safe_load_all(out) if d and d["kind"] == "Deployment")
    env = {e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["SCANNER_IMAGE_PULL_POLICY"] == "Always"
    ing = subprocess.run(["kustomize", "build", str(ROOT / "infra/ingress/overlays/local")],
                         capture_output=True, text=True, check=True).stdout
    assert "ai-scan.local`" not in ing and "argocd.ai-scan.localhost" in ing and "jenkins.ai-scan.localhost" in ing


def test_repo_token_guarded_and_kubeconfig_private():
    s = SCRIPT.read_text()
    i = s.index('if [ -n "${GITHUB_REPO_TOKEN:-}" ]; then')
    assert s.index("set +x", i) < s.index("$GITHUB_REPO_TOKEN", i + 40)
    assert 'chmod 600 "$KUBECONFIG"' in s


def test_gateway_public_url_matches_ingress_host_per_overlay():
    """OAuth redirect_uri = PUBLIC_URL/auth/github/callback must be on the host the IngressRoute serves."""
    import re
    import shutil
    import subprocess
    import pytest as _pt
    kubectl = shutil.which("kubectl")
    if not kubectl:
        _pt.skip("kubectl not installed")
    root = pathlib.Path(__file__).resolve().parents[2]
    for ov in ("eks", "k3s", "local"):
        gw = subprocess.run([kubectl, "kustomize", str(root / f"deploy/gateway/overlays/{ov}")],
                            capture_output=True, text=True, check=True).stdout
        pf = subprocess.run([kubectl, "kustomize", str(root / f"deploy/platform/overlays/{ov}")],
                            capture_output=True, text=True, check=True).stdout
        url = re.search(r"name: PUBLIC_URL\n\s+value: (\S+)", gw).group(1)
        hosts = set(re.findall(r"Host\(`([^`]+)`\)", pf))
        assert hosts == {url.removeprefix("https://")}, (ov, url, hosts)


def _env(rendered, name):
    import re
    m = re.search(rf"name: {name}\n\s+value: \"?([^\"\n]+)", rendered)
    return m.group(1) if m else None


def test_oauth_env_local_vs_strict_overlays():
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl not installed")
    root = pathlib.Path(__file__).resolve().parents[2]
    r = lambda p: subprocess.run([kubectl, "kustomize", str(root / p)], capture_output=True, text=True, check=True).stdout
    loc = r("deploy/overlays/local")
    assert _env(loc, "PUBLIC_URL") == "http://localhost:8080"
    assert _env(loc, "COOKIE_SECURE") == "false" and _env(loc, "APP_ENV") == "local"
    assert _env(loc, "ALLOWED_GITHUB_LOGINS") == "almog975" and _env(loc, "ADMIN_GITHUB_LOGINS") == "almog975"
    for ov in ("eks", "k3s", "local"):
        g = r(f"deploy/gateway/overlays/{ov}")
        assert _env(g, "APP_ENV") == "production" and _env(g, "COOKIE_SECURE") == "true"
        assert _env(g, "PUBLIC_URL").startswith("https://")
