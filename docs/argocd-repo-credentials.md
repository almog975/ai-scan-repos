# Argo CD repo credentials (runbook, placeholders only)

Almog creates the credentials himself. Never commit a Secret or a token.
Placeholders: `<ARGOCD_HOST>`, `<GITHUB_USER>`, `<GITHUB_PAT>` (fine-grained, read-only Contents on almog975/ai-scan-repos).

## Option A: argocd CLI
```bash
argocd login <ARGOCD_HOST> --username admin --grpc-web
argocd repo add https://github.com/almog975/ai-scan-repos.git --username <GITHUB_USER> --password <GITHUB_PAT>
argocd repo list
```

## Option B: kubectl-applied Secret (created from the CLI, never committed)
```bash
kubectl -n argocd create secret generic repo-ai-scan-repos \
  --from-literal=type=git \
  --from-literal=url=https://github.com/almog975/ai-scan-repos.git \
  --from-literal=username=<GITHUB_USER> \
  --from-literal=password=<GITHUB_PAT>
kubectl -n argocd label secret repo-ai-scan-repos argocd.argoproj.io/secret-type=repository
```
The credential is global (no `project` field) because the root app is in project `default` and the children in `ai-scan`.
If the repo is public, no credential is needed.

## Rotate / remove
`argocd repo rm https://github.com/almog975/ai-scan-repos.git` or `kubectl -n argocd delete secret repo-ai-scan-repos`.
