"""infra/helm/ai-scan/files/* are copies (Helm can't read files outside the chart dir). Keep them identical."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "infra" / "helm" / "ai-scan" / "files"


def test_heal_script_copy_matches():
    assert (FILES / "heal.sh").read_text() == (ROOT / "ci/heal/heal.sh").read_text(), \
        "run: cp ci/heal/heal.sh infra/helm/ai-scan/files/heal.sh"


def test_dashboard_copy_matches():
    assert (FILES / "ai-scan-repos-dashboard.json").read_text() == \
        (ROOT / "infra/k8s/platform/base/dashboards/ai-scan-repos.json").read_text(), \
        "run: cp infra/k8s/platform/base/dashboards/ai-scan-repos.json infra/helm/ai-scan/files/ai-scan-repos-dashboard.json"


def test_prometheusrules_spec_copy_matches():
    src = (ROOT / "infra/k8s/platform/base/prometheusrules.yaml").read_text()
    spec = src[src.index("spec:"):]
    copy = (FILES / "prometheusrules-spec.yaml").read_text()
    assert copy.split("\n", 1)[1] == spec, "re-copy the spec: section of infra/k8s/platform/base/prometheusrules.yaml"


# --- built-in defaults for autoheal/monitoring (templates/_helpers.tpl) ---------------------------
# `helm upgrade --reuse-values` from a release made before these keys existed reuses the old chart's
# values, so .Values.autoheal / .Values.monitoring are nil. The helpers fill them in; these tests keep
# the helper defaults equal to values.yaml and check an explicit false still wins.
import shutil
import subprocess

import pytest
import yaml

CHART = ROOT / "infra" / "helm" / "ai-scan"
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(chart, *sets):
    args = ["helm", "template", "ai-scan", str(chart), "-n", "ai-scan-repos"]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _without_keys(tmp_path, *keys):
    old = tmp_path / "ai-scan"
    shutil.copytree(CHART, old)
    values = yaml.safe_load((old / "values.yaml").read_text())
    for k in keys:
        values.pop(k, None)
    (old / "values.yaml").write_text(yaml.safe_dump(values))
    return old


@needs_helm
def test_helper_defaults_match_values_yaml():
    assert _render(CHART, "autoheal=null", "monitoring=null") == _render(CHART), \
        "update the defaults in ai-scan.autoheal / ai-scan.monitoring (templates/_helpers.tpl)"


@needs_helm
def test_upgrade_from_release_without_autoheal_keys(tmp_path):
    old = _without_keys(tmp_path, "autoheal", "monitoring")
    on = ("monitoring.enabled=true", "autoheal.cronjob.enabled=true")
    assert _render(old, *on) == _render(CHART, *on)


@needs_helm
def test_explicit_false_is_kept():
    out = _render(CHART, "autoheal.enabled=false", "autoheal.jenkins.enabled=false")
    assert "ai-scan-autoheal" not in out
    out = _render(CHART, "autoheal.jenkins.enabled=false")
    assert "kind: Role\nmetadata:\n  name: ai-scan-autoheal" in out
    assert "namespace: jenkins" not in out
