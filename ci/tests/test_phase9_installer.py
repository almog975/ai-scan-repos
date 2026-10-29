"""Phase 9 installer: egress placeholders from installer.env, registry default, purge message, strict NotFound."""
import subprocess

import pytest
import yaml
from test_phase8_installer import sb  # noqa: F401  (fixture)

EGRESS = {"AI_EGRESS_CIDRS": "100.64.10.0/24,100.64.20.5/32", "KUBE_API_ENDPOINT": "10.0.0.15", "KUBE_SERVICE_IP": "10.43.0.1"}


def build(sb, comp, overlay):
    out = subprocess.run(["kustomize", "build", str(sb.tmp / "repo" / "deploy" / comp / "overlays" / overlay)],
                         capture_output=True, text=True, check=True).stdout
    return {d["metadata"]["name"]: d for d in yaml.safe_load_all(out) if d and d["kind"] == "NetworkPolicy"}


def cidrs(rule):
    return [t["ipBlock"]["cidr"] for t in rule["to"] if "ipBlock" in t]


@pytest.mark.parametrize("overlay", ["k3s", "eks", "local"])
def test_egress_from_env(sb, overlay):
    env = {**EGRESS, "REGISTRY_HOST": "reg.example.com"}
    r = sb.run("--overlay", overlay, "--mode", "direct", "egress", env=env)
    assert r.returncode == 0, r.stderr
    np = build(sb, "platform", overlay)
    assert cidrs(np["review-api-ai-egress"]["spec"]["egress"][0]) == ["100.64.10.0/24", "100.64.20.5/32"]
    kube = np["review-api-kube-api-egress"]["spec"]["egress"]
    assert cidrs(kube[0]) == ["10.0.0.15/32"] and cidrs(kube[1]) == ["10.43.0.1/32"]
    scan = [d for d in build(sb, "scanner-worker", overlay).values()
            if any("100.64.10.0/24" in cidrs(e) for e in d["spec"].get("egress", []) if "to" in e)]
    assert scan, "scanner AI egress not rewritten"
    # idempotent: second run changes nothing
    diff1 = subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff"], capture_output=True, text=True).stdout
    assert sb.run("--overlay", overlay, "--mode", "direct", "egress", env=env).returncode == 0
    assert subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff"], capture_output=True, text=True).stdout == diff1


def test_egress_unset_keeps_placeholders_with_warning(sb):
    r = sb.run("egress")
    assert r.returncode == 0 and "AI_EGRESS_CIDRS unset" in r.stderr and "KUBE_API_ENDPOINT unset" in r.stderr
    assert subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff", "--quiet"]).returncode == 0


def test_egress_dry_run_touches_nothing(sb):
    r = sb.run("--dry-run", "egress", env=EGRESS)
    assert r.returncode == 0 and r.calls == []
    assert subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff", "--quiet"]).returncode == 0


@pytest.mark.parametrize("env", [{"AI_EGRESS_CIDRS": "0.0.0.0/0"}, {"AI_EGRESS_CIDRS": "10.0.0.0/4"},
                                 {"AI_EGRESS_CIDRS": "300.1.1.1/32"}, {"AI_EGRESS_CIDRS": "example.com"},
                                 {"KUBE_API_ENDPOINT": "10.0.0.0/24"}, {"KUBE_SERVICE_IP": "x;rm -rf /"}])
def test_egress_rejects_bad_or_broad(sb, env):
    r = sb.run("egress", env=env)
    assert r.returncode != 0 and "ERROR" in r.stderr
    assert subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff", "--quiet"]).returncode == 0


def test_egress_in_install_order():
    from conftest import ROOT
    s = (ROOT / "infra/installer/aiscan-install.sh").read_text()
    assert "images; egress; deploy;" in s


def test_registry_default_in_cluster(sb):
    r = sb.run("--dry-run", "images")
    assert "registry.registry:5000/ai-scan/review-api:" in r.stdout and "nip.io" not in r.stdout


def test_purge_message_accurate(sb):
    r = sb.run("--yes", "--purge", "uninstall")
    assert r.returncode != 0 and "ALL data" not in r.stderr
    for s in ("postgres-data", "postgres-backups", "jenkins", "monitoring", "jenkins-home", "--confirm-purge"):
        assert s in r.stderr


@pytest.mark.parametrize("err", ['error: context "prod" not found', 'Error from server (NotFound): namespaces "ai-scan-repos" not found',
                                 'error: the server doesn\'t have a resource type "secret"'])
def test_only_secret_notfound_is_absent(sb, err):
    r = sb.run("secrets", env={"SHIM_GET_ERR": err})
    assert r.returncode != 0 and sb.secrets() == {}


@pytest.mark.parametrize("overlay,cidr", [("k3s", "10.0.0.0/8"), ("k3s", "10.42.5.0/24"), ("k3s", "10.43.0.1/32"),
                                          ("local", "10.32.0.0/11"), ("eks", "10.40.7.7/32"), ("eks", "172.16.0.0/12")])
def test_ai_cidr_overlapping_cluster_rejected(sb, overlay, cidr):
    env = {"AI_EGRESS_CIDRS": f"192.0.2.10/32,{cidr}"}
    if overlay == "eks":
        env["REGISTRY_HOST"] = "reg.example.com"
    r = sb.run("--overlay", overlay, "--mode", "direct", "egress", env=env)
    assert r.returncode != 0 and "overlaps cluster range" in r.stderr + r.stdout
    assert subprocess.run(["git", "-C", str(sb.tmp / "repo"), "diff", "--quiet"]).returncode == 0


def test_ai_cidr_custom_pod_cidr_and_non_overlap_ok(sb):
    r = sb.run("egress", env={"AI_EGRESS_CIDRS": "11.0.0.0/8,100.64.0.0/10"})
    assert r.returncode == 0, r.stderr
    r = sb.run("egress", env={"AI_EGRESS_CIDRS": "100.64.0.0/10", "POD_CIDR": "100.64.0.0/16"})
    assert r.returncode != 0 and "overlaps" in r.stderr + r.stdout
