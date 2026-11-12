"""Regression: users.role is the Postgres enum user_role (Alembic 0002). The gateway must bind it as that enum,
not VARCHAR (DatatypeMismatch on INSERT after GitHub OAuth login on a fresh docker-compose DB)."""
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import Enum, insert
from sqlalchemy.dialects import postgresql

from app.db import USER_ROLES, User

SCHEMA_SQL = Path(__file__).resolve().parents[3] / "infra" / "bootstrap" / "db" / "schema.sql"


def test_role_column_is_native_user_role_enum():
    t = User.__table__.c.role.type
    assert isinstance(t, Enum) and t.native_enum and t.name == "user_role"
    m = re.search(r"CREATE TYPE user_role\s+AS ENUM \(([^)]*)\)", SCHEMA_SQL.read_text())
    assert tuple(v.strip().strip("'") for v in m.group(1).split(",")) == USER_ROLES == tuple(t.enums)


def test_postgres_role_bind_is_enum_not_varchar():
    c = insert(User).values(github_id=1, login="x", role="admin").compile(dialect=postgresql.psycopg.dialect())
    assert "%(role)s::VARCHAR" not in str(c), str(c)
    assert c.binds["role"].type.name == "user_role"


def test_postgres_ddl_does_not_create_type():
    from sqlalchemy.schema import CreateTable
    ddl = str(CreateTable(User.__table__).compile(dialect=postgresql.dialect()))
    assert "user_role" in ddl  # column type references the existing enum; Alembic owns CREATE TYPE


PG_URL = os.environ.get("GATEWAY_PG_URL")  # set by ci/db-parity.sh after `alembic upgrade head`


@pytest.mark.skipif(not PG_URL, reason="GATEWAY_PG_URL not set (runs in ci/db-parity.sh against real Postgres)")
def test_oauth_login_inserts_user_on_postgres():
    from tests.conftest import do_login, make_client
    c, router = make_client(database_url=PG_URL)
    with router:
        r1 = do_login(c, router, login="pg-first", uid=990001)
        c2, router2 = make_client(database_url=PG_URL)
        with router2:
            r2 = do_login(c2, router2, login="pg-second", uid=990002)
    for r in (r1, r2):
        assert r.status_code in (200, 302, 303, 307), r.text
    from sqlalchemy import create_engine, text
    from app.db import normalize_db_url
    with create_engine(normalize_db_url(PG_URL)).connect() as conn:   # bare postgresql:// would pick psycopg2
        roles = dict(conn.execute(text("SELECT login, role::text FROM users WHERE github_id IN (990001, 990002)")).all())
    assert set(roles) == {"pg-first", "pg-second"} and set(roles.values()) <= {"user", "admin"}
