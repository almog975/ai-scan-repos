cd /workspace/verify-r2/p10/scan
trivy fs --scanners vuln,secret,misconfig --format json -o trivy-fs.json /workspace/aiscan-pr10 > trivy-fs.log 2>&1
trivy config --format json -o trivy-config-manifests.json manifests > trivy-cm.log 2>&1
for c in gateway review-api scanner-worker frontend ci/agent; do n=$(echo $c|tr / -); trivy fs --scanners vuln,secret,misconfig --include-dev-deps --format json -o trivy-comp-$n.json /workspace/aiscan-pr10/$c > /dev/null 2>&1; done
gitleaks dir /workspace/aiscan-pr10 --no-banner -f json -r gitleaks-tree.json > gitleaks-tree.log 2>&1; echo "tree rc=$?" >> gitleaks-tree.log
gitleaks git /workspace/aiscan-pr10 --no-banner -f json -r gitleaks-hist.json > gitleaks-hist.log 2>&1; echo "hist rc=$?" >> gitleaks-hist.log
for o in k3s local eks; do for fw in nsa mitre; do kubescape scan framework $fw all-$o.yaml --format json --output ks-$o-$fw.json > ks-$o-$fw.log 2>&1; done; done
cd /workspace/aiscan-pr10 && semgrep scan --config p/default --config p/python --config p/security-audit --config p/secrets --config p/typescript --config p/react --json -o /workspace/verify-r2/p10/scan/semgrep.json --metrics=off > /workspace/verify-r2/p10/scan/semgrep.log 2>&1
echo SCANS-DONE >> /workspace/verify-r2/p10/scan/done
