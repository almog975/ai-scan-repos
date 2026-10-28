"""Gateway shares the Postgres schema owned by review-api's Alembic migrations
(tables github_accounts + sessions). For sqlite (dev/tests) it creates them."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class GithubAccount(Base):
    __tablename__ = "github_accounts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    github_user_id: Mapped[int] = mapped_column(Integer, unique=True)
    login: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(cookie token), never the raw token
    account_id: Mapped[str] = mapped_column(ForeignKey("github_accounts.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
