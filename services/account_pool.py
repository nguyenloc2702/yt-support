"""Account Pool Manager for Rotating 10 Pro Google Accounts.

Manages sessions stored in sessions/acc_XX.json (and sessions/test_account.json).
Key features:
1. Persistent state to `sessions/pool_state.json` with file-locking protection across restarts.
2. 70% daily quota budgeting per account to prevent hitting hard bans or depletion.
3. Automated daily reset of counters and recovery of EXHAUSTED accounts.
4. HARD KILL-SWITCH: stops all Mode 2 generation immediately upon AUTH/VERIFY errors to protect remaining accounts.
5. Round-robin with jitter and fair rotation.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Dict, List, Optional
from filelock import FileLock

logger = logging.getLogger("account_pool")

DEFAULT_SESSION_DIR = Path("sessions")
DEFAULT_POOL_STATE_FILE = Path("sessions/pool_state.json")
MAX_SHOTS_PER_ACCOUNT_DAY = 20  # Safe ~70% daily budget per Pro account


@dataclass
class AccountInfo:
    acc_id: str
    session_file: str
    status: str  # "AVAILABLE", "COOLDOWN", "EXHAUSTED", "AUTH_ERROR", "DISABLED"
    cooldown_until: Optional[str] = None  # ISO format string
    shots_today: int = 0
    total_shots: int = 0
    consecutive_errors: int = 0
    last_reset_date: str = ""  # YYYY-MM-DD
    in_flight: int = 0


class AccountPoolManager:
    def __init__(
        self,
        session_dir: Optional[Path] = None,
        state_file: Optional[Path] = None,
        max_daily_shots: int = MAX_SHOTS_PER_ACCOUNT_DAY,
    ):
        self.session_dir = Path(session_dir or DEFAULT_SESSION_DIR)
        self.state_file = Path(state_file or DEFAULT_POOL_STATE_FILE)
        self.lock_file = self.state_file.with_suffix(".lock")
        self.max_daily_shots = max_daily_shots
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._load_or_init_state()

    @property
    def kill_switch_active(self) -> bool:
        try:
            with FileLock(str(self.lock_file), timeout=5):
                if self.state_file.exists():
                    state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    return bool(state.get("kill_switch_active", False))
        except Exception:
            pass
        return self._kill_switch_active

    @kill_switch_active.setter
    def kill_switch_active(self, val: bool):
        self._kill_switch_active = bool(val)

    @property
    def kill_switch_reason(self) -> str:
        try:
            with FileLock(str(self.lock_file), timeout=5):
                if self.state_file.exists():
                    state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    return str(state.get("kill_switch_reason", ""))
        except Exception:
            pass
        return self._kill_switch_reason

    @kill_switch_reason.setter
    def kill_switch_reason(self, val: str):
        self._kill_switch_reason = str(val)

    def _today_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_or_init_state(self):
        """Loads state from file or initializes from filesystem sessions."""
        with FileLock(str(self.lock_file), timeout=10):
            state = {}
            if self.state_file.exists():
                try:
                    state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    self._kill_switch_active = state.get("kill_switch_active", False)
                    self._kill_switch_reason = state.get("kill_switch_reason", "")
                except Exception as e:
                    logger.error("Failed to load pool state file: %s", e)
                    corrupt_backup = self.state_file.with_suffix(f".corrupt.{int(time.time())}")
                    try:
                        self.state_file.rename(corrupt_backup)
                        logger.warning("Corrupted pool state backed up to %s", corrupt_backup)
                    except Exception:
                        pass

            # Discover all json files in sessions/
            discovered = sorted(list(self.session_dir.glob("acc_*.json")))
            test_acc = self.session_dir / "test_account.json"
            if test_acc.exists() and test_acc not in discovered:
                discovered.append(test_acc)

            accounts_data = state.get("accounts", {})
            today = self._today_str()

            for f in discovered:
                acc_id = f.stem
                if acc_id not in accounts_data:
                    accounts_data[acc_id] = {
                        "acc_id": acc_id,
                        "session_file": str(f),
                        "status": "AVAILABLE",
                        "cooldown_until": None,
                        "shots_today": 0,
                        "total_shots": 0,
                        "consecutive_errors": 0,
                        "last_reset_date": today,
                        "in_flight": 0,
                    }
                else:
                    # Update session_file path
                    accounts_data[acc_id]["session_file"] = str(f)
                    # Reset in_flight counter on worker startup to prevent leakage from crashes
                    accounts_data[acc_id]["in_flight"] = 0
                    # Reset daily counter if day changed
                    if accounts_data[acc_id].get("last_reset_date") != today:
                        accounts_data[acc_id]["shots_today"] = 0
                        accounts_data[acc_id]["last_reset_date"] = today
                        if accounts_data[acc_id]["status"] == "EXHAUSTED":
                            accounts_data[acc_id]["status"] = "AVAILABLE"

            state["accounts"] = accounts_data
            state["kill_switch_active"] = self._kill_switch_active
            state["kill_switch_reason"] = self._kill_switch_reason
            self._save_state_unlocked(state)

    def _save_state_unlocked(self, state: dict):
        """Atomic write: write to temp file then replace to prevent state corruption."""
        tmp_file = self.state_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(self.state_file)

    def reload_accounts(self) -> int:
        """Refreshes accounts from disk."""
        self._load_or_init_state()
        with FileLock(str(self.lock_file), timeout=10):
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return len(data.get("accounts", {}))

    def get_available_account(self, reserve: bool = False) -> Optional[AccountInfo]:
        """
        Picks the best available account for the next shot:
        - Fails immediately if kill-switch is active.
        - Restores expired cooldowns.
        - Restores EXHAUSTED if day has rolled over.
        - Checks 70% daily quota threshold.
        - Returns AccountInfo with least (shots_today + in_flight).
        - If reserve=True, atomically increments in_flight inside the lock.
        """
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if state.get("kill_switch_active", False):
                logger.error("⛔ Kill-switch is ACTIVE. Mode 2 paused. Reason: %s", state.get("kill_switch_reason"))
                return None

            now = datetime.now(timezone.utc)
            today = self._today_str()
            accounts = state.get("accounts", {})

            candidates = []
            for acc in accounts.values():
                # Check day rollover
                if acc.get("last_reset_date") != today:
                    acc["shots_today"] = 0
                    acc["in_flight"] = 0
                    acc["last_reset_date"] = today
                    if acc["status"] == "EXHAUSTED":
                        acc["status"] = "AVAILABLE"

                # Check cooldown expiry
                if acc["status"] == "COOLDOWN" and acc.get("cooldown_until"):
                    try:
                        cd_time = datetime.fromisoformat(acc["cooldown_until"])
                        if cd_time.tzinfo is None:
                            cd_time = cd_time.replace(tzinfo=timezone.utc)
                        if now >= cd_time:
                            acc["status"] = "AVAILABLE"
                            acc["cooldown_until"] = None
                    except Exception:
                        acc["status"] = "AVAILABLE"

                # Check daily quota cap (including active in_flight reservations)
                total_budget_used = acc.get("shots_today", 0) + acc.get("in_flight", 0)
                if acc["status"] == "AVAILABLE" and total_budget_used >= self.max_daily_shots:
                    acc["status"] = "EXHAUSTED"
                    logger.info("Account %s reached daily safe budget (%d/%d shots).", acc["acc_id"], total_budget_used, self.max_daily_shots)

                if acc["status"] == "AVAILABLE":
                    # Verify session file still exists
                    if Path(acc["session_file"]).exists():
                        candidates.append(acc)

            if not candidates:
                self._save_state_unlocked(state)
                return None

            # Pick candidate with least (shots_today + in_flight), then least errors
            candidates.sort(key=lambda x: (x.get("shots_today", 0) + x.get("in_flight", 0), x.get("consecutive_errors", 0)))
            chosen = candidates[0]
            if reserve:
                chosen["in_flight"] = chosen.get("in_flight", 0) + 1

            self._save_state_unlocked(state)

            return AccountInfo(
                acc_id=chosen["acc_id"],
                session_file=chosen["session_file"],
                status=chosen["status"],
                cooldown_until=chosen.get("cooldown_until"),
                shots_today=chosen.get("shots_today", 0),
                total_shots=chosen.get("total_shots", 0),
                consecutive_errors=chosen.get("consecutive_errors", 0),
                last_reset_date=chosen.get("last_reset_date", today),
                in_flight=chosen.get("in_flight", 0),
            )

    def report_success(self, acc_id: str):
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state["accounts"]:
                acc = state["accounts"][acc_id]
                acc["in_flight"] = max(0, acc.get("in_flight", 0) - 1)
                acc["shots_today"] = acc.get("shots_today", 0) + 1
                acc["total_shots"] = acc.get("total_shots", 0) + 1
                acc["consecutive_errors"] = 0
                if acc["shots_today"] >= self.max_daily_shots:
                    acc["status"] = "EXHAUSTED"
                self._save_state_unlocked(state)

    def report_rate_limit(self, acc_id: str, cooldown_seconds: int = 3600):
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state["accounts"]:
                acc = state["accounts"][acc_id]
                acc["in_flight"] = max(0, acc.get("in_flight", 0) - 1)
                acc["status"] = "COOLDOWN"
                cd_time = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
                acc["cooldown_until"] = cd_time.isoformat()
                acc["consecutive_errors"] = acc.get("consecutive_errors", 0) + 1
                logger.warning("Account %s in COOLDOWN for %ds.", acc_id, cooldown_seconds)
                self._save_state_unlocked(state)

    def report_quota_exhausted(self, acc_id: str):
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state["accounts"]:
                acc = state["accounts"][acc_id]
                acc["in_flight"] = max(0, acc.get("in_flight", 0) - 1)
                acc["status"] = "EXHAUSTED"
                logger.warning("Account %s marked as EXHAUSTED.", acc_id)
                self._save_state_unlocked(state)

    def release_reservation(self, acc_id: str):
        """Releases an in-flight reservation slot if shot processing terminated without recording success/cooldown."""
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state.get("accounts", {}):
                acc = state["accounts"][acc_id]
                acc["in_flight"] = max(0, acc.get("in_flight", 0) - 1)
                self._save_state_unlocked(state)

    def trigger_kill_switch(self, acc_id: str, reason: str):
        """
        CRITICAL SAFETY: Triggers the global kill-switch.
        Immediately stops Mode 2 to protect all accounts from getting banned.
        """
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.kill_switch_active = True
            self.kill_switch_reason = f"Account {acc_id} error: {reason}"
            state["kill_switch_active"] = True
            state["kill_switch_reason"] = self.kill_switch_reason
            if acc_id in state["accounts"]:
                state["accounts"][acc_id]["in_flight"] = 0
                state["accounts"][acc_id]["status"] = "AUTH_ERROR"
            self._save_state_unlocked(state)
            logger.critical("🚨 KILL-SWITCH ACTIVATED! %s", self.kill_switch_reason)

    def reset_kill_switch(self):
        """Resets the kill-switch after admin updates credentials."""
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.kill_switch_active = False
            self.kill_switch_reason = ""
            state["kill_switch_active"] = False
            state["kill_switch_reason"] = ""
            self._save_state_unlocked(state)
            logger.info("✔ Kill-switch has been deactivated.")

    def get_status_summary(self) -> dict:
        """Returns overview of all accounts for /status report."""
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            accounts = state.get("accounts", {})
            return {
                "total_accounts": len(accounts),
                "available": sum(1 for a in accounts.values() if a.get("status") == "AVAILABLE"),
                "cooldown": sum(1 for a in accounts.values() if a.get("status") == "COOLDOWN"),
                "exhausted": sum(1 for a in accounts.values() if a.get("status") == "EXHAUSTED"),
                "auth_error": sum(1 for a in accounts.values() if a.get("status") == "AUTH_ERROR"),
                "kill_switch_active": state.get("kill_switch_active", False),
                "kill_switch_reason": state.get("kill_switch_reason", ""),
            }
