"""Caller auth. review-api is internal-only: the gateway authenticates the browser session and
forwards `X-Internal-Token` + `X-User-Id` (users.id). Missing/invalid -> 401 UNAUTHORIZED;
non-admin on /admin/* -> 403 FORBIDDEN. The scanner worker uses the internal token only."""
import hmac

from fastapi import Depends, Header
from sqlalchemy import func, select

from .config import Settings, get_settings
from .db import get_db
from .errors import forbidden, unauthorized
from .models import Role, User


def require_internal(x_internal_token: str | None = Header(default=None),
                     settings: Settings = Depends(get_settings)) -> None:
    if not x_internal_token or not hmac.compare_digest(x_internal_token.encode(), settings.internal_api_token.encode()):
        raise unauthorized("invalid internal token")


def current_user(_: None = Depends(require_internal), x_user_id: str | None = Header(default=None),
                 db=Depends(get_db)) -> User:
    if not x_user_id:
        raise unauthorized("missing X-User-Id")
    try:
        user = db.get(User, x_user_id)
    except Exception:  # malformed id
        user = None
    if user is None:
        raise unauthorized("unknown user")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise forbidden()
    return user


def ensure_user(db, github_id: int, login: str, **extra) -> User:
    """Upsert a GitHub user. The very first user becomes admin (also enforced by the
    users_first_admin trigger on Postgres)."""
    u = db.scalars(select(User).where(User.github_id == github_id)).first()
    if u:
        u.login = login
        for k, v in extra.items():
            setattr(u, k, v)
    else:
        first = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
        u = User(github_id=github_id, login=login, role=Role.admin if first else Role.user, **extra)
        db.add(u)
    db.commit()
    return u
