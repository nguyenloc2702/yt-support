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
