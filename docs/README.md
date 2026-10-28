# docs/

Architecture notes, ADRs and runbooks for AI Scan Repos.

- `adr/` – [template](adr/0000-template.md), [0001 repo layout](adr/0001-repo-layout.md), [0002 Job per review](adr/0002-k8s-job-per-review.md), [0003 AI key rotation (deferred)](adr/0003-ai-key-rotation.md)
- `diagrams/` – [architecture v1 (target)](diagrams/architecture-v1.md) ([SVG](diagrams/architecture-v1.svg)), [Phase 1 backend as built](diagrams/architecture.md)
- `runbooks/` – [runbook](runbooks/runbook.md), [demo script](runbooks/demo-script.md), [admin: AI providers](runbooks/admin-ai-providers.md)
- [ci.md](ci.md) – CI/CD and GitOps flow
- [severity-mapping.md](severity-mapping.md) – scanner severities → normalized severity
- [report-md-spec.md](report-md-spec.md) – structure of report.md and its report.json sources
 Contract docs: [../contracts/README.md](../contracts/README.md).
