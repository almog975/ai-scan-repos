"""Round 2 re-checks for PR #7 (c529dc7): db-parity round trip gate, root make propagation, NetworkPolicy reachability.
Strict xfail = finding."""
import os, re, shutil, subprocess
import pytest
from conftest import ROOT

needs_pg = pytest.mark.skipif(not (os.environ.get("PGHOST") and shutil.which("pg_dump")), reason="needs PGHOST + pg_dump")


def scratch(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", "node_modules", "reports", "__pycache__", "dist"))
    return dst


@needs_pg
def test_parity_round_trip_failure_fails(tmp_path):
    dst = scratch(tmp_path)
    mig = next((dst / "services/review-api/alembic/versions").glob("0004_*.py"))
    s = mig.read_text()
    s2 = re.sub(r"(def downgrade\(\)[^:]*:\n)", r"\1    raise RuntimeError('injected downgrade failure')\n", s, count=1)
    assert s2 != s
    mig.write_text(s2)
    r = subprocess.run(["bash", "ci/db-parity.sh"], cwd=dst, capture_output=True, text=True)
    assert "db parity ok" in r.stdout and "migration round trip ok" not in r.stdout
    assert r.returncode != 0 and "round trip" in r.stderr


@pytest.mark.skipif(not shutil.which("make"), reason="make")
def test_root_make_test_all_propagates(tmp_path):
    """`make test` (all components) must stop nonzero when a later component (review-api) fails."""
    dst = scratch(tmp_path)
    (dst / "services/review-api/tests/test_zz_injected.py").write_text("def test_injected():\n    assert 1 == 2\n")
    env = dict(os.environ); env.pop("PYTEST_ADDOPTS", None)
    r = subprocess.run(["make", "-k", "test-gateway", "test-review-api"], cwd=dst, capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "test_injected" in r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("make"), reason="make")
def test_root_make_build_delegates():
    out = subprocess.run(["make", "-n", "build"], cwd=ROOT, capture_output=True, text=True)
    for c in ("frontend", "gateway", "review-api", "scanner-worker"):
        assert f"-C services/{c}" in out.stdout + out.stderr


def test_jenkins_shared_includes_root_makefile():
    jf = (ROOT / "Jenkinsfile").read_text()
    assert re.search(r"def SHARED = \[[^\]]*'Makefile'", jf)
    assert "allowEmptyResults: false" in jf and "allowEmptyResults: true" not in jf


# ---------- NetworkPolicy reachability model (default-deny ingress + allows) ----------
def pods_and_policies(k3s):
    pods, nps = [], []
    for d in k3s:
        k = d["kind"]
        if k in ("Deployment", "StatefulSet", "Job"):
            pods.append((d["metadata"]["name"], d["spec"]["template"]["metadata"].get("labels", {})))
        elif k == "CronJob":
            pods.append((d["metadata"]["name"], d["spec"]["jobTemplate"]["spec"]["template"]["metadata"].get("labels", {})))
        elif k == "NetworkPolicy":
            nps.append(d["spec"])
    return dict(pods), nps


def sel(selector, labels):
    return all(labels.get(k) == v for k, v in selector.get("matchLabels", {}).items())


def allowed(nps, src_labels, dst_labels, port, src_ns="ai-scan-repos"):
    applicable = [p for p in nps if "Ingress" in p.get("policyTypes", []) and sel(p["podSelector"], dst_labels)]
    if not applicable:
        return True
    for p in applicable:
        for rule in p.get("ingress", []):
            if rule.get("ports") and port not in [x["port"] for x in rule["ports"]]:
                continue
            for peer in rule.get("from", []):
                ns_ok = sel(peer["namespaceSelector"], {"kubernetes.io/metadata.name": src_ns}) if "namespaceSelector" in peer else src_ns == "ai-scan-repos"
                pod_ok = sel(peer["podSelector"], src_labels) if "podSelector" in peer else True
                if ns_ok and pod_ok:
                    return True
    return False


TRAEFIK = ({"app.kubernetes.io/name": "traefik"}, "kube-system")
PROM = ({"app.kubernetes.io/name": "prometheus"}, "monitoring")
SCANNER = {"app": "scanner", "review-id": "r1"}


def test_default_deny_ingress_present(k3s):
    _, nps = pods_and_policies(k3s)
    assert any(p["podSelector"] == {} and p["policyTypes"] == ["Ingress"] and not p.get("ingress") for p in nps)


@pytest.mark.parametrize("src,dst,port,ok", [
    ("gateway", "review-api", 8001, True),
    ("scanner", "review-api", 8001, True),
    ("review-api", "postgres", 5432, True),
    ("gateway", "postgres", 5432, True),            # gateway owns users/sessions (DATABASE_URL)
    ("traefik", "gateway", 8000, True),
    ("traefik", "frontend", 8080, True),
    ("prometheus", "gateway", 8000, True),
    ("prometheus", "review-api", 8001, True),
    ("frontend", "postgres", 5432, False),
    ("scanner", "postgres", 5432, False),
    ("frontend", "review-api", 8001, False),
    ("traefik", "review-api", 8001, False),
    ("traefik", "postgres", 5432, False),
    ("scanner", "gateway", 8000, False),
    ("frontend", "gateway", 8000, True),            # nginx proxy (services/frontend/nginx.conf)
    ("migrate", "postgres", 5432, True),            # migrations Job (alembic upgrade head)
    ("grafana", "gateway", 8000, False),            # other monitoring pods: ns AND pod selector
    ("grafana", "review-api", 8001, False),
    ("migrate", "review-api", 8001, False),
])
def test_np_matrix(k3s, src, dst, port, ok):
    pods, nps = pods_and_policies(k3s)
    ns = "ai-scan-repos"
    if src == "traefik":
        sl, ns = TRAEFIK
    elif src == "prometheus":
        sl, ns = PROM
    elif src == "grafana":
        sl, ns = {"app.kubernetes.io/name": "grafana"}, "monitoring"
    elif src == "migrate":
        sl = pods["review-api-migrate"]
    elif src == "scanner":
        sl = SCANNER
    else:
        sl = pods[src]
    assert allowed(nps, sl, pods[dst], port, ns) is ok


def test_migrate_job_reaches_postgres(k3s):
    pods, nps = pods_and_policies(k3s)
    assert allowed(nps, pods["review-api-migrate"], pods["postgres"], 5432)


def test_gateway_forwarded_allow_ips_is_cidr(k3s):
    gw = next(d for d in k3s if d["kind"] == "Deployment" and d["metadata"]["name"] == "gateway")
    env = {e["name"]: e.get("value") for e in gw["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["FORWARDED_ALLOW_IPS"] == "10.42.0.0/16"   # K3s default cluster-cidr


def test_postgres_automount_false(k3s):
    pg = next(d for d in k3s if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "postgres")
    assert pg["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


@pytest.mark.parametrize("overlay", ["k3s", "local", "eks"])
def test_np_structure_all_overlays(overlay):
    """Prometheus allow is ONE from-item (namespaceSelector AND podSelector); migrate allowed on postgres; in every overlay."""
    import yaml
    out = subprocess.run(["kustomize", "build", str(ROOT / "infra/k8s/platform/overlays" / overlay)], capture_output=True, text=True, check=True).stdout
    nps = {d["metadata"]["name"]: d["spec"] for d in yaml.safe_load_all(out) if d and d["kind"] == "NetworkPolicy"}
    peers = [p for r in nps["postgres-ingress"]["ingress"] for p in r["from"]]
    assert {"podSelector": {"matchLabels": {"app": "review-api-migrate"}}} in peers
    for name in ("gateway-ingress", "review-api-ingress"):
        for r in nps[name]["ingress"]:
            for p in r["from"]:
                if "namespaceSelector" in p:   # never a bare namespace peer (that would OR-in the whole namespace)
                    assert "podSelector" in p, (name, p)
