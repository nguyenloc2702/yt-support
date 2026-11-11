"""Tests for Agent Feedback API, Storage/GDrive Health, Intermediate Cleanup, and Guide."""
from __future__ import annotations

import os
import time
from pathlib import Path
import pytest
import yaml

from backend.app.config import data_dir, RENDER_SPEC
from backend.app.storage_gdrive import (
    check_storage_health,
    cleanup_item_intermediates,
    sync_item_to_gdrive,
)


@pytest.fixture()
def fast_offline_tts(monkeypatch):
    """Use fast deterministic offline WAV synthesis for tests."""
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")


def test_agent_guide_endpoint_and_files(client, read_key):
    """Verifies AGENT_API_GUIDE.md content, raw formatting, and sync to both repositories."""
    # 1. API endpoint JSON format
    res = client.get("/api/v1/agent-guide", headers=read_key)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["title"] == "External Video Agent API Guide"
    assert data["production_base_url"] == "https://vidfac.gacdero.vn"
    assert len(data["endpoints"]) >= 8
    assert "Step 1: Poll pending jobs" in [ep["description"] for ep in data["endpoints"]]
    assert "External Video Agent API Guide" in data["markdown"]

    # 2. API endpoint raw format
    res_raw = client.get("/api/v1/agent-guide?format=raw", headers=read_key)
    assert res_raw.status_code == 200
    assert "text/markdown" in res_raw.headers.get("content-type", "")
    assert "External Video Agent API Guide" in res_raw.text

    # 3. File existence across repositories
    guide_sys = Path(r"C:\app\work\video-system\AGENT_API_GUIDE.md")
    assert guide_sys.exists()
    assert guide_sys.stat().st_size > 5000

    guide_support = Path(r"C:\app\work\support-video-edit\AGENT_API_GUIDE.md")
    assert guide_support.exists()
    assert guide_support.stat().st_size > 5000


def test_storage_health_and_endpoint(client, read_key):
    """Verifies storage health probe returns disk capacity, rclone config, and endpoint response."""
    health = check_storage_health()
    assert "disk" in health
    assert health["disk"]["total_gb"] > 0
    assert health["disk"]["free_gb"] > 0
    assert health["total_gb"] == health["disk"]["total_gb"]
    assert health["free_gb"] == health["disk"]["free_gb"]
    assert "rclone_installed" in health
    assert health["gdrive_remote"] == "gdrive:video_system_assets"
    assert "auto_sync_on_assemble" in health

    res = client.get("/api/v1/storage/status", headers=read_key)
    assert res.status_code == 200, res.text
    api_health = res.json()
    assert api_health["disk"]["free_gb"] == health["disk"]["free_gb"]
    assert api_health["free_gb"] == health["free_gb"]
    assert api_health["gdrive_remote"] == "gdrive:video_system_assets"


