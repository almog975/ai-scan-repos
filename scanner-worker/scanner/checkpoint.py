"""Reports-volume layout + checkpoint/v1 (contracts/checkpoint.schema.json).

    /reports/<review_id>/
        progress.json                       live progress/v1 (atomic tmp+rename)
        report.json, report.md              final (report/v1)
        attempts/<budget_no>-<n>/
            checkpoint.json                 checkpoint/v1, rewritten after every unit of work
            findings.partial.jsonl          append-only finding/v1 lines; only the first
                                            cursor.findings_offset lines are trusted on resume
            ai_summary.md                   executive summary (once the ai stage produced it)
            logs/
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "checkpoint/v1"
PARTIAL = "findings.partial.jsonl"
SUMMARY = "ai_summary.md"
_ATTEMPT_DIR = re.compile(r"^(\d+)-(\d+)$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, default=str))


def review_dir(reports_dir, review_id: str) -> Path:
    return Path(reports_dir) / review_id


def attempt_dir(reports_dir, review_id: str, budget_no: int, attempt: int) -> Path:
    return review_dir(reports_dir, review_id) / "attempts" / f"{budget_no}-{attempt}"


class Checkpoint:
    def __init__(self, directory: Path, *, review_id: str, attempt: int, budget_no: int, depth: str,
                 commit_sha: str | None = None):
        self.dir = Path(directory)
        self.path = self.dir / "checkpoint.json"
        self.partial = self.dir / PARTIAL
        self.summary_path = self.dir / SUMMARY
        self.state = {"schema": SCHEMA, "review_id": review_id, "commit_sha": commit_sha, "depth": depth,
                      "attempt": attempt, "budget_no": budget_no, "stage": "start", "progress": 0,
                      "cursor": {"scanners_done": [], "files_total": 0, "files_done": [], "findings_offset": 0},
                      "updated_at": now()}
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)

    # ---- state -------------------------------------------------------------------------
    @property
    def cursor(self) -> dict:
        return self.state["cursor"]

    def save(self) -> None:
        self.state["updated_at"] = now()
        atomic_write_json(self.path, self.state)

    # ---- findings (append-only, offset-committed) ---------------------------------------
    def findings(self) -> list[dict]:
        """Committed findings, last record per id wins (AI enrichment re-appends the same id)."""
        out: dict[str, dict] = {}
        if self.partial.is_file():
            with self.partial.open() as f:
                for i, line in enumerate(f):
                    if i >= self.cursor["findings_offset"]:
                        break
                    rec = json.loads(line)
                    out[rec["id"]] = rec
        return list(out.values())

    def append_findings(self, items: list[dict]) -> None:
        """Append + fsync, then commit the new offset in checkpoint.json (caller saves)."""
        self._truncate_to_offset()
        if items:
            with self.partial.open("a") as f:
                for it in items:
                    f.write(json.dumps(it, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
        self.cursor["findings_offset"] += len(items)

    def _truncate_to_offset(self) -> None:
        """Drop lines written after the last committed offset (crash between append and save)."""
        if not self.partial.is_file():
            return
        lines = self.partial.read_text().splitlines(keepends=True)
        keep = [ln for ln in lines[: self.cursor["findings_offset"]] if ln.endswith("\n")]
        if len(keep) != len(lines):
            atomic_write_text(self.partial, "".join(keep))
            self.cursor["findings_offset"] = len(keep)

    # ---- resume -----------------------------------------------------------------------
    def resume_from(self, prev: Path) -> bool:
        """Adopt a previous attempt's progress if it scanned the same commit."""
        try:
            old = json.loads((prev / "checkpoint.json").read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if old.get("schema") != SCHEMA or old.get("depth") != self.state["depth"]:
            return False
        sha = self.state["commit_sha"]
        if not old.get("commit_sha") or (sha and old["commit_sha"] != sha):
            return False
        self.state.update(commit_sha=old["commit_sha"], stage=old["stage"], progress=old["progress"],
                          cursor=old["cursor"])
        if prev.resolve() == self.dir.resolve():
            self._truncate_to_offset()
            self.save()
            return True
        if (prev / PARTIAL).is_file():
            shutil.copyfile(prev / PARTIAL, self.partial)
        else:
            self.cursor["findings_offset"] = 0
        self._truncate_to_offset()
        if (prev / SUMMARY).is_file():
            shutil.copyfile(prev / SUMMARY, self.summary_path)
        self.save()
        return True


def previous_attempts(reports_dir, review_id: str, budget_no: int, attempt: int) -> list[Path]:
    """Attempt dirs to resume from, newest first (across budgets). Includes this attempt's own dir:
    after pause/resume the same attempt is relaunched and continues its own checkpoint."""
    base = review_dir(reports_dir, review_id) / "attempts"
    found = []
    for d in base.iterdir() if base.is_dir() else []:
        m = _ATTEMPT_DIR.match(d.name)
        if m and (int(m[1]), int(m[2])) <= (budget_no, attempt):
            found.append(((int(m[1]), int(m[2])), d))
    return [d for _, d in sorted(found, reverse=True)]
