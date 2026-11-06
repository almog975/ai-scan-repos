"""ci/heal/heal.sh (Jenkins job ai-scan-autoheal) with a fake kubectl/curl, plus its RBAC, NetworkPolicies and JCasC."""
import json, pathlib, shutil, subprocess
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEAL = ROOT / "ci/heal/heal.sh"
SHIMS = ROOT / "ci/tests/heal_shims"
pytestmark = pytest.mark.skipif(not shutil.which("jq"), reason="jq not on PATH")


def pod(name, waiting=None, reason=None):
    p = {"metadata": {"name": name}, "status": {}}
    if reason:
        p["status"]["reason"] = reason
    if waiting:
        p["status"]["containerStatuses"] = [{"state": {"waiting": {"reason": waiting}}}]
    return p


FAILED_JOB = {
    "apiVersion": "batch/v1", "kind": "Job",
    "metadata": {"name": "review-api-migrate", "namespace": "ai-scan-repos", "uid": "u1", "resourceVersion": "9",
                 "creationTimestamp": "x", "managedFields": [{}],
                 "labels": {"app": "review-api-migrate", "controller-uid": "u1", "batch.kubernetes.io/controller-uid": "u1",
                            "job-name": "review-api-migrate"}},
    "spec": {"selector": {"matchLabels": {"batch.kubernetes.io/controller-uid": "u1"}}, "backoffLimit": 2,
             "template": {"metadata": {"labels": {"app": "review-api-migrate", "controller-uid": "u1",
                                                  "batch.kubernetes.io/controller-uid": "u1", "job-name": "review-api-migrate"}},
                          "spec": {"containers": [{"name": "migrate", "image": "x", "command": ["alembic", "upgrade", "head"]}]}}},
    "status": {"conditions": [{"type": "Failed", "status": "True"}]},
}


def app(name, sync, project="ai-scan", rev="main", op=None):
    a = {"metadata": {"name": name}, "spec": {"project": project, "source": {"targetRevision": rev}},
         "status": {"sync": {"status": sync}}}
    if op:
        a["operation"] = op
    return a


@pytest.fixture
def env(tmp_path):
    fix = tmp_path / "fix"; fix.mkdir()
    (fix / "pods.json").write_text(json.dumps({"items": []}))
    (fix / "pgpod").write_text("postgres-0")
    log = tmp_path / "log"; log.write_text("")
    return {"fix": fix, "log": log, "env": {"PATH": f"{SHIMS}:/usr/bin:/bin", "HEAL_FIX": str(fix), "SHIM_LOG": str(log),
                                            "READYZ_INTERVAL": "0", "HOME": str(tmp_path)}}


def run(env, **extra):
    e = dict(env["env"]); e.update(extra)
    r = subprocess.run(["bash", str(HEAL)], capture_output=True, text=True, env=e)
    return r, env["log"].read_text()


def test_healthy_cluster_does_nothing(env):
    r, log = run(env)
    assert r.returncode == 0, r.stderr
    assert "found: 0" in r.stdout and "fixed: 0" in r.stdout
    assert not any(v in log for v in (" delete ", " rollout ", " patch ", " create "))


@pytest.mark.parametrize("p, why", [(pod("a", waiting="CrashLoopBackOff"), "CrashLoopBackOff"),
                                    (pod("a", waiting="ImagePullBackOff"), "ImagePullBackOff"),
                                    (pod("a", reason="Evicted"), "Evicted")])
def test_bad_pods_deleted(env, p, why):
    (env["fix"] / "pods.json").write_text(json.dumps({"items": [p, pod("ok")]}))
    r, log = run(env)
    assert "kubectl -n ai-scan-repos delete pod a --wait=false" in log
    assert "delete pod ok" not in log
    assert why in r.stdout


def test_failed_migrate_job_recreated_from_clean_spec(env):
    (env["fix"] / "job.json").write_text(json.dumps(FAILED_JOB))
    r, log = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "delete job review-api-migrate" in log and "create -f -" in log
    j = json.loads((env["fix"] / "created.json").read_text())
    assert "status" not in j and "selector" not in j["spec"]
    for k in ("uid", "resourceVersion", "creationTimestamp", "managedFields"):
        assert k not in j["metadata"]
    for labels in (j["metadata"]["labels"], j["spec"]["template"]["metadata"]["labels"]):
        assert labels == {"app": "review-api-migrate"}
    assert j["spec"]["template"]["spec"]["containers"][0]["command"] == ["alembic", "upgrade", "head"]


def test_succeeded_migrate_job_left_alone(env):
    j = dict(FAILED_JOB, status={"conditions": [{"type": "Complete", "status": "True"}]})
    (env["fix"] / "job.json").write_text(json.dumps(j))
    _, log = run(env)
    assert "delete job" not in log


