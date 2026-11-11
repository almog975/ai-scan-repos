"""Phase 6 Jenkinsfile: static checks + a Python model of the Loop-guard/changed logic simulated on real git repos.
Strict xfail = gap. The tag-bump push + PR is ci/deploy/open-pr.sh, simulated in test_deploy_pr.py."""
import re, shutil, subprocess
import pytest
from conftest import ROOT

JF = (ROOT / "Jenkinsfile").read_text()
CODE = "\n".join("" if l.strip().startswith("//") else l for l in JF.splitlines())
SERVICES = re.findall(r"'([\w-]+)'\s*:\s*\['([\w/-]+)',", JF)
BOT_EMAIL = re.search(r"BOT_EMAIL\s*=\s*'([^']+)'", JF).group(1)


def stage_order():
    return [m.group(1) for m in re.finditer(r"^    stage\('([^']+)'\)", JF, re.M)]


# ---------- 1. Trivy gate ----------
def test_trivy_gate_flags():
    m = re.search(r'sh "trivy image ([^"]+)"', JF)
    assert m, "trivy image step missing"
    f = m.group(1)
    assert "--exit-code 1" in f and re.search(r"--severity CRITICAL,HIGH\b", f) and "--input" in f


def test_scan_before_push_and_every_service_scanned():
    o = stage_order()
    assert o.index("Build") < o.index("Scan") < o.index("Push") < o.index("Deploy")
    for svc, _ in SERVICES:
        assert f"trivyScan('{svc}')" in JF and f"cranePush('{svc}')" in JF
    assert "--no-push" in JF, "kaniko must not push before the scan"


@pytest.mark.skipif(not shutil.which("trivy"), reason="trivy not on PATH")
def test_trivy_flags_really_fail_on_high(tmp_path):
    flags = ["--exit-code", "1", "--severity", "CRITICAL,HIGH", "--no-progress", "-q"]
    vuln = tmp_path / "vuln"; vuln.mkdir(); (vuln / "requirements.txt").write_text("flask==0.12\nrequests==2.19.0\n")
    clean = tmp_path / "clean"; clean.mkdir(); (clean / "requirements.txt").write_text("flask==3.1.1\n")
    assert subprocess.run(["trivy", "fs", *flags, str(vuln)], capture_output=True).returncode == 1
    assert subprocess.run(["trivy", "fs", *flags, str(clean)], capture_output=True).returncode == 0


# ---------- 2. failing tests block ----------
def test_no_failure_swallowing():
    for bad in ("catchError", "unstable(", "warnError", "returnStatus: true", "propagate: false", "|| exit 0"):
        assert bad not in CODE, bad
    test_part = CODE[CODE.index("stage('Test')"):CODE.index("stage('Build')")]
    assert "|| true" not in test_part


def test_changed_detection_failure_is_fatal():
    assert "|| true" not in CODE[CODE.index("stage('Loop guard')"):CODE.index("stage('Test')")]


def test_junit_not_allow_empty():
    assert "allowEmptyResults: true" not in JF


# ---------- 3. only changed components (model of the Loop guard) ----------
SHARED = re.search(r"def SHARED = \[([^\]]*)\]", JF).group(1).replace("'", "").replace(" ", "").split(",")


def model(files, msg="feat: x", author="dev@x", have_base=True):
    if author == BOT_EMAIL or "[skip ci]" in msg:
        return "skip"
    if files and all(f.startswith("infra/k8s/") for f in files):
        return "skip"
    if not have_base or any(f == s or f.startswith(s) for f in files for s in SHARED):
        return sorted(s for s, _ in SERVICES)
    return sorted(s for s, folder in SERVICES if any(f.startswith(folder + "/") for f in files))


def test_missing_base_selects_all():
    assert "haveBase" in JF and "SERVICES.keySet() as List" in JF
    assert model([], have_base=False) == sorted(s for s, _ in SERVICES)


def test_deploy_uses_same_set_as_build():
    # every Build/Scan/Push stage is gated only by wants() (= 'changed'), and Deploy iterates 'changed'
    assert "changeset '" not in CODE
    assert "changed.each { s ->" in CODE[CODE.index("stage('Deploy')"):]


def test_model_matches_jenkinsfile_source():
    assert "author == BOT_EMAIL" in JF and "msg.contains('[skip ci]')" in JF
    assert "files.every { it.startsWith('infra/k8s/') }" in JF
    assert 'files.any { it.startsWith("${SERVICES[s][0]}/") }' in JF


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, files, msg, email="dev@x"):
    for f in files:
        p = repo / f; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((p.read_text() if p.exists() else "") + "x\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", f"user.email={email}", "commit", "-qm", msg)
    return git(repo, "diff", "--name-only", "HEAD~1", "HEAD").split("\n"), msg, email


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"; r.mkdir(); git(r, "init", "-q")
    (r / "README.md").write_text("x\n"); git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=dev@x", "commit", "-qm", "init")
    return r


@pytest.mark.parametrize("files,expect", [
    (["services/frontend/src/App.tsx"], ["frontend"]),
    (["services/gateway/app/main.py", "services/review-api/app/state.py"], ["gateway", "review-api"]),
    (["services/scanner-worker/scanner/run.py", "docs/x.md"], ["scanner-worker"]),
    (["docs/user-guide.md"], []),
    (["infra/k8s/gateway/overlays/k3s/kustomization.yaml"], "skip"),
])
def test_changed_simulation(repo, files, expect):
    assert model(*commit(repo, files, "change")) == expect


def test_tag_bump_commit_is_skipped(repo):
    msg = re.search(r"commit -m '([^']+)'", JF).group(1)
    assert "[skip ci]" in msg
    files, m, a = commit(repo, ["infra/k8s/frontend/overlays/k3s/kustomization.yaml"], "ci(k3s): frontend -> abc [skip ci]", BOT_EMAIL)
    assert model(files, m, a) == "skip"
    # each guard alone is enough
    assert model(files) == "skip" and model(["services/frontend/x"], m) == "skip" and model(["services/frontend/x"], author=BOT_EMAIL) == "skip"


@pytest.mark.parametrize("f", ["services/contracts/review-api.openapi.yaml", "ci/mk/common.mk"])
def test_shared_changes_test_dependents(repo, f):
    assert model(*commit(repo, [f], "change")) not in ([], "skip")


def test_push_is_pr_not_direct_to_main():
    assert "git push" not in CODE, "Jenkinsfile must not push itself; ci/deploy/open-pr.sh pushes a deploy/* branch"
    sh = (ROOT / "ci/deploy/open-pr.sh").read_text()
    assert 'HEAD:refs/heads/${DEPLOY_BRANCH}' in sh and "HEAD:main" not in sh and "GIT_ASKPASS" in sh


def test_insecure_flags_for_plain_http_registry_kaniko_and_crane():
    jf = (ROOT / "Jenkinsfile").read_text()
    assert "INSECURE_REGISTRIES = ['registry.registry:5000']" in jf
    assert "--insecure-registry=${env.REGISTRY}" in jf and "${kInsecure}--cleanup" in jf  # kaniko
    assert "crane push ${insecure}" in jf  # crane