def test_cleanup_intermediates_logic():
    """Verifies that cleanup_item_intermediates purges temporary folders and files,
    and selectively removes raw clips while preserving final assets."""
    item_id = "itm_cleanup_test_001"
    item_dir = data_dir() / "objects" / item_id
    item_dir.mkdir(parents=True, exist_ok=True)

    # Populate temporary folders & files
    tmp_norm = item_dir / "exports" / "_tmp_norm"
    tmp_norm.mkdir(parents=True, exist_ok=True)
    (tmp_norm / "shot1_norm.mp4").write_bytes(b"temp_norm_bytes" * 50)
    # _tmp_norm có safeguard chống race (không xóa nếu < 300s tuổi). Backdate
    # để test kiểm chứng được nhánh dọn dẹp thật (giữ nguyên intent của test).
    old_ts = time.time() - 400
    os.utime(tmp_norm, (old_ts, old_ts))
    os.utime(tmp_norm / "shot1_norm.mp4", (old_ts, old_ts))

    tmp_shots = item_dir / "todo" / "_tmp_shots"
    tmp_shots.mkdir(parents=True, exist_ok=True)
    (tmp_shots / "shot1.wav").write_bytes(b"temp_shot_wav" * 50)

    ingest_dir = item_dir / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    (ingest_dir / "raw_in.mp4").write_bytes(b"temp_ingest" * 50)

    # Temporary files
    (item_dir / "export.tmp.mp4").write_bytes(b"temp_file" * 50)
    (item_dir / "narration.voice.wav").write_bytes(b"voice_wav" * 50)

    # Permanent / asset files
    videos_dir = item_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    clip_path = videos_dir / "seq_0001.mp4"
    clip_path.write_bytes(b"video_clip_payload" * 200)

    todo_dir = item_dir / "todo"
    todo_audio = todo_dir / "audio_en.wav"
    todo_audio.write_bytes(b"permanent_audio" * 100)

    # 1. Normal cleanup without removing clips
    res1 = cleanup_item_intermediates(item_id, remove_uploaded_clips=False)
    assert res1["freed_bytes"] > 0
    assert not tmp_norm.exists()
    assert not tmp_shots.exists()
    assert not ingest_dir.exists()
    assert not (item_dir / "export.tmp.mp4").exists()
    assert not (item_dir / "narration.voice.wav").exists()
    assert clip_path.exists(), "Raw clip should be retained when remove_uploaded_clips=False"
    assert todo_audio.exists(), "Permanent audio must remain untouched"

    # 2. remove_uploaded_clips=True BEFORE final_en.mp4 exists: raw clips MUST be preserved for safety!
    res_safe = cleanup_item_intermediates(item_id, remove_uploaded_clips=True)
    assert clip_path.exists(), "Raw clip must NOT be deleted if final_en.mp4 does not exist yet"

    # 3. Create final_en.mp4: now remove_uploaded_clips=True should purge raw clips
    exports_dir = item_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    final_video = exports_dir / "final_en.mp4"
    final_video.write_bytes(b"master_final_mp4_bytes" * 50)

    res2 = cleanup_item_intermediates(item_id, remove_uploaded_clips=True)
    assert res2["freed_bytes"] > 0
    assert not clip_path.exists(), "Raw clip should be deleted when final_en.mp4 exists and remove_uploaded_clips=True"
    assert final_video.exists(), "Master video must remain untouched"
    assert todo_audio.exists(), "Permanent audio must still remain untouched"

    # 4. Explicit purge_raw_clips=True deletes even if final_video were absent
    clip_path2 = videos_dir / "seq_0002.mp4"
    clip_path2.write_bytes(b"another_clip" * 100)
    final_video.unlink()
    res3 = cleanup_item_intermediates(item_id, purge_raw_clips=True)
    assert not clip_path2.exists(), "Explicit purge_raw_clips=True must purge clips"


def test_sync_item_to_gdrive_disabled_and_endpoint(client, ingest, monkeypatch):
    """Verifies sync_item_to_gdrive behavior when disabled via env, and API endpoint."""
    monkeypatch.setenv("VIDEO_DISABLE_GDRIVE_SYNC", "1")

    item_id = "itm_sync_test_002"
    item_dir = data_dir() / "objects" / item_id
    item_dir.mkdir(parents=True, exist_ok=True)

    res = sync_item_to_gdrive(item_id)
    assert res["status"] == "skipped"
    assert "VIDEO_DISABLE_GDRIVE_SYNC" in res["detail"]

    # Test via API endpoint
    r = client.post(
        "/api/v1/items",
        headers=ingest,
        json={"title": "Sync Test Item", "source": {"type": "upload"}, "target": {"language": "en"}},
    )
    assert r.status_code == 201
    created_id = r.json()["item_id"]

    r_sync = client.post(f"/api/v1/todos/{created_id}/sync-gdrive", headers=ingest)
    assert r_sync.status_code == 200, r_sync.text
    sync_data = r_sync.json()
    assert sync_data["status"] == "skipped"

    # Also test passing JSON body with purge_raw_clips: true
    r_sync_json = client.post(
        f"/api/v1/todos/{created_id}/sync-gdrive",
        headers=ingest,
        json={"purge_raw_clips": True},
    )
    assert r_sync_json.status_code == 200, r_sync_json.text
    assert r_sync_json.json()["status"] == "skipped"


