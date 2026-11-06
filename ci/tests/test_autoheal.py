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
