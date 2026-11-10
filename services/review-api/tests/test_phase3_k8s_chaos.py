"""Optional cluster chaos test (kind/k3s/k3d). Opt-in: AISCAN_K8S_CHAOS=1 plus a deployed stack
(`kustomize build deploy/platform/overlays/<k3s|local> | kubectl apply -f -`) reachable at AISCAN_K8S_API
(review-api base URL, e.g. via `kubectl port-forward svc/review-api 8001`) with AISCAN_K8S_TOKEN/AISCAN_K8S_USER.

Scenario: create a review -> wait for its scanner pod -> `kubectl delete pod --grace-period=0 --force` (pod kill)
-> wait for the watchdog (HEARTBEAT_TIMEOUT_SECONDS/WATCHDOG_INTERVAL_SECONDS lowered in the overlay) to launch
attempt 2 -> state completed, attempt >= 2, report.json ids unique.
Also: `kubectl rollout restart deploy/review-api` mid-review -> state unchanged, review completes."""
import os
import shutil
import subprocess
import time

import httpx
import pytest

pytestmark = [pytest.mark.k8s_chaos,
              pytest.mark.skipif(os.environ.get("AISCAN_K8S_CHAOS") != "1" or not shutil.which("kubectl"),
                                 reason="set AISCAN_K8S_CHAOS=1 with kubectl + a deployed cluster")]
NS = os.environ.get("AISCAN_K8S_NAMESPACE", "ai-scan-repos")
REPO = os.environ.get("AISCAN_K8S_REPO", "https://github.com/stamparm/DSVW")


def kubectl(*a):
    return subprocess.run(["kubectl", "-n", NS, *a], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def api():
    h = {"X-Internal-Token": os.environ["AISCAN_K8S_TOKEN"], "X-User-Id": os.environ["AISCAN_K8S_USER"]}
    with httpx.Client(base_url=os.environ["AISCAN_K8S_API"], headers=h, timeout=30) as c:
        yield c


def wait(cond, timeout=1800, step=5):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timeout")


def test_scanner_pod_kill_resumes(api):
    rid = api.post("/reviews", json={"repo_url": REPO, "depth": "quick"}).json()["id"]
    pod = wait(lambda: kubectl("get", "pods", "-l", f"review-id={rid}", "-o", "name").strip())
    kubectl("delete", pod, "--grace-period=0", "--force")
    r = wait(lambda: (x := api.get(f"/reviews/{rid}").json())["state"] in ("completed", "failed") and x)
    assert r["state"] == "completed" and r["attempt"] >= 2
    ids = [f["id"] for f in api.get(f"/reviews/{rid}/report.json").json()["findings"]]
    assert len(ids) == len(set(ids))


def test_review_api_rollout_restart_mid_review(api):
    rid = api.post("/reviews", json={"repo_url": REPO, "depth": "quick"}).json()["id"]
    before = api.get(f"/reviews/{rid}").json()
    kubectl("rollout", "restart", "deploy/review-api")
    kubectl("rollout", "status", "deploy/review-api", "--timeout=300s")
    after = wait(lambda: (x := api.get(f"/reviews/{rid}")).status_code == 200 and x.json())
    assert (after["attempt"], after["budget_no"]) == (before["attempt"], before["budget_no"]) or after["state"] == "completed"
    r = wait(lambda: (x := api.get(f"/reviews/{rid}").json())["state"] in ("completed", "failed") and x)
    assert r["state"] == "completed"
