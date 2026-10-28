from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from app import db as dbmod
from app.config import Settings
from app.crypto import KeyCipher
from app.launcher import KubernetesJobLauncher, LocalLauncher, build_job_manifest, make_launcher
from app.models import Depth, Review, ReviewStatus
from app.watchdog import check_stalled


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


class RecLauncher:
    def __init__(self):
        self.calls = []

    def launch(self, rid, attempt, secret_env=None):
        self.calls.append((rid, attempt))
        return f"job-{attempt}"


@pytest.fixture
def db():
    dbmod.init_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(dbmod.get_engine())
    with dbmod.session_factory()() as s:
        yield s


def test_watchdog_retries_then_fails(db):
    clock = FakeClock()
    r = Review(owner_id="u", repo_url="https://github.com/a/b", depth=Depth.quick,
               status=ReviewStatus.running, attempts=1, last_heartbeat=clock())
    db.add(r)
    db.commit()
    l = RecLauncher()
    kw = dict(timeout_seconds=900, max_attempts=5, now=clock)

    clock.advance(600)
    assert check_stalled(db, l, **kw) == []  # still fresh
    for expected in (2, 3, 4, 5):
        clock.advance(901)
        check_stalled(db, l, **kw)
        assert r.attempts == expected and r.status == ReviewStatus.running
    assert [a for _, a in l.calls] == [2, 3, 4, 5]
    assert r.job_name == "job-5"
    clock.advance(901)
    check_stalled(db, l, **kw)
    assert r.status == ReviewStatus.failed and len(l.calls) == 4


def test_watchdog_ignores_paused_and_heartbeating(db):
    clock = FakeClock()
    p = Review(owner_id="u", repo_url="https://github.com/a/b", depth=Depth.quick,
               status=ReviewStatus.paused, attempts=1, last_heartbeat=clock())
    db.add(p)
    db.commit()
    clock.advance(10_000)
    assert check_stalled(db, RecLauncher(), timeout_seconds=900, max_attempts=5, now=clock) == []


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
