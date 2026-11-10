"""Phase 4 provider cloud_ack columns (cloud_ack_at, cloud_ack_by).

On Postgres the DDL is alembic/sql/0004_phase4_provider_ack.sql (= the Phase 4 block at the end of infra/bootstrap/db/schema.sql).

Revision ID: 0004
Revises: 0003
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0004_phase4_provider_ack.sql"
COLS = ("cloud_ack_at", "cloud_ack_by")


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(SQL.read_text())
        return
    have = {c["name"] for c in sa.inspect(bind).get_columns("ai_providers")}
    if set(COLS) <= have:
        return
    op.add_column("ai_providers", sa.Column("cloud_ack_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ai_providers", sa.Column("cloud_ack_by", sa.CHAR(36), nullable=True))
    op.execute("UPDATE ai_providers SET cloud_ack_at = created_at, cloud_ack_by = created_by WHERE kind = 'cloud'")


def downgrade():
    with op.batch_alter_table("ai_providers") as b:
        for c in COLS:
            b.drop_column(c)
