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
import threading
import time
from pathlib import Path

from .ai_client import DEFAULT_MODELS, AIClient, parse_ai_json
from .health import AI_CALLS, STAGE
from .api_client import ReviewAPI, Superseded
from .checkpoint import (Checkpoint, atomic_write_json, atomic_write_text, attempt_dir, now, previous_attempts,
                         review_dir)
from .config import WorkerConfig
from .report import build_report, top_findings, write_partial_report, write_report
from .tools import SCANNERS, SEV_ORDER, make_finding, run_scanner, tool_versions

log = logging.getLogger("scanner")
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".php", ".cs", ".rs", ".kt", ".c", ".cpp",
            ".h", ".sh", ".yaml", ".yml", ".tf", ".sql", ".scala", ".swift"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}
STAGE_PCT = {"start": 0, "clone": 5, "semgrep": 20, "gitleaks": 35, "trivy": 50, "ai": 50, "report": 100, "done": 100}
BRIEF_TOP = 10


BRIEF_JSON_HINT = ('Answer in Markdown, then end with one line of JSON exactly like '
                   '{"ai_severity": "critical|high|medium|low|info", "confidence": 0.0-1.0, "ai_fix": "<markdown fix>"}')
DEEP_JSON_HINT = ('Answer in Markdown, then end with one line of JSON: {"findings": [{"id": "<finding id>", '
                  '"ai_severity": "critical|high|medium|low|info", "confidence": 0.0-1.0, "ai_fix": "<markdown>"}]}')
UNSTRUCTURED_CONFIDENCE = 0.5  # docs/severity-mapping.md: AI answered without the JSON line


def _findings_with_ids(findings) -> str:
    return "\n".join(f"- id={f['id']} [{f['severity']}] {f['tool']} {f['rule']} line {f.get('line') or '-'}"
                     for f in findings) or "(no scanner findings)"


def _mask_secret_lines(code: str, related: list[dict]) -> str:
    lines = {f["line"] for f in related if f["tool"] == "gitleaks" and f.get("line")}
    if not lines:
        return code
    out = code.splitlines()
    for n in lines:
        if 0 < n <= len(out):
            out[n - 1] = "[line redacted: possible secret]"
    return "\n".join(out)


def _ai_fields(j: dict, fallback_sev: str) -> dict:
    sev = str(j.get("ai_severity") or "").lower()
    conf = j.get("confidence")
    conf = min(max(float(conf), 0.0), 1.0) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else None
    fix = j.get("ai_fix") if isinstance(j.get("ai_fix"), str) and j.get("ai_fix").strip() else None
    return {"ai_severity": sev if sev in SEV_ORDER else fallback_sev,
            "confidence": UNSTRUCTURED_CONFIDENCE if conf is None else conf, "ai_fix": fix}


def _enrich(f: dict, ans: str) -> dict:
    """Brief: ai_explanation + ai_fix/confidence/ai_severity. The original `severity` is never changed."""
    text, j = parse_ai_json(ans)
    fields = _ai_fields(j or {}, f["severity"])
    if fields["ai_fix"] is None:
        fields["ai_fix"] = text  # unstructured answer: the Markdown itself carries the fix
    return {**f, "ai_explanation": text, **fields}


