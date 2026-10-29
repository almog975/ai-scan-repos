# docs/

Architecture notes, ADRs and runbooks for AI Scan Repos.

- **[User guide](user-guide.md)** – login, running a scan, depth, progress, pause/resume, report
- **[CI/CD flow](ci-cd-flow.md)** – Jenkins → registry → tag bump → Argo CD (diagram)

- `adr/` – [template](adr/0000-template.md), [0001 repo layout](adr/0001-repo-layout.md), [0002 Job per review](adr/0002-k8s-job-per-review.md), [0003 AI key rotation (deferred)](adr/0003-ai-key-rotation.md), [0004 GitOps](adr/0004-gitops.md)
- `diagrams/` – [architecture v1 (target)](diagrams/architecture-v1.md) ([SVG](diagrams/architecture-v1.svg)), [Phase 1 backend as built](diagrams/architecture.md), [review state machine](diagrams/review-state-machine.md)
- `runbooks/` – [runbook](runbooks/runbook.md), [demo script](runbooks/demo-script.md), [admin: AI providers](runbooks/admin-ai-providers.md), [stuck review](runbooks/stuck-review.md), [resume after days](runbooks/resume-after-days.md), [rollback](runbooks/rollback.md)
- [ci.md](ci.md) – CI/CD and GitOps flow
- [installer.md](installer.md) – installer: config (local registry / ECR), secrets, Argo CD, verify, uninstall
- [monitoring.md](monitoring.md) – metrics, alerts, Grafana dashboard
- [severity-mapping.md](severity-mapping.md) – scanner severities → normalized severity
- [report-md-spec.md](report-md-spec.md) – structure of report.md and its report.json sources
 Contract docs: [../contracts/README.md](../contracts/README.md).
