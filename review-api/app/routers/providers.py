"""/ai-providers (public, no secrets) and /admin/ai-providers (admin CRUD, keys AES-GCM encrypted)."""
from __future__ import annotations

import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator
from sqlalchemy import select

from ..auth import current_user, require_admin
from ..crypto import KeyCipher, mask
from ..db import get_db
from ..errors import ApiError, not_found, validation
from ..metrics import observe_ai
from ..models import AIProvider, ProviderKind, User
from ..providers import ProviderError, adapter_for
from ..schemas import Closed
from ..serialize import iso, user_ref
from ..models import Review, utcnow
from ..state import TERMINAL
from .. import settings_store

router = APIRouter()
Url = str


class CloudAckWrite(Closed):
    acknowledged: bool


class AIProviderWrite(Closed):
    name: str = Field(min_length=1)
    kind: Literal["onprem", "cloud"]
    base_url: Url | None = None
    model: str = Field(min_length=1)
    api_key: str | None = None
    enabled: bool = True
    cloud_ack: CloudAckWrite | None = None


class AIProviderPatch(Closed):
    name: str | None = Field(default=None, min_length=1)
    base_url: Url | None = None
    model: str | None = Field(default=None, min_length=1)
    api_key: str | None = None
    enabled: bool | None = None
    kind: Literal["onprem", "cloud"] | None = None
    cloud_ack: CloudAckWrite | None = None

    @model_validator(mode="after")
    def _non_empty(self):
        if not self.model_fields_set:
            raise ValueError("at least one property is required")
        for k in ("name", "model", "enabled", "kind", "cloud_ack"):
            if k in self.model_fields_set and getattr(self, k) is None:
                raise ValueError(f"{k} cannot be null")
        return self


def _check_url(u: str | None):
    if u is not None and not (u.startswith("http://") or u.startswith("https://")):
        raise validation("base_url must be an http(s) URI")


def _cipher(request: Request) -> KeyCipher:
    try:
        return KeyCipher(request.app.state.settings.ai_keys_encryption_key)
    except ValueError as e:
        raise ApiError(500, "INTERNAL", str(e))


def _kind(p) -> str:
    return getattr(p.kind, "value", p.kind)


def _require_ack(kind: str, ack: CloudAckWrite | None):
    if kind == "cloud" and not (ack and ack.acknowledged):
        raise ApiError(422, "CLOUD_ACK_REQUIRED",
                       "cloud providers send repository code outside the cluster; set cloud_ack.acknowledged=true")


def _set_ack(p: AIProvider, admin: User):
    if _kind(p) == "cloud":
        p.cloud_ack_at, p.cloud_ack_by = utcnow(), admin.id
    else:
        p.cloud_ack_at = p.cloud_ack_by = None


def admin_out(p: AIProvider, db=None) -> dict:
    acked = p.cloud_ack_at is not None
    return {"id": p.id, "name": p.name, "kind": _kind(p), "base_url": p.base_url,
            "model": p.model, "key_masked": mask(p.key_last4), "enabled": bool(p.enabled),
            "cloud_ack": {"acknowledged": acked, "at": iso(p.cloud_ack_at),
                          "by": user_ref(db, p.cloud_ack_by) if (db is not None and acked) else None},
            "created_at": iso(p.created_at), "updated_at": iso(p.updated_at or p.created_at)}


def _set_key(p: AIProvider, key: str | None, c: KeyCipher):
    if key:
        p.api_key_enc, p.key_nonce = c.encrypt(key, p.id)
        p.key_last4 = key[-4:]
    else:
        p.api_key_enc = p.key_nonce = p.key_last4 = None


def decrypt_key(p: AIProvider, c: KeyCipher) -> str | None:
    if not p.api_key_enc:
        return None
    return c.decrypt(p.api_key_enc, p.key_nonce, p.id)


def _get(db, id: str) -> AIProvider:
    try:
        uuid.UUID(id)
    except ValueError:  # contract documents only 404 for an unknown id
        raise not_found("provider")
    p = db.get(AIProvider, id)
    if p is None:
        raise not_found("provider")
    return p


