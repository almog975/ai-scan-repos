import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.launcher import LocalLauncher
from app.main import create_app

TOKEN = "test-internal-token"


class FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append((cmd, kw))


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url="sqlite:///:memory:", internal_api_token=TOKEN,
                    ai_keys_encryption_key=Fernet.generate_key().decode(),
                    reports_dir=str(tmp_path / "reports"), watchdog_enabled=False)


@pytest.fixture
def launcher(settings):
    return LocalLauncher(settings, popen=FakePopen)


@pytest.fixture
def client(settings, launcher):
    app = create_app(settings, launcher=launcher, start_watchdog=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def H():
    return {"X-Internal-Token": TOKEN, "X-User-Id": "u1"}
