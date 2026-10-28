import os
from dataclasses import dataclass, field


def _int(name, default):
    return int(os.environ.get(name, default))


@dataclass
class WorkerConfig:
    review_id: str
    repo_url: str = ""
    depth: str = "quick"
    reports_dir: str = field(default_factory=lambda: os.environ.get("REPORTS_DIR", "/reports"))
    work_dir: str = field(default_factory=lambda: os.environ.get("WORK_DIR", "/work"))
    review_api_url: str = field(default_factory=lambda: os.environ.get("REVIEW_API_URL", "http://review-api:8001"))
    internal_token: str = field(default_factory=lambda: os.environ.get("INTERNAL_API_TOKEN", ""))
    ai_provider: str = field(default_factory=lambda: os.environ.get("AI_PROVIDER", "openai_compatible"))
    ai_model: str = field(default_factory=lambda: os.environ.get("AI_MODEL", "local-model"))
    ai_base_url: str = field(default_factory=lambda: os.environ.get("AI_BASE_URL", ""))
    ai_api_key: str = field(default_factory=lambda: os.environ.get("AI_API_KEY", ""))
    scanner_timeout: int = field(default_factory=lambda: _int("SCANNER_TIMEOUT_SECONDS", 900))
    clone_timeout: int = field(default_factory=lambda: _int("CLONE_TIMEOUT_SECONDS", 300))
    deep_max_files: int = field(default_factory=lambda: _int("DEEP_MAX_FILES", 40))
    deep_max_file_bytes: int = field(default_factory=lambda: _int("DEEP_MAX_FILE_BYTES", 60_000))
    brief_top_findings: int = field(default_factory=lambda: _int("BRIEF_TOP_FINDINGS", 10))
