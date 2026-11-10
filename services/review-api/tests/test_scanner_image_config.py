"""SCANNER_IMAGE: strict in production, optional / :latest allowed in dev (Docker Desktop)."""
import pytest

from app.config import Settings

KEY = "a" * 64
PROD = dict(app_env="production", database_url="postgresql://u:p@h/db", internal_api_token="t" * 40,
            ai_keys_encryption_key=KEY, job_launcher="kubernetes")


@pytest.mark.parametrize("img", ["reg/scanner:latest", "reg/scanner", ""])
def test_production_rejects_unpinned_or_unset(img):
    assert any("SCANNER_IMAGE" in p for p in Settings(scanner_image=img, **PROD).problems())


def test_production_accepts_pinned_and_skip_flag():
    assert Settings(scanner_image="ghcr.io/a/s:sha-94304ff", **PROD).problems() == []
    assert Settings(scanner_image="reg/s:latest", skip_scanner_validation=True, **PROD).problems() == []


@pytest.mark.parametrize("env", ["local", "dev", "development", "LOCAL"])
def test_dev_envs_start_with_latest_or_unset(env):
    for img in ("reg/s:latest", ""):
        assert Settings(app_env=env, scanner_image=img).problems() == []
        assert Settings(app_env=env, scanner_image="reg/s:latest").scanner_image_problem() is None


def test_environment_dev_alias_and_env_vars(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("SCANNER_IMAGE", "reg/s:latest")
    assert Settings().scanner_image_problem() is None
    monkeypatch.delenv("ENVIRONMENT")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SKIP_SCANNER_VALIDATION", "true")
    assert Settings().scanner_image_problem() is None


def test_create_review_without_scanner_image_is_clear_503(client, H, settings):
    client.app.state.settings = settings.model_copy(update={"job_launcher": "kubernetes", "scanner_image": ""})
    r = client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "quick"}, headers=H)
    assert r.status_code == 503 and "SCANNER_IMAGE is not set" in r.json()["error"]["message"]
