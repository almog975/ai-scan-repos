"""Migration <-> model <-> infra/db/schema.sql parity (sqlite here; Postgres pg_dump diff runs in CI: ci/db-parity.sh)."""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import models  # noqa: F401
from app.db import Base

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT.parent / "infra" / "db" / "schema.sql"


def _upgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'p.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return create_engine(url)


def test_models_match_migrations(tmp_path, monkeypatch):
    eng = _upgrade(tmp_path, monkeypatch)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
    structural = [d for d in diff if d[0] in ("add_table", "remove_table", "add_column", "remove_column")]
    assert structural == [], structural


def test_schema_sql_tables_columns_match_migrations(tmp_path, monkeypatch):
    import re
    eng = _upgrade(tmp_path, monkeypatch)
    insp = inspect(eng)
    sql = SCHEMA_SQL.read_text()
    want = {}
    for name, body in re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\((.*?)\n\);", sql, re.S | re.I):
        cols = set()
        for line in body.splitlines():
            m = re.match(r"\s*(\w+)\s+\w", line)
            if m and m.group(1).upper() not in ("PRIMARY", "UNIQUE", "CONSTRAINT", "FOREIGN", "CHECK", "EXCLUDE"):
                cols.add(m.group(1))
        want[name] = cols
    assert want, "no tables parsed from schema.sql"
    for t, cols in want.items():
        assert {c["name"] for c in insp.get_columns(t)} == cols, t
    with eng.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar()
