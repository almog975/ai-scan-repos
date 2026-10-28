"""Phase 3 harness: a real review-api (uvicorn in a thread, sqlite *file* DB so it survives restarts) driving
real scanner-worker subprocesses through LocalLauncher, with shimmed scanners and an injected watchdog clock."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn

from app import db as dbmod
from app.auth import ensure_user
from app.config import Settings
from app.launcher import LocalLauncher
from app.main import create_app
from app.watchdog import sweep

ROOT = Path(__file__).resolve().parents[2]
SHIMS = Path(__file__).parent / "phase3_shims"
WORKER_PY = os.environ.get("AISCAN_WORKER_PYTHON", "")
TOKEN = "p3-internal-token"
OWNER, NAME = "aiscan-fixture", "vuln-app"
URL = f"https://github.com/{OWNER}/{NAME}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def git_fixture(d: Path) -> str:
    d.mkdir(parents=True)
    (d / "app.py").write_text("import os\n\ndef run(cmd):\n    return os.system(cmd)\n\ndef calc(e):\n    return eval(e)\n")
    (d / "requirements.txt").write_text("flask==0.12\n")
    (d / "conf.env").write_text("k=REDACTED\n")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(d)]
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(g + ["add", "."], check=True)
    subprocess.run(g + ["commit", "-qm", "fixture"], check=True)
    return subprocess.run(g + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


class FixtureGitHub:
    def __init__(self, sha):
        self.sha = sha

    def repo(self, owner, name):
        from app.errors import not_found
        from app.github import RepoInfo
        if (owner, name) != (OWNER, NAME):
            raise not_found("repository or ref")
        return RepoInfo(False, "main", 64)

    def resolve(self, owner, name, ref):
        self.repo(owner, name)
        return self.sha


class Stack:
    """review-api + LocalLauncher (real worker subprocesses). restart() = kill the API, keep DB/reports/workers."""

    def __init__(self, tmp: Path, monkeypatch):
        self.tmp = tmp
        self.repo = tmp / "origin"
        self.sha = git_fixture(self.repo)
        self.state = tmp / "shim"
        self.state.mkdir()
        (tmp / "work").mkdir()
        self.port = free_port()
        self.db_url = f"sqlite:///{tmp / 'review-api.db'}"
        env = {"WORK_DIR": str(tmp / "work"), "AI_PROVIDER": "none", "REVIEW_API_URL": f"http://127.0.0.1:{self.port}",
               "INTERNAL_API_TOKEN": TOKEN, "P3_SHIM_STATE": str(self.state), "HEALTH_PORT": "0",
               "PATH": f"{SHIMS}:{os.environ['PATH']}", "SCANNER_TIMEOUT": "120",
               "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{self.repo}.insteadOf",
               "GIT_CONFIG_VALUE_0": URL}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        self.settings = Settings(database_url=self.db_url, internal_api_token=TOKEN, ai_keys_encryption_key="00" * 32,
                                 reports_dir=str(tmp / "reports"), watchdog_enabled=False,
                                 local_worker_cmd=f"{WORKER_PY} -m scanner.run",
                                 local_worker_cwd=str(ROOT / "scanner-worker"), heartbeat_timeout_seconds=900)
        self.launcher = None
        self.server = self.thread = None
        self.start()
        with dbmod.session_factory()() as db:
            self.uid = ensure_user(db, 2, "alice").id
        self.H = {"X-Internal-Token": TOKEN, "X-User-Id": self.uid}
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=10)

    # ---- review-api lifecycle ----
    def start(self):
        self.launcher = LocalLauncher(self.settings)  # fresh process table, like a restarted pod
        self.app = create_app(self.settings, launcher=self.launcher, github=FixtureGitHub(self.sha),
                              start_watchdog=False)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "review-api did not start"
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(10)

    def restart(self):
        self.stop()
        self.start()

    def close(self):
        for p in list(self.all_procs):
            if p.poll() is None:
                p.kill()
                p.wait(5)
        self.stop()

    @property
    def all_procs(self):
        return self.__dict__.setdefault("_procs", [])

    def track(self):
        for p in self.launcher.procs.values():
            if p not in self.all_procs:
                self.all_procs.append(p)

    # ---- API helpers ----
    def create(self, depth="quick"):
        r = self.http.post("/reviews", json={"repo_url": URL, "depth": depth}, headers=self.H)
        assert r.status_code == 201, r.text
        self.track()
        return r.json()["id"]

    def get(self, rid):
        return self.http.get(f"/reviews/{rid}", headers=self.H).json()

    def post(self, rid, action):
        r = self.http.post(f"/reviews/{rid}/{action}", headers=self.H)
        self.track()
        return r

    def sweep(self, at: datetime | None = None):
        """One reconciler pass, plus (for reviews it marked stuck) the next pass one interval later: the
        reconciler keeps `stuck` visible for a cycle before relaunching (Planner, Phase 3)."""
        from datetime import timedelta, timezone
        launcher = self.app.state.deps["launcher"]
        with dbmod.session_factory()() as db:
            out = sweep(db, launcher, self.settings, at=at)
        if any(v == "stuck" for v in out.values()):
            nxt = (at or datetime.now(timezone.utc)) + timedelta(seconds=self.settings.watchdog_interval_seconds)
            with dbmod.session_factory()() as db:
                out.update({k: v for k, v in sweep(db, launcher, self.settings, at=nxt).items() if k in out})
        self.track()
        return out

    # ---- worker / chaos helpers ----
    def hang_trivy(self, on=True):
        f = self.state / "hang"
        f.write_text("") if on else f.unlink(missing_ok=True)

    def wait(self, cond, timeout=90, what="condition"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = cond()
            if v:
                return v
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for {what}")

    def wait_trivy_started(self):
        f = self.state / "trivy.started"
        self.wait(f.exists, what="worker reached trivy")
        return int(f.read_text())

    def worker_pids(self, rid):
        out = subprocess.run(["pgrep", "-f", f"scanner.run --review-id {rid}"], capture_output=True, text=True).stdout
        return [int(x) for x in out.split()]

    def sigkill_worker(self, rid):
        pids = self.worker_pids(rid)
        assert pids, "no worker process"
        for pid in pids:
            os.kill(pid, signal.SIGKILL)
        for p in self.all_procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                pass
        (self.state / "trivy.started").unlink(missing_ok=True)
        return pids

    def wait_state(self, rid, *states, timeout=90):
        return self.wait(lambda: self.get(rid)["state"] in states and self.get(rid), timeout, f"state in {states}")

    def calls(self, tool):
        log = self.state / "calls.log"
        return sum(1 for ln in log.read_text().splitlines() if ln.split()[0] == tool) if log.exists() else 0

    def report(self, rid):
        r = self.http.get(f"/reviews/{rid}/report.json", headers=self.H)
        assert r.status_code == 200, r.text
        return r.json()

    def work_empty(self):
        return list((self.tmp / "work").iterdir()) == []


def later(**kw):
    return datetime.now(timezone.utc) + timedelta(**kw)


def ids(rep):
    return [f["id"] for f in rep["findings"]]


def dump(rep):
    return json.dumps(rep, indent=1)[:2000]
