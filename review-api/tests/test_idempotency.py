"""POST /reviews Idempotency-Key (the gateway sends the webhook event+body hash)."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app import db as dbmod
from app.models import Review
from contract import check
from tests.conftest import hdr

URL = "https://github.com/a/b"
KEY = "sha256:" + "ab" * 32


def test_same_key_returns_existing_review(client, H, launcher):
    r1 = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers={**H, "Idempotency-Key": KEY})
    assert r1.status_code == 201, r1.text
    r2 = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers={**H, "Idempotency-Key": KEY})
    assert r2.status_code == 200, r2.text
    check("Review", r2.json())
    assert r2.json()["id"] == r1.json()["id"] and r2.headers["location"] == f"/reviews/{r1.json()['id']}"
    assert launcher.launched == [(r1.json()["id"], 1)]  # no second Job
    with dbmod.session_factory()() as db:
        assert db.query(Review).count() == 1 and db.query(Review).one().idempotency_key == KEY


def test_existing_returned_even_after_review_finished_or_other_body(client, H, launcher):
    r1 = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers={**H, "Idempotency-Key": KEY})
    r2 = client.post("/reviews", json={"repo_url": "https://github.com/c/d", "depth": "deep"},
                     headers={**H, "Idempotency-Key": KEY})
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"] and len(launcher.launched) == 1


def test_key_of_another_user_is_409(client, H, A):
    assert client.post("/reviews", json={"repo_url": URL, "depth": "quick"},
                       headers={**H, "Idempotency-Key": KEY}).status_code == 201
    r = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers={**A, "Idempotency-Key": KEY})
    assert r.status_code == 409
    check("Error", r.json())
    assert "review_id" not in r.text


def test_no_key_and_distinct_keys_create(client, H, launcher):
    a = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "ref": "a" * 40}, headers=H)
    b = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "ref": "b" * 40},
                    headers={**H, "Idempotency-Key": "k1"})
    c = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "ref": "c" * 40},
                    headers={**H, "Idempotency-Key": "k2"})
    assert (a.status_code, b.status_code, c.status_code) == (201, 201, 201) and len(launcher.launched) == 3


def test_key_validation(client, H):
    for k in ("", "x" * 201):
        r = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers={**H, "Idempotency-Key": k})
        assert r.status_code == 400, (k, r.status_code)
        check("Error", r.json())


def test_concurrent_insert_race_returns_existing(client, H, launcher, monkeypatch):
    """The pre-check misses a row committed meanwhile: the unique constraint catches it before any Job launches."""
    from app.routers import reviews as rv
    with dbmod.session_factory()() as db:
        uid = H["X-User-Id"]
        db.add(Review(user_id=uid, repo_url=URL, ref="HEAD", depth="quick", idempotency_key=KEY))
        db.commit()
    real = rv.select
    calls = {"n": 0}

    def blind(*a, **kw):  # the first lookup (pre-check) sees nothing
        q = real(*a, **kw)
        calls["n"] += 1
        return q.where(False) if calls["n"] == 1 else q
    monkeypatch.setattr(rv, "select", blind)
    r = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "ref": "d" * 40},
                    headers={**H, "Idempotency-Key": KEY})
    assert r.status_code == 200, r.text
    assert launcher.launched == []


def test_migration_0005_unique_nullable(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    col = {c["name"]: c for c in insp.get_columns("reviews")}["idempotency_key"]
    assert col["nullable"]
    assert any(u["column_names"] == ["idempotency_key"] for u in insp.get_unique_constraints("reviews"))
    command.downgrade(cfg, "0004")
    assert "idempotency_key" not in {c["name"] for c in inspect(create_engine(url)).get_columns("reviews")}
    command.upgrade(cfg, "head")
