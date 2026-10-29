"""Phase 5 K3s overlays, rendered with kustomize: namespaces/PSA, PVCs, Jobs, Traefik, securityContext,
resources, NetworkPolicies, no hard-coded secrets. Strict xfail = finding."""
import re
import pytest
import yaml

WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")


def pod_specs(docs):
    for d in docs:
        k, s = d["kind"], d.get("spec", {})
        if k in WORKLOADS[:4]:
            yield d, s["template"]["spec"]
        elif k == "CronJob":
            yield d, s["jobTemplate"]["spec"]["template"]["spec"]
        elif k == "ConfigMap":
            for v in (d.get("data") or {}).values():
                if "kind: Job" in v:  # scanner Job template rendered by review-api
                    j = yaml.safe_load(v)
                    yield {"kind": "Job(template)", "metadata": {"name": d["metadata"]["name"],
                           "namespace": d["metadata"].get("namespace")}, "spec": j["spec"]}, j["spec"]["template"]["spec"]


def name(d):
    return f'{d["kind"]}/{d["metadata"]["name"]}'


def containers(ps):
    return ps.get("initContainers", []) + ps["containers"]


def test_app_namespace_restricted_psa(k3s):
    ns = {d["metadata"]["name"]: d["metadata"].get("labels", {}) for d in k3s if d["kind"] == "Namespace"}
    assert ns["ai-scan-repos"]["pod-security.kubernetes.io/enforce"] == "restricted"


def test_namespaced_objects_in_app_namespace(k3s):
    for d in k3s:
        if d["kind"] in WORKLOADS + ("Service", "ConfigMap", "PersistentVolumeClaim", "NetworkPolicy", "IngressRoute"):
            # Phase 6 adds CI/GitOps (Jenkins, Argo CD) in their own namespaces; each must be a declared Namespace
            declared = {x["metadata"]["name"] for x in k3s if x["kind"] == "Namespace"} | {"argocd", "kube-system"}
            assert d["metadata"].get("namespace") in declared, name(d)


def test_pod_security_context(k3s):
    for d, ps in pod_specs(k3s):
        psc = ps.get("securityContext", {})
        assert psc.get("runAsNonRoot") is True, name(d)
        assert psc.get("seccompProfile", {}).get("type") == "RuntimeDefault", name(d)
        for c in containers(ps):
            sc = c.get("securityContext", {})
            assert sc.get("allowPrivilegeEscalation") is False, (name(d), c["name"])
            assert sc.get("readOnlyRootFilesystem") is True, (name(d), c["name"])
            assert sc.get("capabilities", {}).get("drop") == ["ALL"], (name(d), c["name"])


def test_resources_requests_and_limits(k3s):
    for d, ps in pod_specs(k3s):
        for c in containers(ps):
            r = c.get("resources", {})
            for k in ("requests", "limits"):
                assert {"cpu", "memory"} <= set(r.get(k, {})), (name(d), c["name"], k)


def test_images_never_latest(k3s):
    for d, ps in pod_specs(k3s):
        for c in containers(ps):
            assert not c["image"].endswith(":latest") and ":" in c["image"].split("/")[-1] or c["image"] == "${IMAGE}", c["image"]


def test_jobs_bounded(k3s):
    for d, _ in pod_specs(k3s):
        if d["kind"].startswith("Job") or d["kind"] == "CronJob":
            js = d["spec"]["jobTemplate"]["spec"] if d["kind"] == "CronJob" else d["spec"]
            assert "ttlSecondsAfterFinished" in js and "backoffLimit" in js, name(d)
            assert "activeDeadlineSeconds" in js or d["kind"] == "Job(template)", name(d)


def test_pvcs(k3s):
    pvcs = {d["metadata"]["name"]: d["spec"] for d in k3s if d["kind"] == "PersistentVolumeClaim"}
    assert {"reports", "postgres-data"} <= set(pvcs)
    for n, s in pvcs.items():
        assert s.get("storageClassName") and s["resources"]["requests"]["storage"], n


def test_no_secret_objects_or_literal_secrets(k3s):
    assert not [d for d in k3s if d["kind"] == "Secret"], "secrets must come from the runbook, not the repo"
    for d, ps in pod_specs(k3s):
        for c in containers(ps):
            for e in c.get("env", []):
                if "value" in e and re.search(r"(?i)(password|secret|token|api_?key)$", e["name"]):
                    pytest.fail(f"literal secret env {name(d)} {e['name']}")


def test_traefik_tls_and_headers(k3s):
    routes = [d for d in k3s if d["kind"] == "IngressRoute" and d["metadata"].get("namespace") == "ai-scan-repos"]
    secure = [r for r in routes if r["spec"]["entryPoints"] == ["websecure"]]
    assert secure and all("tls" in r["spec"] for r in secure)
    for r in routes:
        for rt in r["spec"]["routes"]:
            assert {"name": "security-headers"} in rt.get("middlewares", []), rt["match"]
    plain = [r for r in routes if r["spec"]["entryPoints"] == ["web"]]
    assert all(any(m["name"] == "redirect-https" for rt in r["spec"]["routes"] for m in rt["middlewares"]) for r in plain)
    mw = {d["metadata"]["name"]: d["spec"] for d in k3s if d["kind"] == "Middleware"}
    assert mw["security-headers"]["headers"]["stsSeconds"] >= 15552000


def test_scanner_egress_restricted(k3s):
    np = {d["metadata"]["name"]: d["spec"] for d in k3s if d["kind"] == "NetworkPolicy"}
    s = np["scanner-egress"]
    assert s["policyTypes"] == ["Egress"] and s["podSelector"] == {"matchLabels": {"app": "scanner"}}
    for rule in s["egress"]:
        assert rule.get("to"), "egress rule without peers = anywhere"


def test_postgres_ingress_restricted(k3s):
    np = [d["spec"] for d in k3s if d["kind"] == "NetworkPolicy" and "Ingress" in d["spec"].get("policyTypes", [])]
    assert any(p["podSelector"] in ({}, {"matchLabels": {"app": "postgres"}}) for p in np)


def test_gateway_forwarded_allow_ips_not_wildcard(k3s):
    gw = next(ps for d, ps in pod_specs(k3s) if d["metadata"]["name"] == "gateway")
    env = {e["name"]: e.get("value") for e in gw["containers"][0].get("env", [])}
    assert env.get("FORWARDED_ALLOW_IPS") != "*"


def test_postgres_no_sa_token(k3s):
    pg = next(ps for d, ps in pod_specs(k3s) if d["metadata"]["name"] == "postgres")
    assert pg.get("automountServiceAccountToken") is False