@router.get("/ai-providers")
def public_list(_: User = Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(select(AIProvider).where(AIProvider.enabled.is_(True)).order_by(AIProvider.name)).all()
    default_id = settings_store.load(db).get("default_provider_id")
    return [{"id": p.id, "name": p.name, "kind": getattr(p.kind, "value", p.kind),
             "cloud_warning": getattr(p.kind, "value", p.kind) == "cloud", "is_default": p.id == default_id} for p in rows]


@router.get("/admin/ai-providers")
def admin_list(_: User = Depends(require_admin), db=Depends(get_db)):
    return [admin_out(p, db) for p in db.scalars(select(AIProvider).order_by(AIProvider.created_at)).all()]


@router.post("/admin/ai-providers", status_code=201)
def create(body: AIProviderWrite, request: Request, admin: User = Depends(require_admin), db=Depends(get_db)):
    _check_url(body.base_url)
    _require_ack(body.kind, body.cloud_ack)
    c = _cipher(request)
    p = AIProvider(id=str(uuid.uuid4()), name=body.name, kind=ProviderKind(body.kind), base_url=body.base_url,
                   model=body.model, enabled=body.enabled, created_by=admin.id)
    _set_key(p, body.api_key, c)
    _set_ack(p, admin)
    db.add(p)
    db.commit()
    return JSONResponse(admin_out(p, db), 201)


@router.patch("/admin/ai-providers/{id}")
def update(id: str, body: AIProviderPatch, request: Request, admin: User = Depends(require_admin), db=Depends(get_db)):
    p = _get(db, id)
    f = body.model_fields_set
    if "kind" in f and body.kind != _kind(p):
        _require_ack(body.kind, body.cloud_ack)  # switching to cloud needs the ack again
        p.kind = ProviderKind(body.kind)
        _set_ack(p, admin)
    elif "cloud_ack" in f and _kind(p) == "cloud":
        _require_ack("cloud", body.cloud_ack)  # an explicit withdrawal is rejected; re-ack refreshes at/by
        _set_ack(p, admin)
    if "base_url" in f:
        _check_url(body.base_url)
        p.base_url = body.base_url
    for k in ("name", "model", "enabled"):
        if k in f:
            setattr(p, k, getattr(body, k))
    if "api_key" in f:
        _set_key(p, body.api_key, _cipher(request))  # null clears the stored key
    db.commit()
    return admin_out(p, db)


@router.delete("/admin/ai-providers/{id}", status_code=204)
def delete(id: str, _: User = Depends(require_admin), db=Depends(get_db)):
    p = _get(db, id)
    if settings_store.load(db).get("default_provider_id") == p.id:
        raise ApiError(409, "PROVIDER_IN_USE", "provider is the default provider", {"reason": "default"})
    active = db.scalars(select(Review.id).where(Review.provider_id == p.id, Review.state.notin_(list(TERMINAL)))).first()
    if active:
        raise ApiError(409, "PROVIDER_IN_USE", "provider is used by an active review",
                       {"reason": "active_review", "review_id": active})
    db.delete(p)
    db.commit()
    return Response(status_code=204)


def _run_test(base_url, kind, model, key):
    k = getattr(kind, "value", kind)
    t0 = time.monotonic()
    try:
        res = adapter_for(base_url, kind, model, key).test()
    except ProviderError as e:
        observe_ai(k, "test", time.monotonic() - t0, ok=False)
        raise ApiError(502, e.code, f"provider check failed: {e}")
    except ValueError as e:
        observe_ai(k, "test", time.monotonic() - t0, ok=False)
        raise ApiError(502, "AI_PROVIDER_UNREACHABLE", f"provider check failed: {e}")
    observe_ai(k, "test", time.monotonic() - t0, ok=True)
    return res


@router.post("/admin/ai-providers/test")
def test_unsaved(body: AIProviderWrite, _: User = Depends(require_admin)):
    _check_url(body.base_url)
    return _run_test(body.base_url, body.kind, body.model, body.api_key)


@router.post("/admin/ai-providers/{id}/test")
def test(id: str, request: Request, _: User = Depends(require_admin), db=Depends(get_db)):
    p = _get(db, id)
    try:
        key = decrypt_key(p, _cipher(request))
    except ValueError as e:
        raise ApiError(500, "INTERNAL", f"cannot decrypt provider key: {e}")
    return _run_test(p.base_url, _kind(p), p.model, key)
