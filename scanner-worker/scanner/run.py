"""Scanner worker entrypoint (one Kubernetes Job == one attempt).

Env (set by review-api's launcher): REVIEW_ID, ATTEMPT, BUDGET_NO, DEPTH, REPO_URL, REF, COMMIT_SHA,
AI_PROVIDER/AI_MODEL/AI_BASE_URL/AI_API_KEY (per-review Secret), REPORTS_DIR, INTERNAL_API_TOKEN.

Stages (checkpoint/v1): start -> clone -> semgrep -> gitleaks -> trivy -> ai -> report -> done.
Every unit of work (a scanner, an AI file/finding) is followed by findings append + checkpoint save,
so a killed Job resumes from the newest earlier attempt that scanned the same commit_sha.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .ai_client import DEFAULT_MODELS, ONPREM, AIClient
from .api_client import ReviewAPI, Superseded
from .checkpoint import (Checkpoint, atomic_write_json, atomic_write_text, attempt_dir, now, previous_attempts,
                         review_dir)
from .config import WorkerConfig
from .report import build_report, top_findings, write_report
from .tools import SCANNERS, make_finding, run_scanner

log = logging.getLogger("scanner")
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".php", ".cs", ".rs", ".kt", ".c", ".cpp",
            ".h", ".sh", ".yaml", ".yml", ".tf", ".sql", ".scala", ".swift"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}
STAGE_PCT = {"start": 0, "clone": 5, "semgrep": 20, "gitleaks": 35, "trivy": 50, "ai": 50, "report": 100, "done": 100}
BRIEF_TOP = 10


class Paused(Exception):
    pass


def _git(args, cwd, timeout):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          timeout=timeout, env=env).stdout.strip()


def clone(repo_url: str, dest: Path, timeout: int, ref: str = "", commit_sha: str = "") -> str:
    """Shallow fetch of exactly commit_sha (or ref/default branch). Returns the checked-out SHA."""
    dest.mkdir(parents=True)
    _git(["init", "-q"], dest, timeout)
    _git(["remote", "add", "origin", "--", repo_url], dest, timeout)
    _git(["fetch", "-q", "--depth", "1", "--no-tags", "origin", commit_sha or ref or "HEAD"], dest, timeout)
    _git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest, timeout)
    return _git(["rev-parse", "HEAD"], dest, timeout)


def list_code_files(src: Path, max_files: int, max_bytes: int) -> list[str]:
    out = []
    for p in sorted(src.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(src).parts):
            continue
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in CODE_EXT and p.stat().st_size <= max_bytes:
            out.append(str(p.relative_to(src)))
            if len(out) >= max_files:
                break
    return out


def _findings_text(findings) -> str:
    return "\n".join(f"- [{f['severity']}] {f['tool']} {f['rule']} {f['file']}:{f.get('line') or ''} — {f['title']}"
                     for f in findings) or "(no scanner findings)"


def _context(src: Path, f: dict) -> str:
    p = src / f["file"]
    if not f.get("line") or not p.is_file() or not p.resolve().is_relative_to(src.resolve()):
        return ""
    ls = p.read_text(errors="replace").splitlines()
    a = max(f["line"] - 5, 0)
    return "```\n" + "\n".join(ls[a:a + 10]) + "\n```"


class Worker:
    def __init__(self, cfg: WorkerConfig, api, ai: AIClient | None, scanner_fn=run_scanner, clone_fn=clone):
        self.cfg, self.api, self.ai = cfg, api, ai
        self.scanner_fn, self.clone_fn = scanner_fn, clone_fn
        self.rdir = review_dir(cfg.reports_dir, cfg.review_id)
        self.cp = Checkpoint(attempt_dir(cfg.reports_dir, cfg.review_id, cfg.budget_no, cfg.attempt),
                             review_id=cfg.review_id, attempt=cfg.attempt, budget_no=cfg.budget_no,
                             depth=cfg.depth, commit_sha=cfg.commit_sha or None)
        self.files: list[str] = []
        self.started_at = now()

    # ---- progress ------------------------------------------------------------------------
    def _progress_doc(self, current=None, state="running") -> dict:
        cur, fs = self.cp.cursor, self.cp.findings()
        by_file: dict[str, int] = {}
        for f in fs:
            by_file[f["file"]] = by_file.get(f["file"], 0) + (f["tool"] != "ai")
        stage = self.cp.state["stage"]
        scanned = len(cur["scanners_done"]) == len(SCANNERS)
        per_file_ai = self.cfg.depth == "deep" and self.ai is not None
        done = set(cur["files_done"])
        files = []
        for p in self.files:
            st = "done" if (p in done or (scanned and not per_file_ai)) else "scanning" if p == current else "pending"
            files.append({"path": p, "status": st, "findings_count": by_file.get(p, 0)})
        return {"schema": "progress/v1", "review_id": self.cfg.review_id, "state": state,
                "stage": {"start": "clone", "done": "report"}.get(stage, stage),
                "attempt": self.cfg.attempt, "budget_no": self.cfg.budget_no, "current_file": current,
                "updated_at": now(),
                "totals": {"files": len(files), "done": sum(f["status"] == "done" for f in files), "skipped": 0,
                           "error": 0, "findings": sum(f["tool"] != "ai" for f in fs)},
                "files": files}

    def _beat(self, stage: str, pct: int | None = None, current=None, outcome=None, error=None):
        st = self.cp.state
        st["stage"] = stage
        st["progress"] = max(st["progress"], STAGE_PCT[stage] if pct is None else pct)
        self.cp.save()
        atomic_write_json(self.rdir / "progress.json",
                          self._progress_doc(current, "completed" if outcome == "success" else "running"))
        body = {"attempt": self.cfg.attempt, "budget_no": self.cfg.budget_no, "stage": stage,
                "progress": st["progress"], "commit_sha": st["commit_sha"]}
        if outcome:  # attempt_outcome: success | error (review-api/watchdog own the review state)
            body["outcome"] = outcome
        if error:
            body["error"] = error[:500]
        self.api.report(self.cfg.review_id, body)
        if not outcome and (self.rdir / "PAUSE").exists():
            raise Paused()

    # ---- main ----------------------------------------------------------------------------
    def run(self) -> str:
        cfg = self.cfg
        resumed = any(self.cp.resume_from(p) for p in
                      previous_attempts(cfg.reports_dir, cfg.review_id, cfg.budget_no, cfg.attempt))
        log.info("review %s %d-%d depth=%s resumed=%s stage=%s", cfg.review_id, cfg.budget_no, cfg.attempt,
                 cfg.depth, resumed, self.cp.state["stage"])
        Path(cfg.work_dir).mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"src-{cfg.review_id[:12]}-", dir=cfg.work_dir))
        try:
            src = tmp / "repo"
            sha = self.clone_fn(cfg.repo_url, src, cfg.clone_timeout, cfg.ref, self.cp.state["commit_sha"] or "")
            if self.cp.state["commit_sha"] and sha != self.cp.state["commit_sha"]:
                raise RuntimeError(f"cloned {sha}, expected {self.cp.state['commit_sha']}")
            self.cp.state["commit_sha"] = sha
            self.files = list_code_files(src, cfg.max_files, 10 * 1024 * 1024)
            self.cp.cursor["files_total"] = len(self.files)
            self._beat("clone")
            for name in SCANNERS:
                if name in self.cp.cursor["scanners_done"]:
                    continue
                self.cp.state["stage"] = name
                res = self.scanner_fn(name, src, cfg.scanner_timeout)
                if res.get("error"):
                    (self.cp.dir / "logs" / f"{name}.log").write_text(res["error"])
                have = {f["id"] for f in self.cp.findings()}
                self.cp.append_findings([f for f in res["findings"] if f["id"] not in have])
                self.cp.cursor["scanners_done"].append(name)
                self._beat(name)
            self._ai_phase(src)
            self._beat("report")
            rep = self._report()
            write_report(self.rdir, rep)
            self._beat("done", 100, outcome="success")
            return "completed"
        except Paused:
            log.info("paused — checkpoint saved")
            return "paused"
        except Superseded as exc:
            log.info("attempt superseded (%s) — stopping", exc)
            return "superseded"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # source code never outlives the job

    # ---- AI --------------------------------------------------------------------------------
    def _ask(self, prompt: str) -> str | None:
        try:
            return self.ai.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            (self.cp.dir / "logs" / "ai.log").open("a").write(f"{now()} {str(exc)[:300]}\n")
            return None

    def _ai_phase(self, src: Path):
        cfg, cur = self.cfg, self.cp.cursor
        if not self.ai or self.cp.state["stage"] in ("report", "done"):
            return
        self.cp.state["stage"] = "ai"
        findings = self.cp.findings()
        if cfg.depth == "deep":
            files = [f for f in self.files[: cfg.deep_max_files]
                     if (src / f).stat().st_size <= cfg.deep_max_file_bytes]
            for i, rel in enumerate(files):
                if rel in cur["files_done"]:
                    continue
                self._beat("ai", current=rel)
                related = [f for f in findings if f["file"] == rel and f["tool"] != "ai"]
                code = (src / rel).read_text(errors="replace")
                ans = self._ask(f"Review file `{rel}` for security issues.\nScanner findings:\n"
                                f"{_findings_text(related)}\n\n```\n{code}\n```")
                if ans:
                    self.cp.append_findings([make_finding("ai", "ai_review", "ai.file_review", "info", rel, None,
                                                          f"AI review of {rel}", ans, ai_explanation=ans)])
                cur["files_done"].append(rel)  # checkpoint after each file
                self._beat("ai", 50 + int(40 * (i + 1) / max(len(files), 1)))
        elif cfg.depth == "brief":
            for f in top_findings(findings, BRIEF_TOP):
                if f.get("ai_explanation"):
                    continue  # enriched by an earlier attempt
                ans = self._ask(f"Explain the risk and give a concrete fix for this finding in {cfg.repo_url}:\n"
                                f"{_findings_text([f])}\n{_context(src, f)}")
                if ans:
                    self.cp.append_findings([{**f, "ai_explanation": ans}])  # same id: last record wins
                self._beat("ai", 50 + 4 * len([x for x in self.cp.findings() if x.get("ai_explanation")]))
        if not self.cp.summary_path.is_file():
            top = top_findings(self.cp.findings(), 30 if cfg.depth == "quick" else BRIEF_TOP)
            ans = self._ask(f"Give a short (<=10 bullets) security summary of {cfg.repo_url}:\n{_findings_text(top)}")
            if ans:
                atomic_write_text(self.cp.summary_path, ans)
        self._beat("ai")

    def _report(self) -> dict:
        prov = None
        if self.ai:
            prov = {"name": self.ai.provider, "model": self.ai.model,
                    "kind": "onprem" if self.ai.provider in ONPREM else "cloud"}
        summary = self.cp.summary_path.read_text() if self.cp.summary_path.is_file() else None
        return build_report(review_id=self.cfg.review_id, repo_url=self.cfg.repo_url,
                            commit_sha=self.cp.state["commit_sha"], ref=self.cfg.ref or None, depth=self.cfg.depth,
                            attempt=self.cfg.attempt, budget_no=self.cfg.budget_no, started_at=self.started_at,
                            finished_at=now(), findings=self.cp.findings(), provider=prov,
                            executive_summary=summary, files_scanned=len(self.files))


def make_ai(cfg: WorkerConfig) -> AIClient | None:
    if cfg.ai_provider in ("", "none"):
        return None
    try:
        model = cfg.ai_model or DEFAULT_MODELS.get(cfg.ai_provider, "")
        return AIClient(cfg.ai_provider, model, cfg.ai_api_key, cfg.ai_base_url)
    except ValueError as exc:
        log.warning("AI disabled: %s", exc)
        return None


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-id")
    args = ap.parse_args(argv)
    cfg = WorkerConfig()
    if args.review_id:
        cfg.review_id = args.review_id
    from .health import READY, start_health_server
    start_health_server(int(os.environ.get("HEALTH_PORT", "8002")))
    READY.set()
    api = ReviewAPI(cfg.review_api_url, cfg.internal_token)
    w = Worker(cfg, api, make_ai(cfg))
    try:
        w.run()
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("scan attempt failed")
        try:  # outcome=error for this attempt; the watchdog decides on attempt n+1 (max 5 per budget)
            w._beat(w.cp.state["stage"], outcome="error", error=f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