def test_readyz_restart_after_n_failures(env):
    r, log = run(env, HEAL_CURL_FAIL="gateway", READYZ_FAILS="3")
    assert log.count("gateway.ai-scan-repos.svc:8000/readyz") == 3
    assert "rollout restart deployment gateway" in log
    assert "rollout restart deployment review-api" not in log and "rollout restart deployment frontend" not in log
    assert "frontend.ai-scan-repos.svc:8080/nginx-health" in log


def test_readyz_recovering_service_not_restarted(env, tmp_path):
    # fails once then succeeds -> fewer than N consecutive failures
    flag = tmp_path / "once"
    shim = tmp_path / "bin"; shim.mkdir()
    (shim / "curl").write_text(f'#!/bin/bash\necho "curl $*" >> "$SHIM_LOG"\n[[ "$*" == *review-api* ]] && [ ! -f {flag} ] && touch {flag} && exit 22\nexit 0\n')
    (shim / "curl").chmod(0o755)
    r, log = run(env, PATH=f"{shim}:{SHIMS}:/usr/bin:/bin")
    assert log.count("review-api.ai-scan-repos.svc:8001/readyz") == 2
    assert "rollout restart" not in log


def test_postgres_check(env):
    r, log = run(env)
    assert "exec postgres-0 -c postgres -- pg_isready" in log and "ok: postgres" in r.stdout
    r, _ = run(env, HEAL_PG_DOWN="1")
    assert "pg_isready failed" in r.stdout


def test_argocd_outofsync_synced(env):
    apps = [app("ai-scan-gateway-k3s", "OutOfSync"), app("ai-scan-frontend-k3s", "Synced"),
            app("ai-scan-jenkins", "OutOfSync", project="ai-scan-ci"),
            app("ai-scan-review-api-k3s", "OutOfSync", op={"sync": {}})]
    (env["fix"] / "apps.json").write_text(json.dumps({"items": apps}))
    _, log = run(env)
    patches = [l for l in log.splitlines() if " patch " in l]
    assert len(patches) == 1 and "applications.argoproj.io ai-scan-gateway-k3s --type merge" in patches[0]
    assert '"sync":{"revision":"main"}' in patches[0]


def test_dry_run_reports_but_changes_nothing(env):
    (env["fix"] / "pods.json").write_text(json.dumps({"items": [pod("a", waiting="CrashLoopBackOff")]}))
    (env["fix"] / "job.json").write_text(json.dumps(FAILED_JOB))
    (env["fix"] / "apps.json").write_text(json.dumps({"items": [app("ai-scan-gateway-k3s", "OutOfSync")]}))
    r, log = run(env, DRY_RUN="1", HEAL_CURL_FAIL="gateway")
    assert r.returncode == 0
    assert "DRY RUN" in r.stdout and r.stdout.count("would ") >= 4
    assert not any(v in log for v in (" delete ", " rollout ", " patch ", " create "))


# --- manifests -----------------------------------------------------------------------------------------------

def docs(*paths):
    out = []
    for p in paths:
        out += [d for d in yaml.safe_load_all((ROOT / p).read_text()) if d]
    return out


RBAC = ["deploy/platform/base/autoheal-rbac.yaml", "infra/argocd/autoheal-rbac.yaml", "deploy/jenkins/manifests/autoheal-sa.yaml"]
ALLOWED = {("", "pods"): {"get", "list", "delete"}, ("", "pods/exec"): {"create"}, ("batch", "jobs"): {"get", "list", "create", "delete"},
           ("apps", "deployments"): {"get", "list", "patch"}, ("argoproj.io", "applications"): {"get", "list", "patch"}}


def test_rbac_namespaced_and_minimal():
    ds = docs(*RBAC)
    assert not [d for d in ds if d["kind"] in ("ClusterRole", "ClusterRoleBinding")]
    roles = {d["metadata"]["namespace"]: d for d in ds if d["kind"] == "Role"}
    assert set(roles) == {"ai-scan-repos", "argocd"}
    for r in roles.values():
        for rule in r["rules"]:
            for g in rule["apiGroups"]:
                for res in rule["resources"]:
                    assert set(rule["verbs"]) <= ALLOWED[(g, res)], (g, res, rule["verbs"])
    for b in (d for d in ds if d["kind"] == "RoleBinding"):
        assert b["roleRef"]["kind"] == "Role"
        assert b["subjects"] == [{"kind": "ServiceAccount", "name": "ai-scan-autoheal", "namespace": "jenkins"}]
    sa = [d for d in ds if d["kind"] == "ServiceAccount"]
    assert sa[0]["metadata"] == {"name": "ai-scan-autoheal", "namespace": "jenkins"}


