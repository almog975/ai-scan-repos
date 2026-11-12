import pytest

from app.config import Settings

TOKEN = "t" * 40


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Settings reads the process env: a developer/CI shell with GITHUB_CLIENT_ID etc. set must not change the result
    for name, f in Settings.model_fields.items():
        for k in {name.upper(), (f.alias or name).upper(), *[a.upper() for a in getattr(f.validation_alias, "choices", []) if isinstance(a, str)]}:
            monkeypatch.delenv(k, raising=False)


def test_dev_ok():
    assert Settings().problems() == []


def test_production_requires_real_config():
    text = " ".join(Settings(app_env="production", cookie_secure=False).problems())
    for k in ("DATABASE_URL", "INTERNAL_API_TOKEN", "GITHUB_CLIENT_ID", "PUBLIC_URL", "COOKIE_SECURE"):
        assert k in text


def test_production_ok():
    s = Settings(app_env="production", database_url="postgresql+psycopg://u:p@postgres/aiscan", internal_api_token=TOKEN,
                 github_client_id="id", github_client_secret="sec", public_url="https://ai-scan.local")
    assert s.problems() == []


def test_get_app_exits(monkeypatch):
    from app import config, main
    monkeypatch.setenv("APP_ENV", "production")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit):
            main.get_app()
    finally:
        config.get_settings.cache_clear()
