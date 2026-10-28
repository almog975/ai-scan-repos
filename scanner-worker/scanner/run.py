"""Scanner worker entrypoint: python -m scanner.run --review-id X"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .ai_client import AIClient
from .api_client import ReviewAPI
from .checkpoint import Checkpoint
from .config import WorkerConfig
from .report import build_report, write_report
from .tools import SCANNERS, SEV_ORDER, run_scanner

log = logging.getLogger("scanner")
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb", ".php", ".cs", ".rs", ".kt", ".c", ".cpp",
            ".h", ".sh", ".yaml", ".yml", ".tf", ".sql", ".scala", ".swift"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__"}


class Paused(Exception):
    pass


def clone(repo_url: str, dest: Path, timeout: int) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--", repo_url, str(dest)],
                   check=True, capture_output=True, text=True, timeout=timeout, env=env)


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


def _top(findings, n):
    return sorted(findings, key=lambda f: SEV_ORDER.index(f["severity"]) if f["severity"] in SEV_ORDER else 99)[:n]


def _findings_text(findings) -> str:
    return "\n".join(f"- [{f['severity']}] {f['scanner']} {f['rule']} {f['file']}:{f.get('line') or ''} — {f['message']}"
                     for f in findings) or "(no scanner findings)"


class Worker:
    def __init__(self, cfg: WorkerConfig, api, ai: AIClient | None, scanner_fn=run_scanner, clone_fn=clone):
        self.cfg, self.api, self.ai = cfg, api, ai
        self.scanner_fn, self.clone_fn = scanner_fn, clone_fn
        self.cp = Checkpoint(Path(cfg.reports_dir) / cfg.review_id)

    def _beat(self, pct: int, msg: str):
        self.cp.state["progress"] = pct
        self.cp.save()
        self.cp.write_progress(pct, msg)
        self.api.progress(self.cfg.review_id, pct, status="running", message=msg)
        if self.cp.paused():
            raise Paused()

    def run(self) -> str:
        cfg = self.cfg
        resumed = self.cp.load()
        log.info("review %s depth=%s resumed=%s", cfg.review_id, cfg.depth, resumed)
        Path(cfg.work_dir).mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f"src-{cfg.review_id[:12]}-", dir=cfg.work_dir))
        try:
            src = tmp / "repo"
            self.clone_fn(cfg.repo_url, src, cfg.clone_timeout)
            self._beat(max(self.cp.state.get("progress", 0), 5), "cloned")
            # 1) scanners (each is a checkpoint step)
            for i, name in enumerate(SCANNERS):
                if name in self.cp.state["scanners"]:
                    continue
                self.cp.state["stage"] = f"scan:{name}"
                self.cp.state["scanners"][name] = self.scanner_fn(name, src, cfg.scanner_timeout)
                self._beat(10 + 15 * (i + 1), f"{name}: {self.cp.state['scanners'][name]['status']}")
            findings = [f for s in self.cp.state["scanners"].values() for f in s["findings"]]
            # 2) AI
            self.cp.state["stage"] = "ai"
            self._ai_phase(src, findings)
            # 3) report
            self.cp.state["stage"] = "report"
            rep = build_report(review_id=cfg.review_id, repo_url=cfg.repo_url, depth=cfg.depth,
                               scanners=self.cp.state["scanners"], ai=self._ai_meta())
            path = write_report(self.cp.dir, rep)
            self.cp.state["stage"] = "done"
            self.cp.save()
            self.cp.write_progress(100, "completed")
            self.api.progress(cfg.review_id, 100, status="completed", message="completed",
                              report_path=str(path), ai_provider=self.ai.provider if self.ai else None)
            return "completed"
        except Paused:
            self.cp.save()
            log.info("paused — checkpoint saved")
            return "paused"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)  # source code never outlives the job

    def _ai_meta(self) -> dict:
        st = self.cp.state
        return {"provider": self.ai.provider if self.ai else None, "model": self.ai.model if self.ai else None,
                "is_cloud": bool(self.ai and self.ai.is_cloud),
                "code_left_network": bool(self.ai and self.ai.is_cloud and st.get("ai_sent")),
                "summary": st.get("ai_summary"), "file_reviews": st.get("ai_files", {}), "error": st.get("ai_error")}

    def _ask(self, prompt: str) -> str | None:
        try:
            self.cp.state["ai_sent"] = True
            return self.ai.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            self.cp.state["ai_error"] = str(exc)[:300]
            return None

    def _ai_phase(self, src: Path, findings: list):
        cfg, st = self.cfg, self.cp.state
        if not self.ai:
            return
        if cfg.depth == "deep":
            files = list_code_files(src, cfg.deep_max_files, cfg.deep_max_file_bytes)
            for i, rel in enumerate(files):
                if rel in st["done_files"]:
                    continue
                code = (src / rel).read_text(errors="replace")
                related = [f for f in findings if f.get("file") and rel in str(f["file"])]
                ans = self._ask(f"Review file `{rel}` for security issues.\nScanner findings:\n"
                                f"{_findings_text(related)}\n\n```\n{code}\n```")
                if ans:
                    st["ai_files"][rel] = ans
                st["done_files"].append(rel)  # checkpoint after each file
                self._beat(55 + int(40 * (i + 1) / max(len(files), 1)), f"AI reviewed {rel}")
        if st.get("ai_summary") is None:
            if cfg.depth == "quick":
                prompt = f"Give a short (<=10 bullets) security summary of {cfg.repo_url}:\n{_findings_text(_top(findings, 30))}"
            else:
                top = _top(findings, cfg.brief_top_findings)
                ctx = []
                for f in top:
                    p = src / str(f.get("file") or "")
                    if f.get("line") and p.is_file() and p.resolve().is_relative_to(src.resolve()):
                        ls = p.read_text(errors="replace").splitlines()
                        a = max(int(f["line"]) - 5, 0)
                        ctx.append(f"{f['file']}:{f['line']}\n```\n" + "\n".join(ls[a:a + 10]) + "\n```")
                prompt = (f"For each top finding in {cfg.repo_url}, explain the risk and give a fix.\n"
                          f"{_findings_text(top)}\n\nCode context:\n" + "\n".join(ctx))
            st["ai_summary"] = self._ask(prompt)
            self._beat(97, "AI summary done")


def make_ai(cfg: WorkerConfig) -> AIClient | None:
    if cfg.ai_provider in ("", "none"):
        return None
    try:
        return AIClient(cfg.ai_provider, cfg.ai_model, cfg.ai_api_key, cfg.ai_base_url)
    except ValueError as exc:
        log.warning("AI disabled: %s", exc)
        return None


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--review-id", required=True)
    args = ap.parse_args(argv)
    cfg = WorkerConfig(review_id=args.review_id)
    api = ReviewAPI(cfg.review_api_url, cfg.internal_token)
    rv = api.get_review(cfg.review_id)
    cfg.repo_url, cfg.depth = rv["repo_url"], rv["depth"]
    if rv.get("ai_provider"):
        cfg.ai_provider = rv["ai_provider"]
    try:
        Worker(cfg, api, make_ai(cfg)).run()
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("scan failed")
        # leave status running: the watchdog retries from checkpoint (max attempts)
        api.progress(cfg.review_id, rv.get("progress", 0), message=f"attempt error: {exc}"[:500])
        return 1


if __name__ == "__main__":
    sys.exit(main())
