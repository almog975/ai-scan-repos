from pathlib import Path

from fastapi import Depends

from app import db as dbmod
from app.auth import current_user, ensure_user, require_admin
from app.errors import ApiError
from app.models import Role
from contract import check, spec
from conftest import TOKEN, hdr


def _routes(app):
    @app.get("/_t/user")
    def u(user=Depends(current_user)):
        return {"id": user.id}

    @app.get("/_t/admin")
    def a(user=Depends(require_admin)):
        return {"id": user.id}

    @app.get("/_t/conflict")
    def c():
        raise ApiError(409, "CONFLICT", "nope", {"state": "running"})


def test_first_user_admin(client):
    with dbmod.session_factory()() as db:
        a, b = ensure_user(db, 10, "first"), ensure_user(db, 11, "second")
        assert a.role == Role.admin and b.role == Role.user
        assert ensure_user(db, 10, "renamed").role == Role.admin  # upsert keeps role


def test_401_403_and_envelope(app, client, users):
    _routes(app)
    admin, user = users
    for r, code in [(client.get("/_t/user"), "UNAUTHORIZED"),
                    (client.get("/_t/user", headers={"X-Internal-Token": "bad", "X-User-Id": user}), "UNAUTHORIZED"),
                    (client.get("/_t/user", headers={"X-Internal-Token": TOKEN}), "UNAUTHORIZED"),
                    (client.get("/_t/user", headers=hdr("00000000-0000-0000-0000-000000000000")), "UNAUTHORIZED")]:
        assert r.status_code == 401
        check("Error", r.json())
        assert r.json()["error"]["code"] == code
    r = client.get("/_t/admin", headers=hdr(user))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    check("Error", r.json())
    assert client.get("/_t/admin", headers=hdr(admin)).status_code == 200
    r = client.get("/_t/conflict", headers={"X-Request-Id": "rid-1"})
    assert r.status_code == 409 and r.json()["request_id"] == "rid-1" and r.headers["x-request-id"] == "rid-1"
    check("Error", r.json())
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    check("Error", r.json())


def test_error_codes_match_contract():
    from app.errors import CODES
    assert list(CODES) == spec()["components"]["schemas"]["Error"]["properties"]["error"]["properties"]["code"]["enum"]


def test_migration_sql_matches_infra_schema():
    root = Path(__file__).resolve().parents[2]
    infra = (root / "infra/db/schema.sql").read_text().splitlines(keepends=True)
    body = "".join(l for i, l in enumerate(infra) if not (i < 3 and l.startswith("--")))
    assert (root / "review-api/alembic/sql/0002_contract_v1.sql").read_text() == body
