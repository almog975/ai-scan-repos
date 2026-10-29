"""Quickstart helper: python -m app.devseed creates/reuses a dev user (SQLite only by default)."""
import subprocess, sys
from pathlib import Path

RA = Path(__file__).resolve().parents[1]


def run(env, *a):
    return subprocess.run([sys.executable, "-m", "app.devseed", *a], cwd=RA, env=env, capture_output=True, text=True)


def test_devseed_idempotent_and_prints_headers(tmp_path):
    import os
    url = f"sqlite:///{tmp_path}/dev.db"
    env = {**os.environ, "DATABASE_URL": url, "INTERNAL_API_TOKEN": "tok"}
    a = run(env, "--id", "alice"); b = run(env, "--id", "alice")
    assert a.returncode == 0, a.stderr
    assert a.stdout.strip() == b.stdout.strip() and a.stdout.strip()
    h = run(env, "alice").stdout
    assert h.startswith("-H X-Internal-Token:tok -H X-User-Id:" + a.stdout.strip())


def test_devseed_refuses_non_sqlite():
    import os
    r = run({**os.environ, "DATABASE_URL": "postgresql+psycopg://x:y@nowhere:5432/z"}, "--id")
    assert r.returncode == 2 and "refusing" in r.stderr
