"""Runtime configuration (all from environment variables)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    # dev | production. production (every K8s overlay) refuses to start with dev defaults; see problems().
    app_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./review-api.db"
    # Shared secret between gateway/worker and review-api. Required in prod.
    internal_api_token: str = "change-me-dev-only"
    # AES-256-GCM master key from Secret ai-keys-master: 32 bytes, hex or base64. Generate: openssl rand -hex 32
    ai_keys_encryption_key: str = ""
    # local | kubernetes
    # Optional GitHub token for API calls (repo metadata / ref resolution)
    github_token: str = ""
    job_launcher: str = "local"
    reports_dir: str = "./reports"
    # Kubernetes Job settings
    k8s_namespace: str = "ai-scan-repos"
    scanner_image: str = "registry.local/ai-scan-repos/scanner-worker:0.1.0"  # never :latest; env SCANNER_IMAGE
    # Optional scanner container imagePullPolicy (Always|IfNotPresent|Never). Empty = template/K8s default.
    # The local k3d overlay sets Always: the mutable :dev tag is re-pushed by infra/local/k3d.sh.
    scanner_image_pull_policy: str = ""
    reports_pvc: str = "reports"
    scanner_service_account: str = "scanner"
    scanner_secret_name: str = "scanner-secrets"
    # Path of the mounted scanner-job-template ConfigMap (Job YAML). Empty -> built-in template.
    scanner_job_template: str = ""
    scanner_active_deadline_seconds: int = 21600
    review_api_internal_url: str = "http://review-api:8001"
    # Watchdog
    heartbeat_timeout_seconds: int = 900  # 15 minutes; stuck after 2 consecutive reconciler passes over it
    queued_job_timeout_seconds: int = 900  # queued/retrying with no Job for 15 min -> Job recreated (same attempt)
    max_attempts: int = 5
    watchdog_interval_seconds: int = 60
    watchdog_enabled: bool = True
    # SSE /reviews/{id}/progress poll interval of the reports volume; optional max stream length
    sse_interval_seconds: float = 1.0
    sse_max_seconds: float | None = None
    # Local launcher command (dev)
    local_worker_cmd: str = "python -m scanner.run"
    local_worker_cwd: str = ""

    def problems(self) -> list[str]:
        """Config errors that make a production start unsafe. Empty list = OK. Config only comes from env."""
        if self.app_env.lower() != "production":
            return []
        out = []
        if self.database_url.startswith("sqlite"):
            out.append("DATABASE_URL must point at PostgreSQL in production")
        if self.internal_api_token in ("", "change-me-dev-only") or len(self.internal_api_token) < 32:
            out.append("INTERNAL_API_TOKEN must be set (>= 32 chars, not the dev default)")
        try:
            from .crypto import parse_key
            parse_key(self.ai_keys_encryption_key)
        except ValueError as e:
            out.append(f"AI_KEYS_ENCRYPTION_KEY: {e}")
        if self.job_launcher != "kubernetes":
            out.append("JOB_LAUNCHER must be kubernetes in production")
        if self.scanner_image.endswith(":latest") or ":" not in self.scanner_image.rsplit("/", 1)[-1]:
            out.append("SCANNER_IMAGE must be pinned (no :latest)")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