def test_agent_feedback_full_lifecycle(client, ingest, read_key, fast_offline_tts):
    """Verifies external agent feedback submission (single and batch), listing,
    filtering, resolution, and inclusion in TODO summaries."""
    # 1. Create a TODO item
    script_text = (
        "In 1995, Wang Chuanfu founded BYD in Shenzhen. "
        "The company expanded from batteries to electric vehicles, "
        "surpassing global automotive giants within three decades."
    )
    r_create = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "BYD Rise Story",
            "script_text": script_text,
            "bgm_enabled": False,
        },
    )
    assert r_create.status_code == 201, r_create.text
    item_id = r_create.json()["item_id"]

    # 2. Submit single feedback
    single_fb = {
        "category": "pronunciation",
        "severity": "warning",
        "shot_index": 1,
        "timestamp_sec": 3.2,
        "original_text": "BYD",
        "suggested_text": "B-Y-D",
        "pronunciation_hint": "Bee-Why-Dee",
        "message": "Please pronounce letter by letter rather than a blended word.",
        "agent_name": "speech_audit_agent",
    }
    r_fb1 = client.post(f"/api/v1/todos/{item_id}/feedback", headers=ingest, json=single_fb)
    assert r_fb1.status_code == 201, r_fb1.text
    d1 = r_fb1.json()
    assert d1["item_id"] == item_id
    assert d1["total_feedbacks"] == 1
    assert d1["open_feedbacks"] == 1
    assert len(d1["created_feedbacks"]) == 1
    fb1_id = d1["created_feedbacks"][0]["id"]
    assert d1["created_feedbacks"][0]["original_text"] == "BYD"
    assert d1["created_feedbacks"][0]["category"] == "pronunciation"

    # 3. Submit batch feedbacks
    batch_fb = {
        "feedbacks": [
            {
                "category": "proper_noun",
                "severity": "critical",
                "shot_index": 1,
                "timestamp_sec": 1.5,
                "original_text": "Wang Chuanfu",
                "pronunciation_hint": "Wahng Chwahn-foo",
                "message": "Mandarin tone clarification for founder name.",
            },
            {
                "category": "year_number",
                "severity": "info",
                "shot_index": 1,
                "timestamp_sec": 0.5,
                "original_text": "1995",
                "suggested_text": "nineteen ninety-five",
                "message": "Ensure natural year phrasing.",
            },
        ]
    }
    r_fb2 = client.post(f"/api/v1/todos/{item_id}/feedbacks", headers=ingest, json=batch_fb)
    assert r_fb2.status_code == 201, r_fb2.text
    d2 = r_fb2.json()
    assert d2["total_feedbacks"] == 3
    assert d2["open_feedbacks"] == 3
    assert len(d2["created_feedbacks"]) == 2
    fb2_id = d2["created_feedbacks"][0]["id"]
    fb3_id = d2["created_feedbacks"][1]["id"]

    # 4. List feedbacks and verify summary
    r_list = client.get(f"/api/v1/todos/{item_id}/feedback", headers=read_key)
    assert r_list.status_code == 200
    list_data = r_list.json()
    assert list_data["count"] == 3
    assert list_data["summary"]["total"] == 3
    assert list_data["summary"]["open"] == 3
    assert list_data["summary"]["resolved"] == 0

    # 5. Filter feedbacks by category
    r_cat = client.get(f"/api/v1/todos/{item_id}/feedback?category=proper_noun", headers=read_key)
    assert r_cat.status_code == 200
    assert r_cat.json()["count"] == 1
    assert r_cat.json()["feedbacks"][0]["original_text"] == "Wang Chuanfu"

    # 6. Resolve feedback #1 via POST /resolve
    r_res = client.post(
        f"/api/v1/todos/{item_id}/feedback/{fb1_id}/resolve",
        headers=ingest,
        json={"resolution_note": "Pronunciation corrected in script phonetics."},
    )
    assert r_res.status_code == 200, r_res.text
    assert r_res.json()["status"] == "resolved"
    assert r_res.json()["resolution_note"] == "Pronunciation corrected in script phonetics."

    # 7. Update feedback #2 via PATCH to ignored
    r_patch = client.patch(
        f"/api/v1/todos/{item_id}/feedback/{fb2_id}",
        headers=ingest,
        json={"status": "ignored", "resolution_note": "Current neural voice reading accepted."},
    )
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["status"] == "ignored"

    # 8. Check TODO detail GET /api/v1/todos/{item_id}
    r_todo = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_todo.status_code == 200
    todo_detail = r_todo.json()
    assert "feedback_summary" in todo_detail
    assert todo_detail["feedback_summary"]["total"] == 3
    assert todo_detail["feedback_summary"]["open"] == 1
    assert todo_detail["feedback_summary"]["resolved"] == 1
    assert todo_detail["feedback_summary"]["ignored"] == 1
    assert len(todo_detail["agent_feedbacks"]) == 3
    assert todo_detail["agent_guide_url"] == "/api/v1/agent-guide"

    # 9. Check TODO queue GET /api/v1/todos includes open_feedbacks_count
    r_queue = client.get("/api/v1/todos?status=todo", headers=read_key)
    assert r_queue.status_code == 200
    queue_items = r_queue.json().get("todos") or r_queue.json().get("items", [])
    target_item = next(it for it in queue_items if it["item_id"] == item_id)
    assert target_item["open_feedbacks_count"] == 1

    # 10. Edge cases & input validation attacks
    # Invalid category
    r_bad_cat = client.post(
        f"/api/v1/todos/{item_id}/feedback",
        headers=ingest,
        json={"category": "unknown_cat", "message": "Invalid category test"},
    )
    assert r_bad_cat.status_code == 422
    assert "Invalid category" in r_bad_cat.json()["detail"]

    # Invalid severity
    r_bad_sev = client.post(
        f"/api/v1/todos/{item_id}/feedback",
        headers=ingest,
        json={"severity": "apocalyptic", "message": "Invalid severity test"},
    )
    assert r_bad_sev.status_code == 422
    assert "Invalid severity" in r_bad_sev.json()["detail"]

    # Negative shot index
    r_bad_idx = client.post(
        f"/api/v1/todos/{item_id}/feedback",
        headers=ingest,
        json={"shot_index": -1, "message": "Negative shot index"},
    )
    assert r_bad_idx.status_code == 422

    # Negative timestamp
    r_bad_ts = client.post(
        f"/api/v1/todos/{item_id}/feedback",
        headers=ingest,
        json={"timestamp_sec": -2.5, "message": "Negative timestamp"},
    )
    assert r_bad_ts.status_code == 422

    # Empty message
    r_empty_msg = client.post(
        f"/api/v1/todos/{item_id}/feedback",
        headers=ingest,
        json={"category": "pronunciation", "message": ""},
    )
    assert r_empty_msg.status_code == 422


