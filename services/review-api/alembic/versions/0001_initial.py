"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade():
    op.create_table("github_accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("github_user_id", sa.Integer, nullable=False, unique=True),
        sa.Column("login", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", TZ, nullable=False))
    op.create_table("sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("github_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
        sa.Column("created_at", TZ, nullable=False))
    op.create_table("reviews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False, index=True),
        sa.Column("repo_url", sa.String(512), nullable=False),
        sa.Column("depth", sa.String(5), nullable=False),
        sa.Column("status", sa.String(9), nullable=False, index=True),
        sa.Column("progress", sa.Integer, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("last_heartbeat", TZ),
        sa.Column("job_name", sa.String(128)),
        sa.Column("ai_provider", sa.String(64)),
        sa.Column("report_path", sa.String(512)),
        sa.Column("message", sa.Text),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False))
    op.create_table("settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("updated_at", TZ, nullable=False))
    op.create_table("ai_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False, index=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary, nullable=False),
        sa.Column("created_at", TZ, nullable=False))


def downgrade():
    for t in ("ai_keys", "settings", "reviews", "sessions", "github_accounts"):
        op.drop_table(t)
