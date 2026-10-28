"""Gateway uses the shared schema (contracts/../schema.sql, applied by review-api's Alembic migration):
users + sessions + webhook_deliveries. For sqlite (dev/tests) it creates the tables itself."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, create_engine
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import CHAR, TypeDecorator


def utcnow():
    return datetime.now(timezone.utc)


class GUID(TypeDecorator):
    """uuid on Postgres, CHAR(36) elsewhere (same as review-api/app/types.py)."""
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID
            return dialect.type_descriptor(UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        return None if value is None else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else str(value)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(GUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # user_role enum on Postgres; plain text binds fine (implicit cast from unknown)
    role: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)  # sha256(cookie token), never the raw token
    user_id: Mapped[str] = mapped_column(GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(Text().with_variant(INET(), "postgresql"), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(Text, default="accepted")


def make_sessionmaker(url: str):
    kw = {}
    if url.startswith("sqlite"):
        kw["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool
            kw["poolclass"] = StaticPool
    engine = create_engine(url, pool_pre_ping=True, **kw)
    if url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
