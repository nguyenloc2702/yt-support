"""Mode `cinematic` — short kể chuyện điện ảnh (không đưa tin).

Spec: docs/short_mode_cinematic_spec.md. Mode mới KHÔNG được đổi output của 4
mode cũ (news/decode/daily_digest/trailer) — test regression nằm cuối file.
"""
from __future__ import annotations

import subprocess

import pytest

from backend.app.pipeline_short import assembler, overlay_renderer
from backend.app.service_shorts import ShortService, _image_prompt_for
from conftest import default_short_script


def cinematic_script() -> dict:
    """Kịch bản kể chuyện VI đủ ngân sách 100–140 từ (tổng 114 từ)."""
    hook = "Sáu giờ sáng, khi cả thành phố vẫn còn ngủ, một con tàu khổng lồ lặng lẽ rời bến"
    beats = [
        "Trên boong tàu, thuyền trưởng nhìn bản đồ giấy đã ố vàng và đánh dấu một hải trình chưa ai từng đi",
        "Mỗi ngày con tàu chở theo bốn nghìn thùng hàng, đủ nuôi sống cả một thị trấn nhỏ ven biển",
        "Ba tuần sau, bão lớn ập đến và mọi liên lạc với đất liền bỗng nhiên im bặt",
        "Thủy thủ đoàn mười hai người chỉ còn một chiếc radio cũ và niềm tin mong manh",
    ]
    closing = "Và khi con tàu trở về, điều duy nhất còn nguyên vẹn là câu chuyện họ kể lại"
    texts = [hook] + beats + [closing]
    return {
        "title": "Chuyến tàu cuối cùng rời bến lúc bình minh",
        "hook": hook, "beats": beats, "closing": closing,
        "search_keywords_en": ["foggy harbor dawn", "old paper sea chart",
                               "cargo ship deck", "storm at sea", "empty harbor return"],
        "hashtags": {"tiktok": ["#kechuyen"], "facebook": ["#cinematic"],
                     "youtube": ["#shorts"]},
        "description": "Một chuyến tàu rời bến lúc bình minh và trở về với một câu chuyện.",
        "source_note": "Nguồn: Tư liệu biển",
        "word_count": sum(len(t.split()) for t in texts),
    }


@pytest.fixture()
def job_env(monkeypatch):
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")


def _create(client, ingest, short_adapters, mode="cinematic", script=None):
    short_adapters["llm"].script = script or cinematic_script()
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": mode,
        "source_text": "Chuyến tàu rời bến lúc bình minh, ba tuần sau gặp bão.",
        "source_note": "Nguồn: Tư liệu biển",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_create_cinematic_short_is_scripted_with_story_scenes(client, ingest, short_adapters):
    short = _create(client, ingest, short_adapters)
    assert short["mode"] == "cinematic"
    assert short["status"] == "scripted"
    # Scene = hook + 4 beat + closing
    assert len(short["script"]["beats"]) == 4
    assert len(short["scenes"]) == 6
    assert [s["seq_num"] for s in short["scenes"]] == [1, 2, 3, 4, 5, 6]
    # LLM nhận system prompt kể chuyện (không phải giọng tin)
    system = short_adapters["llm"].calls[0]["system"]
    assert "KỂ CHUYỆN ĐIỆN ẢNH" in system
    assert "Theo thông tin" in system  # luật cấm mở cảnh kiểu tin
    assert "search_keywords_en" in system


def test_publish_job_cinematic_uses_cinematic_visual_prompt(client, ingest, short_adapters,
                                                           job_env):
    short = _create(client, ingest, short_adapters)
    sid = short["id"]
    r = client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "queued"
    assert job["mode"] == "cinematic"

    for scene in job["scenes"]:
        prompt = scene["visual_prompt"]
        assert "CINEMATIC EDITORIAL 9:16" in prompt
        assert "DURATION:" in prompt and "NARRATION BEAT:" in prompt
        assert "CINEMATIC COMPOSITION" in prompt
        # Không còn dấu hiệu news: badge, tactical map, HUD
        assert "TIN NÓNG" not in prompt
        assert "tactical map" not in prompt.lower()
        assert "HUD" not in prompt
        for sub in scene["sub_shots"]:
            assert "CINEMATIC EDITORIAL 9:16" in sub["visual_prompt"]

    # template phản ánh style cinematic
    template = job["template"]
    assert "CINEMATIC EDITORIAL 9:16" in template["style_lock"]
    assert "cinematic" in template["skill_name"]


