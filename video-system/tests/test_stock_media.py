"""Stock media connector — Pixabay primary + Pexels fallback.

Không key → honest None; Pixabay key → parse + cache; video chỉ lấy variant
dọc; Pexels fallback khi thiếu Pixabay key (tài khoản cũ có key từ trước).
"""
from __future__ import annotations

import json
import wave

import pytest

from backend.app.pipeline_short import stock_media


@pytest.fixture(autouse=True)
def _no_stock_keys(monkeypatch):
    monkeypatch.delenv("VIDEO_PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("VIDEO_PEXELS_API_KEY", raising=False)


def test_keywords_for_query_strips_punctuation_and_stopwords():
    q = stock_media.keywords_for_query(
        "The bank later confirmed a gang posing as support staff stealing OTP codes!")
    assert "bank" in q and "gang" in q  # 6 từ ý chính đầu tiên
    assert "posing" in q
    assert "the" not in q.lower().split()
    assert "later" not in q.split()
    assert "," not in q
    assert len(q.split()) <= 6


def test_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("VIDEO_PIXABAY_API_KEY", raising=False)
    monkeypatch.delenv("VIDEO_PEXELS_API_KEY", raising=False)
    assert not stock_media.is_configured()
    assert stock_media.fetch_photo("bank run") is None
    assert stock_media.fetch_video("bank run") is None


def test_fetch_photo_pixabay_downloads_and_caches(monkeypatch):
    monkeypatch.setenv("VIDEO_PIXABAY_API_KEY", "pb-key")
    calls = {"search": 0, "download": 0}
    payload = json.dumps({
        "hits": [{"largeImageURL": "https://cdn.pixabay.test/p1.jpg",
                  "webformatURL": "https://cdn.pixabay.test/p0.jpg",
                  "user": "Jane Doe", "tags": "bank"}]
    }).encode()

    def fake_http_get(url, params=None, headers=None):
        if "pixabay.com/api/" in url and "videos" not in url:
            calls["search"] += 1
            assert params["key"] == "pb-key"
            assert params["orientation"] == "vertical"
            return payload
        calls["download"] += 1
        return b"FAKE_JPEG" * 10

    monkeypatch.setattr(stock_media, "_http_get", fake_http_get)
    r1 = stock_media.fetch_photo("bank queue")
    assert r1 and r1["kind"] == "image"
    assert r1["path"].exists()
    assert "Jane Doe / Pixabay" in r1["attribution"]
    r2 = stock_media.fetch_photo("bank queue")
    assert r2["path"] == r1["path"]
    assert calls["download"] == 1  # cache trúng, không tải lại


def test_pixabay_video_prefers_vertical_falls_back_horizontal(monkeypatch):
    monkeypatch.setenv("VIDEO_PIXABAY_API_KEY", "pb-key")
    # Có dọc 720x1280 → chọn dọc; bỏ dọc quá lớn (>1080) và webm
    vertical = {
        "large": {"url": "https://v.test/2160w.mp4", "width": 2160, "height": 3840},
        "medium": {"url": "https://v.test/720w.mp4", "width": 720, "height": 1280},
        "small": {"url": "https://v.test/horizontal.mp4", "width": 1280, "height": 720},
        "tiny": {"url": "https://v.test/t.webm", "width": 480, "height": 854},
    }
    assert stock_media._pick_pixabay_video_variant(vertical)["url"] == "https://v.test/720w.mp4"

    # Chỉ có variant ngang → fallback ngang ≤1080 (assembler crop-fill 9:16)
    horiz_only = {
        "large": {"url": "https://v.test/2560w.mp4", "width": 2560, "height": 1440},
        "small": {"url": "https://v.test/1280w.mp4", "width": 1280, "height": 720},
        "tiny": {"url": "https://v.test/960w.mp4", "width": 960, "height": 540},
    }
    assert stock_media._pick_pixabay_video_variant(horiz_only)["url"] == "https://v.test/960w.mp4"

    # user là STRING trong video API (khác API ảnh) — không được crash
    payload = json.dumps({
        "hits": [{"videos": horiz_only, "user": "John Cam", "duration": 12}]
    }).encode()

    def fake_http_get(url, params=None, headers=None):
        if "pixabay.com/api/videos" in url:
            return payload
        return b"FAKE_MP4" * 10

    monkeypatch.setattr(stock_media, "_http_get", fake_http_get)
    r = stock_media.fetch_video("city traffic")
    assert r and r["kind"] == "video_clip"
    assert "John Cam / Pixabay" in r["attribution"]
    assert r["path"].suffix == ".mp4"


def test_pexels_fallback_when_no_pixabay_key(monkeypatch):
    monkeypatch.setenv("VIDEO_PEXELS_API_KEY", "px-key")
    payload = json.dumps({
        "photos": [{"src": {"large2x": "https://images.pexels.test/p1.jpg"},
                    "photographer": "Jane Doe", "alt": "bank"}]
    }).encode()

    def fake_http_get(url, params=None, headers=None):
        if "api.pexels.com/v1/search" in url:
            assert headers.get("Authorization") == "px-key"
            return payload
        return b"FAKE_JPEG" * 10

    monkeypatch.setattr(stock_media, "_http_get", fake_http_get)
    r = stock_media.fetch_photo("bank queue")
    assert r and "Photo: Jane Doe / Pexels" in r["attribution"]


def test_pixabay_preferred_when_both_keys_set(monkeypatch):
    monkeypatch.setenv("VIDEO_PIXABAY_API_KEY", "pb-key")
    monkeypatch.setenv("VIDEO_PEXELS_API_KEY", "px-key")

    def fake_http_get(url, params=None, headers=None):
        if "pixabay.com/api/" in url:
            return json.dumps({"hits": [
                {"largeImageURL": "https://cdn.pixabay.test/pb.jpg", "user": "PB"}]}).encode()
        if "api.pexels.com" in url:
            raise AssertionError("Pexels không được gọi khi Pixabay trả kết quả")
        return b"FAKE_JPEG" * 10  # download asset

    monkeypatch.setattr(stock_media, "_http_get", fake_http_get)
    r = stock_media.fetch_photo("market")
    assert r and "Pixabay" in r["attribution"]


def test_assemble_with_clip_scene_offline(monkeypatch, tmp_path):
    """VIDEO_FAKE_FFMPEG: scene clip_path vẫn ra container fake đúng header."""
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    from backend.app.pipeline_short import assembler

    wav = tmp_path / "voice.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)  # 1s

    clip = tmp_path / "broll.mp4"
    clip.write_bytes(b"FAKE_MP4" * 20)
    ass = tmp_path / "subs.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")

    scenes = [
        {"seq": 1, "text": "hook", "start_sec": 0.0, "end_sec": 0.5,
         "image_path": None, "clip_path": str(clip)},
        {"seq": 2, "text": "beat", "start_sec": 0.5, "end_sec": 1.0,
         "image_path": None, "clip_path": None},
    ]
    out = assembler.assemble_short("sho_test_clip", scenes, str(wav), [], str(ass),
                                   tmp_path / "out")
    header = (tmp_path / "out" / "short_sho_test_clip.mp4").read_bytes().split(b"\n")[0].decode()
    assert header.startswith("SHORT_MP4_CONTAINER|")
    assert out["duration"] == pytest.approx(1.0, abs=0.01)


