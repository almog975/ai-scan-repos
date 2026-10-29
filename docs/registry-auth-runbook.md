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
Credential ID **`registry-push`** (Username with password), used by the Jenkinsfile Push stage via
`withCredentials([usernamePassword(credentialsId: 'registry-push', ...)])`. It is an **Opaque** K8s Secret in ns
jenkins (not docker-registry), surfaced to Jenkins by the kubernetes-credentials-provider plugin:
```bash
kubectl -n jenkins create secret generic registry-push \
  --from-literal=username=<PUSH_USER> --from-literal=password='<PUSH_PASSWORD>'
kubectl -n jenkins label secret registry-push jenkins.io/credentials-type=usernamePassword
kubectl -n jenkins annotate secret registry-push jenkins.io/credentials-description='registry push user'
```
No click-ops credential in the Jenkins UI.

## 2b. Pull secret regcred in ns jenkins (agent image)
The agent pod template pulls `<REGISTRY_HOST>/ai-scan/jenkins-agent:<sha>` (SA `jenkins-agent`, imagePullSecrets `regcred`):
```bash
kubectl -n jenkins create secret docker-registry regcred \
  --docker-server=<REGISTRY_HOST> --docker-username=<PULL_USER> --docker-password='<PULL_PASSWORD>' \
  --dry-run=client -o yaml | kubectl apply -f -
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
