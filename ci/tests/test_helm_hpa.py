"""HorizontalPodAutoscaler for frontend / gateway / review-api (charts/ai-scan, docs/cicd-docker-desktop.md)."""
import shutil
import subprocess

import pytest
import yaml
from conftest import ROOT

CHART = ROOT / "charts" / "ai-scan"
DD = ["-f", str(CHART / "values-docker-desktop.yaml"), "-f", str(CHART / "values-images.yaml")]
SVCS = {"frontend": "frontend", "gateway": "gateway", "reviewApi": "review-api"}   # values key: Deployment
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _docs(*args):
    out = subprocess.run(["helm", "template", "ai-scan", str(CHART), "-n", "ai-scan-repos", *args],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _by(docs, kind):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


def test_disabled_by_default_keeps_replicas():
    docs = _docs()
    assert not _by(docs, "HorizontalPodAutoscaler")
    deps = _by(docs, "Deployment")
    for name in SVCS.values():
        assert deps[name]["spec"]["replicas"] == 1
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    for key in SVCS:
        assert values[key]["autoscaling"] == {"enabled": False, "minReplicas": 1, "maxReplicas": 3,
                                              "targetCPUUtilizationPercentage": 70}


def test_docker_desktop_enables_hpa_and_drops_replicas():
    docs = _docs(*DD)
    hpas, deps = _by(docs, "HorizontalPodAutoscaler"), _by(docs, "Deployment")
    assert set(hpas) == set(SVCS.values())
    for name in SVCS.values():
        h = hpas[name]
        assert h["apiVersion"] == "autoscaling/v2"
        assert h["spec"]["scaleTargetRef"] == {"apiVersion": "apps/v1", "kind": "Deployment", "name": name}
        assert (h["spec"]["minReplicas"], h["spec"]["maxReplicas"]) == (1, 3)
        assert h["spec"]["metrics"] == [{"type": "Resource", "resource": {
            "name": "cpu", "target": {"type": "Utilization", "averageUtilization": 70}}}]
        assert "replicas" not in deps[name]["spec"], name          # HPA owns it; Argo has nothing to diff
    # never postgres or the scanner
    assert not {d["spec"]["scaleTargetRef"]["name"] for d in hpas.values()} & {"postgres", "scanner"}
    assert _by(docs, "StatefulSet")["postgres"]["spec"]["replicas"] == 1


def test_memory_metric_is_optional():
    docs = _docs(*DD, "--set", "gateway.autoscaling.targetMemoryUtilizationPercentage=80",
                 "--set", "gateway.autoscaling.maxReplicas=5")
    g = _by(docs, "HorizontalPodAutoscaler")["gateway"]["spec"]
    assert g["maxReplicas"] == 5
    assert [m["resource"]["name"] for m in g["metrics"]] == ["cpu", "memory"]
    assert g["metrics"][1]["resource"]["target"] == {"type": "Utilization", "averageUtilization": 80}


def _render_chart(chart, *args):
    out = subprocess.run(["helm", "template", "ai-scan", str(chart), "-n", "ai-scan-repos", *args],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def test_nil_safe_for_reuse_values(tmp_path):
    # --reuse-values from a release made before <svc>.autoscaling existed: the key is missing -> off, replicas kept
    docs = _docs("--set", "frontend.autoscaling=null", "--set", "gateway.autoscaling=null",
                 "--set", "reviewApi.autoscaling=null")
    assert not _by(docs, "HorizontalPodAutoscaler")
    assert _by(docs, "Deployment")["gateway"]["spec"]["replicas"] == 1
    old = tmp_path / "ai-scan"
    shutil.copytree(CHART, old)
    values = yaml.safe_load((old / "values.yaml").read_text())
    for key in SVCS:
        values[key].pop("autoscaling")
    (old / "values.yaml").write_text(yaml.safe_dump(values))
    assert _render_chart(old) == _docs()                        # nothing changes when the keys are absent
    h = _by(_render_chart(old, "--set", "frontend.autoscaling.enabled=true"), "HorizontalPodAutoscaler")["frontend"]["spec"]
    assert (h["minReplicas"], h["maxReplicas"], h["metrics"][0]["resource"]["target"]["averageUtilization"]) == (1, 3, 70)


def test_requests_set_and_hardening_kept():
    for args in ((), tuple(DD)):
        deps = _by(_docs(*args), "Deployment")
        for name in SVCS.values():
            c = deps[name]["spec"]["template"]["spec"]["containers"][0]
            assert c["resources"]["requests"]["cpu"] and c["resources"]["requests"]["memory"], name
            assert c["resources"]["limits"]["cpu"] and c["resources"]["limits"]["memory"], name
            sc = c["securityContext"]
            assert sc["readOnlyRootFilesystem"] and not sc["allowPrivilegeEscalation"] and sc["capabilities"] == {"drop": ["ALL"]}


def test_helm_lint_with_docker_desktop_values():
    r = subprocess.run(["helm", "lint", str(CHART), *DD], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_argo_app_needs_no_ignore_differences():
    app = yaml.safe_load((ROOT / "deploy/argocd/docker-desktop/ai-scan.yaml").read_text())
    assert "ignoreDifferences" not in app["spec"]