def test_jenkins_agent_still_has_no_api_access():
    for p in ROOT.glob("deploy/**/*.yaml"):
        for d in yaml.safe_load_all(p.read_text()):
            if isinstance(d, dict) and d.get("kind") in ("RoleBinding", "ClusterRoleBinding"):
                assert all(s.get("name") != "jenkins-agent" for s in d.get("subjects", [])), p


def test_wired_into_kustomizations_and_installer():
    assert "autoheal-rbac.yaml" in (ROOT / "deploy/platform/base/kustomization.yaml").read_text()
    k = (ROOT / "deploy/jenkins/manifests/kustomization.yaml").read_text()
    assert "autoheal-sa.yaml" in k and "autoheal-networkpolicy.yaml" in k
    assert "infra/argocd/autoheal-rbac.yaml" in (ROOT / "infra/installer/aiscan-install.sh").read_text()


def test_networkpolicies_scoped_to_autoheal_pods():
    eg = docs("deploy/jenkins/manifests/autoheal-networkpolicy.yaml")[0]
    assert eg["spec"]["podSelector"] == {"matchLabels": {"jenkins/label": "autoheal"}}
    ing = [d for d in docs("deploy/platform/base/autoheal-rbac.yaml") if d["kind"] == "NetworkPolicy"][0]
    assert ing["spec"]["ingress"][0]["from"][0]["podSelector"] == {"matchLabels": {"jenkins/label": "autoheal"}}
    ports = {p["port"] for p in ing["spec"]["ingress"][0]["ports"]}
    assert ports == {p["port"] for p in eg["spec"]["egress"][0]["ports"]} == {8000, 8001, 8080}


def test_jcasc_job_and_pod_template():
    v = yaml.safe_load((ROOT / "deploy/jenkins/values.yaml").read_text())
    jobs = v["controller"]["JCasC"]["configScripts"]["ai-scan-jobs"]
    assert "multibranchPipelineJob('ai-scan-repos')" in jobs
    js = jobs[jobs.index("pipelineJob('ai-scan-autoheal')"):]
    for s in ("spec('H/10 * * * *')", "disableConcurrentBuilds()", "scriptPath('ci/heal/Jenkinsfile')", "booleanParam('DRY_RUN', false"):
        assert s in js, s
    tpl = yaml.safe_load(v["agent"]["podTemplates"]["autoheal"])[0]
    assert tpl["label"] == "autoheal" and tpl["serviceAccount"] == "ai-scan-autoheal"
    img = [c["image"] for c in tpl["containers"] if c["name"] == "kubectl"][0]
    assert ":" in img and not img.endswith(":latest")
    main = yaml.safe_load(v["agent"]["podTemplates"]["ai-scan-agent"])[0]
    assert main["serviceAccount"] == "jenkins-agent"
    jf = (ROOT / "ci/heal/Jenkinsfile").read_text()
    assert "label 'autoheal'" in jf and "disableConcurrentBuilds()" in jf and "params.DRY_RUN" in jf and "ci/heal/heal.sh" in jf


def test_argocd_missing_or_forbidden_is_skipped(env):
    # no apps.json -> fake kubectl fails like NotFound/Forbidden on applications.argoproj.io
    r, log = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skipped: Argo CD" in r.stdout and "errors: 0" in r.stdout


