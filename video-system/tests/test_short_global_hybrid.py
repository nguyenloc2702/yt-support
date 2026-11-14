"""Tests cho G.14 — Global Explainer Hybrid (100% tin quốc tế, song song VI/EN)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.config import NEWS_CONFIG, SHORT_CONFIG
from backend.app.pipeline_short import assembler, overlay_renderer, tts_vi
from test_shorts_en import default_short_script_en


def test_news_config_is_100_percent_international():
    assert NEWS_CONFIG["feeds"] == []
    assert NEWS_CONFIG["queries"] == []
    assert len(NEWS_CONFIG["feeds_en"]) >= 4
    assert len(NEWS_CONFIG["queries_en"]) >= 4


def test_short_config_valid_voices_and_presets():
    assert SHORT_CONFIG["tts_vi"]["default_voice"] == "vi-VN-HoaiMyNeural"
    assert SHORT_CONFIG["tts_en"]["default_voice"] == "en-US-GuyNeural"
    assert SHORT_CONFIG["render_presets"]["vi_default"] == "viral_redpill"
    assert SHORT_CONFIG["render_presets"]["en_default"] == "split_news_60"


def test_assembler_finds_repo_bgm_dir_and_filters_mood():
    bgm_dir = Path(assembler.__file__).resolve().parents[4] / "assets" / "music" / "bgm"
    assert bgm_dir.exists()
    t_om = assembler._pick_bgm_track(bgm_dir, "seed_1", mood="ominous")
    t_en = assembler._pick_bgm_track(bgm_dir, "seed_1", mood="energetic")
    assert t_om is not None and t_om.name.startswith(("ominous__", "mysterious__", "tense__"))
    assert t_en is not None and t_en.name.startswith(("energetic__", "ominous__"))


def test_tts_vi_passes_word_boundary_flag(tmp_path, monkeypatch):
    import asyncio
    import edge_tts

    captured = {}

    class FakeComm:
        def __init__(self, text, voice=None, rate=None, boundary=None):
            captured["boundary"] = boundary

        async def stream(self):
            yield {"type": "audio", "data": b"ID3"}
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "Hello"}

    monkeypatch.setattr(edge_tts, "Communicate", FakeComm)
    words = asyncio.run(tts_vi._edge_stream_cue("Hello", "en-US-GuyNeural", "+6%", tmp_path / "x.mp3"))
    assert captured["boundary"] == "WordBoundary"
    assert words == [{"text": "Hello", "start": 0.1, "end": 0.3}]


def test_overlay_renderer_frames_1080x1920():
    if not overlay_renderer.is_available():
        return
    words = [{"text": "300", "start": 0.0, "end": 0.4, "cue_idx": 0},
             {"text": "triệu", "start": 0.4, "end": 0.8, "cue_idx": 0}]
    vi_img = overlay_renderer._make_vi_frame(words, 0, False, "Headline", "Nguồn: Reuters")
    en_img = overlay_renderer._make_en_frame(words, 0, True, "Global Freight Surge", "Source: CNBC", 30.0)
    assert vi_img.size == (1080, 1920)
    assert en_img.size == (1080, 1920)


def test_update_script_preserves_en_lang(client, ingest, short_adapters):
    short_adapters["llm"].script = default_short_script_en()
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news", "lang": "en",
        "source_text": "Brief: global shipping surge.",
        "source_note": "Source: CNBC",
    })
    assert r.status_code == 201
    sid = r.json()["id"]
    upd = client.patch(f"/api/v1/shorts/{sid}/script", headers=ingest, json={
        "title": "Updated Global Freight Alert",
    })
    assert upd.status_code == 200, upd.text
    p0 = upd.json()["scenes"][0]["image_prompt"]
    assert "Scene:" in p0
    assert "Ý cảnh" not in p0


def test_render_spec_and_moving_watermark_png(tmp_path):
    from backend.app.config import RENDER_SPEC
    from backend.app.service_assembler import _render_watermark_png

    wm_cfg = RENDER_SPEC.get("watermark") or {}
    assert wm_cfg.get("text") == "@StickSto"
    assert wm_cfg.get("default_enabled") is True

    out_png = tmp_path / "wm.png"
    ok = _render_watermark_png("@StickSto", out_png, opacity=0.22, font_size=36)
    assert ok is True
    assert out_png.exists() and out_png.stat().st_size > 100

