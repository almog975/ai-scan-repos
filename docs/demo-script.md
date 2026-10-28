# Demo script — AI Scan Repos (≈10 min)

Only shows what exists today (Phase 1 backend). Prepare beforehand: venv with both requirement sets,
`semgrep` installed (pip), optionally `gitleaks`/`trivy` on PATH, and optionally an on-prem LLM
(LM Studio/Ollama) reachable at `AI_BASE_URL`.

## 0. Slide: the problem (1 min)
"Paste a GitHub URL → get a prioritized, AI-explained security report, without the code leaving your
network unless you choose a cloud model." Show UI mockups from `/workspace/ai-scan-ui/` (`index.html`,
`02-new-scan.html`, `03-depth.html`, `05-results.html`) — state clearly they are **design mockups**.

## 1. Architecture (1 min)
Show the mermaid diagram in `docs/architecture.md`: review-api → K8s Job per scan → reports PVC,
heartbeat + watchdog, pluggable AI provider. Point out dashed boxes = roadmap.

## 2. Tests (1 min)
```bash
(cd review-api && pytest -q) ; (cd scanner-worker && pytest -q)
```
Mention: fixtures for semgrep/gitleaks/trivy, mocked AI transport, watchdog + crypto + launcher tests.

## 3. Start the API (1 min)
```bash
export INTERNAL_API_TOKEN=dev-token REPORTS_DIR=$PWD/reports LOCAL_WORKER_CWD=$PWD/scanner-worker
export AI_KEYS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export AI_PROVIDER=openai_compatible AI_BASE_URL=http://<llm-host>:1234/v1 AI_MODEL=<model>   # or AI_PROVIDER=none
(cd review-api && uvicorn --factory app.main:get_app --port 8000) &
curl -s localhost:8000/healthz; curl -s localhost:8000/readyz
```

## 4. Security of the API (1 min)
```bash
curl -i -X POST localhost:8000/reviews -d '{}'                         # rejected: no internal token
H='-H X-Internal-Token:dev-token -H X-User-Id:alice -H Content-Type:application/json'
curl -s $H -X POST localhost:8000/reviews -d '{"repo_url":"file:///etc/passwd"}'   # 422 validation
```

## 5. Run a scan (3 min)
```bash
ID=$(curl -s $H -X POST localhost:8000/reviews \
  -d '{"repo_url":"https://github.com/OWASP/NodeGoat","depth":"brief"}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
watch -n2 "curl -s $H localhost:8000/reviews/$ID"     # progress 5 → 25 → 40 → 55 → 97 → 100
```
Explain stages while it runs: clone → semgrep → gitleaks → trivy → AI → report.

## 6. Pause / resume (1 min) — on a second, `deep` review
```bash
curl -s $H -X POST localhost:8000/reviews/$ID2/pause ; cat reports/$ID2/checkpoint.json
curl -s $H -X POST localhost:8000/reviews/$ID2/resume   # attempts=2, continues from checkpoint
```

## 7. Report (1 min)
```bash
curl -s $H localhost:8000/reviews/$ID/report.md | less
```
Highlight: severity summary, per-scanner tables, redacted secrets, AI recommendations, and the
"on-prem / no code left the network" vs ⚠️ cloud banner.

## 8. Ops hooks (30 s)
`curl -s localhost:8000/metrics | grep ai_scan_` and show `build_job_manifest` in
`review-api/app/launcher.py` (hardened K8s Job spec). Mention watchdog (15 min heartbeat, 5 attempts).

## 9. Roadmap (30 s)
Gateway + GitHub OAuth, UI, Helm chart + Argo CD app, Jenkins pipeline, per-user AI keys to worker,
Grafana dashboard. TODO: replace with live K8s demo once manifests exist.
