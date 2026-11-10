"""Phase 9: default-deny egress + ingress reachability model over every k3s overlay (ai-scan-repos, jenkins,
registry). A flow is allowed only if the source's egress policies AND the destination's ingress policies allow it
(standard NetworkPolicy semantics: no selecting policy of a type = that direction is open)."""
import ipaddress
import pytest
import yaml

NS = "ai-scan-repos"
DNS = ({"k8s-app": "kube-dns"}, "kube-system")
TRAEFIK = ({"app.kubernetes.io/name": "traefik"}, "kube-system")


def nps(k3s):
    return [d for d in k3s if d["kind"] == "NetworkPolicy"]


def pod_labels(k3s, name):
    for d in k3s:
        if d["metadata"].get("name") == name:
            if d["kind"] == "CronJob":
                return d["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
            if d["kind"] in ("Deployment", "StatefulSet", "Job"):
                return d["spec"]["template"]["metadata"]["labels"]
    raise KeyError(name)


def match(selector, labels):
    if all(labels.get(k) == v for k, v in selector.get("matchLabels", {}).items()):
        for e in selector.get("matchExpressions", []):
            v = labels.get(e["key"])
            op = e["operator"]
            if (op == "In" and v not in e["values"]) or (op == "NotIn" and v in e["values"]) \
                    or (op == "Exists" and v is None) or (op == "DoesNotExist" and v is not None):
                return False
        return True
    return False


def peer_ok(peer, policy_ns, tgt):
    """tgt: dict(labels, ns) for a pod or dict(ip) for an external address."""
    if "ipBlock" in peer:
        if "ip" not in tgt:
            return False
        ip = ipaddress.ip_address(tgt["ip"])
        b = peer["ipBlock"]
        return ip in ipaddress.ip_network(b["cidr"]) and not any(ip in ipaddress.ip_network(x) for x in b.get("except", []))
    if "ip" in tgt:
        return False
    ns_ok = match(peer["namespaceSelector"], {"kubernetes.io/metadata.name": tgt["ns"]}) if "namespaceSelector" in peer \
        else tgt["ns"] == policy_ns
    return ns_ok and (match(peer["podSelector"], tgt["labels"]) if "podSelector" in peer else True)


def direction_ok(policies, kind, me, other, port, proto="TCP"):
    key, peers = ("egress", "to") if kind == "Egress" else ("ingress", "from")
    sel = [p for p in policies if p["metadata"]["namespace"] == me["ns"] and kind in p["spec"].get("policyTypes", [])
           and match(p["spec"]["podSelector"], me["labels"])]
    if not sel:
        return True
    for p in sel:
        for rule in p["spec"].get(key, []) or []:
            if rule.get("ports") and not any(x["port"] == port and x.get("protocol", "TCP") == proto for x in rule["ports"]):
                continue
            if peers not in rule or any(peer_ok(pe, p["metadata"]["namespace"], other) for pe in rule[peers]):
                return True
    return False


def allowed(policies, src, dst, port, proto="TCP"):
    if not direction_ok(policies, "Egress", src, dst, port, proto):
        return False
    return "ip" in dst or direction_ok(policies, "Ingress", dst, src, port, proto)


def pod(labels, ns=NS):
    return {"labels": labels, "ns": ns}



GITHUB = "140.82.112.3"  # github.com (in api.github.com/meta "web")
SCANNER = {"app": "scanner", "review-id": "r1"}


@pytest.fixture(scope="module")
def P(k3s):
    """k3s overlays + the jenkins manifests and the registry (applied by the installer, not an overlay)."""
    from conftest import ROOT, build
    docs = list(k3s) + build(ROOT / "infra/jenkins/manifests")
    docs += [d for d in yaml.safe_load_all((ROOT / "infra/bootstrap/registry/registry.yaml").read_text()) if d]
    seen, out = set(), []
    for d in nps(docs):
        k = (d["metadata"]["namespace"], d["metadata"]["name"])
        if k not in seen:
            seen.add(k); out.append(d)
    return out


@pytest.fixture(scope="module")
def pods(k3s):
    names = ["review-api", "gateway", "frontend", "postgres", "review-api-migrate", "postgres-backup"]
    out = {n: pod(pod_labels(k3s, n)) for n in names}
    out["scanner"] = pod(SCANNER)
    out["dns"] = pod(*DNS)
    out["traefik"] = pod(*TRAEFIK)
    out["registry"] = pod({"app": "registry"}, "registry")
    out["jenkins-agent"] = pod({"jenkins/label": "agent"}, "jenkins")
    return out


def test_default_deny_egress_everywhere(P):
    for ns in (NS, "jenkins", "registry"):
        assert any(p["metadata"]["namespace"] == ns and p["spec"]["podSelector"] == {} and "Egress" in p["spec"]["policyTypes"]
                   and p["metadata"]["name"].startswith("default-deny")
                   and all(x["port"] == 53 for r in p["spec"].get("egress") or [] for x in r.get("ports", [{"port": 0}])) for p in P), ns


@pytest.mark.parametrize("src", ["review-api", "gateway", "frontend", "scanner", "review-api-migrate", "postgres-backup", "jenkins-agent", "registry"])
@pytest.mark.parametrize("proto", ["UDP", "TCP"])
def test_dns(P, pods, src, proto):
    assert allowed(P, pods[src], pods["dns"], 53, proto)


@pytest.mark.parametrize("src,dst,port,ok", [
    ("review-api", "postgres", 5432, True),
    ("gateway", "postgres", 5432, True),
    ("review-api-migrate", "postgres", 5432, True),
    ("postgres-backup", "postgres", 5432, True),
    ("gateway", "review-api", 8001, True),
    ("frontend", "gateway", 8000, True),
    ("scanner", "review-api", 8001, True),
    ("jenkins-agent", "registry", 5000, True),     # pushes to registry.registry:5000
    ("traefik", "registry", 5000, True),
    # denied
    ("scanner", "postgres", 5432, False),
    ("frontend", "postgres", 5432, False),
    ("frontend", "review-api", 8001, False),
    ("postgres-backup", "review-api", 8001, False),
    ("review-api-migrate", "gateway", 8000, False),
    ("scanner", "gateway", 8000, False),
    ("jenkins-agent", "postgres", 5432, False),
    ("registry", "postgres", 5432, False),
])
def test_in_cluster_matrix(P, pods, src, dst, port, ok):
    assert allowed(P, pods[src], pods[dst], port) is ok


def test_github_https(P, pods):
    for src in ("gateway", "review-api", "scanner"):
        assert allowed(P, pods[src], {"ip": GITHUB}, 443), src
    for src in ("frontend", "postgres", "review-api-migrate", "postgres-backup"):
        assert not allowed(P, pods[src], {"ip": GITHUB}, 443), src


def _cidr(P, name, egress_idx):
    p = next(d for d in P if d["metadata"]["name"] == name)
    return p["spec"]["egress"][egress_idx]["to"][0]["ipBlock"]["cidr"]


def test_ai_provider(P, pods):
    ai = str(next(ipaddress.ip_network(_cidr(P, "review-api-ai-egress", 0)).hosts()))
    assert allowed(P, pods["review-api"], {"ip": ai}, 443)
    scanner_np = next(d for d in P if d["metadata"]["name"] == "scanner-egress")
    s_ai = [r for r in scanner_np["spec"]["egress"][3:] if "to" in r]
    assert s_ai, "scanner AI rule appended by the overlay"
    sip = str(next(ipaddress.ip_network(s_ai[0]["to"][0]["ipBlock"]["cidr"]).hosts()))
    assert allowed(P, pods["scanner"], {"ip": sip}, 443)
    assert not allowed(P, pods["gateway"], {"ip": ai}, 443)


def test_review_api_reaches_kube_api_only_it(P, pods):
    ep = str(ipaddress.ip_network(_cidr(P, "review-api-kube-api-egress", 0)).network_address)
    svc = str(ipaddress.ip_network(_cidr(P, "review-api-kube-api-egress", 1)).network_address)
    assert allowed(P, pods["review-api"], {"ip": ep}, 6443)
    assert allowed(P, pods["review-api"], {"ip": svc}, 443)
    for src in ("gateway", "scanner", "frontend", "postgres-backup", "review-api-migrate"):
        assert not allowed(P, pods[src], {"ip": svc}, 443), src
        assert not allowed(P, pods[src], {"ip": ep}, 6443), src


def test_scanner_has_no_internet_besides_allowlist(P, pods):
    assert not allowed(P, pods["scanner"], {"ip": "8.8.8.8"}, 443)
    assert not allowed(P, pods["review-api"], {"ip": "8.8.8.8"}, 443)
