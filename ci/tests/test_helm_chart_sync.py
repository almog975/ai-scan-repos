"""charts/ai-scan/files/* are copies (Helm can't read files outside the chart dir). Keep them identical."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "charts" / "ai-scan" / "files"


def test_heal_script_copy_matches():
    assert (FILES / "heal.sh").read_text() == (ROOT / "ci/heal/heal.sh").read_text(), \
        "run: cp ci/heal/heal.sh charts/ai-scan/files/heal.sh"


def test_dashboard_copy_matches():
    assert (FILES / "ai-scan-repos-dashboard.json").read_text() == \
        (ROOT / "deploy/platform/base/dashboards/ai-scan-repos.json").read_text(), \
        "run: cp deploy/platform/base/dashboards/ai-scan-repos.json charts/ai-scan/files/ai-scan-repos-dashboard.json"


def test_prometheusrules_spec_copy_matches():
    src = (ROOT / "deploy/platform/base/prometheusrules.yaml").read_text()
    spec = src[src.index("spec:"):]
    copy = (FILES / "prometheusrules-spec.yaml").read_text()
    assert copy.split("\n", 1)[1] == spec, "re-copy the spec: section of deploy/platform/base/prometheusrules.yaml"
