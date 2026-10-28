"""Read-side of the reports volume written by scanner-worker (see scanner/checkpoint.py for the layout)."""
from __future__ import annotations

import json
from pathlib import Path

from .models import Review
from .serialize import iso, _v


def review_dir(settings, review_id: str) -> Path:
    return Path(settings.reports_dir) / review_id


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):  # missing, or mid-rename on a non-POSIX volume
        return None


def progress(settings, r: Review) -> dict:
    """progress/v1 snapshot; the DB row is authoritative for state/attempt/budget_no."""
    doc = _read_json(review_dir(settings, r.id) / "progress.json")
    if not isinstance(doc, dict) or doc.get("review_id") != r.id:
        doc = {"schema": "progress/v1", "review_id": r.id, "current_file": None,
               "updated_at": iso(r.updated_at or r.created_at),
               "totals": {"files": r.files_total or 0, "done": r.files_done or 0, "skipped": 0, "error": 0,
                          "findings": r.findings_count or 0}, "files": []}
    doc.update(state=_v(r.state), attempt=r.attempt, budget_no=r.budget_no)
    return doc


def report_json(settings, r: Review):
    return _read_json(review_dir(settings, r.id) / "report.json")


def report_md(settings, r: Review) -> str | None:
    p = review_dir(settings, r.id) / "report.md"
    return p.read_text() if p.is_file() else None
