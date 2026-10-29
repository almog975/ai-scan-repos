"""Phase 7 monitoring verification: promtool unit tests, dashboard and ServiceMonitor wiring."""
import json, pathlib, re, shutil, subprocess
import pytest, yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/platform/base"
HERE = pathlib.Path(__file__).parent / "promtool"
promtool = shutil.which("promtool")
needs_promtool = pytest.mark.skipif(not promtool, reason="promtool not installed")


def _rules(tmp_path):
    groups = [g for d in yaml.safe_load_all((BASE / "prometheusrules.yaml").read_text())
              if d and d.get("kind") == "PrometheusRule" for g in d["spec"]["groups"]]
    (tmp_path / "rules.yaml").write_text(yaml.safe_dump({"groups": groups}))
    return groups


def _run(tmp_path, name):
    shutil.copy(HERE / name, tmp_path / name)
    return subprocess.run([promtool, "test", "rules", name], cwd=tmp_path, capture_output=True, text=True)


@needs_promtool
def test_check_rules(tmp_path):
    groups = _rules(tmp_path)
    assert sum(len(g["rules"]) for g in groups) == 9
    r = subprocess.run([promtool, "check", "rules", "rules.yaml"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_promtool
def test_all_alerts_fire_and_clear(tmp_path):
    _rules(tmp_path)
    r = _run(tmp_path, "rules_test.yaml")
    assert r.returncode == 0, r.stdout + r.stderr


@needs_promtool
@pytest.mark.xfail(strict=True, reason="ReconcilerNotRunning silent when result=\"ok\" series never existed (labels not pre-initialized)")
def test_reconciler_all_errors_fires(tmp_path):
    _rules(tmp_path)
    r = _run(tmp_path, "gaps_test.yaml")
    assert r.returncode == 0, r.stdout + r.stderr


def test_dashboard_datasource_variable():
    d = json.loads((BASE / "dashboards/ai-scan-repos.json").read_text())
    assert any(v["name"] == "datasource" and v["type"] == "datasource" for v in d["templating"]["list"])
    for p in d["panels"]:
        for obj in [p] + p.get("targets", []):
            if "datasource" in obj:
                assert obj["datasource"]["uid"] == "${datasource}", p.get("title")


def test_no_high_cardinality_labels():
    for f in ["review-api/app/metrics.py", "gateway/app/main.py", "scanner-worker/scanner/health.py"]:
        src = (ROOT / f).read_text()
        for labels in re.findall(r"(?:Counter|Gauge|Histogram|Summary|GaugeMetricFamily|CounterMetricFamily)\([^\[\)]*\[([^\]]*)\]", src):
            assert not re.search(r"['\"](repo|repo_url|review_id|id|user|login|commit_sha)['\"]", labels), (f, labels)


def test_servicemonitors_match_services():
    sms = [d for d in yaml.safe_load_all((BASE / "servicemonitors.yaml").read_text()) if d]
    svcs = [d for p in (ROOT / "deploy").rglob("*.yaml") for d in yaml.safe_load_all(p.read_text())
            if isinstance(d, dict) and d.get("kind") == "Service"]
    assert sms
    assert re.search(r"Counter|Histogram", (ROOT / "review-api/app/metrics.py").read_text())
    for sm in sms:
        sel = sm["spec"]["selector"]["matchLabels"]
        hit = [s for s in svcs if sel.items() <= (s["metadata"].get("labels") or {}).items()]
        assert hit, sel
        ports = {p.get("name") for s in hit for p in s["spec"]["ports"]}
        for ep in sm["spec"]["endpoints"]:
            assert ep["port"] in ports, (sel, ep)
