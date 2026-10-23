"""Unit tests for AccountPoolManager (Step 3)."""

import json
from pathlib import Path
import pytest

from services.account_pool import AccountPoolManager


@pytest.fixture
def mock_pool_env(tmp_path: Path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    state_file = sessions_dir / "pool_state.json"

    # Create 3 mock account session files
    (sessions_dir / "acc_01.json").write_text(json.dumps({"__Secure-1PSID": "p1"}))
    (sessions_dir / "acc_02.json").write_text(json.dumps({"__Secure-1PSID": "p2"}))
    (sessions_dir / "acc_03.json").write_text(json.dumps({"__Secure-1PSID": "p3"}))

    return {
        "session_dir": sessions_dir,
        "state_file": state_file,
    }


def test_account_pool_init_and_rotation(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    summary = mgr.get_status_summary()
    assert summary["total_accounts"] == 3
    assert summary["available"] == 3
    assert summary["kill_switch_active"] is False

    # Get first available
    acc1 = mgr.get_available_account()
    assert acc1 is not None
    assert acc1.acc_id == "acc_01"

    # Report success on acc_01 -> next pick should be acc_02
    mgr.report_success(acc1.acc_id)
    acc2 = mgr.get_available_account()
    assert acc2.acc_id == "acc_02"


def test_account_pool_quota_budgeting(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=2,  # Cap at 2 shots
    )

    acc = mgr.get_available_account()
    mgr.report_success(acc.acc_id)  # 1/2
    mgr.report_success(acc.acc_id)  # 2/2 -> should be EXHAUSTED

    # Ensure acc_01 is now EXHAUSTED
    state = json.loads(mock_pool_env["state_file"].read_text())
    assert state["accounts"]["acc_01"]["status"] == "EXHAUSTED"


def test_account_pool_kill_switch(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
    )

    # Simulate AUTH/VERIFY error on acc_01
    mgr.trigger_kill_switch("acc_01", reason="Captcha / Verification required")

    assert mgr.kill_switch_active is True
    # When kill switch is active, NO accounts are returned!
    assert mgr.get_available_account() is None

    summary = mgr.get_status_summary()
    assert summary["kill_switch_active"] is True
    assert "Verification required" in summary["kill_switch_reason"]

    # Reset kill-switch (simulating user /resume)
    mgr.reset_kill_switch()
    assert mgr.kill_switch_active is False
    assert mgr.get_available_account() is not None


def test_account_pool_atomic_reservation(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    # Pick acc_01 with reservation
    acc1 = mgr.get_available_account(reserve=True)
    assert acc1.acc_id == "acc_01"
    assert acc1.in_flight == 1

    # Next call with reserve=True should pick acc_02 (since acc_01 now has 1 in_flight)
    acc2 = mgr.get_available_account(reserve=True)
    assert acc2.acc_id == "acc_02"
    assert acc2.in_flight == 1

    # Report success on acc1: in_flight becomes 0, shots_today becomes 1
    mgr.report_success(acc1.acc_id)
    # Release reservation on acc2: in_flight becomes 0, shots_today stays 0
    mgr.release_reservation(acc2.acc_id)

    state = json.loads(mock_pool_env["state_file"].read_text())
    assert state["accounts"]["acc_01"]["in_flight"] == 0
    assert state["accounts"]["acc_01"]["shots_today"] == 1
    assert state["accounts"]["acc_02"]["in_flight"] == 0
    assert state["accounts"]["acc_02"]["shots_today"] == 0


def test_account_pool_quota_exhausted_reset(mock_pool_env):
    from datetime import datetime, timezone, timedelta
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    # Expire in 1 hour
    future_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mgr.report_quota_exhausted("acc_01", reset_at=future_time)

    state = json.loads(mock_pool_env["state_file"].read_text())
    assert state["accounts"]["acc_01"]["status"] == "EXHAUSTED"
    assert state["accounts"]["acc_01"]["cooldown_until"] == future_time

    # When cooldown_until has passed:
    past_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state["accounts"]["acc_01"]["cooldown_until"] = past_time
    mock_pool_env["state_file"].write_text(json.dumps(state))

    # Getting available account should restore acc_01
    acc = mgr.get_available_account()
    assert acc is not None
    assert acc.acc_id == "acc_01"
    assert acc.status == "AVAILABLE"


def test_account_pool_keep_alive_refresh(mock_pool_env):
    from unittest.mock import MagicMock
    from services.video_provider import SessionValidationResult

    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )

    mock_provider = MagicMock()
    mock_provider.validate_session.return_value = SessionValidationResult(
        valid=True,
        tier="PRO",
        credits_remaining=5,
        reset_at="2026-10-22T20:00:00Z",
    )

    # Run keep alive ping across all accounts
    res = mgr.ping_and_refresh_all_accounts(provider=mock_provider, pacing_delay=0.0)
    assert len(res) == 3
    assert all(res.values()) is True
    assert mock_provider.validate_session.call_count == 3

    # Check start and stop lifecycle
    mgr.start_keep_alive(interval_seconds=10, provider=mock_provider)
    assert mgr._keep_alive_running is True
    assert mgr._keep_alive_thread is not None
    mgr.stop_keep_alive()
    assert mgr._keep_alive_running is False


def _auth_fail_provider():
    from unittest.mock import MagicMock

    from services.video_provider import SessionValidationResult

    provider = MagicMock()
    provider.validate_session.return_value = SessionValidationResult(
        valid=False, error_kind="AUTH", message="UNAUTHENTICATED"
    )
    return provider


def _pro_provider():
    from unittest.mock import MagicMock

    from services.video_provider import SessionValidationResult

    provider = MagicMock()
    provider.validate_session.return_value = SessionValidationResult(
        valid=True, tier="PRO", credits_remaining=5, reset_at="2026-10-22T20:00:00Z"
    )
    return provider


def test_ping_quarantines_account_after_repeated_auth_failures(mock_pool_env):
    """A dead cookie cannot be pinged awake, so 3 failures must park it and alert once."""
    from services.account_pool import KEEP_ALIVE_FAIL_QUARANTINE
    from unittest.mock import MagicMock

    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    notifier = MagicMock()
    notifier.is_configured = True
    provider = _auth_fail_provider()

    for _ in range(KEEP_ALIVE_FAIL_QUARANTINE - 1):
        mgr.ping_and_refresh_all_accounts(provider=provider, pacing_delay=0.0, notifier=notifier)

    state = mgr.get_all_accounts_state()
    assert all(state[a]["status"] == "AVAILABLE" for a in state)
    assert notifier.send_message.call_count == 0

    mgr.ping_and_refresh_all_accounts(provider=provider, pacing_delay=0.0, notifier=notifier)

    state = mgr.get_all_accounts_state()
    assert all(s["status"] == "AUTH_ERROR" for s in state.values())
    assert all(s["ping_fail_streak"] == KEEP_ALIVE_FAIL_QUARANTINE for s in state.values())
    assert notifier.send_message.call_count == 3  # one alert per account
    alert = notifier.send_message.call_args_list[0].args[0]
    assert "/setcookie" in alert

    # Already quarantined -> no second spam wave on the next cycle.
    notifier.send_message.reset_mock()
    mgr.ping_and_refresh_all_accounts(provider=provider, pacing_delay=0.0, notifier=notifier)
    assert notifier.send_message.call_count == 0


def test_ping_revive_clears_quarantine_and_reports_tier(mock_pool_env):
    from unittest.mock import MagicMock

    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    notifier = MagicMock()
    notifier.is_configured = True

    for _ in range(3):
        mgr.ping_and_refresh_all_accounts(
            provider=_auth_fail_provider(), pacing_delay=0.0, notifier=notifier
        )
    assert mgr.get_all_accounts_state()["acc_01"]["status"] == "AUTH_ERROR"

    notifier.send_message.reset_mock()
    mgr.ping_and_refresh_all_accounts(
        provider=_pro_provider(), pacing_delay=0.0, notifier=notifier
    )

    record = mgr.get_all_accounts_state()["acc_01"]
    assert record["status"] == "AVAILABLE"
    assert record["ping_fail_streak"] == 0
    assert record["last_success_at"]
    assert "PRO" in notifier.send_message.call_args_list[0].args[0]


def test_kill_switch_skips_keep_alive_ping(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    provider = _pro_provider()
    mgr.ping_and_refresh_all_accounts(provider=provider, pacing_delay=0.0)
    assert provider.validate_session.call_count == 3

    mgr.trigger_kill_switch("acc_01", "auth dead")
    provider.validate_session.reset_mock()
    assert mgr.ping_and_refresh_all_accounts(provider=provider, pacing_delay=0.0) == {}
    assert provider.validate_session.call_count == 0


def test_apply_session_cookies_writes_file_and_reports_tier(mock_pool_env):
    import time as _time

    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    provider = _pro_provider()

    res = mgr.apply_session_cookies(
        "acc_01", "PSID-VALUE-1234567890", "PSIDTS-VALUE-abcdefgh", provider=provider
    )

    assert res["ok"] and res["valid"] and res["tier"] == "PRO"
    saved = json.loads((mock_pool_env["session_dir"] / "acc_01.json").read_text())
    assert saved["__Secure-1PSID"] == "PSID-VALUE-1234567890"
    assert saved["__Secure-1PSIDTS"] == "PSIDTS-VALUE-abcdefgh"
    assert saved["updated_at"] == pytest.approx(_time.time(), abs=30)
    assert mgr.get_all_accounts_state()["acc_01"]["status"] == "AVAILABLE"
    assert mgr.session_age_hours("acc_01") < 1 / 60  # minutes old, not hours


def test_session_age_hours_reads_updated_at(mock_pool_env):
    import time as _time

    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    # Fixture files have no updated_at -> age unknown.
    assert mgr.session_age_hours("acc_02") is None

    path = mock_pool_env["session_dir"] / "acc_02.json"
    path.write_text(json.dumps({"__Secure-1PSID": "p2", "updated_at": _time.time() - 7200}))
    assert 1.9 < mgr.session_age_hours("acc_02") < 2.1


def test_apply_session_cookies_rejects_missing_psid(mock_pool_env):
    mgr = AccountPoolManager(
        session_dir=mock_pool_env["session_dir"],
        state_file=mock_pool_env["state_file"],
        max_daily_shots=5,
    )
    assert mgr.apply_session_cookies("acc_01", "")["ok"] is False

