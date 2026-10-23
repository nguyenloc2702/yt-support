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
import random
import threading
import time
from typing import Any, Dict, List, Optional
from filelock import FileLock

logger = logging.getLogger("account_pool")

DEFAULT_SESSION_DIR = Path("sessions")
DEFAULT_POOL_STATE_FILE = Path("sessions/pool_state.json")
MAX_SHOTS_PER_ACCOUNT_DAY = 20  # Safe ~70% daily budget per Pro account

# A dead cookie cannot be pinged back to life, so repeated AUTH rejections must
# park the account and tell the owner instead of silently retrying for days.
KEEP_ALIVE_FAIL_QUARANTINE = 3
SESSION_STALE_WARN_HOURS = 12.0


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
    last_success_at: Optional[str] = None  # ISO UTC of the last keep-alive ping that passed
    ping_fail_streak: int = 0  # consecutive AUTH failures from keep-alive


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
        self._keep_alive_thread: Optional[threading.Thread] = None
        self._keep_alive_running: bool = False
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
                        "last_success_at": None,
                        "ping_fail_streak": 0,
                    }
                else:
                    # Update session_file path
                    accounts_data[acc_id]["session_file"] = str(f)
                    accounts_data[acc_id].setdefault("last_success_at", None)
                    accounts_data[acc_id].setdefault("ping_fail_streak", 0)
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

                # Check cooldown / quota reset expiry
                if acc["status"] in ("COOLDOWN", "EXHAUSTED") and acc.get("cooldown_until"):
                    try:
                        cd_time = datetime.fromisoformat(acc["cooldown_until"])
                        if cd_time.tzinfo is None:
                            cd_time = cd_time.replace(tzinfo=timezone.utc)
                        if now >= cd_time:
                            acc["status"] = "AVAILABLE"
                            acc["cooldown_until"] = None
                            logger.info("Account %s cooldown/quota expired. Restored to AVAILABLE.", acc.get("acc_id"))
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

    def report_rate_limit(self, acc_id: str, cooldown_seconds: int = 120):
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

    def report_quota_exhausted(self, acc_id: str, reset_at: Optional[str] = None):
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state["accounts"]:
                acc = state["accounts"][acc_id]
                acc["in_flight"] = max(0, acc.get("in_flight", 0) - 1)
                acc["status"] = "EXHAUSTED"
                if reset_at:
                    acc["cooldown_until"] = reset_at
                else:
                    cd = datetime.now(timezone.utc) + timedelta(hours=5)
                    acc["cooldown_until"] = cd.isoformat()
                logger.warning("Account %s marked as EXHAUSTED until %s.", acc_id, acc.get("cooldown_until"))
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

    def get_all_accounts_state(self) -> dict:
        """Returns snapshot of all registered accounts."""
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            return state.get("accounts", {})

    def update_account_status(self, acc_id: str, status: str, cooldown_until: Optional[str] = None):
        """Updates account status in pool state (e.g. after realtime check)."""
        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if acc_id in state.get("accounts", {}):
                state["accounts"][acc_id]["status"] = status
                state["accounts"][acc_id]["cooldown_until"] = cooldown_until
                self._save_state_unlocked(state)

    # -------------------------------------------------------------------------
    # AUTOMATED KEEP-ALIVE DAEMON (Token Rotation & Session Preservation)
    # -------------------------------------------------------------------------

    def start_keep_alive(
        self,
        interval_seconds: int = 1800,
        provider: Optional[Any] = None,
        notifier: Optional[Any] = None,
    ):
        """
        Starts a background daemon thread that periodically pings Google Gemini
        for each valid account, triggering token rotation (__Secure-1PSIDTS)
        and atomically saving the fresh tokens back to disk.
        """
        if self._keep_alive_thread and self._keep_alive_thread.is_alive():
            return
        self._keep_alive_running = True
        self._keep_alive_thread = threading.Thread(
            target=self._keep_alive_loop,
            args=(interval_seconds, provider, notifier),
            name="account_keep_alive",
            daemon=True,
        )
        self._keep_alive_thread.start()
        logger.info("Auto Keep-Alive daemon started (cycle: ~%ds with natural jitter).", interval_seconds)

    def stop_keep_alive(self):
        """Stops the background keep-alive thread."""
        self._keep_alive_running = False

    def _keep_alive_loop(
        self,
        interval_seconds: int,
        provider: Optional[Any] = None,
        notifier: Optional[Any] = None,
    ):
        while self._keep_alive_running:
            # Add jitter ±300s (5 minutes) to mimic authentic human browsing rhythm
            jitter = random.uniform(-300, 300)
            sleep_time = max(600, interval_seconds + jitter)

            elapsed = 0
            while elapsed < sleep_time and self._keep_alive_running:
                time.sleep(5)
                elapsed += 5

            if not self._keep_alive_running:
                break

            try:
                self.ping_and_refresh_all_accounts(
                    provider=provider,
                    pacing_delay=random.uniform(5.0, 10.0),
                    notifier=notifier,
                )
            except Exception as e:
                logger.warning("Error during keep-alive refresh: %s", e)

    def session_age_hours(self, acc_id: str) -> Optional[float]:
        """Hours since an account's cookies were last written (None if unknown)."""
        info = self.get_all_accounts_state().get(acc_id) or {}
        path = Path(info.get("session_file") or (self.session_dir / f"{acc_id}.json"))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        updated_at = data.get("updated_at")
        if not updated_at:
            return None
        return (time.time() - float(updated_at)) / 3600.0

    def apply_session_cookies(
        self,
        acc_id: str,
        secure_1psid: str,
        secure_1psidts: Optional[str] = None,
        provider: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Store freshly pasted cookies and validate them once.

        This is the two-minute revival path for Google's rolling token, and the
        only writer besides keep-alive, so its rotation cannot race a second
        process. Reports the account tier, which is what tells the owner whether
        Veo is even available to them.
        """
        psid = (secure_1psid or "").strip()
        if not psid:
            return {"ok": False, "message": "Thiếu __Secure-1PSID."}

        path = self.session_dir / f"{acc_id}.json"
        payload = {
            "__Secure-1PSID": psid,
            "__Secure-1PSIDTS": (secure_1psidts or "").strip(),
            "updated_at": time.time(),
        }
        tmp_file = path.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(payload), encoding="utf-8")
        tmp_file.replace(path)

        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}
            accounts = state.get("accounts", {})
            record = accounts.setdefault(acc_id, {"acc_id": acc_id})
            record.update(
                {
                    "session_file": str(path),
                    "status": "AVAILABLE",
                    "cooldown_until": None,
                    "consecutive_errors": 0,
                    "ping_fail_streak": 0,
                }
            )
            state["accounts"] = accounts
            state["kill_switch_active"] = self._kill_switch_active
            state["kill_switch_reason"] = self._kill_switch_reason
            self._save_state_unlocked(state)

        out: Dict[str, Any] = {"ok": True, "acc_id": acc_id, "session_file": str(path)}
        try:
            from services.gemini_webapi_provider import GeminiWebApiProvider

            val_provider = provider or GeminiWebApiProvider()
            res = val_provider.validate_session(path)
            out.update(
                {
                    "valid": bool(res.valid),
                    "tier": res.tier,
                    "credits_remaining": res.credits_remaining,
                    "message": res.message or "Cookie hợp lệ.",
                }
            )
            if res.valid:
                with FileLock(str(self.lock_file), timeout=10):
                    st = json.loads(self.state_file.read_text(encoding="utf-8"))
                    st.setdefault("accounts", {}).setdefault(acc_id, {})[
                        "last_success_at"
                    ] = datetime.now(timezone.utc).isoformat()
                    self._save_state_unlocked(st)
        except Exception as exc:
            out.update({"valid": False, "tier": None, "message": f"Không kiểm tra được cookie: {exc}"})
        return out

    def ping_and_refresh_all_accounts(
        self,
        provider: Optional[Any] = None,
        pacing_delay: float = 5.0,
        notifier: Optional[Any] = None,
    ) -> Dict[str, bool]:
        """
        Pings Google Gemini for all registered accounts to auto-renew __Secure-1PSIDTS.
        Saves updated cookies to disk and updates account status in pool_state.json.
        """
        if self.kill_switch_active:
            logger.warning(
                "⏸ [Auto Keep-Alive] Kill-switch đang bật (%s) — bỏ qua vòng ping này "
                "để tránh đổ thêm lỗi xác thực lên toàn bộ tài khoản.",
                self.kill_switch_reason or "không rõ lý do",
            )
            return {}

        from services.gemini_webapi_provider import GeminiWebApiProvider
        val_provider = provider or GeminiWebApiProvider()
        results = {}

        with FileLock(str(self.lock_file), timeout=10):
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            accounts = dict(state.get("accounts", {}))

        for acc_id, info in accounts.items():
            if self._keep_alive_thread and self._keep_alive_thread.is_alive() and not self._keep_alive_running:
                break
            if info.get("status") == "DISABLED":
                continue
            if info.get("in_flight", 0) > 0:
                logger.debug("Account %s is currently in-flight (%d). Skipping keep-alive ping.", acc_id, info.get("in_flight"))
                continue

            session_file = Path(info.get("session_file") or (self.session_dir / f"{acc_id}.json"))
            if not session_file.exists():
                continue

            try:
                logger.info("💓 [Auto Keep-Alive] Refreshing session token for account %s...", acc_id)
                res = val_provider.validate_session(session_file)
                if res.valid:
                    was_quarantined = info.get("status") == "AUTH_ERROR"
                    self._record_ping_success(acc_id)
                    logger.info("✔ [Auto Keep-Alive] Account %s successfully refreshed! (Tier: %s, Credits: %s)",
                                acc_id, res.tier, res.credits_remaining)
                    if was_quarantined:
                        self._notify(
                            notifier,
                            f"✅ <b>hồi phục!</b>\n"
                            f"Tài khoản <code>{acc_id}</code> đã xác thực lại được và trở về trạng thái AVAILABLE.\n"
                            f"• Tier: <b>{res.tier or 'Không rõ'}</b>\n"
                            f"• Credits 5h còn lại: {res.credits_remaining if res.credits_remaining is not None else 'Không rõ'}",
                        )
                    results[acc_id] = True
                else:
                    logger.warning("⚠️ [Auto Keep-Alive] Account %s validation failed: %s (%s)",
                                   acc_id, res.error_kind, res.message)
                    self._record_ping_failure(acc_id, res.message or str(res.error_kind), notifier)
                    results[acc_id] = False
            except Exception as e:
                logger.error("Error refreshing account %s: %s", acc_id, e)
                self._record_ping_failure(acc_id, str(e), notifier)
                results[acc_id] = False

            if pacing_delay > 0:
                time.sleep(pacing_delay)

        return results

    def _record_ping_success(self, acc_id: str) -> None:
        with FileLock(str(self.lock_file), timeout=10):
            st = json.loads(self.state_file.read_text(encoding="utf-8"))
            record = st.setdefault("accounts", {}).get(acc_id)
            if record is None:
                return
            revived = record.get("status") == "AUTH_ERROR"
            record["last_success_at"] = datetime.now(timezone.utc).isoformat()
            record["ping_fail_streak"] = 0
            if revived:
                record["status"] = "AVAILABLE"
                record["cooldown_until"] = None
                record["consecutive_errors"] = 0
            self._save_state_unlocked(st)

    def _record_ping_failure(self, acc_id: str, reason: str, notifier: Optional[Any] = None) -> None:
        """Count AUTH failures and park the account once ping alone clearly cannot save it.

        Google rotates __Secure-1PSIDTS on every usable request; a rejected cookie is
        already dead, so the only useful action left is to tell the owner while the
        cookie age is still short enough to re-paste.
        """
        with FileLock(str(self.lock_file), timeout=10):
            st = json.loads(self.state_file.read_text(encoding="utf-8"))
            record = st.setdefault("accounts", {}).get(acc_id)
            if record is None:
                return
            streak = int(record.get("ping_fail_streak", 0)) + 1
            record["ping_fail_streak"] = streak
            was_quarantined = record.get("status") == "AUTH_ERROR"
            should_alert = False
            if streak >= KEEP_ALIVE_FAIL_QUARANTINE and not was_quarantined:
                record["status"] = "AUTH_ERROR"
                record["cooldown_until"] = None
                should_alert = True
            self._save_state_unlocked(st)

        if not should_alert:
            return

        age = self.session_age_hours(acc_id)
        age_text = f"{age:.1f} giờ" if age is not None else "không rõ (thiếu updated_at)"
        self._notify(
            notifier,
            f"🚨 <b>Cookie hết hạn — tài khoản <code>{acc_id}</code></b>\n"
            f"Ping giữ session sống thất bại {streak} lần liên tiếp: {reason[:200]}\n"
            f"• Cookie cũ: <b>{age_text}</b>\n"
            f"• Trạng thái: AUTH_ERROR (tạm cách ly, không sinh video bằng tài khoản này)\n\n"
            f"ℹ️ Cookie Google quay vòng token, ping KHÔNG hồi sinh được cookie đã chết. "
            f"Lấy cookie mới từ trình duyệt rồi chạy:\n"
            f"<code>/setcookie {acc_id} &lt;__Secure-1PSID&gt; &lt;__Secure-1PSIDTS&gt;</code>",
        )

    @staticmethod
    def _notify(notifier: Optional[Any], message: str) -> None:
        if notifier is None or not getattr(notifier, "is_configured", False):
            return
        try:
            notifier.send_message(message)
        except Exception as exc:
            logger.warning("Không gửi được thông báo keep-alive: %s", exc)

