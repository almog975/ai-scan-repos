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

## Jenkins and Argo CD (infra/ingress, applied by the installer)
TLS secrets live in each namespace: **`jenkins-tls`** (ns jenkins) and **`argocd-ingress-tls`** (ns argocd).
Hosts: local `jenkins.ai-scan.local` / `argocd.ai-scan.local`; k3s `jenkins.ai-scan.127.0.0.1.nip.io` /
`argocd.ai-scan.127.0.0.1.nip.io`; eks `jenkins.ai-scan.example.com` / `argocd.ai-scan.example.com` (placeholders).
```bash
for app in jenkins argocd; do
  NS=$app; SECRET=$([ $app = argocd ] && echo argocd-ingress-tls || echo jenkins-tls)
  HOST=$app.ai-scan.local          # k3s: $app.ai-scan.127.0.0.1.nip.io
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 -keyout /tmp/$app.key -out /tmp/$app.crt \
    -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST"
  kubectl -n $NS create secret tls $SECRET --cert=/tmp/$app.crt --key=/tmp/$app.key --dry-run=client -o yaml | kubectl apply -f -
  shred -u /tmp/$app.key; rm -f /tmp/$app.crt
done
echo "127.0.0.1 jenkins.ai-scan.local argocd.ai-scan.local" | sudo tee -a /etc/hosts   # local only
```
- Argo CD: TLS ends at Traefik, so argocd-server runs plain HTTP: Helm `configs.params."server.insecure"=true`
  (chart argo/argo-cd; equivalent to `--insecure`). Don't name the secret `argocd-server-tls`: Argo CD reads that
  name itself. CLI through Traefik: `argocd login argocd.<domain> --grpc-web`.
- Middlewares `security-headers` / `redirect-https` are copied into ns jenkins and argocd, so K3s Traefik's
  `providers.kubernetesCRD.allowCrossNamespace` (default false) can stay off. To share the one in ai-scan-repos
  instead, set it to true via a HelmChartConfig for traefik in kube-system and reference
  `ai-scan-repos-security-headers@kubernetescrd`.
- The Jenkins/Argo CD headers omit the app CSP (both UIs use inline scripts/styles).