def test_audio_config_spec():
    """Verifies audio_config.yaml tuning: slower speech, longer pauses, and louder BGM."""
    audio_cfg_path = Path(r"C:\app\work\video-system\config\audio_config.yaml")
    assert audio_cfg_path.exists()
    cfg = yaml.safe_load(audio_cfg_path.read_text(encoding="utf-8"))

    assert cfg.get("version") == 2
    tts = cfg["tts_en"]
    assert tts["rate"] == "-6%", "Speech rate must be tuned to -6% (slower)"
    assert tts["words_per_sec_estimate"] == 2.45
    assert tts["inter_shot_pause_sec"] == 0.32, "Inter-shot pause must be 0.32s"
    assert tts["intra_sentence_pause_sec"] == 0.26, "Intra-sentence pause must be 0.26s"

    bgm = cfg["bgm"]
    assert bgm["duck_db"] == -20.0, "Ducking level must match audio_config.yaml (-20.0 dB)"
    assert bgm["bed_target_lufs"] == -38.0, "BGM bed target must match audio_config.yaml (-38.0 LUFS)"
    assert bgm["music_break_sec"] == 10.0, "Closing music break must be 10.0s"


def test_regenerate_audio_endpoint(client, ingest, read_key, fast_offline_tts):
    """Verifies audio regeneration API endpoint, retiming, and contiguity."""
    script_text = (
        "Larry Fink co-founded BlackRock in 1988 with eight partners. "
        "Through risk management and Aladdin software, it became the world largest asset manager."
    )
    r_create = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "BlackRock Short Test",
            "script_text": script_text,
            "bgm_enabled": False,
        },
    )
    assert r_create.status_code == 201, r_create.text
    item_id = r_create.json()["item_id"]
    orig_dur = r_create.json()["audio_duration_sec"]

    # Call regenerate audio
    r_regen = client.post(
        f"/api/v1/todos/{item_id}/regenerate-audio",
        headers=ingest,
        json={
            "slow_factor": 0.96,
            "extra_pause_sec": 0.12,
            "bgm_enabled": False,
        },
    )
    assert r_regen.status_code == 200, r_regen.text
    regen_data = r_regen.json()
    new_dur = regen_data["audio_duration_sec"]
    assert new_dur > 0

    # Verify contiguous tiling across shots
    shots = regen_data["shots"]
    assert len(shots) >= 2
    assert round(shots[0]["audio_start_sec"], 3) == 0.0
    for i in range(len(shots) - 1):
        assert round(shots[i]["audio_end_sec"], 3) == round(shots[i + 1]["audio_start_sec"], 3)
    assert round(shots[-1]["audio_end_sec"], 3) == round(new_dur, 3)

    # Verify TODO detail reflects new audio duration
    r_detail = client.get(f"/api/v1/todos/{item_id}", headers=read_key)
    assert r_detail.status_code == 200
    assert r_detail.json()["audio_duration_sec"] == new_dur
