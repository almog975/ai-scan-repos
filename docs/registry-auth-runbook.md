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
**In-cluster address:** pods (Jenkins agents: kaniko/crane push) must use `registry.registry:5000` (Service `registry`
in ns `registry`, plain HTTP inside the cluster, htpasswd auth). The external names below go through Traefik and are
for the kubelet/node and for pushes from outside the cluster; `*.127.0.0.1.nip.io` resolves to 127.0.0.1 inside a pod,
so it cannot be used for in-cluster pushes. NetworkPolicies allow 5000 only from ns `jenkins` and Traefik.

`<REGISTRY_HOST>`: local `registry.ai-scan.local`, k3s `registry.registry:5000` (in-cluster Service; nodes reach it via the registries.yaml mirror → NodePort 30500, see infra/bootstrap/registry/registries.yaml.example), eks `registry.ai-scan.example.com`.
Referenced by (in git): SA `default` (infra/k8s/platform/base/default-sa.yaml; frontend, gateway, migrations Job),
SA `review-api`, SA `scanner`, and the scanner Job template pod spec. Without `regcred` pods fail with ImagePullBackOff.
On K3s, containerd also needs the creds in `/etc/rancher/k3s/registries.yaml` only if pulls bypass the kubelet
(they don't for normal pods); see infra/bootstrap/registry/registries.yaml.example for the mirror/TLS config.

## Rotate
Recreate the Secret with new values (same commands), update the Jenkins credential, then
`kubectl -n registry rollout restart deploy/registry`.

## NodePort 30500 (K3s): keep it node-local
The registry Service is NodePort 30500 so containerd on the node can pull `registry.registry:5000/...` through the
`registries.yaml` mirror (`http://127.0.0.1:30500`). It must not be reachable from the LAN/internet (plain HTTP, basic auth):
1. K3s server flag (in `/etc/rancher/k3s/config.yaml`): `kube-proxy-arg: ["nodeport-addresses=127.0.0.0/8"]`, then
   `systemctl restart k3s`. NodePorts then listen only on loopback (affects every NodePort Service; Traefik uses
   servicelb hostPorts, not NodePorts).
2. Firewall, belt and braces: `ufw deny in on <ext-if> to any port 30500 proto tcp` (or
   `iptables -I INPUT ! -i lo -p tcp --dport 30500 -j DROP`, persisted).
3. Check from another host: `curl -m3 http://<node-ip>:30500/v2/` must time out.
`externalTrafficPolicy: Local` keeps remote client IPs, so the registry NetworkPolicy also rejects them; it is not a
substitute for 1+2 (NetworkPolicy on NodePort traffic depends on the CNI's SNAT handling).
