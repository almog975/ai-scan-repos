"""Runtime configuration (all from environment variables)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    database_url: str = "sqlite:///./review-api.db"
    # Shared secret between gateway/worker and review-api. Required in prod.
    internal_api_token: str = "change-me-dev-only"
    # Fernet key (urlsafe base64, 32 bytes). Generate: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    ai_keys_encryption_key: str = ""
    # local | kubernetes
    job_launcher: str = "local"
    reports_dir: str = "./reports"
    # Kubernetes Job settings
    k8s_namespace: str = "ai-scan-repos"
    scanner_image: str = "registry.local/ai-scan-repos/scanner-worker:0.1.0"  # never :latest; env SCANNER_IMAGE
    reports_pvc: str = "reports"
    scanner_service_account: str = "scanner"
    scanner_secret_name: str = "scanner-secrets"
    # Path of the mounted scanner-job-template ConfigMap (Job YAML). Empty -> built-in template.
    scanner_job_template: str = ""
    scanner_active_deadline_seconds: int = 21600
    review_api_internal_url: str = "http://review-api:8001"
    # Watchdog
    heartbeat_timeout_seconds: int = 900  # 15 minutes
    max_attempts: int = 5
    watchdog_interval_seconds: int = 60
    watchdog_enabled: bool = True
    # Local launcher command (dev)
    local_worker_cmd: str = "python -m scanner.run"
    local_worker_cwd: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
