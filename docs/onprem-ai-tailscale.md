# On-prem AI over Tailscale (stub)

> **Status: design stub. Implementation comes later.**

## Goal
Let scanner Job pods on K3S-SRV call a self-hosted AI endpoint (e.g. Ollama/vLLM
on a GPU box) over Tailscale, without opening any other egress.

## Architecture
```
scanner pod (ai-scan-repos, app=scanner)
   │  egress allowed only by NetworkPolicy scanner-egress
   ▼
K3S-SRV node ── tailscaled (subnet router / exit for pod traffic, SNAT)
   │  WireGuard (tailnet)
   ▼
AI host (tailscale 100.x.y.z, or LAN subnet advertised by another router)
```
- `tailscaled` runs on K3S-SRV (host service, not in-cluster) with
  `--advertise-routes=<AI LAN subnet>` on the AI side, or `--accept-routes` on K3S-SRV.
- Pod traffic to the AI CIDR is routed by the host via `tailscale0` (SNAT).
- Tailscale ACLs restrict K3S-SRV to the AI host:port only.

## CIDR to add to the overlay allowlist
Edit `infra/k8s/scanner-worker/overlays/k3s/networkpolicy-ai-egress.yaml` and replace
the placeholder `192.0.2.0/24` with **one** of:
- the AI host's Tailscale IP as `/32` (e.g. `100.101.102.103/32`), preferred; or
- the advertised AI LAN subnet (e.g. `192.168.50.0/24`).

Do **not** use the whole `100.64.0.0/10` tailnet range, and make sure the CIDR does
not overlap node IPs, the pod/service CIDRs (`10.42.0.0/16`, `10.43.0.0/16`), or
`10.43.0.1`. If a broad CIDR is unavoidable, add `except:` entries for those. Set
the port to the AI server port (e.g. 11434 for Ollama) if it isn't 443.

## TODOs
- [ ] Install/enroll tailscaled on K3S-SRV (auth key in secret store, not in git).
- [ ] Decide subnet-router vs direct tailnet IP for the AI host.
- [ ] Tailscale ACL: tag:k3s-srv -> tag:ai-host:<port> only.
- [ ] Verify kube-router enforces policy with pod->tailscale0 SNAT path.
- [ ] Fill the real CIDR/port in the k3s overlay; add AI_BASE_URL to scanner config.
- [ ] TLS on the AI endpoint (or rely on WireGuard + document risk).
- [ ] Connectivity test Job + negative test (apiserver/other IPs blocked).
