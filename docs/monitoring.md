# Monitoring (Phase 7)

Prometheus + Grafana come from **kube-prometheus-stack** (`infra/helm/kube-prometheus-stack/values.yaml`,
namespace `monitoring`). Everything app-specific lives next to the app in `infra/k8s/platform/base` and is synced by
Argo CD with the rest of the platform:

| File | What |
|---|---|
| `servicemonitors.yaml` | `ServiceMonitor` review-api (port `http` = 8001) and gateway (port `http` = 8000), `/metrics` every 30s |
| `prometheusrules.yaml` | `PrometheusRule ai-scan-repos` (alerts below) |
| `dashboards/ai-scan-repos.json` | Grafana dashboard, rendered into ConfigMap `grafana-dashboard-ai-scan-repos` (label `grafana_dashboard: "1"`) |

Selection: both CRs carry `release: kube-prometheus-stack`, which matches `serviceMonitorSelector` and
`ruleSelector` in the Helm values. The Grafana dashboard sidecar watches only `ai-scan-repos` for ConfigMaps
labelled `grafana_dashboard=1` (folder from the `grafana_folder` annotation). NetworkPolicies (Phase 6) already let
Prometheus pods in `monitoring` reach gateway:8000 and review-api:8001; nothing else was opened.

## Cardinality rules

Labels are bounded enums only: `state`, `outcome`, `result`, `action`, `kind` (`onprem|cloud|unknown`),
`source` (`test|worker`), `stage`, `method`, `route` (the **route template**, e.g. `/reviews/{id}`, or
`unmatched`), `status`. Never a review id, user, repo, URL or commit. `ci/tests/test_monitoring.py` and
`services/review-api/tests/test_metrics.py` enforce this.

## review-api metrics

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `reviews_by_state` | gauge | state | Reviews currently in each state (all 8 states, read from the DB at scrape time) |
| `reviews_heartbeat_overdue` | gauge | – | `running` reviews whose last heartbeat is older than `HEARTBEAT_TIMEOUT_SECONDS` |
| `review_oldest_heartbeat_age_seconds` | gauge | – | Oldest heartbeat age among running reviews |
| `review_db_metrics_up` | gauge | – | 0 when the DB-backed gauges could not be read (the scrape still succeeds) |
| `reviews_total` | counter | state | Transitions into a state (`queued` = created) |
| `review_attempts_used` | histogram (1–5) | state | Attempts used in the current budget when a review ends (completed/failed/cancelled) |
| `review_duration_seconds` | histogram | state | created → completed/failed/cancelled |
| `scan_attempts_total` | counter | outcome | Finished scanner attempts |
| `job_launch_errors_total` | counter | – | Scanner Job launches that raised |
| `reconciler_runs_total` | counter | result (`ok|error`) | Reconciler (watchdog) passes |
| `reconciler_run_duration_seconds` | histogram | – | Reconciler pass duration |
| `reconciler_actions_total` | counter | action (`requeued|overdue|stuck|relaunched|failed`) | What the passes did |
| `stuck_detections_total` | counter | reason (`heartbeat|job_failed|job_missing|worker_error`) | Attempts marked stuck; a pod that exited non-zero is `job_failed`, not a heartbeat stall |
| `ai_provider_requests_total` | counter | kind, outcome, source | AI calls. `source=test`: admin connection test in review-api; `source=worker`: scanner calls reported via heartbeat |
| `ai_provider_request_seconds_total` | counter | kind, source | Time spent in AI calls (avg latency = this / requests) |
| `ai_provider_request_duration_seconds` | histogram | kind | Latency of review-api's own AI calls |
| `worker_heartbeats_total` | counter | stage | Heartbeats received from scanner Jobs (unknown stages → `other`) |
| `http_request_duration_seconds` | histogram | method, route, status | HTTP latency/throughput |

Every bounded label combination of the counters/histograms is pre-initialised to 0 at process start, so
`increase()` sees the first event after a restart and `AIScanReconcilerNotRunning` fires even when every pass errors.

The DB gauges are exported by every replica with the same value: aggregate with `max()`, never `sum()`.

## gateway metrics