def _enrich_from_file_review(related: list[dict], ans: str) -> list[dict]:
    """Deep: per-finding AI fields from the file review's JSON line (only findings it mentions)."""
    _, j = parse_ai_json(ans)
    by_id = {x.get("id"): x for x in (j or {}).get("findings", []) if isinstance(x, dict)}
    out = []
    for f in related:
        if f["id"] in by_id:
            out.append({**f, **_ai_fields(by_id[f["id"]], f["severity"])})
    return out


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
    if f["tool"] == "gitleaks":  # never send the secret: only the masked snippet
        return "```\n" + f["snippet"] + "\n```" if f.get("snippet") else ""
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
        self._ai_degraded = False
        self._superseded: str | None = None
        self._stop = threading.Event()
        self._ai_lock = threading.Lock()
        self._ai_stats = {"calls": 0, "errors": 0, "seconds": 0.0}  # delta since the last accepted heartbeat

    def _with_ai(self, body: dict) -> dict:
        """Attach (and reset) the AI-call delta; review-api turns it into ai_provider_* metrics."""
        with self._ai_lock:
            if self._ai_stats["calls"]:
                body["ai"] = {**self._ai_stats, "seconds": round(self._ai_stats["seconds"], 3)}
                self._ai_stats = {"calls": 0, "errors": 0, "seconds": 0.0}
        return body

    def _restore_ai(self, body: dict) -> None:
        ai = body.get("ai")
        if ai:
            with self._ai_lock:
                for k in ("calls", "errors", "seconds"):
                    self._ai_stats[k] += ai[k]

    def _send(self, body: dict) -> None:
        self._with_ai(body)
        try:
            self.api.report(self.cfg.review_id, body)
        except Superseded:
            raise
        except Exception:
            self._restore_ai(body)
            raise

    # ---- heartbeat -----------------------------------------------------------------------
    def _heartbeat_loop(self) -> None:
        """Background heartbeat while a long unit of work runs (clone, a scanner, an AI call):
        review-api's reconciler marks an attempt stuck after HEARTBEAT_TIMEOUT_SECONDS of silence."""
        while not self._stop.wait(self.cfg.heartbeat_seconds):
            st = self.cp.state
            try:
                self._send({"attempt": self.cfg.attempt, "budget_no": self.cfg.budget_no,
                            "stage": st["stage"], "progress": st["progress"], "commit_sha": st["commit_sha"]})
            except Superseded as exc:
                self._superseded = str(exc) or "superseded"
                return
            except Exception:  # noqa: BLE001 - a heartbeat blip must not kill the scan
                log.warning("heartbeat failed", exc_info=True)

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
        if self._superseded and not outcome:
            raise Superseded(self._superseded)
        st = self.cp.state
        st["stage"] = stage
        for name in STAGE_PCT:
            STAGE.labels(name).set(1 if name == stage else 0)
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
        self._send(body)
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
        hb = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
        if cfg.heartbeat_seconds > 0:
            hb.start()
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
            self._partial()
            return "paused"
        except Superseded as exc:
            log.info("attempt superseded (%s) — stopping", exc)
            return "superseded"
        except BaseException:  # error, SIGTERM (cancel / force-fail / pause), kill by the watchdog
            self._partial()
            raise
        finally:
            self._stop.set()
            shutil.rmtree(tmp, ignore_errors=True)  # source code never outlives the job

    # ---- AI --------------------------------------------------------------------------------
    def _count_ai(self, t0: float, ok: bool) -> None:
        dt = time.monotonic() - t0
        AI_CALLS.labels(self.cfg.ai_kind or "unknown", "ok" if ok else "error").inc()
        with self._ai_lock:
            self._ai_stats["calls"] += 1
            self._ai_stats["errors"] += 0 if ok else 1
            self._ai_stats["seconds"] += dt

    def _ask(self, prompt: str) -> str | None:
        t0 = time.monotonic()
        try:
            if self._ai_degraded and hasattr(self.ai, "complete_once"):
                out = self.ai.complete_once(prompt)  # provider already failed after retries: don't hammer it
            else:
                out = self.ai.complete(prompt)
            self._count_ai(t0, True)
            return out
        except Exception as exc:  # noqa: BLE001
            self._count_ai(t0, False)
            self._ai_degraded = True
            with (self.cp.dir / "logs" / "ai.log").open("a") as fh:
                fh.write(f"{now()} {type(exc).__name__}: {str(exc)[:300]}\n")
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
                code = _mask_secret_lines((src / rel).read_text(errors="replace"), related)
                ans = self._ask(f"Review file `{rel}` for security issues.\nScanner findings:\n"
                                f"{_findings_with_ids(related)}\n\n```\n{code}\n```\n\n{DEEP_JSON_HINT}")
                if ans:
                    self.cp.append_findings(_enrich_from_file_review(related, ans))
                    self.cp.append_findings([make_finding("ai", "ai_review", "ai.file_review", "info", rel, None,
                                                          f"AI review of {rel}", ans, ai_explanation=ans)])
                cur["files_done"].append(rel)  # checkpoint after each file
                self._beat("ai", 50 + int(40 * (i + 1) / max(len(files), 1)))
        elif cfg.depth == "brief":
            for f in top_findings(findings, BRIEF_TOP):
                if f.get("ai_explanation"):
                    continue  # enriched by an earlier attempt
                ans = self._ask(f"Explain the risk and give a concrete fix for this finding in {cfg.repo_url}:\n"
                                f"{_findings_text([f])}\n{_context(src, f)}\n\n{BRIEF_JSON_HINT}")
                if ans:
                    self.cp.append_findings([_enrich(f, ans)])  # same id: last record wins
                self._beat("ai", 50 + 4 * len([x for x in self.cp.findings() if x.get("ai_explanation")]))
        if not self.cp.summary_path.is_file():
            top = top_findings(self.cp.findings(), 30 if cfg.depth == "quick" else BRIEF_TOP)
            ans = self._ask(f"Give a short (<=10 bullets) security summary of {cfg.repo_url}:\n{_findings_text(top)}")
            if ans:
                atomic_write_text(self.cp.summary_path, ans)
        self._beat("ai")

    def _partial(self) -> None:
        """Keep results so far (Phase 3: partial reports on failed/cancelled). Never masks the original error."""
        if not self.cp.state.get("commit_sha") or not self.files:
            return  # nothing scanned yet (e.g. clone failed): no partial report
        try:
            rep = self._report()
            # scanners run over the whole tree: files count as scanned once every scanner finished
            scanned = len(self.cp.cursor["scanners_done"]) == len(SCANNERS)
            rep["summary"]["files_scanned"] = len(self.files) if scanned else 0
            write_partial_report(self.rdir, rep)
        except Exception:  # noqa: BLE001
            log.warning("could not write partial report", exc_info=True)

    def _report(self) -> dict:
        prov = None
        if self.ai:  # kind = DB provider row's kind (AI_KIND), never derived from the adapter name
            prov = {"name": getattr(self.ai, "name", "") or self.ai.provider, "model": self.ai.model or "unknown",
                    "kind": "cloud" if getattr(self.ai, "is_cloud", True) else "onprem"}
        summary = self.cp.summary_path.read_text() if self.cp.summary_path.is_file() else None
        return build_report(review_id=self.cfg.review_id, repo_url=self.cfg.repo_url,
                            commit_sha=self.cp.state["commit_sha"], ref=self.cfg.ref or None, depth=self.cfg.depth,
                            attempt=self.cfg.attempt, budget_no=self.cfg.budget_no, started_at=self.started_at,
                            finished_at=now(), findings=self.cp.findings(), provider=prov,
                            executive_summary=summary, files_scanned=len(self.files),
                            tool_versions=tool_versions(), private=self.cfg.repo_private)


