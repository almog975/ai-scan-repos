"""contract v1 schema (= infra/bootstrap/db/schema.sql)

Drops the Phase-0 prototype tables (no production data exists) and creates the
contract v1 schema. On Postgres the DDL is alembic/sql/0002_contract_v1.sql, which a test keeps
byte-identical to infra/bootstrap/db/schema.sql (minus its header comment). Other dialects (dev sqlite) use the ORM metadata.

Revision ID: 0002
Revises: 0001
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0002_contract_v1.sql"
LEGACY = ("ai_keys", "reviews", "sessions", "settings", "github_accounts")
NEW = ("webhook_deliveries", "settings", "review_attempts", "reviews", "ai_providers", "sessions", "users")
TYPES = ("attempt_outcome", "provider_kind", "review_state", "review_depth", "user_role")


def upgrade():
    bind = op.get_bind()
    for t in LEGACY:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE" if bind.dialect.name == "postgresql" else f"DROP TABLE IF EXISTS {t}")
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(SQL.read_text())
    else:
        from app import models  # noqa: F401
        from app.db import Base
        Base.metadata.create_all(bind)


def downgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    for t in NEW:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE" if pg else f"DROP TABLE IF EXISTS {t}")
    if pg:
        op.execute("DROP FUNCTION IF EXISTS first_user_admin() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
        for t in TYPES:
            op.execute(f"DROP TYPE IF EXISTS {t}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("m0001", Path(__file__).with_name("0001_initial.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.upgrade()
