"""Local dev only: create (or reuse) a dev user and print the headers for curl against review-api.

    python -m app.devseed [login]          -> prints: -H X-Internal-Token:... -H X-User-Id:<id> -H Content-Type:application/json
    python -m app.devseed --id [login]     -> prints only the user id

Idempotent (fixed github_id per login). The first user ever becomes admin (same rule as GitHub login).
Refuses to run when DATABASE_URL is not SQLite unless AISCAN_DEV_SEED=1, so it is never run against a real DB by accident.
"""
from __future__ import annotations

import os
import sys
import zlib


def main(argv: list[str]) -> int:
    only_id = "--id" in argv
    args = [a for a in argv if a != "--id"]
    login = args[0] if args else "dev"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if not url.startswith("sqlite") and os.environ.get("AISCAN_DEV_SEED") != "1":
        print("refusing: DATABASE_URL is not SQLite (set AISCAN_DEV_SEED=1 for a dev Postgres)", file=sys.stderr)
        return 2
    from . import db as d
    from .auth import ensure_user
    d.init_engine(url)
    if url.startswith("sqlite"):
        from . import models  # noqa: F401  (register tables)
        d.Base.metadata.create_all(d.get_engine())   # same as app startup on SQLite; Postgres uses Alembic
    github_id = 900_000_000 + zlib.crc32(login.encode()) % 100_000_000   # stable, outside real-ish ids
    with d.session_factory()() as s:
        u = ensure_user(s, github_id, login)
        uid = u.id
    if only_id:
        print(uid)
    else:
        tok = os.environ.get("INTERNAL_API_TOKEN", "")
        print(f"-H X-Internal-Token:{tok} -H X-User-Id:{uid} -H Content-Type:application/json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
