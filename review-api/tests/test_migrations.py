"""upgrade -> downgrade -> upgrade on sqlite (Postgres variant runs in CI, see docs/phase-1-plan.md step 12)."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]


def test_upgrade_downgrade_cycle(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"users", "sessions", "ai_providers", "reviews", "review_attempts", "settings", "webhook_deliveries"} <= tables
    command.downgrade(cfg, "0001")
    assert "github_accounts" in inspect(create_engine(url)).get_table_names()
    command.upgrade(cfg, "head")


def test_database_url_with_percent_escape(tmp_path, monkeypatch):
    """A URL-encoded password (p%40ss) must survive alembic's configparser interpolation (alembic/env.py)."""
    d = tmp_path / "p%40ss"
    d.mkdir()
    url = f"sqlite:///{d / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    assert "reviews" in inspect(create_engine(url)).get_table_names()
