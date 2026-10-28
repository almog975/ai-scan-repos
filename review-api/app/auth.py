"""Internal auth placeholder until the gateway (Phase 2).

review-api is never exposed publicly. Callers (gateway, scanner worker) must send
`X-Internal-Token: <INTERNAL_API_TOKEN>`; only then is `X-User-Id` trusted.
"""
import hmac

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings


def require_internal(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not x_internal_token or not hmac.compare_digest(x_internal_token.encode(), settings.internal_api_token.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid internal token")


def current_user(
    _: None = Depends(require_internal),
    x_user_id: str | None = Header(default=None),
) -> str:
    if not x_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-User-Id")
    return x_user_id
