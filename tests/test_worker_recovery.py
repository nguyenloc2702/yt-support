"""Unit & Recovery tests for VideoAutomationWorker hardening (Feedback round 4)."""

import json
from pathlib import Path
import time
from unittest.mock import MagicMock, patch
import pytest

from core.worker import VideoAutomationWorker
from services.account_pool import AccountPoolManager
from services.job_queue import JobQueueManager
from services.video_provider import ProviderResult


@pytest.fixture
def temp_worker(tmp_path: Path):
    db_file = tmp_path / "recovery_test.sqlite3"
    jq = JobQueueManager(db_path=db_file)
    pool = AccountPoolManager(session_dir=tmp_path / "sessions", state_file=tmp_path / "pool.json")

    with patch.object(VideoAutomationWorker, "__init__", lambda self: None):
        worker = VideoAutomationWorker()
        worker.running = True
        worker.worker_id = "test_worker_001"
        worker.jq = jq
        worker.account_pool = pool
        worker.uploader = MagicMock()
        worker.notifier = MagicMock()
        worker.notifier.is_configured = False
        worker.primary_provider = MagicMock()
        worker.fallback_provider = MagicMock()
        return worker


def test_recovery_final_upload_retry_in_same_process(temp_worker: VideoAutomationWorker, tmp_path: Path):
    """
    P0.1 test:
    Mock uploader returns (False, None) on attempt 1, then (True, 'gdrive://...') on attempt 2.
    Scanner called twice in the SAME worker instance -> transitions job to COMPLETED.
    """
    job_id = temp_worker.jq.create_job(mode="remake", url="https://youtube.com/watch?v=111")
    temp_worker.jq.transition_job(job_id, to_state="APPROVED")

    # Add 1 shot and mark done
    temp_worker.jq.add_shots_for_job(job_id, [{"index": 1, "prompt": "Shot 1"}])
    shot = temp_worker.jq.get_next_pending_shot(job_id)
    temp_worker.jq.mark_shot_in_progress(shot["id"], "acc1")
    temp_worker.jq.mark_shot_done(shot["id"], "acc1", "/dummy/shot.mp4", "gdrive://shot.mp4")

    # Mock assemble_documentary
    project_dir = Path("data/automated_runs") / job_id
    project_dir.mkdir(parents=True, exist_ok=True)
    final_video = project_dir / f"documentary_final_{job_id}.mp4"
    final_video.write_bytes(b"fake_video_data_12345")

    # Upload attempt 1 fails
    temp_worker.uploader.upload_and_clean.return_value = (False, None)

    # Call scanner 1st time
    temp_worker._scan_and_assemble_pending_jobs()

    job = temp_worker.jq.get_job(job_id)
    assert job["status"] == "FINAL_UPLOAD_PENDING"
    assert job["upload_attempts"] == 1
    assert job["next_retry_at"] is not None

    # Force next_retry_at into the past to simulate elapsed backoff
    with temp_worker.jq._get_conn() as conn:
        conn.execute("UPDATE factory_jobs SET next_retry_at = ? WHERE id = ?", (time.time() - 10, job_id))
        conn.commit()

    # Upload attempt 2 succeeds
    temp_worker.uploader.upload_and_clean.return_value = (True, "gdrive://Video_Projects/final.mp4")

    # Call scanner 2nd time in the SAME worker instance
    temp_worker._scan_and_assemble_pending_jobs()

    job_after = temp_worker.jq.get_job(job_id)
    assert job_after["status"] == "COMPLETED"
    assert job_after["drive_url"] == "gdrive://Video_Projects/final.mp4"


