# Kustomize layout

```
deploy/<component>/base               component manifests
deploy/<component>/overlays/{local,k3s,eks}
components: platform (namespaced only: Traefik IngressRoute + middlewares, reports PVC, ServiceMonitors),
            postgres (StatefulSet, headless svc, postgres-data PVC),
            frontend, gateway, review-api (Deployment + ClusterIP svc),
            review-api also holds SA review-api + Role/RoleBinding (jobs, pods) + SA scanner
            scanner-worker (ConfigMap scanner-job-template only; overlays generate ConfigMap scanner-image)
infra/bootstrap/{base,overlays/{local,k3s,eks}}  cluster-scoped: 5 Namespaces, PV ai-scan-reports-pv + SC reports-local (k3s),
                    SC efs-sc (eks). Not Argo CD-managed; installer runs kubectl apply -k before Argo CD.
infra/ingress/{base,overlays/{local,k3s,eks}}  IngressRoutes jenkins.<domain> (ns jenkins) + argocd.<domain> (ns argocd); installer-applied.
infra/registry      registry:2 (ns registry) + K3s registries.yaml example
infra/helm/kube-prometheus-stack   values + install.sh
infra/installer     wizard image + install scripts; infra/installer/terraform/eks
```
Single namespace `ai-scan-repos`. Overlays are per target cluster; there is no dev/prod split.
Image tags (`<registry>/ai-scan/<svc>:<git-sha>`) and Argo CD Applications belong to Pipelines and are not set here.
Base host `ai-scan.local`; k3s uses `ai-scan.<ip>.nip.io`, eks uses `ai-scan.example.com`. Subdomains: jenkins., argocd., grafana., registry.

## Images
Each overlay of frontend, gateway, and review-api has:
```yaml
images:
  - name: ai-scan/<component>
    newName: <registry-host>/ai-scan/<component>
    newTag: GIT_SHA_PLACEHOLDER
```
Jenkins: `cd deploy/<c>/overlays/<o> && kustomize edit set image ai-scan/<c>=<registry-host>/ai-scan/<c>:$GIT_SHA`

## SCANNER_IMAGE
Kustomize `images:` does not rewrite text inside ConfigMap data, so the scanner image goes through a generated ConfigMap.
`deploy/scanner-worker/overlays/<o>` generates ConfigMap `scanner-image` (no hash suffix) with the literal
`SCANNER_IMAGE=<registry-host>/ai-scan/scanner-worker:<sha>`. review-api reads it through `configMapKeyRef`
and substitutes it for `${IMAGE}` in `scanner-job-template`. Jenkins updates it with either of these:
```bash
cd deploy/scanner-worker/overlays/$OVERLAY
kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=$REGISTRY/ai-scan/scanner-worker:$GIT_SHA
# or
sed -i "s#SCANNER_IMAGE=.*#SCANNER_IMAGE=$REGISTRY/ai-scan/scanner-worker:$GIT_SHA#" kustomization.yaml
```
The Jenkinsfile uses `kustomize edit set configmap` (kustomize >= v5.1) and falls back to the sed command.
review-api only reads env at pod start, so a SCANNER_IMAGE change needs `kubectl rollout restart deploy/review-api`. Argo CD does not restart it on a ConfigMap change.
