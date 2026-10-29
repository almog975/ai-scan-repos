from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # dev | production. production (every K8s overlay) refuses to start with dev defaults; see problems().
    app_env: str = "dev"
    log_level: str = "INFO"
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
    session_cookie: str = "aiscan_session"
    csrf_cookie: str = "aiscan_csrf"
    # 429 RATE_LIMITED: requests/minute per client IP on /auth/* + /webhooks/*, per user on /api/*
    rate_limit_auth_per_minute: int = 30
    rate_limit_api_per_minute: int = 300

    def problems(self) -> list[str]:
        """Config errors that make a production start unsafe. Empty list = OK. Config only comes from env."""
        if self.app_env.lower() != "production":
            return []
        out = []
        if self.database_url.startswith("sqlite"):
            out.append("DATABASE_URL must point at PostgreSQL in production")
        if self.internal_api_token in ("", "change-me-dev-only") or len(self.internal_api_token) < 32:
            out.append("INTERNAL_API_TOKEN must be set (>= 32 chars, not the dev default)")
        if not self.github_client_id or not self.github_client_secret:
            out.append("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set")
        if not self.public_url.startswith("https://"):
            out.append("PUBLIC_URL must be https:// in production")
        if not self.cookie_secure:
            out.append("COOKIE_SECURE must be true in production")
        return out

    def logins(self, raw: str) -> set[str]:
        return {x.strip().lower() for x in raw.split(",") if x.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