def make_ai(cfg: WorkerConfig) -> AIClient | None:
    if cfg.ai_provider in ("", "none"):
        return None
    try:
        model = cfg.ai_model or DEFAULT_MODELS.get(cfg.ai_provider, "")
        return AIClient(cfg.ai_provider, model, cfg.ai_api_key, cfg.ai_base_url, kind=cfg.ai_kind,
                        name=cfg.ai_provider_name)
    except ValueError as exc:
        log.warning("AI disabled: %s", exc)
        return None


def _on_sigterm(signum, frame):
    # Job deleted (cancel/pause/supersede): raise so Worker.run()'s finally removes the /work clone.
    # SystemExit is not an Exception -> no outcome=error is reported for a deliberate stop.
    raise SystemExit(128 + signum)


def main(argv=None) -> int:
    import signal
    signal.signal(signal.SIGTERM, _on_sigterm)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-id")
    args = ap.parse_args(argv)
    cfg = WorkerConfig()
    if args.review_id:
        cfg.review_id = args.review_id
    from .health import READY, SCAN_ATTEMPTS, start_health_server
    start_health_server(int(os.environ.get("HEALTH_PORT", "8002")))
    READY.set()
    api = ReviewAPI(cfg.review_api_url, cfg.internal_token)
    w = Worker(cfg, api, make_ai(cfg))
    try:
        w.run()
        SCAN_ATTEMPTS.labels("success").inc()
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("scan attempt failed")
        SCAN_ATTEMPTS.labels("error").inc()
        try:  # outcome=error for this attempt; the watchdog decides on attempt n+1 (max 5 per budget)
            w._beat(w.cp.state["stage"], outcome="error", error=f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
