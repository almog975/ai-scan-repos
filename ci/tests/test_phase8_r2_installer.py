"""Phase 8 r2: re-checks of the HIGH/LOW installer fixes against shims (no real cluster or cloud)."""
import pytest
from test_phase8_installer import sb  # noqa: F401  (fixture)

GET_ERRORS = [
    'Error from server (Forbidden): secrets "x" is forbidden: User "u" cannot get resource "secrets"',
    "Unable to connect to the server: dial tcp 10.0.0.1:6443: i/o timeout",
    "error: You must be logged in to the server (Unauthorized)",
]


@pytest.mark.parametrize("err", GET_ERRORS)
@pytest.mark.parametrize("args", [["secrets"], ["registry"], ["--rotate-secrets", "secrets"]])
def test_non_notfound_get_error_aborts_without_writes(sb, err, args):
    assert sb.run("secrets").returncode == 0
    before = sb.secrets()
    r = sb.run(*args, env={"SHIM_GET_ERR": err})
    assert r.returncode != 0 and "refusing to guess" in r.stderr, r.stderr
    assert sb.secrets() == before
    assert not [c for c in r.calls if c.startswith("SECRET ")]


def test_notfound_is_absent(sb):
    r = sb.run("secrets")
    assert r.returncode == 0, r.stderr
    assert len(sb.secrets()) == 5


@pytest.mark.parametrize("script", ["exit 127", "exit 0", 'echo "   "', "echo not-hex-zz"])
def test_openssl_failure_or_empty_writes_nothing(sb, script):
    fb = sb.tmp / "badssl"; fb.mkdir(exist_ok=True)
    (fb / "openssl").write_text(f"#!/bin/bash\n{script}\n"); (fb / "openssl").chmod(0o755)
    r = sb.run("secrets", path_prefix=str(fb))
    assert r.returncode != 0 and "GITHUB_WEBHOOK_SECRET" in r.stderr, r.stderr
    assert sb.secrets() == {}


def test_rotate_with_broken_openssl_keeps_existing(sb):
    assert sb.run("secrets").returncode == 0
    before = sb.secrets()
    fb = sb.tmp / "badssl"; fb.mkdir(exist_ok=True)
    (fb / "openssl").write_text("#!/bin/bash\nexit 0\n"); (fb / "openssl").chmod(0o755)
    assert sb.run("--rotate-secrets", "secrets", path_prefix=str(fb)).returncode != 0
    assert sb.secrets() == before


@pytest.mark.parametrize("extra", [[], ["--confirm-purge", "wrong"], ["--confirm-purge", "AI-SCAN-REPOS"]])
def test_purge_gate_refuses(sb, extra):
    r = sb.run("--yes", "--purge", *extra, "uninstall")
    assert r.returncode != 0 and "--confirm-purge ai-scan-repos" in r.stderr
    assert r.calls == []


def test_purge_with_confirm_runs(sb):
    r = sb.run("--yes", "--purge", "--confirm-purge", "ai-scan-repos", "--mode", "direct", "uninstall")
    assert r.returncode == 0, r.stderr
    assert any("delete pvc --all" in c for c in r.calls)


def test_gitops_uninstall_orphans_children_before_delete(sb):
    r = sb.run("--yes", "uninstall")
    assert r.returncode == 0, r.stderr
    calls = r.calls
    i_list = next(i for i, c in enumerate(calls) if "get applications -o name" in c)
    i_del = next(i for i, c in enumerate(calls) if "delete -n argocd -f" in c)
    assert i_list < i_del
    assert not any("delete pvc" in c or "PersistentVolumeClaim" in c for c in calls)


@pytest.mark.parametrize("flag", ["--config", "--tag", "--overlay", "--mode", "--registry"])
def test_value_flag_without_arg(sb, flag):
    r = sb.run(flag)
    assert r.returncode != 0 and f"{flag} needs a value" in r.stderr
    assert "unbound variable" not in r.stderr


def test_err_trap_names_step(sb):
    fb = sb.tmp / "failapply"; fb.mkdir()
    (fb / "kubectl").write_text(f'#!/bin/bash\nfor a; do [ "$a" = apply ] && {{ echo boom >&2; exit 3; }}; done\n'
                                f'exec {sb.tmp}/bin/kubectl "$@"\n'); (fb / "kubectl").chmod(0o755)
    r = sb.run("--mode", "direct", "deploy", path_prefix=str(fb))
    assert r.returncode == 3 and 'step "deploy" failed' in r.stderr, r.stderr


def test_images_targeted_edit(sb):
    import subprocess
    assert sb.run("--tag", "abc123", "images").returncode == 0
    d = subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff", "--numstat"], capture_output=True, text=True).stdout
    rows = [l.split() for l in d.splitlines()]
    assert rows and all(r[0] == "1" and r[1] == "1" for r in rows), d


def test_5xx_only_provider_502_excluded(tmp_path):
    import shutil, subprocess, yaml, pathlib
    from test_phase7_monitoring import _rules, HERE
    if not shutil.which("promtool"):
        pytest.skip("promtool not installed")
    _rules(tmp_path)
    shutil.copy(HERE / "http5xx_502_only_test.yaml", tmp_path)
    r = subprocess.run([shutil.which("promtool"), "test", "rules", "http5xx_502_only_test.yaml"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
