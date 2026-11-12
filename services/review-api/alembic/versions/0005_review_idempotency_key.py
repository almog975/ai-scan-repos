"""Phase 9 reviews.idempotency_key (unique, nullable): POST /reviews Idempotency-Key.

On Postgres the DDL is alembic/sql/0005_review_idempotency_key.sql (= the Phase 9 block at the end of infra/bootstrap/db/schema.sql).

Revision ID: 0005
Revises: 0004
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parent.parent / "sql" / "0005_review_idempotency_key.sql"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(SQL.read_text())
        return
    if "idempotency_key" in {c["name"] for c in sa.inspect(bind).get_columns("reviews")}:
        return
    with op.batch_alter_table("reviews") as b:
        b.add_column(sa.Column("idempotency_key", sa.Text(), nullable=True))
        b.create_unique_constraint("reviews_idempotency_key_key", ["idempotency_key"])


def downgrade():
    # dropping the column drops its unique constraint (Postgres; SQLite batch rebuilds the table without it)
    with op.batch_alter_table("reviews") as b:
        b.drop_column("idempotency_key")
