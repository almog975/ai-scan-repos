"""Phase 3 recovery columns (failure_code, stuck_since, next_retry_at, paused_by, cancelled_at, cancelled_by,
overdue_since).

On Postgres the DDL is alembic/sql/0003_phase3_recovery.sql (= the Phase 3 block at the end of infra/bootstrap/db/schema.sql).
failure_detail is the existing failure_reason column (free text). paused_at already exists (0002).

Revision ID: 0003
Revises: 0002
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0003_phase3_recovery.sql"
CODES = ("CLONE_FAILED", "REPO_TOO_LARGE", "SCANNER_ERROR", "AI_PROVIDER_ERROR", "AI_RATE_LIMITED", "TIMEOUT",
         "MAX_ATTEMPTS_EXCEEDED", "INTERNAL")
COLS = ("failure_code", "stuck_since", "next_retry_at", "paused_by", "cancelled_at", "cancelled_by", "overdue_since")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(SQL.read_text())
        return
    have = {c["name"] for c in sa.inspect(bind).get_columns("reviews")}  # dev sqlite 0002 = create_all(current ORM)
    if set(COLS) <= have:
        return
    tz = sa.DateTime(timezone=True)
    op.add_column("reviews", sa.Column("failure_code", sa.Enum(*CODES, name="failure_code", create_constraint=False), nullable=True))
    op.add_column("reviews", sa.Column("stuck_since", tz, nullable=True))
    op.add_column("reviews", sa.Column("next_retry_at", tz, nullable=True))
    op.add_column("reviews", sa.Column("paused_by", sa.CHAR(36), nullable=True))
    op.add_column("reviews", sa.Column("cancelled_at", tz, nullable=True))
    op.add_column("reviews", sa.Column("cancelled_by", sa.CHAR(36), nullable=True))
    op.add_column("reviews", sa.Column("overdue_since", tz, nullable=True))


def downgrade():
    with op.batch_alter_table("reviews") as b:
        for c in COLS:
            b.drop_column(c)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS failure_code")
