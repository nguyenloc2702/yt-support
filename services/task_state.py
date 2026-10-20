"""Task State Machine for Managing Video Generation Queue.

Persists progress to a JSON state file with file-locking protection.
Handles status transitions: PENDING -> IN_PROGRESS -> DONE / FAILED.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from filelock import FileLock

logger = logging.getLogger("task_state")

DEFAULT_STATE_FILE = Path("data/history_of_microsoft/tasks_state.json")
DEFAULT_SCRIPT_FILE = Path("data/history_of_microsoft/script_granular_10s_full.json")


class TaskStateManager:
    def __init__(self, state_file: Optional[Path] = None, script_file: Optional[Path] = None):
        self.state_file = Path(state_file or DEFAULT_STATE_FILE)
        self.script_file = Path(script_file or DEFAULT_SCRIPT_FILE)
        self.lock_file = self.state_file.with_suffix(".lock")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Initialize state file from granular script if not existing."""
        with FileLock(str(self.lock_file), timeout=10):
            if not self.state_file.exists():
                if self.script_file.exists():
                    logger.info("Initializing task state from %s", self.script_file)
                    raw_shots = json.loads(self.script_file.read_text(encoding="utf-8"))
                    tasks = []
                    for s in raw_shots:
                        tasks.append({
                            "shot_id": s.get("shot_id", f"SHOT_{s['index']:03d}"),
                            "index": s["index"],
                            "prompt": s.get("gemini_argon_prompt", ""),
                            "timecode": s.get("timecode", ""),
                            "duration": s.get("duration", 10.0),
                            "status": "PENDING",  # PENDING, IN_PROGRESS, DONE, FAILED
                            "attempts": 0,
                            "last_error": None,
                            "assigned_acc": None,
                            "video_file": None,
                            "gdrive_url": None,
                            "started_at": None,
                            "completed_at": None,
                        })
                    self._save_tasks_unlocked(tasks)
                else:
                    logger.warning("Script file %s not found. Empty state initialized.", self.script_file)
                    self._save_tasks_unlocked([])

    def _load_tasks_unlocked(self) -> List[Dict[str, Any]]:
        if not self.state_file.exists():
            return []
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to read task state: %s", e)
            return []

    def _save_tasks_unlocked(self, tasks: List[Dict[str, Any]]):
        temp_file = self.state_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(self.state_file)

    def reset_in_progress(self):
        """Reset interrupted tasks and release their assignment for retry."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            modified = False
            for t in tasks:
                if t["status"] == "IN_PROGRESS":
                    t["status"] = "PENDING"
                    t["assigned_acc"] = None
                    t["started_at"] = None
                    # The attempt was interrupted by a process restart, not a
                    # provider failure; make it eligible for the same retry budget.
                    if t.get("attempts", 0) > 0:
                        t["attempts"] -= 1
                    modified = True
            if modified:
                self._save_tasks_unlocked(tasks)
                logger.info("Reset all stuck IN_PROGRESS tasks to PENDING.")

    def get_next_pending(self) -> Optional[Dict[str, Any]]:
        """Retrieve the next pending shot to process."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            for t in tasks:
                if t["status"] == "PENDING":
                    return t
            return None

    def mark_in_progress(self, shot_id: str, acc_id: str) -> bool:
        """Mark a shot as currently being generated."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            for t in tasks:
                if t["shot_id"] == shot_id:
                    t["status"] = "IN_PROGRESS"
                    t["assigned_acc"] = acc_id
                    t["attempts"] += 1
                    t["started_at"] = datetime.now().isoformat()
                    self._save_tasks_unlocked(tasks)
                    return True
            return False

    def mark_done(self, shot_id: str, acc_id: str, video_file: str, gdrive_url: Optional[str] = None) -> bool:
        """Mark a shot as successfully generated and uploaded."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            for t in tasks:
                if t["shot_id"] == shot_id:
                    t["status"] = "DONE"
                    t["assigned_acc"] = acc_id
                    t["video_file"] = video_file
                    t["gdrive_url"] = gdrive_url
                    t["completed_at"] = datetime.now().isoformat()
                    self._save_tasks_unlocked(tasks)
                    return True
            return False

    def mark_failed(self, shot_id: str, error_msg: str, max_retries: int = 3) -> bool:
        """Mark a shot as failed or return to PENDING if attempts < max_retries."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            for t in tasks:
                if t["shot_id"] == shot_id:
                    t["last_error"] = error_msg
                    if t["attempts"] < max_retries:
                        t["status"] = "PENDING"
                        t["assigned_acc"] = None
                        logger.warning("Shot %s failed (attempt %d/%d). Reset to PENDING.", shot_id, t["attempts"], max_retries)
                    else:
                        t["status"] = "FAILED"
                        logger.error("Shot %s failed max attempts (%d). Marked as FAILED.", shot_id, max_retries)
                    self._save_tasks_unlocked(tasks)
                    return True
            return False

    def get_summary(self) -> Dict[str, Any]:
        """Return statistics on current progress."""
        with FileLock(str(self.lock_file), timeout=10):
            tasks = self._load_tasks_unlocked()
            total = len(tasks)
            done = sum(1 for t in tasks if t["status"] == "DONE")
            in_progress = sum(1 for t in tasks if t["status"] == "IN_PROGRESS")
            pending = sum(1 for t in tasks if t["status"] == "PENDING")
            failed = sum(1 for t in tasks if t["status"] == "FAILED")
            percent = (done / total * 100) if total > 0 else 0.0

            return {
                "total": total,
                "done": done,
                "in_progress": in_progress,
                "pending": pending,
                "failed": failed,
                "percent": round(percent, 2),
            }


if __name__ == "__main__":
    sm = TaskStateManager()
    summary = sm.get_summary()
    print(f"Task Summary: {summary}")