def test_cinematic_assemble_job_selects_clean_karaoke_preset(client, ingest, short_adapters,
                                                            job_env, monkeypatch):
    short = _create(client, ingest, short_adapters)
    sid = short["id"]
    client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    monkeypatch.setattr(ShortService, "_probe_clip",
                        lambda self, path: {"duration_sec": 60.0, "width": 1080,
                                            "height": 1920, "fps": 30.0})
    scene_count = len(short["scenes"])
    for seq in range(1, scene_count + 1):
        rs = client.post(
            f"/api/v1/shorts/{sid}/clips", headers=ingest,
            files={"file": (f"scene_{seq}.mp4", b"FAKE_AGENT_CLIP" * 100, "video/mp4")},
            data={"seq_num": str(seq), "covered_scene_indexes": str(seq)})
        assert rs.status_code == 201, rs.text

    # Ép nhánh ffmpeg thật (không fake container) để chạm overlay_renderer
    monkeypatch.setattr(assembler, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(assembler, "_wav_duration", lambda path: 10.0)
    monkeypatch.setattr(assembler, "_probe_video",
                        lambda path: {"width": 1080, "height": 1920, "fps": 30.0,
                                      "codec": "h264", "duration": 10.0})
    monkeypatch.setattr(assembler, "_mix_bgm_bed", lambda *a, **kw: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)

    captured: dict = {}

    def fake_overlay(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(overlay_renderer, "render_overlay_and_mux", fake_overlay)

    r = client.post(f"/api/v1/shorts/{sid}/assemble", headers=ingest)
    assert r.status_code == 200, r.text
    assert captured.get("preset") == "clean_karaoke"
    # Karaoke-only: không truyền headline/source để burn badge — vẫn giữ kwarg rỗng
    assert captured.get("hook_headline") == "Chuyến tàu cuối cùng rời bến lúc bình minh"


def test_news_visual_prompt_unchanged_regression(client, ingest, short_adapters, job_env):
    short = _create(client, ingest, short_adapters, mode="news",
                    script=default_short_script())
    r = client.post(f"/api/v1/shorts/{short['id']}/publish-job", headers=ingest)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["template"]["style_lock"].startswith("STYLE LOCKED")
    assert "GLOBAL EXPLAINER HYBRID 9:16" in job["template"]["style_lock"]
    first = job["scenes"][0]["visual_prompt"]
    assert "GLOBAL EXPLAINER HYBRID 9:16" in first
    assert "3-LAYER COMPOSITION" in first
    assert "SAFE-ZONE:" in first
    assert "CINEMATIC EDITORIAL" not in first


def test_image_prompt_for_cinematic_drops_news_editorial():
    cinematic = _image_prompt_for("sho_x", 1, "Con tàu rời bến lúc bình minh", "vi",
                                  "cinematic")
    assert "news editorial" not in cinematic
    assert "photojournalistic" not in cinematic
    assert "Cinematic editorial visual" in cinematic
    assert "Con tàu rời bến" in cinematic

    news = _image_prompt_for("sho_x", 1, "Con tàu rời bến lúc bình minh", "vi")
    assert "news editorial" in news
    assert "photojournalistic" in news


def test_clean_karaoke_overlay_has_no_badge(tmp_path, monkeypatch):
    if not overlay_renderer.is_available():
        pytest.skip("Pillow/fonts unavailable")
    monkeypatch.setattr(overlay_renderer.subprocess, "run", lambda *a, **kw: None)

    words = [
        {"text": "Sáu", "start": 1.0, "end": 1.2},
        {"text": "giờ", "start": 1.2, "end": 1.4},
        {"text": "sáng", "start": 1.4, "end": 1.7},
    ]
    base = tmp_path / "base.mp4"
    wav = tmp_path / "voice.wav"
    out = tmp_path / "out.mp4"
    base.write_bytes(b"MP4")
    wav.write_bytes(b"WAV")

    ok = overlay_renderer.render_overlay_and_mux(
        base, wav, 3.0, words, out, tmp_path,
        preset="clean_karaoke",
        hook_headline="HEADLINE KHÔNG ĐƯỢC BURN",
        source_note="Nguồn: Không được burn",
    )
    assert ok is True

    frames = sorted((tmp_path / "overlays").glob("ov_*.png"))
    assert frames, "phải render frame overlay"
    from PIL import Image

    # Frame đầu (trước từ đầu tiên) hoàn toàn trong suốt → không source pill,
    # không hook card, không badge "TIN NÓNG QUỐC TẾ", không headline.
    with Image.open(frames[0]).convert("RGBA") as im:
        assert im.getbbox() is None
    # Nhưng karaoke vẫn được vẽ khi có từ đang đọc.
    later = frames[1:]
    assert later
    drawn = False
    for f in later:
        with Image.open(f).convert("RGBA") as im:
            if im.getbbox() is not None:
                drawn = True
                break
    assert drawn


def test_patch_script_cinematic_keeps_all_beats(client, ingest, short_adapters):
    """PATCH kịch bản không được cắt beat còn 4 với cinematic (bug trước đây).

    `validate_script` dùng cap 4 cho mọi mode không phải digest/trailer, nên owner
    sửa kịch bản 8 đoạn trong UI sẽ mất âm thầm 4 đoạn — trong khi
    `create_from_script` cho tới 18 scene. Giờ cả hai đọc `prompts.max_beats`.
    """
    short = _create(client, ingest, short_adapters)
    sid = short["id"]
    beats = [f"Beat số {i} kể tiếp một nhịp của câu chuyện." for i in range(1, 8)]
    r = client.patch(f"/api/v1/shorts/{sid}/script", headers=ingest,
                     json={"beats": beats})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["script"]["beats"]) == 7
    assert len(body["scenes"]) == 9  # hook + 7 beat + closing


def test_max_beats_keeps_legacy_caps():
    from backend.app.pipeline_short import prompts
    assert prompts.max_beats("news") == 4
    assert prompts.max_beats("decode") == 4
    assert prompts.max_beats("trailer") == 3
    assert prompts.max_beats("daily_digest") == 15
    assert prompts.max_beats("cinematic") == 12


def test_patch_script_cinematic_keeps_all_beats(client, ingest, short_adapters):
    """PATCH kịch bản không được cắt beat còn 4 với cinematic (bug trước đây).

    `validate_script` dùng cap 4 cho mọi mode không phải digest/trailer, nên owner
    sửa kịch bản 8 đoạn trong UI sẽ mất âm thầm 4 đoạn — trong khi
    `create_from_script` cho tới 18 scene. Giờ cả hai đọc `prompts.max_beats`.
    """
    short = _create(client, ingest, short_adapters)
    sid = short["id"]
    beats = [f"Beat số {i} kể tiếp một nhịp của câu chuyện." for i in range(1, 8)]
    r = client.patch(f"/api/v1/shorts/{sid}/script", headers=ingest,
                     json={"beats": beats})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["script"]["beats"]) == 7
    assert len(body["scenes"]) == 9  # hook + 7 beat + closing


