# infra/bootstrap – cluster-scoped objects (NOT managed by Argo CD)
Namespaces (ai-scan-repos, jenkins, argocd, monitoring, registry), PV `ai-scan-reports-pv` + StorageClass
`reports-local` (k3s), StorageClass `efs-sc` (eks). Applied by the installer scripts before Argo CD:
`kubectl apply -k infra/bootstrap/overlays/<local|k3s|eks>`. deploy/platform holds only namespaced objects.
