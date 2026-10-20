"""Unit tests for TaskStateManager (Task State Machine)."""

import json
from pathlib import Path
import pytest

from services.task_state import TaskStateManager


@pytest.fixture
def temp_task_env(tmp_path: Path):
    script_file = tmp_path / "script.json"
    state_file = tmp_path / "tasks_state.json"
    sample_shots = [
        {"index": 1, "shot_id": "SHOT_001", "gemini_argon_prompt": "Cinematic shot 1"},
        {"index": 2, "shot_id": "SHOT_002", "gemini_argon_prompt": "Cinematic shot 2"},
        {"index": 3, "shot_id": "SHOT_003", "gemini_argon_prompt": "Cinematic shot 3"},
    ]
    script_file.write_text(json.dumps(sample_shots), encoding="utf-8")
    return {"script_file": script_file, "state_file": state_file}


def test_task_state_init_and_get_pending(temp_task_env):
    mgr = TaskStateManager(
        state_file=temp_task_env["state_file"],
        script_file=temp_task_env["script_file"],
    )
    summary = mgr.get_summary()
    assert summary["total"] == 3
    assert summary["pending"] == 3
    assert summary["done"] == 0

    task = mgr.get_next_pending()
    assert task is not None
    assert task["shot_id"] == "SHOT_001"
    assert task["status"] == "PENDING"


def test_task_state_transitions(temp_task_env):
    mgr = TaskStateManager(
        state_file=temp_task_env["state_file"],
        script_file=temp_task_env["script_file"],
    )

    # In progress
    mgr.mark_in_progress("SHOT_001", acc_id="acc_01")
    summary = mgr.get_summary()
    assert summary["in_progress"] == 1
    assert summary["pending"] == 2

    # Done
    mgr.mark_done("SHOT_001", acc_id="acc_01", video_file="shot_001.mp4", gdrive_url="https://drive.google.com/test")
    summary = mgr.get_summary()
    assert summary["done"] == 1
    assert summary["in_progress"] == 0

    # Next pending should now be SHOT_002
    next_task = mgr.get_next_pending()
    assert next_task["shot_id"] == "SHOT_002"

    # Mark failed attempt 1: status should reset to PENDING
    mgr.mark_in_progress("SHOT_002", acc_id="acc_01")
    mgr.mark_failed("SHOT_002", error_msg="RPC timeout 1")
    summary = mgr.get_summary()
    assert summary["failed"] == 0
    assert summary["pending"] == 2

    # Attempt 2
    mgr.mark_in_progress("SHOT_002", acc_id="acc_01")
    mgr.mark_failed("SHOT_002", error_msg="RPC timeout 2")

    # Attempt 3: now should become FAILED
    mgr.mark_in_progress("SHOT_002", acc_id="acc_01")
    mgr.mark_failed("SHOT_002", error_msg="RPC timeout 3")
    summary = mgr.get_summary()
    assert summary["failed"] == 1
    assert summary["pending"] == 1


def test_reset_in_progress_refunds_attempt(temp_task_env):
    """Verifies that unexpected container restart refunds the burned attempt count."""
    mgr = TaskStateManager(
        state_file=temp_task_env["state_file"],
        script_file=temp_task_env["script_file"],
    )

    mgr.mark_in_progress("SHOT_001", acc_id="acc_01")
    task_before = next(t for t in mgr._load_tasks_unlocked() if t["shot_id"] == "SHOT_001")
    assert task_before["status"] == "IN_PROGRESS"
    assert task_before["attempts"] == 1

    # Simulate container restart: reset_in_progress
    mgr.reset_in_progress()

    task_after = next(t for t in mgr._load_tasks_unlocked() if t["shot_id"] == "SHOT_001")
    assert task_after["status"] == "PENDING"
    assert task_after["attempts"] == 0  # Attempt refunded!