`http_request_duration_seconds{method,route,status}`, `gateway_upstream_duration_seconds{status}` (proxy → review-api,
to response headers), `ai_scan_gateway_proxied_total{status}`, `ai_scan_gateway_logins_total{result}`,
`ai_scan_gateway_webhooks_total{result}`.

## scanner-worker metrics (heartbeat, no pushgateway)

Scanner Jobs are short-lived and not scraped (the plan has no pushgateway). The worker keeps a local
`/metrics` on :8002 (`scan_stage`, `scan_attempts_total`, `scan_ai_calls_total{kind,outcome}`) for debugging, and
sends what matters to review-api in its heartbeat (`POST /internal/reviews/{id}/progress`, internal):

```json
{"attempt": 1, "budget_no": 1, "stage": "ai", "progress": 70, "ai": {"calls": 3, "errors": 1, "seconds": 4.2}}
```

`ai` is a **delta** since the last accepted heartbeat (omitted when zero; restored and resent if the POST fails).
review-api adds it to `ai_provider_*{source="worker"}` using the provider `kind` from the DB row (not from the
worker), and counts `worker_heartbeats_total{stage}`. Liveness of workers is `reviews_heartbeat_overdue`.

**No PodMonitor for scanner-worker, on purpose.** The probe server does serve `/metrics` on :8002, but scanner Jobs
are short-lived (often shorter than a 30s scrape), each attempt is a new pod (a new series per pod), and the
Phase 6 NetworkPolicies deliberately keep scanner pods unreachable from `monitoring`. Everything worth alerting
on reaches Prometheus through review-api (heartbeat-derived metrics above), so no PodMonitor ships and no
NetworkPolicy is widened. `kubectl port-forward` to :8002 while a scan runs for debugging.

## Alerts

| Alert | Condition | Severity |
|---|---|---|
| `AIScanReviewsStuck` | `max(reviews_by_state{state="stuck"}) > 0` for 15m | warning |
| `AIScanReviewFailureRateHigh` | > 25% of reviews finished in the last hour failed (min 4 finished), for 15m | warning |
| `AIScanNoWorkerHeartbeat` | `max(reviews_heartbeat_overdue) > 0` for 10m | warning |
| `AIScanReconcilerNotRunning` | no `reconciler_runs_total{result="ok"}` increase in 15m (`or vector(0)`: also when only errors exist), for 15m | critical |
| `AIScanMetricsDBReadFailing` | `review_db_metrics_up == 0` for 10m | warning |
| `AIScanTargetDown` | `up{job=~"review-api|gateway"} == 0` for 5m | critical |
| `AIScanPodRestarting` | > 2 container restarts in 15m (kube-state-metrics) | warning |
| `AIScanAIProviderErrors` | > 50% of AI calls of a `kind` failed in 30m (min 5 calls, plain ratio), for 15m | warning |
| `AIScanHTTP5xxHigh` | > 5% 5xx per job over 10m, for 10m; `/admin/ai-providers/{id}/test` and `/admin/ai-providers/test` are excluded (their 502 is the contract answer for an unreachable provider) | warning |

Stuck / no-heartbeat → [runbooks/stuck-review.md](runbooks/stuck-review.md). Others: check the pod logs
(`kubectl -n ai-scan-repos logs deploy/review-api`), `kubectl describe pod`, and the dashboard panels for the metric.

## Dashboard

"AI Scan Repos" (uid `ai-scan-repos`): rows *Reviews* (state stats, by-state, finished/hour, duration p50/p95,
attempts), *Reconciler & workers* (passes/actions, pass p95, stuck detections, heartbeats, oldest heartbeat,
scanner attempts), *AI providers* (calls by kind/source/outcome, latency by kind), *HTTP* (req/s, p95 by route,
gateway → review-api p95, pod restarts). Edit `dashboards/ai-scan-repos.json` and let Argo CD sync it.

## Validation

`promtool check rules` and `promtool test rules` run from `ci/tests/test_phase7_monitoring.py`
(fixtures in `ci/tests/promtool/`) when promtool is installed. `bash ci/kubeconform.sh` validates the ServiceMonitor/PrometheusRule objects strictly against the CRD schemas
(no `-ignore-missing-schemas` for those). `ci/tests/test_monitoring.py` checks selectors, required alerts, that
every metric in the rules and dashboard exists in the code, and label cardinality.
