"""Phase 8 installer verification against logging/stateful shims (no real cluster or cloud)."""
import os, pathlib, shutil, subprocess
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIMS = pathlib.Path(__file__).parent / "installer_shims"
SECRET_ENV = {"GITHUB_CLIENT_ID": "cid", "GITHUB_CLIENT_SECRET": "S3CRET_gh", "REGISTRY_PUSH_USER": "pusher",
              "REGISTRY_PUSH_PASSWORD": "S3CRET_push", "REGISTRY_PULL_USER": "puller",
              "REGISTRY_PULL_PASSWORD": "S3CRET_pull", "GRAFANA_ADMIN_PASSWORD": "S3CRET_graf", "NODE_NAME": "node1"}
pytestmark = pytest.mark.skipif(not shutil.which("kustomize") or not shutil.which("openssl"), reason="kustomize/openssl")


@pytest.fixture
def sb(tmp_path):
    root = tmp_path / "repo"
    for d in ("deploy", "infra"):
        shutil.copytree(ROOT / d, root / d)
    g = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(g[:3] + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True); subprocess.run(g + ["commit", "-qm", "s"], check=True)
    bin_ = tmp_path / "bin"; bin_.mkdir()
    for f in SHIMS.iterdir():
        shutil.copy(f, bin_ / f.name); (bin_ / f.name).chmod(0o755)
    for t in ("helm", "docker", "k3s", "curl"):
        (bin_ / t).symlink_to(bin_ / "_log")
    for t in ("kustomize", "openssl"):
        real = shutil.which(t)
        (bin_ / t).write_text(f'#!/bin/bash\necho "{t} $*" >> "$SHIM_LOG"; exec {real} "$@"\n'); (bin_ / t).chmod(0o755)
    (tmp_path / "k").mkdir()

    class S:
        log = tmp_path / "calls.log"; state = tmp_path / "k"

        def run(self, *args, env=None, path_prefix=None):
            self.log.write_text("")
            e = {"PATH": f"{path_prefix + ':' if path_prefix else ''}{bin_}:/usr/bin:/bin", "HOME": str(tmp_path),
                 "SHIM_LOG": str(self.log), "KSTATE": str(self.state), "ROOT": str(root), **SECRET_ENV, **(env or {})}
            r = subprocess.run(["bash", str(ROOT / "infra/installer/aiscan-install.sh"), *args], env=e,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
            r.calls = self.log.read_text().splitlines()
            return r

        def secrets(self):
            return {str(p.relative_to(self.state)): p.read_text() for p in self.state.rglob("*") if p.is_file()}
    S.tmp = tmp_path
    return S()


ECR = {"AWS_ACCOUNT_ID": "123456789012", "AWS_REGION": "eu-west-1"}


@pytest.mark.parametrize("cmd", ["preflight", "bootstrap", "registry", "secrets", "images", "deploy", "verify",
                                 "uninstall", "install"])
@pytest.mark.parametrize("opts", [[], ["--overlay", "local", "--mode", "direct"], ["--registry", "ecr"]])
def test_dry_run_makes_no_calls(sb, cmd, opts):
    r = sb.run("--dry-run", *opts, cmd, env=ECR if "ecr" in opts else None)
    assert r.returncode == 0, r.stderr
    assert r.calls == [], r.calls
    assert "S3CRET" not in r.stdout + r.stderr
    assert subprocess.run(["git", "diff", "--quiet", "--no-index", str(ROOT / "deploy"), str(sb.tmp / "repo/deploy")]).returncode == 0


def test_idempotent_and_rotate(sb):
    assert sb.run("install").returncode == 0
    first = sb.secrets()
    assert len(first) == 9
    r = sb.run("install"); assert r.returncode == 0
    assert sb.secrets() == first
    assert not [c for c in r.calls if c.startswith("SECRET") and "->" in c and c.split()[-1].split("->")[0] != c.split("->")[1]]
    assert sb.run("--rotate-secrets", "secrets").returncode == 0
    rot = sb.secrets()
    for s in ("ai-scan-repos/gateway-secrets", "ai-scan-repos/db-secrets", "ai-scan-repos/scanner-secrets"):
        assert rot[s] != first[s], s
    assert rot["ai-scan-repos/ai-keys-master"] == first["ai-scan-repos/ai-keys-master"]


def test_no_secret_in_output_or_leftover_tmp(sb):
    before = set(pathlib.Path("/tmp").glob("tmp.*"))
    r = sb.run("install")
    assert "S3CRET" not in r.stdout + r.stderr
    assert set(pathlib.Path("/tmp").glob("tmp.*")) - before == set()


@pytest.mark.xfail(strict=True, reason="aiscan-install.sh:170-171 htpasswd -Bbn puts registry passwords on argv (ps)")
def test_no_secret_in_argv(sb):
    r = sb.run("registry")
    assert not [c for c in r.calls if "S3CRET" in c]


@pytest.mark.xfail(strict=True, reason="aiscan-install.sh:215-217,234 ': ${X:=$(gen)}' ignores openssl failure -> empty passwords/master key")
def test_generator_failure_aborts(sb):
    d = sb.tmp / "badssl"; d.mkdir(); (d / "openssl").write_text("#!/bin/bash\nexit 127\n"); (d / "openssl").chmod(0o755)
    r = sb.run("secrets", env={"INTERNAL_API_TOKEN": "x" * 64}, path_prefix=str(d))
    assert r.returncode != 0


@pytest.mark.xfail(strict=True, reason="aiscan-install.sh:91 exists() treats any 'get' error as absent -> replaces ai-keys-master")
def test_get_error_does_not_replace_existing(sb):
    assert sb.run("secrets").returncode == 0
    first = sb.secrets()
    r = sb.run("secrets", env={"SHIM_KUBECTL_FAIL": "get secret"})
    assert r.returncode != 0 or sb.secrets() == first


@pytest.mark.xfail(strict=True, reason="aiscan-install.sh:307-309 uninstall without --purge deletes the postgres overlay incl. PVC postgres-data")
def test_uninstall_keeps_pvcs(sb):
    r = sb.run("--yes", "--mode", "direct", "uninstall")
    assert r.returncode == 0
    pvc_overlays = [c for c in r.calls if c.startswith("kubectl delete -k") and
                    "PersistentVolumeClaim" in subprocess.run(["kustomize", "build", c.split()[3]], capture_output=True, text=True).stdout]
    assert pvc_overlays == []


def test_uninstall_needs_yes(sb):
    r = sb.run("uninstall")
    assert r.returncode == 1 and "--yes" in r.stderr and r.calls == []


@pytest.mark.parametrize("args,env,msg", [
    (["--bogus", "install"], {}, "unknown flag"),
    (["--overlay", "x", "install"], {}, "--overlay must be"),
    (["preflight"], {"SHIM_KUBECTL_FAIL": "version"}, "no cluster access"),
    (["preflight"], {"SHIM_KUBECTL_FAIL": "customresourcedefinitions"}, "insufficient RBAC"),
    (["install"], {"SHIM_KUBECTL_FAIL": "apply -k"}, "forced failure"),
    (["--registry", "ecr", "install"], {}, "AWS_ACCOUNT_ID required"),
])
def test_error_paths(sb, args, env, msg):
    r = sb.run(*args, env=env)
    assert r.returncode != 0 and msg in r.stderr, r.stderr
    assert "smoke ok" not in r.stdout


def test_missing_tool(sb):
    (sb.tmp / "bin/kustomize").unlink()
    r = sb.run("preflight")
    assert r.returncode != 0 and "missing tools: kustomize" in r.stderr


def test_ecr_token_not_on_argv(sb):
    r = sb.run("--registry", "ecr", "--overlay", "eks", "--mode", "direct", "registry", env={**ECR})
    assert r.returncode == 0, r.stderr
    assert any("get-login-password" in c for c in r.calls)
    assert not [c for c in r.calls if "ECRTOKEN" in c] and "ECRTOKEN" not in r.stdout + r.stderr
