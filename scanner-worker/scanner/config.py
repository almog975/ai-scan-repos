import os
from dataclasses import dataclass, field


def _int(name, default):
    return int(os.environ.get(name, default))


@dataclass
class WorkerConfig:
    review_id: str = field(default_factory=lambda: os.environ.get("REVIEW_ID", ""))
    repo_url: str = field(default_factory=lambda: os.environ.get("REPO_URL", ""))
    ref: str = field(default_factory=lambda: os.environ.get("REF", ""))
    commit_sha: str = field(default_factory=lambda: os.environ.get("COMMIT_SHA", ""))
    depth: str = field(default_factory=lambda: os.environ.get("DEPTH", "quick"))
    attempt: int = field(default_factory=lambda: _int("ATTEMPT", 1))
    budget_no: int = field(default_factory=lambda: _int("BUDGET_NO", 1))
    reports_dir: str = field(default_factory=lambda: os.environ.get("REPORTS_DIR", "/reports"))
    work_dir: str = field(default_factory=lambda: os.environ.get("WORK_DIR", "/work"))
    review_api_url: str = field(default_factory=lambda: os.environ.get("REVIEW_API_URL", "http://review-api:8001"))
    internal_token: str = field(default_factory=lambda: os.environ.get("INTERNAL_API_TOKEN", ""))
    ai_provider: str = field(default_factory=lambda: os.environ.get("AI_PROVIDER", "none"))
    ai_model: str = field(default_factory=lambda: os.environ.get("AI_MODEL", ""))
    ai_base_url: str = field(default_factory=lambda: os.environ.get("AI_BASE_URL", ""))
    ai_api_key: str = field(default_factory=lambda: os.environ.get("AI_API_KEY", ""))
    scanner_timeout: int = field(default_factory=lambda: _int("SCANNER_TIMEOUT_SECONDS", 900))
    clone_timeout: int = field(default_factory=lambda: _int("CLONE_TIMEOUT_SECONDS", 300))
    max_files: int = field(default_factory=lambda: _int("MAX_FILES", 5000))
    deep_max_files: int = field(default_factory=lambda: _int("DEEP_MAX_FILES", 40))
    deep_max_file_bytes: int = field(default_factory=lambda: _int("DEEP_MAX_FILE_BYTES", 60_000))
    brief_top_findings: int = field(default_factory=lambda: _int("BRIEF_TOP_FINDINGS", 10))
    ai_kind: str = field(default_factory=lambda: os.environ.get("AI_KIND", ""))
    ai_provider_name: str = field(default_factory=lambda: os.environ.get("AI_PROVIDER_NAME", ""))
    repo_private: bool = field(default_factory=lambda: os.environ.get("REPO_PRIVATE", "false").lower() == "true")
