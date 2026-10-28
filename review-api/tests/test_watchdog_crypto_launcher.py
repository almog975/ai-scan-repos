import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.crypto import KeyCipher
from app.launcher import KubernetesJobLauncher, LocalLauncher, build_job_manifest, make_launcher


def test_encryption_roundtrip():
    c = KeyCipher(Fernet.generate_key().decode())
    tok = c.encrypt("sk-secret")
    assert b"sk-secret" not in tok and c.decrypt(tok) == "sk-secret"
    with pytest.raises(ValueError):
        KeyCipher(Fernet.generate_key().decode()).decrypt(tok)
    with pytest.raises(ValueError):
        KeyCipher("")


def test_job_manifest_hardened():
    s = Settings()
    m = build_job_manifest(s, "abc123", 2, "scan-abc")
    pod = m["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] and pod["securityContext"]["runAsUser"] == 10001
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert c["args"] == ["--review-id", "abc123"]
    assert {"name": "work", "emptyDir": {"sizeLimit": "2Gi"}} in pod["volumes"]
    assert any(v.get("persistentVolumeClaim") for v in pod["volumes"])
    assert c["envFrom"][0]["secretRef"]["name"] == s.scanner_secret_name
    assert m["spec"]["backoffLimit"] == 0
    assert not any("TOKEN" in e["name"] or "KEY" in e["name"] for e in c["env"])


def test_k8s_launcher_uses_api():
    class FakeBatch:
        def create_namespaced_job(self, namespace, body):
            self.ns, self.body = namespace, body
    api = FakeBatch()
    name = KubernetesJobLauncher(Settings(), batch_api=api, core_api=object()).launch("rid", 1)
    assert api.ns == "ai-scan-repos" and api.body["metadata"]["name"] == name


def test_make_launcher():
    assert isinstance(make_launcher(Settings(job_launcher="local")), LocalLauncher)
    with pytest.raises(ValueError):
        make_launcher(Settings(job_launcher="nope"))


def test_local_launcher_passes_no_secret_args(tmp_path):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
    LocalLauncher(Settings(reports_dir=str(tmp_path)), popen=popen).launch("rid", 3)
    assert seen["cmd"][-2:] == ["--review-id", "rid"] and seen["env"]["ATTEMPT"] == "3"
