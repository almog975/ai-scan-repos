"""DoD e2e: LocalLauncher runs the real scanner-worker against a local git fixture.

Needs a Python with scanner-worker requirements (and ideally semgrep/gitleaks/trivy on PATH):
set AISCAN_WORKER_PYTHON (Jenkins: .ci-venv-worker/bin/python). Skipped otherwise."""
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.launcher import JobSpec, LocalLauncher
from tests.contract import check

ROOT = Path(__file__).resolve().parents[2]
PY = os.environ.get("AISCAN_WORKER_PYTHON", "")
URL = "https://github.com/aiscan-fixture/vuln-app"
RID = "5d7e1c2a-9b3f-4e8a-a1c4-0e6f2b9d8c71"
pytestmark = pytest.mark.skipif(not PY or not Path(PY).exists(), reason="AISCAN_WORKER_PYTHON not set")


def _git_fixture(d: Path) -> str:
    d.mkdir()
    (d / "app.py").write_text("import os\n\ndef run(cmd):\n    return os.system(cmd)  # command injection\n\n"
                              "def calc(expr):\n    return eval(expr)\n")
    (d / "requirements.txt").write_text("flask==0.12\n")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(d)]
    subprocess.run(g[:5] + ["-C", str(d), "init", "-q"], check=True)
    subprocess.run(g + ["add", "."], check=True)
    subprocess.run(g + ["commit", "-qm", "fixture"], check=True)
    return subprocess.run(g + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_local_launcher_e2e_completed(tmp_path, monkeypatch):
    sha = _git_fixture(tmp_path / "repo")
    reports, work = tmp_path / "reports", tmp_path / "work"
    work.mkdir()
    for k, v in {"WORK_DIR": str(work), "AI_PROVIDER": "none", "HEALTH_PORT": str(_free_port()),
                 "REVIEW_API_URL": "http://127.0.0.1:9", "INTERNAL_TOKEN": "t",
                 # contract repo.url is github-only: rewrite it to the local fixture for the clone
                 "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{tmp_path / 'repo'}.insteadOf",
                 "GIT_CONFIG_VALUE_0": URL}.items():
        monkeypatch.setenv(k, v)
    s = Settings(reports_dir=str(reports), local_worker_cmd=f"{PY} -m scanner.run",
                 local_worker_cwd=str(ROOT / "scanner-worker"))
    ll = LocalLauncher(s)
    name = ll.launch(JobSpec(RID, 1, 1, "quick", URL, sha, "HEAD"))
    deadline = time.time() + 600
    while ll.status(name) == "active" and time.time() < deadline:
        time.sleep(1)
    assert ll.status(name) == "succeeded"
    rdir = reports / RID
    assert json.loads((rdir / "progress.json").read_text())["state"] == "completed"
    rep = json.loads((rdir / "report.json").read_text())
    check("report.schema.json", rep)
    assert rep["repo"]["commit_sha"] == sha and rep["provider"] == {"name": "none", "kind": "onprem", "model": "none"}
    assert (rdir / "report.md").read_text().startswith("# Security review:")
    assert not list(work.iterdir())  # clone dir removed
