"""Admin settings (contract `Settings`), stored as one row per key in `settings`."""
from __future__ import annotations

from .models import Setting

DEFAULTS = {"default_provider_id": None, "max_concurrent_reviews": 3, "max_repo_size_mb": 500}


def load(db) -> dict:
    out = dict(DEFAULTS)
    for row in db.query(Setting).filter(Setting.key.in_(DEFAULTS)).all():
        out[row.key] = (row.value or {}).get("v")
    return out


def save(db, values: dict, user_id: str | None) -> dict:
    for k, v in values.items():
        row = db.get(Setting, k)
        if row is None:
            db.add(Setting(key=k, value={"v": v}, updated_by=user_id))
        else:
            row.value, row.updated_by = {"v": v}, user_id
    db.commit()
    return load(db)
