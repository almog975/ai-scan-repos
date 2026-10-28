# TLS runbook (Secret `ai-scan-tls`, ns ai-scan-repos)

The IngressRoute `ai-scan` listens on Traefik entryPoint `websecure` with `tls.secretName: ai-scan-tls`;
`ai-scan-http` on `web` only redirects to https (Middleware `redirect-https`). **No key or cert is committed.**

## local / k3s: self-signed (default)
```bash
HOST=ai-scan.local            # k3s: ai-scan.127.0.0.1.nip.io
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout /tmp/ai-scan.key -out /tmp/ai-scan.crt \
  -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST"
kubectl -n ai-scan-repos create secret tls ai-scan-tls --cert=/tmp/ai-scan.crt --key=/tmp/ai-scan.key \
  --dry-run=client -o yaml | kubectl apply -f -
shred -u /tmp/ai-scan.key; rm -f /tmp/ai-scan.crt
```
Browsers will warn (self-signed); trust the cert locally or use `curl -k` for checks.

## local / k3s: cert-manager (if installed)
```bash
kubectl get crd certificates.cert-manager.io && kubectl apply -f - <<'Y'
apiVersion: cert-manager.io/v1
kind: Issuer
metadata: {name: selfsigned, namespace: ai-scan-repos}
spec: {selfSigned: {}}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: ai-scan-tls, namespace: ai-scan-repos}
spec:
  secretName: ai-scan-tls
  dnsNames: [ai-scan.local]
  issuerRef: {name: selfsigned, kind: Issuer}
Y
```

## EKS (placeholder)
Either terminate TLS at the AWS load balancer with an ACM certificate (then drop `tls` from the IngressRoute
and route on `web`), or install cert-manager with a Let's Encrypt ClusterIssuer issuing `ai-scan-tls` for
`ai-scan.example.com`. Decision pending.

## /etc/hosts for ai-scan.local
```bash
echo "127.0.0.1 ai-scan.local" | sudo tee -a /etc/hosts      # Docker Desktop / k3d on this machine
# remote K3s: use the node IP instead of 127.0.0.1
```
The k3s overlay uses `ai-scan.127.0.0.1.nip.io`, which needs no hosts entry.

## Verify
```bash
curl -kI https://ai-scan.local/                     # security headers present (CSP, HSTS, X-Frame-Options DENY)
curl -I  http://ai-scan.local/                      # 308 -> https
curl -kN https://ai-scan.local/api/reviews/<id>/progress -H 'Cookie: <session>'   # SSE streams without buffering
```

## Notes
- SSE route uses `PathRegexp` (Traefik v3 rule syntax, K3s >= 1.32). On Traefik v2 use
  `Path(\`/api/reviews/{id:[^/]+}/progress\`)` instead.
- Never add the `buffering` middleware to `/api` routes; it would break SSE.
