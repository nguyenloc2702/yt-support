"""G.13 — Job queue cho agent video ngoài (short EN).

publish-job → agent submit clips → assemble. FakeProbe thay ffprobe, fake
ffmpeg container cho assemble; TTS offline.
"""
from __future__ import annotations

import json

import pytest

from backend.app.main import shorts as shorts_service
from backend.app.service_shorts import ShortService
from conftest import default_short_script  # script VI chuẩn của conftest


def _en_script():
    """Script EN đủ ngân sách (tự định nghĩa để không đụng module test khác)."""
    hook = "Overnight, three billion dong vanished from dozens of bank accounts"
    beats = [
        "By morning, customers kept calling the hotline about strange transactions they never made",
        "The bank later confirmed a gang posing as support staff had been stealing OTP codes",
        "Average losses hit fifty million dong each, with some victims losing two hundred million dong",
    ]
    closing = "So who bears the final responsibility — the bank or the customer?"
    texts = [hook] + beats + [closing]
    return {
        "title": "Three billion dong gone overnight",
        "hook": hook, "beats": beats, "closing": closing,
        "hashtags": {"tiktok": ["#breakingnews"], "facebook": ["#news"],
                     "youtube": ["#shorts"]},
        "description": "Bank OTP scam drained customer accounts overnight.",
        "source_note": "Source: BBC Business",
        "word_count": sum(len(t.split()) for t in texts),
    }


def _create_en_short(client, ingest, short_adapters):
    short_adapters["llm"].script = _en_script()
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news", "lang": "en",
        "source_text": "Brief: bank OTP scam drained accounts overnight.",
        "source_note": "Source: BBC Business",
    })
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def job_env(monkeypatch):
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")


def test_publish_job_en_and_package(client, ingest, short_adapters, job_env):
    short = _create_en_short(client, ingest, short_adapters)
    sid = short["id"]
    r = client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["lang"] == "en"
    assert len(job["scenes"]) == 5
    assert job["template"]["style_lock"].startswith("STYLE LOCKED")
    assert job["audio_url"].endswith("/job/audio")
    assert "agent_guide_url" in job["template"]
    # scene có timing + keyword gợi ý
    s1 = job["scenes"][0]
    assert s1["audio_end_sec"] > s1["audio_start_sec"]
    assert s1["stock_search_keywords"]
    # job xuất hiện trong poll list cho agent
    r2 = client.get("/api/v1/short-jobs?status=queued", headers=ingest)
    assert any(j["id"] == sid for j in r2.json()["jobs"])


def test_publish_job_vi_and_detailed_prompts(client, ingest, short_adapters, job_env):
    # G.14: publish-job mở khóa cho cả VI và EN, sinh prompt dài chi tiết + sub_shots
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news", "lang": "vi",
        "source_text": "Tin quốc tế thử nghiệm cho mảng VI.",
    })
    assert r.status_code == 201
    r2 = client.post(f"/api/v1/shorts/{r.json()['id']}/publish-job", headers=ingest)
    assert r2.status_code == 200, r2.text
    job = r2.json()
    assert job["status"] == "queued"
    assert job["lang"] == "vi"
    assert "GLOBAL EXPLAINER HYBRID 9:16" in job["template"]["style_lock"]
    s0 = job["scenes"][0]
    assert "SAFE-ZONE:" in s0["visual_prompt"]
    assert "3-LAYER COMPOSITION" in s0["visual_prompt"]
    assert isinstance(s0.get("sub_shots"), list) and len(s0["sub_shots"]) >= 1


