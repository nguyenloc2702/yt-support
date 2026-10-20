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
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger("job_queue")

DEFAULT_DB_PATH = Path("data/factory_jobs.sqlite3")
LOCK_NAME_HEAVY = "HEAVY_PROCESS_LOCK"
HEAVY_LOCK_TIMEOUT_SEC = 600.0  # 10 minutes safety auto-expire (managed with active heartbeats)

JOB_TRANSITIONS = {
    "QUEUED": {"ANALYZING", "WAITING_APPROVAL", "APPROVED", "CANCELLED", "FAILED"},
    "ANALYZING": {"WAITING_APPROVAL", "APPROVED", "FAILED", "CANCELLED"},
    "WAITING_APPROVAL": {"APPROVED", "CANCELLED", "FAILED"},
    "APPROVED": {"PROCESSING", "ASSEMBLING", "RENDERING", "CANCELLED", "FAILED"},
    "PROCESSING": {"ASSEMBLING", "RENDERING", "COMPLETED", "FAILED", "CANCELLED"},
    "RENDERING": {"COMPLETED", "FAILED", "CANCELLED"},
    "ASSEMBLING": {"FINAL_UPLOAD_PENDING", "ASSEMBLY_FAILED", "COMPLETED", "CANCELLED", "FAILED"},
    "ASSEMBLY_FAILED": {"ASSEMBLING", "APPROVED", "CANCELLED"},
    "FINAL_UPLOAD_PENDING": {"COMPLETED", "FINAL_UPLOAD_PENDING", "CANCELLED", "ASSEMBLING"},
    "FAILED": {"APPROVED", "CANCELLED"},
    "CANCELLED": {"APPROVED"},
    "COMPLETED": set(),
}


def extract_youtube_id(url: str) -> str:
    """Extracts YouTube 11-char video ID or returns stripped URL if not found."""
    import re
    url_str = (url or "").strip()
    m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)", url_str)
    return m.group(1) if m else url_str


