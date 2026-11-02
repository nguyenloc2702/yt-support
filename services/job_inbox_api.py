"""FastAPI Job Inbox API consumed by Muse."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from services.job_inbox_store import JobInboxConflict, JobInboxNotFound, JobInboxStore


DB_PATH = os.environ.get("JOB_INBOX_DB_PATH", "data/muse_job_inbox.sqlite3")
API_TOKEN = os.environ.get("CLAUDE_JOBS_TOKEN", "").strip()
store = JobInboxStore(Path(DB_PATH))
app = FastAPI(title="Muse Job Inbox", version="1.0")


class JobCreate(BaseModel):
    job_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    priority: int = Field(default=3, ge=1, le=5)
    max_attempts: int = Field(default=3, ge=1)
    source: str = "video-web-ui"
    payload: dict[str, Any]
    note: str | None = None


class ClaimRequest(BaseModel):
    worker: str = Field(min_length=1)
    lease_sec: int = Field(default=900, ge=1, le=86400)


class HeartbeatRequest(ClaimRequest):
    progress_note: str = ""


class ResultRequest(BaseModel):
    worker: str | None = None
    finished_at: str | None = None
    summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)


class FailRequest(BaseModel):
    worker: str | None = None
    error: dict[str, Any]
    retryable: bool = False


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {API_TOKEN}" if API_TOKEN else ""
    if not expected or authorization != expected:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid bearer token"})


def _job_or_404(job_id: str) -> dict[str, Any]:
    try:
        return store.get(job_id)
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "conflict", "message": str(exc)})


@app.post("/jobs", dependencies=[Depends(require_token)])
def create_job(job: JobCreate) -> dict[str, Any]:
    try:
        record, created = store.create(job.model_dump())
    except JobInboxConflict as exc:
        raise _conflict(exc) from exc
    return {"job": record, "created": created}


@app.get("/jobs/pending", dependencies=[Depends(require_token)])
def get_pending(limit: int = Query(default=10, ge=1, le=100)) -> dict[str, Any]:
    jobs = store.pending(limit)
    return {"ok": True, "count": len(jobs), "jobs": jobs}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict[str, Any]:
    return {"job": _job_or_404(job_id)}


@app.post("/jobs/{job_id}/claim", dependencies=[Depends(require_token)])
def claim_job(job_id: str, request: ClaimRequest) -> dict[str, Any]:
    try:
        return {"job": store.claim(job_id, request.worker, request.lease_sec)}
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
    except JobInboxConflict as exc:
        raise _conflict(exc) from exc


@app.post("/jobs/{job_id}/heartbeat", dependencies=[Depends(require_token)])
def heartbeat_job(job_id: str, request: HeartbeatRequest) -> dict[str, Any]:
    try:
        return {"job": store.heartbeat(job_id, request.worker, request.lease_sec, request.progress_note)}
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
    except JobInboxConflict as exc:
        raise _conflict(exc) from exc


@app.post("/jobs/{job_id}/result", dependencies=[Depends(require_token)])
def result_job(job_id: str, request: ResultRequest) -> dict[str, Any]:
    try:
        job, duplicate = store.complete(job_id, request.model_dump(exclude_none=True))
        return {"ok": True, "job_id": job_id, "status": job["status"], "duplicate": duplicate}
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
    except JobInboxConflict as exc:
        raise _conflict(exc) from exc


@app.post("/jobs/{job_id}/fail", dependencies=[Depends(require_token)])
def fail_job(job_id: str, request: FailRequest) -> dict[str, Any]:
    try:
        job, duplicate = store.fail(job_id, request.error, request.retryable)
        return {"ok": True, "job_id": job_id, "status": job["status"], "duplicate": duplicate}
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
    except JobInboxConflict as exc:
        raise _conflict(exc) from exc


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return {"job": store.cancel(job_id)}
    except JobInboxNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Job not found"}) from exc
