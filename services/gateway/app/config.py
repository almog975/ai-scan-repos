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
    # OAuth redirect_uri. Empty = derived: PUBLIC_URL + /auth/github/callback (the single source of truth). Set only
    # when the GitHub OAuth App must use a different URL than the browser origin.
    github_callback_url: str = ""
    # Space/comma-separated scopes asked at login. Login only reads GET /user (login, id, avatar, public email) and the
    # token is discarded after login, so read:user is enough. Full "repo" grants write access to every private repo
    # and triggers org "access restricted" (OAuth App access restrictions) notices; review-api clones with its own
    # GITHUB_TOKEN, not the user's token.
    github_oauth_scopes: str = "read:user"
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
    # Webhook replay protection. GitHub signs only the body (no signed timestamp header), so:
    #  - every accepted body's sha256 is remembered for this many seconds; the same body under a new
    #    X-GitHub-Delivery id is a 409 (a replay can't retrigger a review);
    #  - push payloads carry repository.pushed_at (inside the signed body); one older than the window is
    #    rejected, so once a hash expires the old body is already too old. Other events rely on the hash.
    webhook_replay_window_seconds: int = 86400
    # A "pending" claim (delivery id or payload hash) older than this belongs to a gateway that died mid-processing:
    # a new delivery may take it over atomically, and the purge removes it. Must be >= 3x REVIEW_API_TIMEOUT_SECONDS
    # (checked at startup, see webhook_problems()).
    webhook_pending_reclaim_seconds: int = 300
    # Timeout of each gateway -> review-api call. A webhook's whole processing (sender lookup + POST /reviews) is cut off
    # after 2x this (webhook_deadline_seconds), always shorter than the reclaim window, so a live gateway never holds a
    # claim that another delivery may reclaim. A cut-off delivery gets 502 and is released; a retry is deduplicated by
    # review-api through the Idempotency-Key (event+body hash).
    review_api_timeout_seconds: float = 20.0

    @property
    def oauth_callback_url(self) -> str:
        return self.github_callback_url.strip() or f"{self.public_url.rstrip('/')}/auth/github/callback"

    @property
    def oauth_scope(self) -> str:
        return " ".join(x for x in self.github_oauth_scopes.replace(",", " ").split() if x)

    @property
    def webhook_deadline_seconds(self) -> float:
        return 2 * self.review_api_timeout_seconds

    def webhook_problems(self) -> list[str]:
        """Webhook timing errors; checked at every start (any APP_ENV). Empty list = OK."""
        out = []
        if self.review_api_timeout_seconds <= 0:
            out.append("REVIEW_API_TIMEOUT_SECONDS must be > 0")
        elif self.webhook_pending_reclaim_seconds < 3 * self.review_api_timeout_seconds:
            out.append(f"WEBHOOK_PENDING_RECLAIM_SECONDS ({self.webhook_pending_reclaim_seconds}) must be >= 3x "
                       f"REVIEW_API_TIMEOUT_SECONDS ({self.review_api_timeout_seconds:g})")
        elif self.webhook_deadline_seconds >= self.webhook_pending_reclaim_seconds:
            out.append("webhook processing deadline must be shorter than WEBHOOK_PENDING_RECLAIM_SECONDS")
        return out

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
