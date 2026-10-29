"""make test: real exit codes + JUnit, proven by injecting a failing test into a scratch copy."""
import shutil, subprocess, os
import pytest
from conftest import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("make"), reason="make not on PATH")


@pytest.mark.xfail(reason="medium: root Makefile lists test-%/build-% in .PHONY, and GNU make skips implicit-rule "
                          "search for phony targets, so `make test`/`make build` at the root do nothing and exit 0", strict=True)
def test_root_make_test_delegates():
    out = subprocess.run(["make", "-n", "test"], cwd=ROOT, capture_output=True, text=True)
    assert "-C gateway" in out.stdout + out.stderr


INJECT = {
    "gateway": ("tests/test_zz_injected.py", "def test_injected_failure():\n    assert False\n"),
    "review-api": ("tests/test_zz_injected.py", "def test_injected_failure():\n    assert False\n"),
    "scanner-worker": ("tests/test_zz_injected.py", "def test_injected_failure():\n    assert False\n"),
    "frontend": ("src/test/zz_injected.test.ts", "import {test,expect} from 'vitest'\ntest('injected',()=>expect(1).toBe(2))\n"),
}


@pytest.mark.slow
@pytest.mark.parametrize("comp", list(INJECT))
def test_component_make_test_fails_with_junit(tmp_path, comp):
    if comp == "frontend" and not (ROOT / "frontend/node_modules").is_dir():
        pytest.skip("frontend/node_modules missing (npm ci)")
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", "reports", "__pycache__", "dist", "node_modules"))
    if (ROOT / "frontend/node_modules").is_dir():
        (dst / "frontend/node_modules").symlink_to(ROOT / "frontend/node_modules")
    rel, body = INJECT[comp]
    (dst / comp / rel).write_text(body)
    env = dict(os.environ, AISCAN_WORKER_PYTHON=os.environ.get("AISCAN_WORKER_PYTHON", shutil.which("python3")))
    env.pop("PYTEST_ADDOPTS", None)
    r = subprocess.run(["make", "test"], cwd=dst / comp, capture_output=True, text=True, env=env)
    assert r.returncode != 0, r.stdout[-2000:]
    xml = (dst / comp / "reports" / "junit.xml").read_text()
    assert "injected" in xml and 'failures="1"' in xml
