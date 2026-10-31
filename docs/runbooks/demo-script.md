# Live demo walkthrough (timed, ~15 min)

A final, step-by-step live demo of AI Scan Repos on the K3s deployment. The times are cumulative. Screens match the current UI ([user-guide.md](../user-guide.md), screenshots in `docs/images/user-guide/`). The recorded version is [demo-video-script.md](../demo-video-script.md).

## Before the demo (T-30 min)

- [ ] `kubectl -n ai-scan-repos get pods` shows all pods Running, and `argocd app list | grep ai-scan` shows everything Synced/Healthy.
- [ ] Grafana dashboard "AI Scan Repos" is open in a tab ([monitoring.md](../monitoring.md)).
- [ ] An on-prem provider (e.g. Ollama over Tailscale) passes **Test connection**. A cloud provider (Grok, `openai_compatible`, `https://api.x.ai/v1`, `kind: cloud`) is configured ([admin-ai-providers.md](admin-ai-providers.md)).
- [ ] A finished **brief** review of `https://github.com/OWASP/NodeGoat` already exists, so you can fall back to it if the network is slow.
- [ ] You are logged in as the admin in browser window A, and window B is a private window, not logged in.
- [ ] A terminal is open with `kubectl -n ai-scan-repos get jobs -w` ready.
- [ ] Jenkins and the Argo CD UI are open in tabs.

## Walkthrough

| Time | Step | Do | Say |
|---|---|---|---|
| 0:00–1:00 | Intro | Show the README architecture diagram | "Paste a GitHub repo, and a Kubernetes Job per review runs Semgrep, Gitleaks and Trivy, then AI explains and fixes. On-prem AI by default." |
| 1:00–2:00 | Login | Window B: `https://<host>` → **Log in with GitHub** | "GitHub OAuth only. Sessions are cookies with CSRF, and no tokens are kept in the browser." |
| 2:00–3:00 | Admin: providers | Window A → sidebar **AI providers** → **Test connection** on the on-prem provider | "The admin decides which AI is used. The DB `kind` field decides cloud vs on-prem. Keys are AES-256-GCM encrypted and the UI shows only the last 4." |
| 3:00–4:30 | Start review | **New review**: Repository URL `https://github.com/OWASP/NodeGoat`, depth **Deep**, the on-prem provider → **Start review** | Explain the depths: Quick, Brief (AI on the top 10 findings), Deep (AI on every code file). |
| 4:30–6:00 | Live progress | Review page: progress bar, files done/total, "Last update"; terminal shows the new Job | "One Job per review. Updates arrive over SSE, with polling every 5 s as a fallback." |
| 6:00–7:00 | Pause / resume | **Pause** → state *paused* → **Resume** | "The checkpoint is saved after every file, so resume continues from there. Pause works only while running; otherwise the API returns 409." |
| 7:00–8:00 | Cloud warning | **New review**, pick Grok (cloud) → show the warning and the **Start review** button disabled until the checkbox is ticked | "Code leaves the network only after an explicit acknowledgement (`ack_cloud`), and the report carries a cloud banner." Do not start it. |
| 8:00–10:00 | Report | Open the prepared NodeGoat brief report: summary, severity counts, filters, a finding with its AI explanation and fix, **Download report.md** | "The scanners find, the AI explains. Everything is also in report.json and report.md." |
| 10:00–11:30 | Recovery | Show [stuck-review.md](stuck-review.md) and the Grafana panel for stuck reviews and heartbeats | "The reconciler detects a missing heartbeat, marks the review stuck and retries from the checkpoint, up to 5 attempts." |
| 11:30–13:30 | CI/CD | Jenkins: last `main` run, with the stages Loop guard → Test → Build → Scan → Push → Deploy; Argo CD: `ai-scan-root` and the child apps | "Trivy blocks HIGH and CRITICAL. The tag bump goes in Git and Argo CD syncs it. Rollback is a git revert." Show the auto-opened deploy PR (`ci(k3s): … [skip ci]`), merge it, Argo CD syncs. |
| 13:30–14:00 | Hebrew | Footer language switch → עברית (RTL) | "Fully localised, with accessibility checks run with axe." |
| 14:00–15:00 | Wrap-up | README → Status | Point out the open items: key re-encryption, backups. |

## If something breaks

| Problem | Fallback |
|---|---|
| GitHub is slow, or the clone fails | Use the prepared completed review |
| AI provider is down | Start the review with the empty provider ("Default provider"; with none configured, only the scanners run) |
| A Job is stuck or pending | `kubectl -n ai-scan-repos describe job <name>`, then [stuck-review.md](stuck-review.md); move on to the report |
| The UI is down | `kubectl -n ai-scan-repos rollout restart deploy/frontend deploy/gateway`; meanwhile show the screenshots in the [user guide](../user-guide.md) |
