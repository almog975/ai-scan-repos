# Demo video script (shot by shot, ~6 min)

Recorded screen capture, 1920×1080, browser zoom 110%, UI in English. It uses the same environment and preparation as the [live demo](runbooks/demo-script.md#before-the-demo-t-30-min). The narration is a voice-over. Every screen shown exists in the current UI ([user-guide.md](user-guide.md)).

| # | Scene | On-screen action | Narration | Dur. |
|---|---|---|---|---|
| 1 | Title | Title card "AI Scan Repos – AI-assisted security review" over the architecture diagram | "AI Scan Repos reviews GitHub repositories for security issues with classic scanners and AI, on Kubernetes." | 0:15 |
| 2 | Architecture | Slow zoom across `architecture-v1.svg`: frontend → gateway → review-api → scanner Job → reports PVC | "A React front end and a gateway with GitHub login. review-api launches one short-lived scanner Job per review: Semgrep, Gitleaks and Trivy, then AI." | 0:30 |
| 3 | Login | Login page → **Log in with GitHub** → empty home page | "Users sign in with GitHub. The session lives in a secure cookie." | 0:20 |
| 4 | Admin providers | Sidebar **AI providers** → list with an on-prem and a cloud row → **Test connection** → "Connected: … answered in N ms" | "Admins choose the AI. On-prem models such as Ollama keep code inside the network. Keys are encrypted, and only the last four characters are ever shown." | 0:35 |
| 5 | Grok as cloud | Open the Grok provider form: kind cloud, Base URL `https://api.x.ai/v1`, the acknowledgement checkbox | "Grok is added as an OpenAI-compatible endpoint and marked as cloud, so an acknowledgement is required." | 0:20 |
| 6 | New review | Home: enter Repository URL `https://github.com/OWASP/NodeGoat`, choose depth Deep and the on-prem provider, **Start review** | "Paste a repo, pick a depth – quick, brief, or deep – and start." | 0:25 |
| 7 | Progress | Review page: the progress bar moves, files count up; cut to the terminal `kubectl get jobs` showing the scan Job | "Progress streams live. Behind it is a Kubernetes Job that saves a checkpoint after every file." | 0:30 |
| 8 | Pause/resume | **Pause** → badge *paused* → **Resume** → running again | "Pause and resume at any time; the scan continues from the last checkpoint." | 0:25 |
| 9 | Cloud warning | New review with Grok selected: the warning appears, **Start review** is disabled, tick the checkbox | "Choosing a cloud model shows a clear warning. Nothing is sent until the user confirms." | 0:20 |
| 10 | Report | Cut to the completed report: summary, severity counts, filter by High, expand a finding with its AI explanation and suggested fix | "The report ranks findings by severity, and the AI explains each one and suggests a fix." | 0:45 |
| 11 | Download | **Download report.md** → the file opens in an editor | "It is also available as Markdown and JSON for tickets or audits." | 0:15 |
| 12 | Resilience | Grafana "AI Scan Repos" dashboard: reviews by state, heartbeats, stuck alert | "If a worker dies, the reconciler notices the missing heartbeat and retries from the checkpoint. Alerts fire for stuck reviews." | 0:30 |
| 13 | CI/CD | Jenkins stage view: Test → Build → Scan → Push → Deploy; then Argo CD tree under `ai-scan-root`, all Synced | "Every commit is tested, built with kaniko and scanned with Trivy. Deployments are GitOps with Argo CD, and a rollback is a git revert." | 0:40 |
| 14 | Hebrew | Switch the language to עברית, and the layout flips right-to-left | "The interface is fully bilingual and accessible." | 0:15 |
| 15 | Outro | Title card with the repo URL `github.com/almog975/ai-scan-repos` | "AI Scan Repos – scanners find, AI explains, and Kubernetes keeps it running." | 0:15 |

**Total ≈ 6:00.**

Notes:
- Record scene 10 from a review that has already finished, so you do not have to wait.
- Do not show secrets, real keys or the `.env` file. Blur the session cookie if dev tools are open.
- Scene 13: the tag-bump push is still an open item, so show the commit made inside the Jenkins run and the Argo CD sync of the current `main`. Do not claim the push is automated.
