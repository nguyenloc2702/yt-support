"""SQLite Job & Shot Queue Manager for Automated Video Factory.

Replaces static JSON task_state.
Handles:
1. Multi-job lifecycle: QUEUED -> ANALYZING -> WAITING_APPROVAL -> APPROVED -> PROCESSING -> RENDERING -> COMPLETED | FAILED.
2. Granular shots queue per job_id.
3. Concurrency Semaphore Lock: limits to 1 heavy process (Whisper/Render) across the container.
4. Thread-safe with SQLite WAL mode and busy timeout.
"""

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("job_queue")

DEFAULT_DB_PATH = Path("data/factory_jobs.sqlite3")
LOCK_NAME_HEAVY = "HEAVY_PROCESS_LOCK"
HEAVY_LOCK_TIMEOUT_SEC = 1800.0  # 30 minutes safety auto-expire


class JobQueueManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS factory_jobs (
                id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                target_duration REAL DEFAULT 180.0,
                project_dir TEXT,
                script_summary TEXT,
                script_file TEXT,
                final_video TEXT,
                drive_url TEXT,
                error_message TEXT,
                created_at REAL,
                updated_at REAL
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS factory_shots (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                index_num INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                duration REAL DEFAULT 10.0,
                is_key_scene INTEGER DEFAULT 1,
                status TEXT NOT NULL,
                account_id TEXT,
                video_path TEXT,
                drive_url TEXT,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at REAL,
                completed_at REAL,
                FOREIGN KEY(job_id) REFERENCES factory_jobs(id)
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS factory_locks (
                lock_name TEXT PRIMARY KEY,
                job_id TEXT,
                acquired_at REAL
            );
            """)
            conn.commit()

    # --- Concurrency Semaphore ---

    def acquire_heavy_lock(self, job_id: str) -> bool:
        """
        Attempts to acquire the single heavy-process lock (Whisper STT or FFmpeg render).
        Protects the VPS RAM from running two memory-intensive operations simultaneously.
        Uses SQLite BEGIN IMMEDIATE transaction for atomic compare-and-set across processes.
        """
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM factory_locks WHERE lock_name = ?", (LOCK_NAME_HEAVY,)).fetchone()
            if row:
                acquired_at = row["acquired_at"]
                if row["job_id"] == job_id:
                    # Refresh lease timestamp for the holding job
                    conn.execute("UPDATE factory_locks SET acquired_at = ? WHERE lock_name = ? AND job_id = ?", (now, LOCK_NAME_HEAVY, job_id))
                    conn.commit()
                    return True

                if now - acquired_at < HEAVY_LOCK_TIMEOUT_SEC:
                    # Currently held by another active job
                    conn.rollback()
                    return False
                else:
                    logger.warning("Heavy lock timed out (held by %s for >30m). Forcing release.", row["job_id"])

            conn.execute("INSERT OR REPLACE INTO factory_locks (lock_name, job_id, acquired_at) VALUES (?, ?, ?)",
                         (LOCK_NAME_HEAVY, job_id, now))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            # Another process is holding an IMMEDIATE transaction
            logger.debug("Failed to acquire heavy lock (database locked: %s)", e)
            return False
        finally:
            conn.close()

    def heartbeat_heavy_lock(self, job_id: str) -> bool:
        """Refreshes the lock timestamp while a long operation is still executing."""
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            cur.execute(
                "UPDATE factory_locks SET acquired_at = ? WHERE lock_name = ? AND job_id = ?",
                (now, LOCK_NAME_HEAVY, job_id)
            )
            updated = cur.rowcount > 0
            conn.commit()
            return updated
        except Exception as e:
            logger.debug("Heartbeat heavy lock error: %s", e)
            return False
        finally:
            conn.close()

    def release_heavy_lock(self, job_id: str):
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM factory_locks WHERE lock_name = ? AND job_id = ?", (LOCK_NAME_HEAVY, job_id))
            conn.commit()
        finally:
            conn.close()

    # --- Jobs Management ---

    def create_job(self, mode: str, url: str, target_duration: float = 180.0, project_dir: Optional[str] = None) -> str:
        job_id = f"{mode}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        with self._get_conn() as conn:
            conn.execute("""
            INSERT INTO factory_jobs (id, mode, url, status, target_duration, project_dir, created_at, updated_at)
            VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?)
            """, (job_id, mode, url, target_duration, project_dir, now, now))
            conn.commit()
        logger.info("Created job %s (mode: %s, url: %s)", job_id, mode, url)
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update_job_status(
        self,
        job_id: str,
        status: str,
        script_summary: Optional[str] = None,
        script_file: Optional[str] = None,
        final_video: Optional[str] = None,
        drive_url: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        now = time.time()
        with self._get_conn() as conn:
            fields = ["status = ?", "updated_at = ?"]
            values = [status, now]

            if script_summary is not None:
                fields.append("script_summary = ?")
                values.append(script_summary)
            if script_file is not None:
                fields.append("script_file = ?")
                values.append(script_file)
            if final_video is not None:
                fields.append("final_video = ?")
                values.append(final_video)
            if drive_url is not None:
                fields.append("drive_url = ?")
                values.append(drive_url)
            if error_message is not None:
                fields.append("error_message = ?")
                values.append(error_message)

            values.append(job_id)
            query = f"UPDATE factory_jobs SET {', '.join(fields)} WHERE id = ?"
            conn.execute(query, values)
            conn.commit()

    def get_next_runnable_job(self) -> Optional[Dict[str, Any]]:
        """Returns the next job ready for processing (QUEUED, APPROVED, or PROCESSING)."""
        with self._get_conn() as conn:
            row = conn.execute("""
            SELECT * FROM factory_jobs 
            WHERE status IN ('QUEUED', 'APPROVED', 'PROCESSING')
            ORDER BY created_at ASC LIMIT 1
            """).fetchone()
            return dict(row) if row else None

    def approve_job(self, job_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] != "WAITING_APPROVAL":
                return False
            conn.execute("UPDATE factory_jobs SET status = 'APPROVED', updated_at = ? WHERE id = ?", (time.time(), job_id))
            conn.commit()
            return True

    def reject_job(self, job_id: str, reason: str = "Rejected by user") -> bool:
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] != "WAITING_APPROVAL":
                return False
            conn.execute("UPDATE factory_jobs SET status = 'CANCELLED', error_message = ?, updated_at = ? WHERE id = ?", (reason, time.time(), job_id))
            conn.commit()
            return True

    def cancel_job(self, job_id: str, reason: str = "Cancelled by user") -> bool:
        """Cancels an active or pending job and marks uncompleted shots CANCELLED."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] in ("COMPLETED", "CANCELLED"):
                return False
            now = time.time()
            conn.execute("UPDATE factory_jobs SET status = 'CANCELLED', error_message = ?, updated_at = ? WHERE id = ?", (reason, now, job_id))
            conn.execute("UPDATE factory_shots SET status = 'CANCELLED' WHERE job_id = ? AND status IN ('PENDING', 'IN_PROGRESS')", (job_id,))
            conn.commit()
        self.release_heavy_lock(job_id)
        logger.info("Cancelled job %s: %s", job_id, reason)
        return True

    def retry_job(self, job_id: str) -> bool:
        """Retries a FAILED or CANCELLED job, resetting uncompleted shots to PENDING."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] not in ("FAILED", "CANCELLED"):
                return False
            now = time.time()
            conn.execute("UPDATE factory_jobs SET status = 'APPROVED', error_message = NULL, updated_at = ? WHERE id = ?", (now, job_id))
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'PENDING', attempts = 0, last_error = NULL
            WHERE job_id = ? AND status IN ('FAILED', 'CANCELLED')
            """, (job_id,))
            conn.commit()
        logger.info("Retried job %s (status reset to APPROVED)", job_id)
        return True

    # --- Shots Management ---

    def add_shots_for_job(self, job_id: str, shots: List[Dict[str, Any]]):
        now = time.time()
        with self._get_conn() as conn:
            for s in shots:
                shot_id = s.get("shot_id") or f"{job_id}_SHOT_{s.get('index', 0):03d}"
                conn.execute("""
                INSERT OR REPLACE INTO factory_shots 
                (id, job_id, index_num, prompt, duration, is_key_scene, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """, (
                    shot_id,
                    job_id,
                    s.get("index", 0),
                    s.get("prompt", ""),
                    s.get("duration", 10.0),
                    1 if s.get("is_key_scene", True) else 0,
                    now,
                ))
            conn.commit()

    def get_next_pending_shot(self, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            if job_id:
                row = conn.execute("""
                SELECT * FROM factory_shots 
                WHERE job_id = ? AND status = 'PENDING'
                ORDER BY index_num ASC LIMIT 1
                """, (job_id,)).fetchone()
            else:
                row = conn.execute("""
                SELECT s.* FROM factory_shots s
                JOIN factory_jobs j ON s.job_id = j.id
                WHERE s.status = 'PENDING' AND j.status IN ('APPROVED', 'PROCESSING')
                ORDER BY s.created_at ASC, s.index_num ASC LIMIT 1
                """).fetchone()
            return dict(row) if row else None

    def mark_shot_in_progress(self, shot_id: str, account_id: str):
        with self._get_conn() as conn:
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'IN_PROGRESS', account_id = ?, attempts = attempts + 1
            WHERE id = ?
            """, (account_id, shot_id))
            conn.commit()

    def mark_shot_done(self, shot_id: str, account_id: str, video_path: str, drive_url: Optional[str]):
        now = time.time()
        with self._get_conn() as conn:
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'DONE', account_id = ?, video_path = ?, drive_url = ?, completed_at = ?
            WHERE id = ?
            """, (account_id, video_path, drive_url, now, shot_id))
            conn.commit()

    def mark_shot_failed(self, shot_id: str, error_message: str, max_retries: int = 3) -> bool:
        """Resets to PENDING if attempts < max_retries, otherwise marks FAILED."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT attempts FROM factory_shots WHERE id = ?", (shot_id,)).fetchone()
            if not row:
                return False
            attempts = row["attempts"]
            new_status = "PENDING" if attempts < max_retries else "FAILED"
            conn.execute("""
            UPDATE factory_shots 
            SET status = ?, last_error = ?
            WHERE id = ?
            """, (new_status, error_message, shot_id))
            conn.commit()
            return new_status == "FAILED"

    def reset_interrupted_shots(self):
        """Refunds attempts for shots interrupted mid-flight when worker restarts."""
        with self._get_conn() as conn:
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'PENDING', attempts = MAX(0, attempts - 1)
            WHERE status = 'IN_PROGRESS'
            """)
            conn.commit()

    def get_job_summary(self, job_id: str) -> Dict[str, Any]:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ?", (job_id,)).fetchone()[0]
            done = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'DONE'", (job_id,)).fetchone()[0]
            in_progress = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'IN_PROGRESS'", (job_id,)).fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'FAILED'", (job_id,)).fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'PENDING'", (job_id,)).fetchone()[0]

            percent = round((done / total * 100), 1) if total > 0 else 0.0
            return {
                "total": total,
                "done": done,
                "in_progress": in_progress,
                "failed": failed,
                "pending": pending,
                "percent": percent,
            }