def test_max_beats_keeps_legacy_caps():
    from backend.app.pipeline_short import prompts
    assert prompts.max_beats("news") == 4
    assert prompts.max_beats("decode") == 4
    assert prompts.max_beats("trailer") == 3
    assert prompts.max_beats("daily_digest") == 15
    assert prompts.max_beats("cinematic") == 12


def test_short_payload_exposes_lang_aware_word_budget(client, ingest, short_adapters):
    """UI phải thấy ngân sách đúng theo lang, không phải hint tiếng Việt cắt chuỗi."""
    from backend.app.pipeline_short import prompts
    assert prompts.word_budget("cinematic", "en") == (85, 120)
    assert prompts.word_budget("cinematic", "vi") == (100, 140)
    short = _create(client, ingest, short_adapters)
    assert short["word_budget"] == {"min": 100, "max": 140}  # script fixture là VI


def test_from_script_marks_owner_script_so_ui_skips_budget(client, ingest, short_adapters):
    r = client.post("/api/v1/shorts/from-script", headers=ingest, json={
        "title": "Owner script", "mode": "cinematic", "lang": "en",
        "hook": "Hook line here.",
        "beats": [f"Beat number {i} describes the long descent through fire and the silence "
                  f"that follows it afterwards." for i in range(1, 9)],
        "closing": "Closing line here.",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["script"]["owner_script"] is True
    assert body["word_budget"] == {"min": 85, "max": 120}
    assert body["word_count"] > body["word_budget"]["max"]  # cố ý vượt, không bị chặn
