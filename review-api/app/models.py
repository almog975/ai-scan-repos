"""ORM models = infra/db/schema.sql (contract v1). Enum names match the Postgres types."""
import enum
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import INET
from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .types import GUID, JSONType, new_uuid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    user = "user"
    admin = "admin"


class Depth(str, enum.Enum):
    quick = "quick"
    brief = "brief"
    deep = "deep"


class State(str, enum.Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    stuck = "stuck"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ProviderKind(str, enum.Enum):
    onprem = "onprem"
    cloud = "cloud"


class FailureCode(str, enum.Enum):  # common.schema.json#/$defs/failure_code
    CLONE_FAILED = "CLONE_FAILED"
    REPO_TOO_LARGE = "REPO_TOO_LARGE"
    SCANNER_ERROR = "SCANNER_ERROR"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    AI_RATE_LIMITED = "AI_RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    MAX_ATTEMPTS_EXCEEDED = "MAX_ATTEMPTS_EXCEEDED"
    INTERNAL = "INTERNAL"


class Outcome(str, enum.Enum):
    success = "success"
    stuck = "stuck"
    error = "error"
    failed = "failed"
    cancelled = "cancelled"


def _enum(e, name):
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x], create_type=False)


TZ = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_uuid)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), default=Role.user)
    gh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # sha256(cookie token)
    user_id: Mapped[str] = mapped_column(GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    ip: Mapped[str | None] = mapped_column(Text().with_variant(INET(), "postgresql"), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class AIProvider(Base):
    __tablename__ = "ai_providers"
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[ProviderKind] = mapped_column(_enum(ProviderKind, "provider_kind"))
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text)
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cloud_ack_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    cloud_ack_by: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    repo_url: Mapped[str] = mapped_column(Text)
    repo_private: Mapped[bool] = mapped_column(Boolean, default=False)
    ref: Mapped[str] = mapped_column(Text, default="HEAD")
    commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth: Mapped[Depth] = mapped_column(_enum(Depth, "review_depth"))
    provider_id: Mapped[str | None] = mapped_column(GUID, ForeignKey("ai_providers.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[State] = mapped_column(_enum(State, "review_state"), default=State.queued, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    budget_no: Mapped[int] = mapped_column(Integer, default=1)
    files_done: Mapped[int] = mapped_column(Integer, default=0)
    files_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)
    findings: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    # Phase 3 (alembic 0003). failure_reason is the contract's failure_detail (free text).
    failure_code: Mapped[FailureCode | None] = mapped_column(_enum(FailureCode, "failure_code"), nullable=True)
    stuck_since: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    paused_by: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # internal (not in the API): first reconciler pass that saw the heartbeat over the threshold
    overdue_since: Mapped[datetime | None] = mapped_column(TZ, nullable=True)


class ReviewAttempt(Base):
    __tablename__ = "review_attempts"
    __table_args__ = (UniqueConstraint("review_id", "budget_no", "n"),)
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=new_uuid)
    review_id: Mapped[str] = mapped_column(GUID, ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    budget_no: Mapped[int] = mapped_column(Integer, default=1)
    n: Mapped[int] = mapped_column(Integer)
    job_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    outcome: Mapped[Outcome | None] = mapped_column(_enum(Outcome, "attempt_outcome"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_by: Mapped[str | None] = mapped_column(GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    status: Mapped[str] = mapped_column(Text, default="accepted")
