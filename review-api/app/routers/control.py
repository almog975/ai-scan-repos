"""Progress (SSE + JSON), files, report, pause/resume/cancel, and the worker's internal progress channel."""
from __future__ import annotations

import enum
import json
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import db as dbmod
from .. import reports, settings_store
from ..auth import current_user, require_internal
from ..db import get_db
from ..errors import ApiError, conflict, not_found
from ..models import Outcome, Review, State, User
from ..service import current_attempt, end_attempt, now, start_attempt
from ..state import ACTIVE, TERMINAL, next_attempt, transition
from .reviews import check_capacity, get_review, out

router = APIRouter()


class FileStatus(str, enum.Enum):  # common.schema.json#/$defs/file_status
    pending = "pending"
    scanning = "scanning"
    done = "done"
    skipped = "skipped"
    error = "error"


def _settings(request: Request):
    return request.app.state.settings


def _jobs(request: Request):
    return request.app.state.deps["launcher"]


# ---- progress ---------------------------------------------------------------------------------
def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def progress_events(settings, review_id: str, interval: float, max_seconds: float | None = None):
    """progress on every progress.json change, state on state/attempt/budget change, done (terminal) then close."""
    last_prog = last_state = None
    started = last_send = time.monotonic()
    while True:
        with dbmod.session_factory()() as db:
            r = db.get(Review, review_id)
            if r is None:
                return
            snap = reports.progress(settings, r)
            st = {"state": snap["state"], "attempt": r.attempt, "budget_no": r.budget_no}
            terminal = State(r.state) in TERMINAL
        if st != last_state:
            last_state = st
            yield _sse("state", st)
            last_send = time.monotonic()
        key = (snap["updated_at"], snap["state"], snap["attempt"], snap["budget_no"])
        if key != last_prog:
            last_prog = key
            yield _sse("progress", snap)
            last_send = time.monotonic()
        if terminal:
            yield _sse("done", {"state": st["state"]})
            return
        if time.monotonic() - last_send > 15:
            yield ": ping\n\n"
            last_send = time.monotonic()
        if max_seconds is not None and time.monotonic() - started > max_seconds:
            return
        time.sleep(interval)


@router.get("/reviews/{id}/progress")
def get_progress(id: str, request: Request, format: str | None = Query(None, pattern="^json$"),
                 user: User = Depends(current_user), db=Depends(get_db)):
    r = get_review(db, id, user)
    s = _settings(request)
    if format == "json":
        return reports.progress(s, r)
    return StreamingResponse(progress_events(s, r.id, s.sse_interval_seconds, s.sse_max_seconds),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/reviews/{id}/files")
def list_files(id: str, request: Request, status: FileStatus | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100), user: User = Depends(current_user), db=Depends(get_db)):
    r = get_review(db, id, user)
    files = [{"path": f["path"], "status": f["status"], "findings_count": f["findings_count"]}
             for f in reports.progress(_settings(request), r)["files"]]
    if status:
        files = [f for f in files if f["status"] == status.value]
    a = (page - 1) * page_size
    return {"items": files[a:a + page_size], "page": page, "page_size": page_size, "total": len(files)}


# ---- reports ----------------------------------------------------------------------------------
def _completed(r: Review):
    if State(r.state) != State.completed:
        raise conflict(f"report is available once the review is completed (state is {State(r.state).value})",
                       state=State(r.state).value)


