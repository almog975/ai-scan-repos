import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app import db as dbmod
from app.config import Settings
from app.launcher import job_name_for, JobSpec, KubernetesJobLauncher, LocalLauncher, build_job_manifest, make_launcher
from app.models import Review

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "deploy/scanner-worker/base/scanner-job-template.yaml"
RID = "0f8fad5b-d9cb-469f-a165-70867728950e"
SPEC = JobSpec(RID, 2, 3, "deep", "https://github.com/a/b", "c" * 40, "main")


def env(c):
    return {e["name"]: e["value"] for e in c["env"]}


@pytest.mark.parametrize("template", ["", str(TPL)])
def test_manifest_contract(template, tmp_path):
    s = Settings(scanner_job_template=template, scanner_image="reg/ai-scan/scanner-worker:abc123")
    m = build_job_manifest(s, SPEC, ai_secret="scan-x-ai")
    assert m["metadata"]["name"] == f"scan-{RID}-a2" and len(m["metadata"]["name"]) <= 63
    assert m["metadata"]["labels"] == {"app": "scanner", "review-id": RID, "attempt": "2", "budget-no": "3"}
    assert m["spec"]["activeDeadlineSeconds"] == 21600 and m["spec"]["backoffLimit"] == 0
    pod = m["spec"]["template"]["spec"]
    c = pod["containers"][0]
    e = env(c)
    assert (e["DEPTH"], e["ATTEMPT"], e["BUDGET_NO"], e["COMMIT_SHA"], e["REVIEW_ID"]) == ("deep", "2", "3", "c" * 40, RID)
    assert c["image"] == s.scanner_image and c["args"] == ["--review-id", RID]
    assert {"name": "reports", "persistentVolumeClaim": {"claimName": "reports"}} in pod["volumes"]
    assert {"name": "reports", "mountPath": "/reports"} in c["volumeMounts"]
    assert [x["secretRef"]["name"] for x in c["envFrom"]] == ["scanner-secrets", "scan-x-ai"]
    assert pod["securityContext"]["runAsNonRoot"] and c["securityContext"]["readOnlyRootFilesystem"]
    assert pod["serviceAccountName"] == "scanner" and pod["automountServiceAccountToken"] is False
    assert not any("KEY" in k or "TOKEN" in k for k in e)
    assert "${" not in json.dumps(m)
    kc = shutil.which("kubeconform") or str(Path.home() / ".local/bin/kubeconform")
    if Path(kc).exists():
        f = tmp_path / "job.yaml"
        f.write_text(yaml.safe_dump(m))
        out = subprocess.run([kc, "-strict", "-summary", str(f)], capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("img", ["reg/scanner", "reg/scanner:latest"])
def test_image_must_be_pinned(img):
    with pytest.raises(ValueError):
        build_job_manifest(Settings(scanner_image=img), SPEC)
    build_job_manifest(Settings(scanner_image="reg/scanner@sha256:" + "a" * 64), SPEC)


def test_template_rejects_unsafe_values():
    bad = JobSpec(RID, 1, 1, "quick", 'https://github.com/a/b"\n  evil: x', None)
    with pytest.raises(ValueError):
        build_job_manifest(Settings(scanner_job_template=str(TPL)), bad)


class ApiErr(Exception):
    def __init__(self, status):
        self.status = status


class FakeBatch:
    def __init__(self, conflicts=0):
        self.created, self.deleted, self.conflicts = [], [], conflicts

    def create_namespaced_job(self, namespace, body):
        if self.conflicts:
            self.conflicts -= 1
            raise ApiErr(409)
        self.created.append(body)
        return type("J", (), {"metadata": type("M", (), {"uid": "uid-1"})()})()

    def delete_namespaced_job(self, name, namespace, propagation_policy):
        self.deleted.append((name, propagation_policy))
        raise ApiErr(404)


class FakeCore:
    def __init__(self):
        self.secrets = []

    def create_namespaced_secret(self, namespace, body):
        self.secrets.append(body)

    def delete_namespaced_secret(self, name, namespace):
        self.deleted = getattr(self, "deleted", []) + [name]


def test_k8s_launcher_secret_owned_and_conflict_retry():
    b, c = FakeBatch(conflicts=1), FakeCore()
    k = KubernetesJobLauncher(Settings(), batch_api=b, core_api=c, sleep=lambda s: None)
    name = k.launch(SPEC, secret_env={"AI_API_KEY": "sk"})
    assert b.deleted == [(name, "Background")]  # stale same-name Job from an older budget
    assert b.created[0]["metadata"]["name"] == name
    sec = c.secrets[0]
    assert sec["metadata"]["ownerReferences"][0]["uid"] == "uid-1" and sec["stringData"] == {"AI_API_KEY": "sk"}
    k.delete(name)  # 404 ignored
    k.cleanup(RID)  # by known names only (RBAC has no list/deletecollection)
    assert c.deleted == [f"{job_name_for(RID, n)}-ai" for n in range(1, 6)]


def test_make_launcher():
    assert isinstance(make_launcher(Settings(job_launcher="local")), LocalLauncher)
    with pytest.raises(ValueError):
        make_launcher(Settings(job_launcher="nope"))


def test_local_launcher_env_only(tmp_path):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
    LocalLauncher(Settings(reports_dir=str(tmp_path)), popen=popen).launch(SPEC, {"AI_API_KEY": "sk"})
    assert seen["cmd"][-2:] == ["--review-id", RID] and "sk" not in " ".join(seen["cmd"])
    assert seen["env"]["ATTEMPT"] == "2" and seen["env"]["BUDGET_NO"] == "3" and seen["env"]["AI_API_KEY"] == "sk"


def test_review_gets_decrypted_provider_key(client, A, H, launcher, monkeypatch):
    got = {}
    orig = launcher.launch
    monkeypatch.setattr(launcher, "launch", lambda spec, secret_env=None: got.update(env=secret_env) or orig(spec, secret_env))
    p = client.post("/admin/ai-providers", json={"name": "o", "kind": "onprem", "base_url": "http://ollama:11434",
                                                 "model": "llama3", "api_key": "k-123456"}, headers=A).json()
    r = client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "deep", "provider_id": p["id"]}, headers=H)
    assert r.status_code == 201
    assert got["env"] == {"AI_PROVIDER": "ollama", "AI_KIND": "onprem", "AI_PROVIDER_NAME": "o", "AI_MODEL": "llama3", "AI_BASE_URL": "http://ollama:11434", "AI_API_KEY": "k-123456"}


