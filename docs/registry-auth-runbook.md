# Registry auth runbook (placeholders only, nothing committed)

Registry `registry:2.8.3` in ns `registry` uses htpasswd auth (`REGISTRY_AUTH=htpasswd`), reading
`/auth/htpasswd` from Secret **`registry-htpasswd`** (key `htpasswd`). Two users: a push user for Jenkins and a
read-only pull user for the cluster. (registry:2 htpasswd has no per-user ACLs: "read-only" is a convention;
only the pull user's password lives in the cluster.)

## 1. Secret registry-htpasswd (ns registry)
```bash
htpasswd -Bbn <PUSH_USER> '<PUSH_PASSWORD>' >  /tmp/htpasswd     # bcrypt is required by registry:2
htpasswd -Bbn <PULL_USER> '<PULL_PASSWORD>' >> /tmp/htpasswd
# no htpasswd binary: docker run --rm httpd:2.4-alpine htpasswd -Bbn <USER> '<PASSWORD>'
kubectl -n registry create secret generic registry-htpasswd --from-file=htpasswd=/tmp/htpasswd \
  --dry-run=client -o yaml | kubectl apply -f -
shred -u /tmp/htpasswd
kubectl -n registry rollout restart deploy/registry
```

## 2. Jenkins push credential (ns jenkins)
The Jenkinsfile uses credential ID **`registry-push`** (Username with password), via
`withCredentials([usernamePassword(credentialsId: 'registry-push', ...)])`.
Create it in Jenkins: *Manage Jenkins > Credentials > System > Global > Add* — Kind "Username with password",
ID `registry-push`, username `<PUSH_USER>`, password `<PUSH_PASSWORD>`.
Same name for the Kubernetes Secret (needed by kaniko/buildkit agents that read a docker config file): **`registry-push`**:
```bash
kubectl -n jenkins create secret docker-registry registry-push \
  --docker-server=<REGISTRY_HOST> --docker-username=<PUSH_USER> --docker-password='<PUSH_PASSWORD>'
```

## 3. Pull secret regcred (ns ai-scan-repos)
```bash
kubectl -n ai-scan-repos create secret docker-registry regcred \
  --docker-server=<REGISTRY_HOST> --docker-username=<PULL_USER> --docker-password='<PULL_PASSWORD>' \
  --dry-run=client -o yaml | kubectl apply -f -
```
`<REGISTRY_HOST>`: local `registry.ai-scan.local`, k3s `registry.ai-scan.127.0.0.1.nip.io`, eks `registry.ai-scan.example.com`.
Referenced by (in git): SA `default` (deploy/platform/base/default-sa.yaml; frontend, gateway, migrations Job),
SA `review-api`, SA `scanner`, and the scanner Job template pod spec. Without `regcred` pods fail with ImagePullBackOff.
On K3s, containerd also needs the creds in `/etc/rancher/k3s/registries.yaml` only if pulls bypass the kubelet
(they don't for normal pods); see infra/registry/registries.yaml.example for the mirror/TLS config.

## Rotate
Recreate the Secret with new values (same commands), update the Jenkins credential, then
`kubectl -n registry rollout restart deploy/registry`.
