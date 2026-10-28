"""Phase 2: user's stored AI key reaches the Job only via secret env (never argv)."""
from types import SimpleNamespace

from app.config import Settings
from app.launcher import SECRET_LABEL, KubernetesJobLauncher, build_job_manifest
from tests.conftest import TOKEN


def test_local_launcher_gets_user_key_in_env(client, H, launcher):
    FakePopen = launcher._popen
    FakePopen.calls.clear()
    assert client.put("/ai-keys", json={"provider": "anthropic", "api_key": "sk-ant-secret"}, headers=H).status_code == 204
    r = client.post("/reviews", json={"repo_url": "https://github.com/a/b", "ai_provider": "anthropic"}, headers=H)
    assert r.status_code == 201
    cmd, kw = FakePopen.calls[-1]
    assert kw["env"]["AI_API_KEY"] == "sk-ant-secret" and kw["env"]["AI_PROVIDER"] == "anthropic"
    assert "sk-ant-secret" not in " ".join(cmd)


def test_no_provider_no_secret(client, H, launcher):
    FakePopen = launcher._popen
    FakePopen.calls.clear()
    client.post("/reviews", json={"repo_url": "https://github.com/a/b"}, headers=H)
    assert "AI_API_KEY" not in {k: v for k, v in FakePopen.calls[-1][1]["env"].items() if v == "sk-ant-secret"}
    # provider chosen but no key stored -> provider only
    client.post("/reviews", json={"repo_url": "https://github.com/a/b", "ai_provider": "openai_compatible"}, headers=H)
    env = FakePopen.calls[-1][1]["env"]
    assert env["AI_PROVIDER"] == "openai_compatible"


class FakeBatch:
    def create_namespaced_job(self, namespace, body):
        self.body = body
        return SimpleNamespace(metadata=SimpleNamespace(uid="job-uid-1"))


class FakeCore:
    def __init__(self, fail=False):
        self.secrets, self.deleted, self.fail = [], [], fail

    def create_namespaced_secret(self, namespace, body):
        self.secrets.append(body)

    def delete_collection_namespaced_secret(self, namespace, label_selector):
        if self.fail:
            raise RuntimeError("boom")
        self.deleted.append(label_selector)


def test_k8s_per_review_secret_owned_by_job():
    b, c = FakeBatch(), FakeCore()
    l = KubernetesJobLauncher(Settings(), batch_api=b, core_api=c)
    name = l.launch("rev123", 1, secret_env={"AI_PROVIDER": "openai", "AI_API_KEY": "sk-1"})
    sec = c.secrets[0]
    assert sec["metadata"]["name"] == f"{name}-ai" and sec["stringData"]["AI_API_KEY"] == "sk-1"
    assert sec["metadata"]["ownerReferences"][0]["uid"] == "job-uid-1"
    assert sec["metadata"]["labels"][SECRET_LABEL] == "true"
    env_from = b.body["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert env_from[-1] == {"secretRef": {"name": f"{name}-ai"}}
    assert "sk-1" not in str(b.body)
    l.cleanup("rev123")
    assert c.deleted == [f"{SECRET_LABEL}=true,review-id=rev123"]
    KubernetesJobLauncher(Settings(), batch_api=b, core_api=FakeCore(fail=True)).cleanup("x")  # tolerated


def test_k8s_no_secret_when_no_key():
    b, c = FakeBatch(), FakeCore()
    KubernetesJobLauncher(Settings(), batch_api=b, core_api=c).launch("r", 1)
    assert c.secrets == [] and len(build_job_manifest(Settings(), "r", 1, "n")["spec"]["template"]["spec"]["containers"][0]["envFrom"]) == 1


def test_cleanup_called_on_completion(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class Rec:
        def __init__(self):
            self.cleaned = []

        def launch(self, rid, attempt, secret_env=None):
            return "job"

        def cleanup(self, rid):
            self.cleaned.append(rid)
    rec = Rec()
    with TestClient(create_app(settings, launcher=rec, start_watchdog=False)) as c:
        H = {"X-Internal-Token": TOKEN, "X-User-Id": "u"}
        rid = c.post("/reviews", json={"repo_url": "https://github.com/a/b"}, headers=H).json()["id"]
        c.post(f"/internal/reviews/{rid}/progress", json={"progress": 100, "status": "failed"},
               headers={"X-Internal-Token": TOKEN})
        assert rec.cleaned == [rid]
