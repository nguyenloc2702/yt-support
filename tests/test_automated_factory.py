"""Unit tests for AutomatedFactory Mode 1 and Mode 2 pipelines."""

import json
from pathlib import Path
import pytest

from pipeline.automated_factory import AutomatedFactory
from services.job_queue import JobQueueManager


@pytest.fixture
def test_factory(tmp_path: Path):
    db_file = tmp_path / "factory_test.sqlite3"
    out_dir = tmp_path / "factory_runs"
    return AutomatedFactory(output_dir=out_dir, db_path=db_file)


def test_factory_job_creation(test_factory: AutomatedFactory):
    jq = test_factory.jq
    job_id = jq.create_job(mode="reup", url="https://youtube.com/watch?v=demo123", target_duration=120.0)
    assert job_id.startswith("reup_")

    job = jq.get_job(job_id)
    assert job["mode"] == "reup"
    assert job["target_duration"] == 120.0


def test_factory_mode2_dynamic_shots(test_factory: AutomatedFactory, monkeypatch):
    # Mock download_video and try_extract_subtitles
    monkeypatch.setattr("pipeline.automated_factory.download_video", lambda url, p, **kw: p / "download.mp4")
    
    synthetic_subs = [
        {"start": i * 15.0, "end": (i + 1) * 15.0, "text": f"Đoạn diễn biến lịch sử quan trọng {i+1} đột ngột xảy ra"}
        for i in range(12)
    ]
    monkeypatch.setattr("pipeline.automated_factory.try_extract_subtitles", lambda p: synthetic_subs)

    job_id = test_factory.run_ai_genesis("https://youtube.com/watch?v=documentary_test")
    job = test_factory.jq.get_job(job_id)
    assert job["status"] == "WAITING_APPROVAL"

    summary = test_factory.jq.get_job_summary(job_id)
    # Total shots should be dynamically calculated between 6 and 24, not hardcoded to 10
    assert summary["total"] >= 6
    assert summary["pending"] == summary["total"]

    # Verify shots plan was saved
    proj_dir = test_factory.output_dir / job_id
    shots_file = proj_dir / "shots_plan.json"
    assert shots_file.exists()
    shots_data = json.loads(shots_file.read_text(encoding="utf-8"))
    assert len(shots_data) == summary["total"]
    # Check that dramatic keyword correctly triggers is_key_scene
    assert any(s["is_key_scene"] for s in shots_data)


def test_deduplicate_vtt_segments():
    from pipeline.automated_factory import deduplicate_vtt_segments

    rolling_cues = [
        {"start": 0.0, "end": 2.0, "text": "Xin chào các bạn"},
        {"start": 1.0, "end": 3.0, "text": "Xin chào các bạn đã quay trở lại"},
        {"start": 3.0, "end": 5.0, "text": "với kênh video hôm nay"},
        {"start": 4.0, "end": 6.0, "text": "với kênh video hôm nay chúng ta sẽ tìm hiểu"},
    ]
    deduped = deduplicate_vtt_segments(rolling_cues)
    assert len(deduped) == 2
    assert deduped[0]["text"] == "Xin chào các bạn đã quay trở lại"
    assert deduped[1]["text"] == "với kênh video hôm nay chúng ta sẽ tìm hiểu"


def test_execute_approved_reup_rejects_non_reup(test_factory: AutomatedFactory):
    # If execute_approved_reup is called on a remake job, it must return False safely without corrupting DB
    job_id = test_factory.jq.create_job(mode="remake", url="https://youtube.com/watch?v=mode2_test")
    test_factory.jq.update_job_status(job_id, "APPROVED")

    success = test_factory.execute_approved_reup(job_id)
    assert success is False
    job = test_factory.jq.get_job(job_id)
    # Status should remain APPROVED for background worker
    assert job["status"] == "APPROVED"