class HeavyLockLease:
    """
    Context manager that acquires the single heavy-process lock and runs
    a background daemon thread that heartbeats the lease every `interval` seconds.
    Ensures that long-running operations (Whisper, FFmpeg encode, TTS mix, Assembly)
    never lose their lease to timeout while actively executing.
    """
    def __init__(self, job_queue: "JobQueueManager", job_id: str, interval: float = 30.0, wait_timeout: Optional[float] = None):
        self.jq = job_queue
        self.job_id = job_id
        self.interval = interval
        self.wait_timeout = wait_timeout
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lost_ownership = False

    @property
    def is_valid(self) -> bool:
        return not self._lost_ownership

    @property
    def should_abort(self) -> bool:
        """Indicates if current operation must abort due to lost lock ownership or job cancellation."""
        if self._lost_ownership:
            return True
        try:
            job = self.jq.get_job(self.job_id)
            if job and job.get("status") == "CANCELLED":
                return True
        except Exception:
            pass
        return False

    def __enter__(self):
        start_wait = time.time()
        while True:
            acquired = self.jq.acquire_heavy_lock(self.job_id)
            if acquired:
                break
            if self.wait_timeout is not None and (time.time() - start_wait) > self.wait_timeout:
                raise TimeoutError(f"Timed out waiting to acquire heavy lock for job {self.job_id}")
            time.sleep(5.0)

        self._stop_event.clear()
        self._lost_ownership = False
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"heartbeat-{self.job_id}"
        )
        self._thread.start()
        return self

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self.interval):
            success = self.jq.heartbeat_heavy_lock(self.job_id)
            if not success:
                logger.error("🚨 Heavy lock heartbeat failed for job %s! Lock may have been preempted.", self.job_id)
                self._lost_ownership = True
                break

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.jq.release_heavy_lock(self.job_id)
        if self._lost_ownership and exc_type is None:
            raise RuntimeError(f"Heavy lock ownership was lost during execution of job {self.job_id}")


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
            conn.execute("""
            CREATE TABLE IF NOT EXISTS factory_kv (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL
            );
            """)
            conn.commit()

            # Dynamic migration for optional / added columns
            self._ensure_column(conn, "factory_jobs", "next_retry_at", "REAL")
            self._ensure_column(conn, "factory_jobs", "upload_attempts", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "factory_shots", "upload_attempts", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "factory_shots", "next_retry_at", "REAL")
            self._ensure_column(conn, "factory_shots", "worker_id", "TEXT")
            self._ensure_column(conn, "factory_shots", "started_at", "REAL")
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, col_type: str):
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [r["name"] for r in cur.fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type};")

    # --- Key-Value Store ---

    def set_kv(self, key: str, value: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO factory_kv (key, value, updated_at) VALUES (?, ?, ?)",
                (key, str(value), time.time()),
            )
            conn.commit()

    def get_kv(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT value FROM factory_kv WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

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
                    logger.warning("Heavy lock timed out (held by %s for >%ds). Forcing release.", row["job_id"], int(HEAVY_LOCK_TIMEOUT_SEC))

            conn.execute("INSERT OR REPLACE INTO factory_locks (lock_name, job_id, acquired_at) VALUES (?, ?, ?)",
                         (LOCK_NAME_HEAVY, job_id, now))
            conn.commit()
            return True
        except sqlite3.OperationalError as e:
            logger.debug("Failed to acquire heavy lock (database locked: %s)", e)
            return False
        finally:
            conn.close()

    def heartbeat_heavy_lock(self, job_id: str) -> bool:
        """Refreshes the lock timestamp while a long operation is still executing. Retries on temporary DB lock."""
        for attempt in range(3):
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
            except sqlite3.OperationalError:
                time.sleep(0.2)
            except Exception as e:
                logger.debug("Heartbeat heavy lock error: %s", e)
                return False
            finally:
                conn.close()
        return False

    def release_heavy_lock(self, job_id: str):
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM factory_locks WHERE lock_name = ? AND job_id = ?", (LOCK_NAME_HEAVY, job_id))
            conn.commit()
        finally:
            conn.close()

    def heavy_lock_lease(self, job_id: str, interval: float = 30.0, wait_timeout: Optional[float] = None) -> HeavyLockLease:
        """Returns a context manager holding the heavy process lock with active heartbeat."""
        return HeavyLockLease(self, job_id=job_id, interval=interval, wait_timeout=wait_timeout)

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

    def create_job_if_no_active(self, mode: str, url: str, target_duration: float = 180.0, project_dir: Optional[str] = None) -> Optional[str]:
        """
        Atomically creates a job only if no active (non-completed, non-failed, non-cancelled) job
        already exists for the given YouTube video URL.
        """
        video_id = extract_youtube_id(url)
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, url FROM factory_jobs WHERE status NOT IN ('COMPLETED', 'CANCELLED', 'FAILED')"
            ).fetchall()
            for r in rows:
                if extract_youtube_id(r["url"]) == video_id:
                    logger.info("Active job %s already exists for YouTube video %s. Rejecting duplicate.", r["id"], video_id)
                    conn.rollback()
                    return None

            job_id = f"{mode}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            now = time.time()
            conn.execute("""
            INSERT INTO factory_jobs (id, mode, url, status, target_duration, project_dir, created_at, updated_at)
            VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?)
            """, (job_id, mode, url, target_duration, project_dir, now, now))
            conn.commit()
            logger.info("Created unique job %s (mode: %s, url: %s)", job_id, mode, url)
            return job_id
        except Exception as e:
            logger.error("Error in create_job_if_no_active: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            conn.close()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def transition_job(
        self,
        job_id: str,
        to_state: str,
        from_states: Optional[Any] = None,
        **fields
    ) -> bool:
        """
        Guarded compare-and-set state transition.
        Ensures race-free transitions according to JOB_TRANSITIONS without overwriting cancelled/completed jobs.
        """
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                conn.rollback()
                return False
            cur_status = row["status"]

            # If already in the target state, treat as idempotent success (unless FINAL_UPLOAD_PENDING updating backoff)
            if cur_status == to_state and to_state != "FINAL_UPLOAD_PENDING":
                conn.rollback()
                return True

            allowed_targets = JOB_TRANSITIONS.get(cur_status, set())
            if to_state not in allowed_targets:
                logger.warning(
                    "Rejected state transition for job %s: %s -> %s (allowed: %s)",
                    job_id, cur_status, to_state, allowed_targets
                )
                conn.rollback()
                return False

            if from_states:
                valid_from = (from_states,) if isinstance(from_states, str) else tuple(from_states)
                if cur_status not in valid_from:
                    logger.warning(
                        "Job %s current status %s not in specified from_states %s",
                        job_id, cur_status, valid_from
                    )
                    conn.rollback()
                    return False

            update_clauses = ["status = ?", "updated_at = ?"]
            values = [to_state, now]
            for k, v in fields.items():
                if v is not None:
                    update_clauses.append(f"{k} = ?")
                    values.append(v)

            values.extend([job_id, cur_status])
            cur = conn.cursor()
            cur.execute(
                f"UPDATE factory_jobs SET {', '.join(update_clauses)} WHERE id = ? AND status = ?",
                values
            )
            success = cur.rowcount > 0
            conn.commit()
            if success:
                logger.info("Job %s state updated: %s -> %s", job_id, cur_status, to_state)
            return success
        except Exception as e:
            logger.error("Error transitioning job %s to %s: %s", job_id, to_state, e)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            conn.close()

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
        """Backwards compatible wrapper around transition_job."""
        self.transition_job(
            job_id=job_id,
            to_state=status,
            script_summary=script_summary,
            script_file=script_file,
            final_video=final_video,
            drive_url=drive_url,
            error_message=error_message,
        )

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
        return self.transition_job(job_id, to_state="APPROVED", from_states=("WAITING_APPROVAL",))

    def reject_job(self, job_id: str, reason: str = "Rejected by user") -> bool:
        return self.transition_job(
            job_id,
            to_state="CANCELLED",
            from_states=("WAITING_APPROVAL",),
            error_message=reason,
        )

    def cancel_job(self, job_id: str, reason: str = "Cancelled by user") -> bool:
        """Cancels an active or pending job and marks uncompleted shots CANCELLED."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] in ("COMPLETED", "CANCELLED"):
                return False
            now = time.time()
            conn.execute("UPDATE factory_jobs SET status = 'CANCELLED', error_message = ?, updated_at = ? WHERE id = ?", (reason, now, job_id))
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'CANCELLED' 
            WHERE job_id = ? AND status IN ('PENDING', 'IN_PROGRESS', 'UPLOAD_PENDING')
            """, (job_id,))
            conn.commit()
        # NOTE: Do NOT release heavy lock here! The running lease will release it upon exit.
        logger.info("Cancelled job %s: %s", job_id, reason)
        return True

    def retry_job(self, job_id: str) -> bool:
        """Retries a FAILED, ASSEMBLY_FAILED, FINAL_UPLOAD_PENDING, or CANCELLED job."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM factory_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row or row["status"] not in ("FAILED", "CANCELLED", "FINAL_UPLOAD_PENDING", "ASSEMBLY_FAILED"):
                return False
            now = time.time()
            cur_status = row["status"]

            if cur_status == "FINAL_UPLOAD_PENDING":
                # Reset upload attempts and backoff timer so scanner picks it up immediately
                conn.execute(
                    "UPDATE factory_jobs SET error_message = NULL, upload_attempts = 0, next_retry_at = NULL, updated_at = ? WHERE id = ?",
                    (now, job_id)
                )
                conn.commit()
                logger.info("Retried job %s (final upload pending backoff reset)", job_id)
                return True

            if cur_status == "ASSEMBLY_FAILED":
                # For assembly failure, keep all completed shots intact, transition to APPROVED so scanner re-assembles
                conn.execute(
                    "UPDATE factory_jobs SET status = 'APPROVED', error_message = NULL, updated_at = ? WHERE id = ?",
                    (now, job_id)
                )
                conn.commit()
                logger.info("Retried job %s (assembly failed reset to APPROVED, shots preserved)", job_id)
                return True

            # General retry from FAILED or CANCELLED
            conn.execute("UPDATE factory_jobs SET status = 'APPROVED', error_message = NULL, updated_at = ? WHERE id = ?", (now, job_id))
            conn.execute("""
            UPDATE factory_shots 
            SET status = 'PENDING', attempts = 0, upload_attempts = 0, next_retry_at = NULL, last_error = NULL
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

    def get_next_upload_pending_shot(self, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Returns next shot ready to be uploaded (UPLOAD_PENDING and backoff expired)."""
        now = time.time()
        with self._get_conn() as conn:
            if job_id:
                row = conn.execute("""
                SELECT * FROM factory_shots 
                WHERE job_id = ? AND status = 'UPLOAD_PENDING' AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY index_num ASC LIMIT 1
                """, (job_id, now)).fetchone()
            else:
                row = conn.execute("""
                SELECT s.* FROM factory_shots s
                JOIN factory_jobs j ON s.job_id = j.id
                WHERE s.status = 'UPLOAD_PENDING' AND (s.next_retry_at IS NULL OR s.next_retry_at <= ?)
                  AND j.status IN ('APPROVED', 'PROCESSING')
                ORDER BY s.created_at ASC, s.index_num ASC LIMIT 1
                """, (now,)).fetchone()
            return dict(row) if row else None

    def mark_shot_in_progress(self, shot_id: str, account_id: str, worker_id: Optional[str] = None) -> bool:
        """Guarded transition to IN_PROGRESS from PENDING."""
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE factory_shots 
            SET status = 'IN_PROGRESS', account_id = ?, worker_id = ?, started_at = ?, attempts = attempts + 1
            WHERE id = ? AND status = 'PENDING'
            """, (account_id, worker_id, now, shot_id))
            conn.commit()
            return cur.rowcount > 0

    def mark_shot_generated(self, shot_id: str, video_path: str) -> bool:
        """Marks shot video generated locally and moves to UPLOAD_PENDING."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE factory_shots 
            SET status = 'UPLOAD_PENDING', video_path = ?, upload_attempts = 0, next_retry_at = NULL
            WHERE id = ? AND status IN ('IN_PROGRESS', 'PENDING')
            """, (str(video_path), shot_id))
            conn.commit()
            return cur.rowcount > 0

    def mark_shot_done(self, shot_id: str, account_id: str, video_path: str, drive_url: Optional[str]) -> bool:
        """Guarded transition to DONE from IN_PROGRESS or UPLOAD_PENDING."""
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE factory_shots 
            SET status = 'DONE', account_id = ?, video_path = ?, drive_url = ?, completed_at = ?
            WHERE id = ? AND status IN ('IN_PROGRESS', 'UPLOAD_PENDING')
            """, (account_id, str(video_path), drive_url, now, shot_id))
            conn.commit()
            return cur.rowcount > 0

    def mark_shot_failed(self, shot_id: str, error_message: str, max_retries: int = 3) -> bool:
        """Resets to PENDING if attempts < max_retries, otherwise marks FAILED."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT attempts FROM factory_shots WHERE id = ?", (shot_id,)).fetchone()
            if not row:
                return False
            attempts = row["attempts"]
            new_status = "PENDING" if attempts < max_retries else "FAILED"
            cur = conn.cursor()
            cur.execute("""
            UPDATE factory_shots 
            SET status = ?, last_error = ?
            WHERE id = ? AND status = 'IN_PROGRESS'
            """, (new_status, error_message, shot_id))
            conn.commit()
            return new_status == "FAILED"

    def mark_shot_upload_failed(self, shot_id: str, error_message: str, max_retries: int = 5) -> bool:
        """
        Handles upload-only failures with exponential backoff without depleting AI generation attempts.
        """
        now = time.time()
        with self._get_conn() as conn:
            row = conn.execute("SELECT upload_attempts FROM factory_shots WHERE id = ?", (shot_id,)).fetchone()
            if not row:
                return False
            attempts = (row["upload_attempts"] or 0) + 1
            if attempts < max_retries:
                backoff = min(3600.0, 30.0 * (2 ** (attempts - 1)))
                next_retry = now + backoff
                conn.execute("""
                UPDATE factory_shots 
                SET status = 'UPLOAD_PENDING', upload_attempts = ?, next_retry_at = ?, last_error = ?
                WHERE id = ? AND status = 'UPLOAD_PENDING'
                """, (attempts, next_retry, error_message, shot_id))
                conn.commit()
                return False
            else:
                conn.execute("""
                UPDATE factory_shots 
                SET status = 'FAILED', upload_attempts = ?, last_error = ?
                WHERE id = ? AND status = 'UPLOAD_PENDING'
                """, (attempts, f"Upload failed after {attempts} attempts: {error_message}", shot_id))
                conn.commit()
                return True

    def reset_interrupted_shots(self, worker_id: Optional[str] = None):
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
            upload_pending = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'UPLOAD_PENDING'", (job_id,)).fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'FAILED'", (job_id,)).fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM factory_shots WHERE job_id = ? AND status = 'PENDING'", (job_id,)).fetchone()[0]

            percent = round((done / total * 100), 1) if total > 0 else 0.0
            return {
                "total": total,
                "done": done,
                "in_progress": in_progress,
                "upload_pending": upload_pending,
                "failed": failed,
                "pending": pending,
                "percent": percent,
            }

