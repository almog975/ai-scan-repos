import pytest

from app.db import normalize_db_url


@pytest.mark.parametrize("raw,want", [
    ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
    ("postgresql+psycopg2://u:p@h/d", "postgresql+psycopg2://u:p@h/d"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_normalize_db_url(raw, want):
    assert normalize_db_url(raw) == want


def test_engine_uses_psycopg3_not_psycopg2():
    from sqlalchemy import create_engine
    e = create_engine(normalize_db_url("postgresql://u:p@127.0.0.1:1/d"))
    assert e.dialect.driver == "psycopg"