def test_submit_clip_marks_scenes_and_progress(client, ingest, short_adapters, job_env,
                                               monkeypatch):
    short = _create_en_short(client, ingest, short_adapters)
    sid = short["id"]
    client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    # FakeProbe (fixtures `adapters`) không được wire mặc định — monkeypatch probe
    monkeypatch.setattr(ShortService, "_probe_clip",
                        lambda self, path: {"duration_sec": 9.0, "width": 1080,
                                            "height": 1920, "fps": 30.0})
    fake_mp4 = b"FAKE_AGENT_CLIP" * 100
    r = client.post(f"/api/v1/shorts/{sid}/clips", headers=ingest, json={
        "seq_num": 1, "file_path": None,
        "covered_scene_indexes": [1, 2],
    })
    # JSON mode cần file_path hoặc file_url — thiếu → 422
    assert r.status_code == 422
    # multipart submit
    r2 = client.post(f"/api/v1/shorts/{sid}/clips", headers=ingest,
                     files={"file": ("scene_1.mp4", fake_mp4, "video/mp4")},
                     data={"seq_num": "1", "covered_scene_indexes": "[1,2]"})
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body["covered_scene_indexes"] == [1, 2]
    assert body["progress"]["clips_uploaded"] == 1
    assert body["progress"]["scenes_covered"] == 2
    # status chuyển queued → rendering
    detail = client.get(f"/api/v1/shorts/{sid}", headers=ingest).json()
    assert detail["status"] == "rendering"
    # scene 1-2 đã checked
    scenes = detail["scenes"]
    assert scenes[0]["agent_checked"] == 1 and scenes[1]["agent_checked"] == 1
    assert scenes[0]["generated_duration_sec"] > 0
    # delete clip → checked reset
    r3 = client.delete(f"/api/v1/shorts/{sid}/clips/1", headers=ingest)
    assert r3.status_code == 200
    scenes = client.get(f"/api/v1/shorts/{sid}", headers=ingest).json()["scenes"]
    assert scenes[0]["agent_checked"] == 0 and scenes[1]["agent_checked"] == 0


def test_assemble_shortfall_409_then_full_flow(client, ingest, short_adapters, job_env,
                                               monkeypatch):
    short = _create_en_short(client, ingest, short_adapters)
    sid = short["id"]
    client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    monkeypatch.setattr(ShortService, "_probe_clip",
                        lambda self, path: {"duration_sec": 8.0, "width": 1080,
                                            "height": 1920, "fps": 30.0})
    # assemble khi chưa submit gì → 409 shortfall
    r = client.post(f"/api/v1/shorts/{sid}/assemble", headers=ingest)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "video_duration_shortfall"
    # submit đủ 5 scene
    for seq in range(1, 6):
        rs = client.post(
            f"/api/v1/shorts/{sid}/clips", headers=ingest,
            files={"file": (f"scene_{seq}.mp4", b"FAKE_AGENT_CLIP" * 100, "video/mp4")},
            data={"seq_num": str(seq), "covered_scene_indexes": str(seq)})
        assert rs.status_code == 201, rs.text
    detail = client.get(f"/api/v1/shorts/{sid}", headers=ingest).json()
    assert detail["status"] == "rendering"
    # assemble fake-ffmpeg → container có source=external-agent → ready
    r2 = client.post(f"/api/v1/shorts/{sid}/assemble", headers=ingest)
    assert r2.status_code == 200, r2.text
    done = r2.json()
    assert done["duration_sec"] > 0
    detail = client.get(f"/api/v1/shorts/{sid}", headers=ingest).json()
    assert detail["status"] == "ready"
    rv = client.get(f"/api/v1/shorts/{sid}/video", headers=ingest)
    assert rv.status_code == 200 and "video" in rv.headers["content-type"]


def test_clip_scopes_and_404(client, read_key, ingest):
    # read scope không được submit
    r = client.post("/api/v1/shorts/sho_xxx/clips", headers=read_key,
                    files={"file": ("a.mp4", b"x" * 10, "video/mp4")})
    assert r.status_code == 403
    # short không tồn tại
    r2 = client.get("/api/v1/shorts/sho_xxx/job", headers=ingest)
    assert r2.status_code == 404
    r3 = client.delete("/api/v1/shorts/sho_xxx/clips/1", headers=ingest)
    assert r3.status_code == 404
    # short-jobs list chỉ nhận status hợp lệ
    r4 = client.get("/api/v1/short-jobs?status=bogus", headers=ingest)
    assert r4.status_code == 422
