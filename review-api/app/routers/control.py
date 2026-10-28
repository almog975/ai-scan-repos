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
from ..auth import current_user, require_admin, require_internal
from ..db import get_db
from ..errors import ApiError, conflict, not_found
from ..models import AIProvider, FailureCode, Outcome, ProviderKind, Review, State, User
from ..service import current_attempt, end_attempt, now, start_attempt
from ..state import ACTIVE, TERMINAL, transition
from ..watchdog import relaunch
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
def expired() -> ApiError:
    return ApiError(410, "REPORT_EXPIRED", "the report was removed by retention (REPORT_RETENTION_DAYS)")


def _report(request: Request, r: Review, kind: str):
    """completed -> report.*; failed|cancelled -> report.partial.* when the worker left one, else 409;
    410 REPORT_EXPIRED when the files (or the whole review dir) were removed by retention."""
    s, st = _settings(request), State(r.state)
    read = reports.report_json if kind == "json" else reports.report_md
    if st == State.completed:
        doc = read(s, r)
        if doc is None:
            raise expired()
        return doc
    if st in (State.failed, State.cancelled):
        if reports.has_partial(s, r):
            doc = read(s, r, partial=True)
            if doc is not None:
                return doc
        if (r.files_done or r.report_path or r.started_at) and not reports.dir_exists(s, r):
            raise expired()
        raise conflict(f"no partial report for this {st.value} review", state=st.value)
    raise conflict(f"report is available once the review is completed (state is {st.value})", state=st.value)


@router.get("/reviews/{id}/report.json")
def get_report_json(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    return _report(request, get_review(db, id, user), "json")


@router.get("/reviews/{id}/report.md")
def get_report_md(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    md = _report(request, get_review(db, id, user), "md")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


# ---- pause / resume / cancel / admin fail ----------------------------------------------------
def _locked(db, id: str, user: User) -> Review:
    r = get_review(db, id, user)
    db.refresh(r, with_for_update=True)  # serialise with the reconciler (no double Job); no-op on sqlite
    return r


def _delete_job(db, r: Review, jobs):
    a = current_attempt(db, r)
    if a is not None and a.job_name:
        jobs.delete(a.job_name)
    return a


@router.post("/reviews/{id}/pause", status_code=202)
def pause_review(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    """queued|running|retrying -> paused. The worker checkpoints after every file (atomic), so the Job is
    deleted right away; the attempt stays open and resume relaunches the same attempt from its checkpoint."""
    r = _locked(db, id, user)
    transition(r, State.paused, by=user.id)
    _delete_job(db, r, _jobs(request))
    db.commit()
    return JSONResponse(out(db, r), 202)


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ack_cloud: bool = False


@router.post("/reviews/{id}/resume", status_code=202)
def resume_review(id: str, request: Request, body: ResumeBody | None = None, user: User = Depends(current_user),
                  db=Depends(get_db)):
    """paused -> queued: same attempt, continues from the checkpoint (does not consume an attempt).
    stuck -> retrying: the reconciler's retry done now (attempt n+1, or failed once the budget is exhausted);
    the row lock + state change mean the reconciler cannot launch a second Job."""
    r = _locked(db, id, user)
    src = State(r.state)
    if src not in (State.paused, State.stuck):
        raise conflict(f"resume is only allowed from paused or stuck (state is {src.value})", state=src.value)
    if r.provider_id:
        p = db.get(AIProvider, r.provider_id)
        if p is None or not p.enabled:
            raise conflict("the review's AI provider is disabled", state=src.value, provider_id=r.provider_id)
        if getattr(p.kind, "value", p.kind) == ProviderKind.cloud.value and not (body and body.ack_cloud):
            raise ApiError(422, "CLOUD_ACK_REQUIRED",
                           "provider is a cloud provider: code will leave the cluster; resend with ack_cloud=true",
                           {"provider_id": p.id})
    check_capacity(db, settings_store.load(db))
    jobs = _jobs(request)
    if src == State.stuck:
        relaunch(db, r, jobs)
    else:
        transition(r, State.queued)
        start_attempt(db, r, jobs, mark_running=False)
    db.commit()
    return JSONResponse(out(db, r), 202)


@router.post("/reviews/{id}/cancel", status_code=202)
def cancel_review(id: str, request: Request, user: User = Depends(current_user), db=Depends(get_db)):
    """queued|running|paused|stuck|retrying -> cancelled (409 otherwise). The Job is deleted (the worker's
    SIGTERM handler removes /work); partial results in the reports volume are kept."""
    r = _locked(db, id, user)
    if State(r.state) not in ACTIVE:
        raise conflict(f"cannot cancel a {State(r.state).value} review", state=State(r.state).value)
    jobs = _jobs(request)
    _delete_job(db, r, jobs)
    end_attempt(db, r, Outcome.cancelled)
    transition(r, State.cancelled, by=user.id)
    jobs.cleanup(r.id, r.budget_no)
    db.commit()
    return JSONResponse(out(db, r), 202)


class ForceFail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failure_code: FailureCode = FailureCode.INTERNAL
    failure_detail: str | None = Field(default=None, max_length=2000)


@router.post("/admin/reviews/{id}/fail", status_code=202)
def force_fail_review(id: str, request: Request, body: ForceFail | None = None,
                      user: User = Depends(require_admin), db=Depends(get_db)):
    """Admin: any active review -> failed (default failure_code INTERNAL). Deletes the Job; keeps partial results."""
    body = body or ForceFail()
    r = _locked(db, id, user)
    if State(r.state) not in ACTIVE:
        raise conflict(f"cannot fail a {State(r.state).value} review", state=State(r.state).value)
    jobs = _jobs(request)
    _delete_job(db, r, jobs)
    detail = body.failure_detail or f"failed by admin {user.login}"
    end_attempt(db, r, Outcome.error, detail)
    transition(r, State.failed, code=body.failure_code, reason=detail)
    jobs.cleanup(r.id, r.budget_no)
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
        _jobs(request).cleanup(r.id, r.budget_no)
    elif body.outcome is not None:  # error: the watchdog relaunches attempt n+1 on its next sweep
        end_attempt(db, r, Outcome.error, body.error)
    db.commit()
    return {"state": State(r.state).value, "attempt": r.attempt, "budget_no": r.budget_no}
