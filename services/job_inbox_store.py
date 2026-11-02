"""SQLite-backed Job Inbox lifecycle store for the Muse connector."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping


class JobInboxConflict(RuntimeError):
    """Raised when an operation conflicts with the current job state."""


class JobInboxNotFound(KeyError):
    """Raised when a job does not exist."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobInboxStore:
    """Atomic SQLite implementation of the Job Inbox state machine."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS muse_jobs (
                    job_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 3,
                    source TEXT NOT NULL DEFAULT 'video-web-ui',
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL,
                    note TEXT,
                    worker TEXT,
                    lease_until REAL,
                    progress_note TEXT,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_muse_jobs_pending "
                "ON muse_jobs(status, priority ASC, created_at ASC)"
            )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("payload_json", "result_json", "error_json"):
            raw = item.pop(key, None)
            item[key.removesuffix("_json")] = json.loads(raw) if raw else None
        return item

    def create(self, job: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        job_id = str(job.get("job_id") or "").strip()
        job_type = str(job.get("type") or "").strip()
        payload = job.get("payload")
        if not job_id or not job_type or not isinstance(payload, Mapping):
            raise ValueError("job_id, type and object payload are required")
        now = _now()
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing:
                stored = self._decode(existing)
                if stored["type"] != job_type or stored["payload"] != dict(payload):
                    raise JobInboxConflict("job_id already exists with different content")
                return stored, False
            conn.execute(
                """INSERT INTO muse_jobs
                (job_id, type, priority, source, max_attempts, status, payload_json,
                 note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    job_id, job_type, int(job.get("priority", 3)),
                    str(job.get("source") or "video-web-ui"),
                    max(1, int(job.get("max_attempts", 3))),
                    json.dumps(dict(payload), ensure_ascii=False),
                    job.get("note"), now, now,
                ),
            )
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._decode(row), True

    def pending(self, limit: int) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as conn:
            expired = conn.execute(
                "SELECT job_id, attempts, max_attempts FROM muse_jobs "
                "WHERE status='running' AND lease_until IS NOT NULL AND lease_until < ?",
                (now,),
            ).fetchall()
            for row in expired:
                next_status = "pending" if int(row["attempts"]) < int(row["max_attempts"]) else "failed"
                error = None if next_status == "pending" else json.dumps(
                    {"code": "lease_expired", "message": "Job lease expired too many times"},
                    ensure_ascii=False,
                )
                conn.execute(
                    "UPDATE muse_jobs SET status=?, worker=NULL, lease_until=NULL, "
                    "error_json=COALESCE(?, error_json), updated_at=? WHERE job_id=?",
                    (next_status, error, _now(), row["job_id"]),
                )
            rows = conn.execute(
                "SELECT * FROM muse_jobs WHERE status='pending' ORDER BY priority ASC, created_at ASC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
            return [self._decode(row) for row in rows]

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise JobInboxNotFound(job_id)
        return self._decode(row)

    def claim(self, job_id: str, worker: str, lease_sec: int) -> dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise JobInboxNotFound(job_id)
            if row["status"] != "pending":
                raise JobInboxConflict("job is not pending")
            attempts = int(row["attempts"]) + 1
            if attempts > int(row["max_attempts"]):
                raise JobInboxConflict("job exceeded max_attempts")
            conn.execute(
                "UPDATE muse_jobs SET status='running', worker=?, lease_until=?, attempts=?, updated_at=? WHERE job_id=?",
                (worker, now + max(1, int(lease_sec)), attempts, _now(), job_id),
            )
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._decode(row)

    def heartbeat(self, job_id: str, worker: str, lease_sec: int, progress_note: str = "") -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise JobInboxNotFound(job_id)
            if row["status"] != "running" or row["worker"] != worker:
                raise JobInboxConflict("worker does not own running job")
            conn.execute(
                "UPDATE muse_jobs SET lease_until=?, progress_note=?, updated_at=? WHERE job_id=?",
                (time.time() + max(1, int(lease_sec)), progress_note, _now(), job_id),
            )
        return self.get(job_id)

    def complete(self, job_id: str, result: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        return self._finish(job_id, "done", result=result)

    def fail(
        self,
        job_id: str,
        error: Mapping[str, Any],
        retryable: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        return self._finish(job_id, "failed", error=error, retryable=retryable)

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise JobInboxNotFound(job_id)
            if row["status"] == "done" and status == "done":
                return self._decode(row), True
            if row["status"] in {"failed", "cancelled"}:
                return self._decode(row), True
            if row["status"] != "running":
                raise JobInboxConflict("job must be running")
            next_status = "pending" if status == "failed" and retryable and int(row["attempts"]) < int(row["max_attempts"]) else status
            conn.execute(
                "UPDATE muse_jobs SET status=?, worker=NULL, lease_until=NULL, result_json=?, error_json=?, updated_at=? WHERE job_id=?",
                (next_status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                 json.dumps(error, ensure_ascii=False) if error is not None else None, _now(), job_id),
            )
        return self.get(job_id), False

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM muse_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise JobInboxNotFound(job_id)
            if row["status"] in {"done", "failed", "cancelled"}:
                return self._decode(row)
            conn.execute("UPDATE muse_jobs SET status='cancelled', lease_until=NULL, updated_at=? WHERE job_id=?", (_now(), job_id))
        return self.get(job_id)
