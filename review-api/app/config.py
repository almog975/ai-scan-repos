"""Runtime configuration (all from environment variables)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


DEV_ENVS = ("dev", "development", "local")


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
    # env SCANNER_IMAGE. Production: required and pinned (tag other than :latest, or @sha256:).
    # Dev/local (APP_ENV local|development|dev, or ENVIRONMENT=dev): optional and :latest allowed; when unset,
    # review-api still starts and POST /reviews answers 503 with a clear message.
    scanner_image: str = ""
    # Escape hatch: accept an unpinned SCANNER_IMAGE even in production (logged as a warning).
    skip_scanner_validation: bool = False
    # Alias some tooling uses for APP_ENV; only "dev"/"development"/"local" have an effect.
    environment: str = ""
    # /readyz gates only on the DB + encryption key; set true to also return 503 when the k8s API is unreachable.
    readyz_require_kube: bool = False
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

    @property
    def is_dev(self) -> bool:
        return (self.app_env.lower() in DEV_ENVS or self.environment.lower() in DEV_ENVS)

    def scanner_image_problem(self) -> str | None:
        """Why scanner Jobs cannot be launched with SCANNER_IMAGE, or None. Same rule at startup and at launch."""
        img = self.scanner_image.strip()
        if not img:
            return "SCANNER_IMAGE is not set (set it to the scanner-worker image, e.g. ghcr.io/almog975/ai-scan-repos-scanner-worker:sha-<short>)"
        if self.is_dev or self.skip_scanner_validation or "@sha256:" in img:
            return None
        tag = img.rsplit("/", 1)[-1]
        if ":" not in tag or tag.endswith(":latest"):
            return ("SCANNER_IMAGE must be pinned (no :latest) in production; use a sha-<short> tag, "
                    "APP_ENV=local for dev clusters, or SKIP_SCANNER_VALIDATION=true")
        return None

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
        bad = self.scanner_image_problem()
        if bad:
            out.append(bad)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
