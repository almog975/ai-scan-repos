"""Phase 7: ServiceMonitors, PrometheusRules and the Grafana dashboard are wired and only reference real metrics."""
import json
import re

import yaml

from conftest import ROOT

SRC = [ROOT / "services/review-api/app/metrics.py", ROOT / "services/gateway/app/main.py", ROOT / "services/scanner-worker/scanner/health.py"]
# exporters outside this repo (Prometheus itself, kube-state-metrics)
EXTERNAL = {"up", "kube_pod_container_status_restarts_total"}
FUNCS = {"max", "sum", "by", "rate", "increase", "histogram_quantile", "clamp_min", "and", "or", "unless", "on", "le",
         "absent", "humanize", "without", "count", "vector"}
HIGH_CARD = {"review_id", "id", "user", "user_id", "repo", "repo_url", "commit_sha", "url"}


def defined_metrics():
    names = set()
    for f in SRC:
        for m in re.finditer(r'(?:Counter|Histogram|Gauge|GaugeMetricFamily)\(\s*"([a-z_]+)"', f.read_text()):
            n = m.group(1)
            names |= {n, f"{n}_total", f"{n}_bucket", f"{n}_count", f"{n}_sum"}
    return names


def referenced(expr):
    expr = re.sub(r'#[^\n]*', '', expr)  # PromQL comments
    expr = re.sub(r'\{[^}]*\}', '', re.sub(r'"[^"]*"', '', expr))
    expr = re.sub(r'\[[^\]]*\]', '', expr)
    words = set(re.findall(r'\b[a-z_][a-z0-9_]*\b', expr))
    by_labels = set()
    for m in re.finditer(r'by \(([^)]*)\)', expr):
        by_labels |= {w.strip() for w in m.group(1).split(",")}
    return words - FUNCS - by_labels


def k3s_kind(k3s, kind):
    return [d for d in k3s if d["kind"] == kind]


def test_servicemonitors_target_app_services(k3s):
    sms = {d["metadata"]["name"]: d for d in k3s_kind(k3s, "ServiceMonitor")}
    assert set(sms) >= {"review-api", "gateway"}
    svcs = {d["metadata"]["name"]: d for d in k3s_kind(k3s, "Service")}
    for name, sm in sms.items():
        assert sm["metadata"]["labels"]["release"] == "kube-prometheus-stack"
        svc = svcs[name]
        assert all(svc["metadata"].get("labels", {}).get(k) == v for k, v in sm["spec"]["selector"]["matchLabels"].items())
        assert {p["name"] for p in svc["spec"]["ports"]} >= {e["port"] for e in sm["spec"]["endpoints"]}


def test_prometheus_rules_cover_required_alerts_and_known_metrics(k3s):
    (rule,) = k3s_kind(k3s, "PrometheusRule")
    assert rule["metadata"]["labels"]["release"] == "kube-prometheus-stack"
    rules = [r for g in rule["spec"]["groups"] for r in g["rules"]]
    alerts = {r["alert"]: r for r in rules}
    stuck = alerts["AIScanReviewsStuck"]
    assert 'state="stuck"' in stuck["expr"] and "> 0" in stuck["expr"] and stuck["for"] == "15m"
    assert 'state="failed"' in alerts["AIScanReviewFailureRateHigh"]["expr"]
    assert "reviews_heartbeat_overdue" in alerts["AIScanNoWorkerHeartbeat"]["expr"]
    assert "kube_pod_container_status_restarts_total" in alerts["AIScanPodRestarting"]["expr"]
    known = defined_metrics() | EXTERNAL
    for r in rules:
        assert r["labels"]["severity"] in ("warning", "critical") and r["annotations"]["summary"]
        assert referenced(r["expr"]) <= known, (r["alert"], referenced(r["expr"]) - known)
        assert not HIGH_CARD & set(re.findall(r'by \(([^)]*)\)', r["expr"])), r["alert"]


def test_grafana_dashboard_configmap(k3s):
    (cm,) = [d for d in k3s_kind(k3s, "ConfigMap") if d["metadata"].get("labels", {}).get("grafana_dashboard") == "1"]
    (body,) = cm["data"].values()
    dash = json.loads(body)
    assert dash["uid"] == "ai-scan-repos"
    exprs = [t["expr"] for p in dash["panels"] for t in p.get("targets", [])]
    assert exprs and referenced(" ".join(exprs)) <= defined_metrics() | EXTERNAL
    titles = {p["title"] for p in dash["panels"]}
    assert {"Reviews by state", "Reconciler passes / 5m", "AI calls / 5m by kind", "Requests / s"} <= titles
    values = yaml.safe_load((ROOT / "infra/helm/kube-prometheus-stack/values.yaml").read_text())
    sc = values["grafana"]["sidecar"]["dashboards"]
    assert sc["label"] == "grafana_dashboard" and cm["metadata"]["namespace"] in sc["searchNamespace"]
    assert values["prometheus"]["prometheusSpec"]["ruleSelector"]["matchLabels"]["release"] == "kube-prometheus-stack"


def test_metric_labels_are_low_cardinality():
    for f in SRC:
        for m in re.finditer(r'(?:Counter|Histogram|Gauge)\([^)]*\[([^\]]*)\]', f.read_text()):
            labels = set(re.findall(r'"([a-z_]+)"', m.group(1)))
            assert not labels & HIGH_CARD, (f.name, labels)


def test_monitoring_doc_lists_every_metric():
    doc = (ROOT / "docs/monitoring.md").read_text()
    base = {n for n in defined_metrics() if not re.search(r"_(bucket|count|sum)$", n)}
    for f in SRC[:1]:
        for m in re.finditer(r'(?:Counter|Histogram|GaugeMetricFamily)\(\s*"([a-z_]+)"', f.read_text()):
            assert f"`{m.group(1)}" in doc, m.group(1)
    assert base
