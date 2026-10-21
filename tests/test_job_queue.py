"""Unit tests for SQLite JobQueueManager (Step 4a & 4b)."""

from pathlib import Path
import pytest

from services.job_queue import JobQueueManager


@pytest.fixture
def temp_jq(tmp_path: Path):
    db_file = tmp_path / "test_jobs.sqlite3"
    return JobQueueManager(db_path=db_file)


def test_job_lifecycle(temp_jq: JobQueueManager):
    job_id = temp_jq.create_job(mode="reup", url="https://youtube.com/watch?v=123", target_duration=120.0)
    assert job_id.startswith("reup_")

    job = temp_jq.get_job(job_id)
    assert job is not None
    assert job["status"] == "QUEUED"
    assert job["mode"] == "reup"

    # Transition to WAITING_APPROVAL
    temp_jq.update_job_status(job_id, status="WAITING_APPROVAL", script_summary="A cool video")
    job = temp_jq.get_job(job_id)
    assert job["status"] == "WAITING_APPROVAL"
    assert job["script_summary"] == "A cool video"

    # Approve
    ok = temp_jq.approve_job(job_id)
    assert ok is True
    job = temp_jq.get_job(job_id)
    assert job["status"] == "APPROVED"


def test_concurrency_heavy_lock(temp_jq: JobQueueManager):
    job1 = "job_1"
    job2 = "job_2"

    # Job 1 acquires lock
    assert temp_jq.acquire_heavy_lock(job1) is True

    # Same job re-acquiring is allowed (idempotent)
    assert temp_jq.acquire_heavy_lock(job1) is True

    # Job 2 cannot acquire while Job 1 is running
    assert temp_jq.acquire_heavy_lock(job2) is False

    # Job 1 releases
    temp_jq.release_heavy_lock(job1)

    # Now Job 2 can acquire
    assert temp_jq.acquire_heavy_lock(job2) is True


def test_shots_queue_and_retry(temp_jq: JobQueueManager):
    job_id = temp_jq.create_job(mode="remake", url="https://youtube.com/watch?v=abc")
    shots = [
        {"index": 1, "prompt": "Shot 1", "duration": 10.0, "is_key_scene": True},
        {"index": 2, "prompt": "Shot 2", "duration": 10.0, "is_key_scene": False},
    ]
    temp_jq.add_shots_for_job(job_id, shots)

    summary = temp_jq.get_job_summary(job_id)
    assert summary["total"] == 2
    assert summary["pending"] == 2

    shot = temp_jq.get_next_pending_shot(job_id)
    assert shot is not None
    assert shot["index_num"] == 1

    # Mark in progress
    temp_jq.mark_shot_in_progress(shot["id"], "acc_01")
    shot_data = temp_jq.get_next_pending_shot(job_id)
    assert shot_data["index_num"] == 2  # next pending

    # Mark shot 1 done
    temp_jq.mark_shot_done(shot["id"], "acc_01", "/tmp/shot_001.mp4", "https://drive.google.com/xyz")
    summary = temp_jq.get_job_summary(job_id)
    assert summary["done"] == 1

    # Test reset interrupted shots on restart
    temp_jq.mark_shot_in_progress(shot_data["id"], "acc_02")
    temp_jq.reset_interrupted_shots()
    shot_after_restart = temp_jq.get_next_pending_shot(job_id)
    assert shot_after_restart["id"] == shot_data["id"]
    assert shot_after_restart["attempts"] == 0  # refunded


def test_cancel_and_retry_job(temp_jq: JobQueueManager):
    job_id = temp_jq.create_job(mode="reup", url="https://youtube.com/watch?v=cancel")
    shots = [
        {"index": 1, "prompt": "Shot 1"},
        {"index": 2, "prompt": "Shot 2"},
    ]
    temp_jq.add_shots_for_job(job_id, shots)

    # Cancel job
    assert temp_jq.cancel_job(job_id, reason="User cancelled") is True
    job = temp_jq.get_job(job_id)
    assert job["status"] == "CANCELLED"
    assert job["error_message"] == "User cancelled"

    # Pending shot should now not be runnable
    assert temp_jq.get_next_pending_shot(job_id) is None

    # Retry job
    assert temp_jq.retry_job(job_id) is True
    job = temp_jq.get_job(job_id)
    assert job["status"] == "APPROVED"
    assert job["error_message"] is None

    # Shots should be back to PENDING
    shot = temp_jq.get_next_pending_shot(job_id)
    assert shot is not None
    assert shot["index_num"] == 1


def test_heavy_lock_heartbeat(temp_jq: JobQueueManager):
    job_id = "test_long_render_job"
    assert temp_jq.acquire_heavy_lock(job_id) is True
    # Heartbeat refreshes lock
    assert temp_jq.heartbeat_heavy_lock(job_id) is True
    # Different job cannot heartbeat foreign lock
    assert temp_jq.heartbeat_heavy_lock("another_job") is False
    temp_jq.release_heavy_lock(job_id)


def test_mark_shot_retry_later(temp_jq: JobQueueManager):
    job_id = temp_jq.create_job(mode="remake", url="https://youtube.com/test_retry_later")
    temp_jq.transition_job(job_id, "APPROVED")
    temp_jq.add_shots_for_job(job_id, [{"index": 1, "prompt": "Shot 1", "duration": 10.0}])

    shot = temp_jq.get_next_pending_shot(job_id)
    assert shot is not None
    shot_id = shot["id"]

    # Mark in progress (increments attempts to 1)
    temp_jq.mark_shot_in_progress(shot_id, "acc_01")
    s_in_prog = temp_jq.get_shot(shot_id)
    assert s_in_prog["attempts"] == 1
    assert s_in_prog["status"] == "IN_PROGRESS"

    # Retry later (due to QUOTA/RATE_LIMIT) -> should reset attempts back to 0 and status to PENDING
    assert temp_jq.mark_shot_retry_later(shot_id, "Quota exhausted") is True
    s_retried = temp_jq.get_shot(shot_id)
    assert s_retried["status"] == "PENDING"
    assert s_retried["attempts"] == 0
    assert s_retried["last_error"] == "Quota exhausted"


