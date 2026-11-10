# Security: accepted risks

Each entry: what, why it is accepted, mitigations, and when to revisit. Owner: Almog.

## Jenkins egress: TCP 443 to any public IP {#jenkins-egress}
- **What:** `infra/jenkins/manifests/networkpolicies.yaml` (`jenkins-egress`) lets every pod in ns `jenkins`
  (controller, agents, smee-client) open TCP 443 to `0.0.0.0/0` except RFC1918, CGNAT (100.64/10), link-local and loopback.
- **Why:** builds need GitHub, smee.io, updates.jenkins.io, PyPI/npm, the Trivy DB and upstream image registries. These are
  hostname-based and CDN-backed, so they cannot be pinned to stable CIDRs in a NetworkPolicy (no FQDN support).
- **Risk:** a malicious PR or dependency running in an agent could exfiltrate data (e.g. the registry-push or github-bot
  credential) over HTTPS.
- **Mitigations:** default-deny everywhere else; no port other than 443 leaves the cluster; private ranges (node, pod, other
  services) are excluded except the explicit registry, Traefik and K8s API rules; agents run with the narrow
  `jenkins-agent` SA; plugins pinned; webhook builds only from the project repo.
- **Revisit:** if an egress proxy (e.g. Squid with a domain allowlist) or an FQDN-capable CNI (Cilium) is added.

## PSA on monitoring and argocd: warn/audit only {#psa-report-only}
- **What:** namespaces `monitoring` and `argocd` carry `pod-security.kubernetes.io/warn` and `audit` = `restricted`, no `enforce`,
  and no default-deny NetworkPolicy.
- **Why:** kube-prometheus-stack needs hostNetwork/hostPID/hostPath for node-exporter, and both charts need API-server
  and cross-namespace traffic; enforcing would break installs. Warnings and audit events show what is still missing.
- **Revisit:** after the first real install, review the warnings; move to `enforce: baseline` (argocd likely `restricted`)
  and add NetworkPolicies per component.

## Postgres backups on the same node
See docs/postgres-backup-restore.md (Known limitation). Single K3s node: no off-node copy.