@router.get("/reviews/{id}/report.json")
def get_report_json(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    r = get_review(db, id, user)
    _completed(r)
    rep = reports.report_json(_settings(request), r)
    if rep is None:
        raise not_found("report")
    return rep


@router.get("/reviews/{id}/report.md")
def get_report_md(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    r = get_review(db, id, user)
    _completed(r)
    md = reports.report_md(_settings(request), r)
    if md is None:
        raise not_found("report")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


# ---- pause / resume / cancel -----------------------------------------------------------------
def _delete_job(db, r: Review, jobs):
    a = current_attempt(db, r)
    if a is not None and a.job_name:
        jobs.delete(a.job_name)
    return a


@router.post("/reviews/{id}/pause", status_code=202)
def pause_review(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    """queued|running|retrying -> paused. The worker checkpoints after every unit of work, so the Job is
    deleted right away; the attempt stays open and resume relaunches the same attempt from its checkpoint."""
    r = get_review(db, id, user)
    transition(r, State.paused)
    _delete_job(db, r, _jobs(request))
    db.commit()
    return JSONResponse(out(db, r), 202)


@router.post("/reviews/{id}/resume", status_code=202)
def resume_review(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    """paused|stuck -> queued with a new Job in the same budget (running on the worker's first heartbeat)."""
    r = get_review(db, id, user)
    src = State(r.state)
    if src not in (State.paused, State.stuck):
        raise conflict(f"resume is only allowed from paused or stuck (state is {src.value})", state=src.value)
    check_capacity(db, settings_store.load(db))
    if src == State.stuck:  # the stuck attempt is closed: continue with n+1 of this budget
        n = next_attempt(r)
        if not n:
            raise conflict("attempt budget exhausted; use POST /reviews/{id}/retry once failed", state=src.value)
        r.attempt = n
    transition(r, State.queued)
    r.paused_at = None
    start_attempt(db, r, _jobs(request), mark_running=False)
    db.commit()
    return JSONResponse(out(db, r), 202)


@router.post("/reviews/{id}/cancel", status_code=202)
def cancel_review(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    r = get_review(db, id, user)
    if State(r.state) not in ACTIVE:
        raise conflict(f"cannot cancel a {State(r.state).value} review", state=State(r.state).value)
    jobs = _jobs(request)
    _delete_job(db, r, jobs)
    end_attempt(db, r, Outcome.cancelled)
    transition(r, State.cancelled)
    jobs.cleanup(r.id)
    db.commit()
    return JSONResponse(out(db, r), 202)


# ---- worker -> review-api (internal, not part of the public contract) -------------------------
class WorkerProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt: int = Field(ge=1, le=5)
    budget_no: int = Field(ge=1)
    stage: str
    progress: int = Field(ge=0, le=100)
    commit_sha: str | None = Field(default=None, pattern="^[a-f0-9]{40}$")
    outcome: Outcome | None = None
    error: str | None = Field(default=None, max_length=2000)


@router.post("/internal/reviews/{id}/progress", dependencies=[Depends(require_internal)])
def worker_progress(id: str, body: WorkerProgress, request: Request, db=Depends(get_db)):
    """Heartbeat from the scanner Job. 409 tells a superseded/paused/cancelled attempt to stop."""
    r = db.get(Review, id)
    if r is None:
        raise not_found("review")
    st = State(r.state)
    if (body.budget_no, body.attempt) != (r.budget_no, r.attempt) or st not in (State.queued, State.running, State.retrying):
        raise conflict("attempt is not current", state=st.value, attempt=r.attempt, budget_no=r.budget_no)
    a = current_attempt(db, r)
    if a is not None and a.ended_at is not None:
        raise conflict("attempt already ended", state=st.value)
    s = _settings(request)
    r.last_heartbeat = now()
    if st in (State.queued, State.retrying):
        transition(r, State.running)
    if body.commit_sha and not r.commit_sha:
        r.commit_sha = body.commit_sha
    snap = reports.progress(s, r)
    r.files_total, r.files_done = snap["totals"]["files"], snap["totals"]["done"]
    r.findings_count = snap["totals"].get("findings", r.findings_count or 0)
    if body.outcome == Outcome.success:
        rep = reports.report_json(s, r)
        if rep is None:
            raise ApiError(400, "VALIDATION_ERROR", "outcome=success but report.json is missing")
        r.summary = rep.get("summary", {})
        r.findings_count = len(rep.get("findings", []))
        r.report_path = str(reports.review_dir(s, r.id) / "report.json")
        end_attempt(db, r, Outcome.success)
        transition(r, State.completed)
        _jobs(request).cleanup(r.id)
    elif body.outcome is not None:  # error: the watchdog relaunches attempt n+1 on its next sweep
        end_attempt(db, r, Outcome.error, body.error)
    db.commit()
    return {"state": State(r.state).value, "attempt": r.attempt, "budget_no": r.budget_no}