def test_runs_without_writable_home_or_cwd(env, tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o555)
    (env["fix"] / "pods.json").write_text(json.dumps({"items": [pod("a", waiting="CrashLoopBackOff")]}))
    (env["fix"] / "apps.json").write_text(json.dumps({"items": [app("x", "OutOfSync")]}))
    e = dict(env["env"], HOME=str(ro), TMPDIR=str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    r = subprocess.run(["bash", str(HEAL)], capture_output=True, text=True, env=e, cwd=ro)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not list(ro.iterdir())


# --- local in-cluster CronJob (deploy/overlays/local) --------------------------------------------------------

@pytest.fixture(scope="module")
def local():
    kc = shutil.which("kubectl")
    if not kc:
        pytest.skip("kubectl not on PATH")
    # kubectl kustomize = default load restrictor (LoadRestrictionsRootOnly)
    out = subprocess.run([kc, "kustomize", str(ROOT / "deploy/overlays/local")], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def one(ds, kind, name):
    m = [d for d in ds if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(m) == 1, (kind, name)
    return m[0]


def test_local_cronjob_psa_restricted(local):
    cj = one(local, "CronJob", "ai-scan-autoheal")
    assert cj["metadata"]["namespace"] == "ai-scan-repos"
    assert cj["spec"]["schedule"] == "*/10 * * * *" and cj["spec"]["concurrencyPolicy"] == "Forbid"
    ps = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert ps["serviceAccountName"] == "ai-scan-autoheal"
    assert ps["securityContext"]["runAsNonRoot"] is True and ps["securityContext"]["runAsUser"] == 65534
    assert ps["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    c = ps["containers"][0]
    assert c["image"] == "alpine/k8s:1.31.4" and c["imagePullPolicy"] == "IfNotPresent"
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False and sc["readOnlyRootFilesystem"] is True and sc["capabilities"]["drop"] == ["ALL"]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["HOME"] == "/tmp" and env["NAMESPACE"] == "ai-scan-repos"
    mounts = {m["mountPath"]: m["name"] for m in c["volumeMounts"]}
    vols = {v["name"]: v for v in ps["volumes"]}
    assert "emptyDir" in vols[mounts["/tmp"]]
    assert vols[mounts["/opt/heal"]]["configMap"]["name"] == "ai-scan-autoheal-script"
    assert c["command"] == ["bash", "/opt/heal/heal.sh"]


def test_local_configmap_is_heal_sh(local):
    cm = one(local, "ConfigMap", "ai-scan-autoheal-script")
    assert cm["metadata"]["namespace"] == "ai-scan-repos"
    assert cm["data"]["heal.sh"] == HEAL.read_text()


def test_local_rbac_reuses_role(local):
    one(local, "ServiceAccount", "ai-scan-autoheal")
    rb = one(local, "RoleBinding", "ai-scan-autoheal-local")
    assert rb["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "ai-scan-autoheal"}
    assert rb["subjects"] == [{"kind": "ServiceAccount", "name": "ai-scan-autoheal", "namespace": "ai-scan-repos"}]
    role = one(local, "Role", "ai-scan-autoheal")
    assert role["metadata"]["namespace"] == "ai-scan-repos"
    assert not [d for d in local if d["kind"] in ("ClusterRole", "ClusterRoleBinding")]


def test_local_has_no_networkpolicies(local):
    assert not [d["metadata"]["name"] for d in local if d["kind"] == "NetworkPolicy"]


def test_e2e_script_lint():
    s = ROOT / "ci/heal/e2e-local.sh"
    assert subprocess.run(["bash", "-n", str(s)]).returncode == 0
    if shutil.which("shellcheck"):
        r = subprocess.run(["shellcheck", str(s), str(HEAL)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout
    t = s.read_text()
    for x in ("--keep", "--timeout", "create job --from=cronjob/ai-scan-autoheal", "heal-baseline", "heal-fix", "PASS", "exit 1"):
        assert x in t, x


# --- Docker Desktop: local-registry must run in dev mode with ghcr images; k3s pulls IfNotPresent ---
def _docs(path):
    import subprocess, yaml as _y
    out = subprocess.run(["kubectl", "kustomize", str(ROOT / path)], check=True, capture_output=True, text=True).stdout
    return [d for d in _y.safe_load_all(out) if d]


def _containers(d):
    ps = d["spec"]["template"]["spec"]
    return ps.get("initContainers", []) + ps["containers"]


def test_local_registry_is_dev_mode_with_ghcr_images():
    docs = _docs("deploy/overlays/local-registry")
    ra = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "review-api")
    env = {e["name"]: e.get("value") for e in _containers(ra)[-1]["env"]}
    assert env["APP_ENV"] == "local"
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "scanner-image")
    assert cm["data"]["SCANNER_IMAGE"].startswith("ghcr.io/almog975/ai-scan-repos-scanner-worker:")
    for d in docs:
        if d["kind"] in ("Deployment", "Job") and d["metadata"]["name"] in ("review-api", "gateway", "frontend", "review-api-migrate"):
            assert all(c["image"].startswith("ghcr.io/almog975/") for c in _containers(d))
    assert not [d for d in docs if d["kind"] == "NetworkPolicy"]


@pytest.mark.parametrize("svc", ["review-api", "gateway", "frontend"])
def test_k3s_overlays_pull_if_not_present(svc):
    for d in _docs(f"deploy/{svc}/overlays/k3s"):
        if d["kind"] in ("Deployment", "Job"):
            assert all(c.get("imagePullPolicy") == "IfNotPresent" for c in _containers(d)), d["metadata"]["name"]
    if svc == "review-api":
        ra = next(d for d in _docs("deploy/review-api/overlays/k3s") if d["kind"] == "Deployment")
        assert {"name": "SCANNER_IMAGE_PULL_POLICY", "value": "IfNotPresent"} in ra["spec"]["template"]["spec"]["containers"][0]["env"]
