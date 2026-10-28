import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.auth import ensure_user
from app.config import Settings
from app.launcher import LocalLauncher
from app.main import create_app

TOKEN = "test-internal-token"
KEY = "00" * 32  # 32-byte AES key, hex (tests only)


class FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append((cmd, kw))


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url="sqlite:///:memory:", internal_api_token=TOKEN,
                    ai_keys_encryption_key=KEY,
                    reports_dir=str(tmp_path / "reports"), watchdog_enabled=False)


@pytest.fixture
def launcher(settings):
    return LocalLauncher(settings, popen=FakePopen)


@pytest.fixture
def app(settings, launcher):
    return create_app(settings, launcher=launcher, start_watchdog=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def users(client):
    with dbmod.session_factory()() as db:
        admin = ensure_user(db, 1, "admin")
        user = ensure_user(db, 2, "alice")
        return admin.id, user.id


def hdr(uid):
    return {"X-Internal-Token": TOKEN, "X-User-Id": uid}


@pytest.fixture
def A(users):
    return hdr(users[0])


@pytest.fixture
def H(users):
    return hdr(users[1])
