# docs/

Architecture notes, ADRs and runbooks for AI Scan Repos.

- **[User guide](user-guide.md)** – login, running a scan, depth, progress, pause/resume, report
- **[Admin guide](admin-guide.md)** – AI providers (cloud vs on-prem, Grok), secrets, deploy/Argo CD, stuck reviews, rollback, report retention and backups
- **[Demo video script](demo-video-script.md)** – shot-by-shot, ~6 min; live version: [runbooks/demo-script.md](runbooks/demo-script.md)
- **[CI/CD flow](ci-cd-flow.md)** – Jenkins → registry → tag bump → Argo CD (diagram)

- `adr/` – [template](adr/0000-template.md), [0001 repo layout](adr/0001-repo-layout.md), [0002 Job per review](adr/0002-k8s-job-per-review.md), [0003 AI key rotation (deferred)](adr/0003-ai-key-rotation.md), [0004 GitOps](adr/0004-gitops.md)
- `diagrams/` – [architecture v1 (target)](diagrams/architecture-v1.md) ([SVG](diagrams/architecture-v1.svg)), [Phase 1 backend as built](diagrams/architecture.md), [review state machine](diagrams/review-state-machine.md)
- `runbooks/` – [runbook](runbooks/runbook.md), [live demo walkthrough (timed)](runbooks/demo-script.md), [admin: AI providers](runbooks/admin-ai-providers.md), [stuck review](runbooks/stuck-review.md), [resume after days](runbooks/resume-after-days.md), [rollback](runbooks/rollback.md)
- [ci.md](ci.md) – CI/CD and GitOps flow
- [contract-gaps.md](contract-gaps.md) – open questions against the contracts
- [installer.md](installer.md) – installer: config (local registry / ECR), secrets, Argo CD, verify, uninstall
- [k3s.md](k3s.md) – K3s production config · [secrets-runbook.md](secrets-runbook.md) · [storage-plan.md](storage-plan.md) · [reports-pvc-recovery.md](reports-pvc-recovery.md) · [known-limitations.md](known-limitations.md) · [ci-cd.md](ci-cd.md)
- [monitoring.md](monitoring.md) – metrics, alerts, Grafana dashboard
- [severity-mapping.md](severity-mapping.md) – scanner severities → normalized severity
- [report-md-spec.md](report-md-spec.md) – structure of report.md and its report.json sources
 Contract docs: [../services/contracts/README.md](../services/contracts/README.md).
- [local-k3d.md](local-k3d.md) - full local stack on k3d (Docker Desktop / WSL2): `make local-k8s`, OAuth callback, Argo CD, troubleshooting
