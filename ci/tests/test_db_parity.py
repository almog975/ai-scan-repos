"""ci/db-parity.sh against a real postgres (PGHOST set; skipped otherwise). The drift case uses a scratch copy."""
import os, shutil, subprocess
import pytest
from conftest import ROOT

pytestmark = pytest.mark.skipif(not (os.environ.get("PGHOST") and shutil.which("pg_dump")), reason="needs PGHOST + pg_dump")


def run(root):
    return subprocess.run(["bash", "ci/db-parity.sh"], cwd=root, capture_output=True, text=True)


def test_parity_clean():
    r = run(ROOT)
    assert r.returncode == 0 and "db parity ok" in r.stdout and "migration round trip ok" in r.stdout, r.stderr[-2000:]


def test_parity_drift_fails(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", "node_modules", "reports", "__pycache__"))
    sql = dst / "infra/db/schema.sql"
    sql.write_text("CREATE TABLE parity_canary (x int);\n" + sql.read_text())
    r = run(dst)
    assert "parity_canary" in r.stdout  # the drift is detected...
    assert r.returncode != 0             # ...and must fail the stage