def test_tts_edge_transient_failure_retries_and_keeps_rest(monkeypatch, tmp_path):
    """1 cue edge-tts fail (transient) → chỉ cue đó offline; cue sau vẫn edge."""
    from backend.app.pipeline_short import tts_vi

    monkeypatch.setattr(tts_vi, "_offline", lambda: False)
    monkeypatch.setattr(tts_vi, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(tts_vi, "_sleep", lambda s: None)

    def fake_mp3_to_wav(mp3_path, wav_path):
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)                       # PCM 24kHz mono — khớp _WAV_RATE
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)  # 1s thật

    monkeypatch.setattr(tts_vi, "_mp3_to_wav", fake_mp3_to_wav)

    async def flaky_cue(text, voice, rate, mp3_path):
        if text.startswith("second"):  # fail ngay lần 1 — không retry được nữa
            raise RuntimeError("No audio was received.")
        words = text.split()
        return [{"text": w, "start": round(i * 0.2, 3),
                 "end": round((i + 1) * 0.2, 3)} for i, w in enumerate(words)]

    monkeypatch.setattr(tts_vi, "_edge_stream_cue", flaky_cue)

    texts = ["first sentence here", "second sentence now", "third sentence again"]
    res = tts_vi.synthesize_short_voice(texts, tmp_path, "sho_tts_flaky")
    assert res["duration"] > 0
    assert len(res["cue_spans"]) == 3
    # word timeline phủ đủ 3 cue (cue 2 offline → word estimate cũng có)
    assert all(w["end"] > w["start"] for w in res["words"])
    assert res["words"][-1]["end"] <= res["duration"] + 0.1