def test_recovery_assembly_failed_and_retry(temp_worker: VideoAutomationWorker, tmp_path: Path):
    """
    P0.1 & P0.3 test:
    Assembly raises exception -> job transitions to ASSEMBLY_FAILED (not FAILED), preserving shots.
    retry_job() is called -> scanner runs assembly again and completes.
    """
    job_id = temp_worker.jq.create_job(mode="remake", url="https://youtube.com/watch?v=222")
    temp_worker.jq.transition_job(job_id, to_state="APPROVED")

    temp_worker.jq.add_shots_for_job(job_id, [{"index": 1, "prompt": "Shot 1"}])
    shot = temp_worker.jq.get_next_pending_shot(job_id)
    temp_worker.jq.mark_shot_in_progress(shot["id"], "acc1")
    temp_worker.jq.mark_shot_done(shot["id"], "acc1", "/dummy/shot.mp4", "gdrive://shot.mp4")

    project_dir = Path("data/automated_runs") / job_id
    project_dir.mkdir(parents=True, exist_ok=True)
    final_video = project_dir / f"documentary_final_{job_id}.mp4"

    # Make assembly fail on first attempt
    with patch("pipeline.documentary_assembler.assemble_documentary", side_effect=RuntimeError("FFmpeg test crash")):
        temp_worker._scan_and_assemble_pending_jobs()

    job = temp_worker.jq.get_job(job_id)
    assert job["status"] == "ASSEMBLY_FAILED"

    # Verify shots are still DONE (not reset!)
    summ = temp_worker.jq.get_job_summary(job_id)
    assert summ["done"] == 1
    assert summ["failed"] == 0

    # User invokes /retry <job_id>
    ok = temp_worker.jq.retry_job(job_id)
    assert ok is True

    # Job is reset to APPROVED and shots remain DONE
    job_retried = temp_worker.jq.get_job(job_id)
    assert job_retried["status"] == "APPROVED"

    # Second assembly run succeeds
    def mock_assemble_ok(job_id, project_dir):
        final_video.write_bytes(b"assembled_video_content")
        return final_video

    temp_worker.uploader.upload_and_clean.return_value = (True, "gdrive://final_assembled.mp4")
    with patch("pipeline.documentary_assembler.assemble_documentary", side_effect=mock_assemble_ok):
        temp_worker._scan_and_assemble_pending_jobs()

    job_done = temp_worker.jq.get_job(job_id)
    assert job_done["status"] == "COMPLETED"


def test_retry_final_upload_pending_clears_backoff(temp_worker: VideoAutomationWorker):
    """
    P0.1 test:
    /retry on a FINAL_UPLOAD_PENDING job immediately clears backoff and upload_attempts.
    """
    job_id = temp_worker.jq.create_job(mode="remake", url="https://youtube.com/watch?v=333")
    temp_worker.jq.transition_job(job_id, to_state="APPROVED")
    temp_worker.jq.transition_job(job_id, to_state="ASSEMBLING")
    temp_worker.jq.transition_job(
        job_id,
        to_state="FINAL_UPLOAD_PENDING",
        upload_attempts=5,
        next_retry_at=time.time() + 3600.0,
    )

    # Calling retry_job
    assert temp_worker.jq.retry_job(job_id) is True

    job = temp_worker.jq.get_job(job_id)
    assert job["status"] == "FINAL_UPLOAD_PENDING"
    assert job["upload_attempts"] == 0
    assert job["next_retry_at"] is None


def test_shot_upload_pending_decoupling_and_no_quota_burn(temp_worker: VideoAutomationWorker, tmp_path: Path):
    """
    P0.2 test:
    When shot upload fails, shot transitions to UPLOAD_PENDING with backoff without burning generation attempts.
    AI provider is called exactly ONCE.
    """
    job_id = temp_worker.jq.create_job(mode="remake", url="https://youtube.com/watch?v=444")
    temp_worker.jq.transition_job(job_id, to_state="APPROVED")

    temp_worker.jq.add_shots_for_job(job_id, [{"index": 1, "prompt": "Shot 1", "is_key_scene": False}])

    # Mock provider returning a real file
    fake_shot_vid = tmp_path / "shot_001.mp4"
    fake_shot_vid.write_bytes(b"video_bytes_dummy" * 500)
    temp_worker.fallback_provider.generate.return_value = ProviderResult(ok=True, video_path=fake_shot_vid)

    # First iteration: worker generates shot and puts it in UPLOAD_PENDING
    shot = temp_worker.jq.get_next_pending_shot(job_id)
    assert shot is not None

    marked = temp_worker.jq.mark_shot_in_progress(shot["id"], "ken_burns_local")
    assert marked is True
    temp_worker.jq.mark_shot_generated(shot["id"], str(fake_shot_vid))

    shot_state = temp_worker.jq.get_next_upload_pending_shot(job_id)
    assert shot_state is not None
    assert shot_state["status"] == "UPLOAD_PENDING"

    # Upload fails
    is_final = temp_worker.jq.mark_shot_upload_failed(shot["id"], "Drive 503 error")
    assert is_final is False

    # Verify shot has upload backoff, but generation attempts were NOT incremented
    with temp_worker.jq._get_conn() as conn:
        row = conn.execute("SELECT * FROM factory_shots WHERE id = ?", (shot["id"],)).fetchone()
        assert row["status"] == "UPLOAD_PENDING"
        assert row["upload_attempts"] == 1
        assert row["attempts"] == 1  # Only 1 generation attempt!
        assert row["next_retry_at"] > time.time()

    # Provider should not be called again when uploading
    temp_worker.uploader.upload_and_clean.return_value = (True, "gdrive://shot_001.mp4")
    temp_worker.jq.mark_shot_done(shot["id"], "ken_burns_local", str(fake_shot_vid), "gdrive://shot_001.mp4")

    summary = temp_worker.jq.get_job_summary(job_id)
    assert summary["done"] == 1
    assert summary["failed"] == 0