def test_job_status():
    class P:
        def __init__(self, rc):
            self.rc = rc

        def poll(self):
            return self.rc
    ll = LocalLauncher(Settings(), popen=lambda *a, **k: None)
    assert ll.status("nope") == "missing"
    ll.procs.update(a=P(None), b=P(0), c=P(2))
    assert [ll.status(n) for n in "abc"] == ["active", "succeeded", "failed"]

    class St:
        def __init__(self, **kw):
            self.succeeded, self.failed = kw.get("s"), kw.get("f")

    class Api:
        def __init__(self, res):
            self.res = res

        def read_namespaced_job(self, name, namespace):  # get jobs only (no jobs/status)
            if isinstance(self.res, Exception):
                raise self.res
            return type("J", (), {"status": self.res})()

    class E(Exception):
        status = 404
    s = Settings()
    assert KubernetesJobLauncher(s, Api(St(s=1)), object()).status("j") == "succeeded"
    assert KubernetesJobLauncher(s, Api(St(f=1)), object()).status("j") == "failed"
    assert KubernetesJobLauncher(s, Api(St()), object()).status("j") == "active"
    assert KubernetesJobLauncher(s, Api(E()), object()).status("j") == "missing"


def test_repo_private_from_github_check_reaches_worker_env():
    """repo.private in report.json comes from the GitHub /repos check stored on the review (REPO_PRIVATE)."""
    from types import SimpleNamespace

    from app.launcher import JobSpec, contract_env
    r = SimpleNamespace(id="r1", attempt=1, budget_no=1, depth="quick", repo_url="https://github.com/o/r",
                        commit_sha=None, ref="main", repo_private=True)
    env = {e["name"]: e["value"] for e in contract_env(JobSpec.of(r))}
    assert env["REPO_PRIVATE"] == "true"
    r.repo_private = False
    assert {e["name"]: e["value"] for e in contract_env(JobSpec.of(r))}["REPO_PRIVATE"] == "false"
