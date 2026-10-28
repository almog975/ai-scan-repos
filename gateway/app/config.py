from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./gateway.db"
    review_api_url: str = "http://review-api:8001"
    internal_api_token: str = "change-me-dev-only"
    github_client_id: str = ""
    github_client_secret: str = ""
    github_oauth_base: str = "https://github.com"
    github_api_base: str = "https://api.github.com"
    # Public URL of the app (frontend origin), used for redirect_uri and post-login redirect
    public_url: str = "http://localhost:8080"
    # Comma-separated GitHub logins allowed to log in; empty = any GitHub user
    allowed_github_logins: str = ""
    admin_github_logins: str = ""
    github_webhook_secret: str = ""
    webhook_auto_review: bool = False
    webhook_review_depth: str = "quick"
    session_ttl_hours: int = 12
    cookie_secure: bool = True
    session_cookie: str = "aisr_session"
    csrf_cookie: str = "aisr_csrf"

    def logins(self, raw: str) -> set[str]:
        return {x.strip().lower() for x in raw.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
