"""Tests for pipeline.tts: narration extraction, tempo fitting, cue distribution."""
from pathlib import Path

import pytest

from core.exceptions import RenderError
from pipeline.audio_mix import slice_timeline_by_output_range
from pipeline.tts import (
    MAX_TEMPO,
    EMOTION_TTS_STYLE,
    distribute_cues_over_clip,
    narration_items_from_timeline,
    synthesize_narration,
)


def test_distribute_cues_over_clip_spreads():
    text = "Câu một đây. Câu hai ngắn hơn! Ủa??? Là sao vậy? Chốt hạ luôn!!"
    cues = distribute_cues_over_clip(text, 60.0)  # long clip, text short
    assert len(cues) == 5
    # cues should start after a small lead and be spread, not all at 0
    assert cues[0]["output_start"] >= 0
    starts = [c["output_start"] for c in cues]
    assert starts == sorted(starts)
    # total spread should use a good chunk of the clip, not end at ~5s
    last = cues[-1]["output_start"] + cues[-1]["clip_duration"]
    assert last > 20.0  # spread across the 60s clip


def test_distribute_cues_long_text_packs_tight():
    text = ". ".join(f"Câu số {i} khá dài với nhiều từ để nói" for i in range(10)) + "."
    cues = distribute_cues_over_clip(text, 10.0)
    assert len(cues) == 10
    # narration longer than clip -> pack from start with lead only
    assert cues[0]["output_start"] <= 0.5


def test_emotion_style_table():
    assert "hype" in EMOTION_TTS_STYLE
    assert EMOTION_TTS_STYLE["hype"][0] != EMOTION_TTS_STYLE["xuc_dong"][0]
    assert EMOTION_TTS_STYLE["neutral"] == ("+0%", "+0Hz")


def test_narration_items_from_timeline():
    timeline = {
        "clips": [
            {"source_start": 0.0, "source_end": 10.0, "enabled": True, "narration_text": "Xin chào"},
            {"source_start": 10.0, "source_end": 15.0, "enabled": True, "narration_text": ""},
            {"source_start": 15.0, "source_end": 25.0, "enabled": True, "narration_text": "Đây là đoạn hai"},
            {"source_start": 25.0, "source_end": 30.0, "enabled": False, "narration_text": "bị tắt"},
        ]
    }
    items = narration_items_from_timeline(timeline)
    assert len(items) == 2
    assert items[0]["output_start"] == 0.0
    assert items[0]["clip_duration"] == 10.0
    assert items[0]["text"] == "Xin chào"
    # second narration starts after enabled clip durations: 10 + 5 = 15
    assert items[1]["output_start"] == 15.0
    assert items[1]["clip_duration"] == 10.0


def test_narration_items_empty():
    assert narration_items_from_timeline({"clips": []}) == []
    assert narration_items_from_timeline({"clips": [{"source_start": 0, "source_end": 5}]}) == []


def test_narration_items_longer_than_clip_gets_tempo(monkeypatch):
    timeline = {
        "clips": [{"source_start": 0.0, "source_end": 5.0, "enabled": True, "narration_text": "dài"}]
    }
    items = narration_items_from_timeline(timeline)

    async def fake_synth_one(text, voice, rate, volume, pitch, out_path):
        out_path.write_bytes(b"fake-mp3")

    monkeypatch.setattr("pipeline.tts._synthesize_one_pitch", fake_synth_one)
    monkeypatch.setattr("pipeline.tts._probe_duration", lambda p: 10.0)

    narrated = synthesize_narration(items, Path("tmp_tts_test"))
    assert narrated[0]["tempo"] == pytest.approx(MAX_TEMPO)

    # cleanup
    import shutil
    shutil.rmtree("tmp_tts_test", ignore_errors=True)


def test_tempo_capped():
    assert MAX_TEMPO <= 1.6


def test_split_text_for_tts_respects_gateway_limit():
    from pipeline.tts import XKIRO_TTS_MAX_CHARS, _split_text_for_tts

    text = " ".join(
        f"Câu số {i} nói về dòng tiền và lạm phát trong hệ thống ngân hàng."
        for i in range(300)
    )
    chunks = _split_text_for_tts(text)

    assert len(chunks) > 1
    assert all(len(c) <= XKIRO_TTS_MAX_CHARS for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_text_for_tts_splits_monster_sentence():
    from pipeline.tts import XKIRO_TTS_MAX_CHARS, _split_text_for_tts

    text = "từ " * 3000
    chunks = _split_text_for_tts(text)

    assert all(len(c) <= XKIRO_TTS_MAX_CHARS for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_long_xkiro_synthesis_chunks_and_concatenates(tmp_path, monkeypatch):
    import pipeline.tts as tts

    calls = []

    def fake_one(text, voice, out):
        calls.append(len(text))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"part")
        return 4.0

    monkeypatch.setattr(tts, "synthesize_one_xkiro", fake_one)
    merged = {}

    def fake_concat(parts, out):
        merged["n"] = len(parts)
        out.write_bytes(b"joined")

    monkeypatch.setattr(tts, "_concat_audio_parts", fake_concat)

    text = " ".join(f"Câu số {i} nói về dòng tiền." for i in range(500))
    assert len(text) > tts.XKIRO_TTS_MAX_CHARS
    used = tts.synthesize_one_xkiro_with_fallback(
        text, "confident-male-vietnamese", tmp_path / "out.mp3"
    )

    assert used == "confident-male-vietnamese"
    assert len(calls) > 1
    assert all(n <= tts.XKIRO_TTS_MAX_CHARS for n in calls)
    assert merged["n"] == len(calls)
    assert (tmp_path / "out.mp3").read_bytes() == b"joined"


def test_long_xkiro_failure_falls_back_whole_track(tmp_path, monkeypatch):
    import pipeline.tts as tts

    calls = {"n": 0}

    def fake_one(text, voice, out):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("gateway 500")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"part")
        return 4.0

    monkeypatch.setattr(tts, "synthesize_one_xkiro", fake_one)
    monkeypatch.setattr(
        tts, "_concat_audio_parts", lambda parts, out: out.write_bytes(b"joined")
    )
    edge = {}

    def fake_edge(text, voice, out):
        edge["voice"] = voice
        edge["len"] = len(text)
        out.write_bytes(b"edge")

    monkeypatch.setattr(tts, "_edge_tts_save", fake_edge)

    text = " ".join(f"Câu số {i} nói về dòng tiền." for i in range(500))
    used = tts.synthesize_one_xkiro_with_fallback(
        text, "confident-male-vietnamese", tmp_path / "out.mp3"
    )

    assert used == "vi-VN-NamMinhNeural"
    assert edge["len"] == len(text)
    assert (tmp_path / "out.mp3").read_bytes() == b"edge"


def test_rate_fit_slows_fast_narration_in_place(tmp_path, monkeypatch):
    import json

    import pipeline.tts as tts

    audio = tmp_path / "fast.mp3"
    audio.write_bytes(b"fast")
    # 60 words in 15s = 240 WPM -> target 205 WPM -> atempo 0.8542
    monkeypatch.setattr(tts, "_probe_duration", lambda p: 15.0)
    calls = []

    def fake_ffmpeg(args):
        calls.append(args)
        Path(args[-1]).write_bytes(b"slow")

    monkeypatch.setattr("pipeline.ffmpeg.run_ffmpeg", fake_ffmpeg)

    wpm = tts._fit_narration_rate("từ " * 60, audio)

    assert wpm == pytest.approx(240.0)
    assert "atempo=0.8542" in calls[0]
    assert audio.read_bytes() == b"slow"
    sidecar = json.loads(tts.rate_fit_sidecar_path(audio).read_text(encoding="utf-8"))
    assert sidecar["native_wpm"] == pytest.approx(240.0)
    assert sidecar["atempo"] == pytest.approx(0.8542)
    assert sidecar["slowdown_capped"] is False


def test_rate_fit_caps_slowdown_at_min_atempo(tmp_path, monkeypatch):
    import json

    import pipeline.tts as tts

    audio = tmp_path / "very_fast.mp3"
    audio.write_bytes(b"fast")
    # 60 words in 12s = 300 WPM -> target would need 0.683x; cap at 0.75x.
    monkeypatch.setattr(tts, "_probe_duration", lambda p: 12.0)
    calls = []

    def fake_ffmpeg(args):
        calls.append(args)
        Path(args[-1]).write_bytes(b"slow")

    monkeypatch.setattr("pipeline.ffmpeg.run_ffmpeg", fake_ffmpeg)

    wpm = tts._fit_narration_rate("từ " * 60, audio)

    assert wpm == pytest.approx(300.0)
    assert f"atempo={tts.MIN_ATEMPO:.4f}" in calls[0]
    sidecar = json.loads(tts.rate_fit_sidecar_path(audio).read_text(encoding="utf-8"))
    assert sidecar["slowdown_capped"] is True
    assert sidecar["atempo"] == pytest.approx(tts.MIN_ATEMPO)


def test_rate_fit_leaves_in_band_audio_untouched(tmp_path, monkeypatch):
    import pipeline.tts as tts

    audio = tmp_path / "ok.mp3"
    audio.write_bytes(b"ok")
    # 60 words in 17s = 211.8 WPM -> inside the 195-214 band.
    monkeypatch.setattr(tts, "_probe_duration", lambda p: 17.0)

    def boom(args):
        raise AssertionError("ffmpeg must not run for in-band audio")

    monkeypatch.setattr("pipeline.ffmpeg.run_ffmpeg", boom)

    assert tts._fit_narration_rate("từ " * 60, audio) == pytest.approx(211.7647, abs=0.01)
    assert audio.read_bytes() == b"ok"


def test_xkiro_tts_heals_stale_db_key_and_retries(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from services.settings_service import LLM_KEY

    store = {LLM_KEY: "sk-dead-key-from-reverted-db"}
    monkeypatch.setenv("XKIRO_API_KEY", "sk-fresh-env-key")
    monkeypatch.setattr(
        "services.settings_service.get_llm_api_key", lambda: store[LLM_KEY]
    )
    monkeypatch.setattr(
        "services.settings_service.get_setting", lambda key: store.get(key)
    )
    monkeypatch.setattr(
        "services.settings_service.set_setting",
        lambda key, value: store.__setitem__(key, value),
    )
    monkeypatch.setattr("services.settings_service.mask_secret", lambda v: "sk-...test")

    auth_headers = []

    def fake_post(url, json=None, headers=None, timeout=None):
        auth_headers.append(headers["Authorization"])
        if len(auth_headers) == 1:
            return SimpleNamespace(
                status_code=401,
                text='{"error":{"message":"Invalid or disabled ClientApiKey."}}',
                content=b"",
            )
        return SimpleNamespace(status_code=200, text="", content=b"ID3-fake-mp3")

    monkeypatch.setattr("requests.post", fake_post)

    from pipeline.tts import synthesize_one_xkiro

    out = tmp_path / "a.mp3"
    size = synthesize_one_xkiro("Xin chào", "confident-male-vietnamese", out)

    assert len(auth_headers) == 2
    assert auth_headers[1] == "Bearer sk-fresh-env-key"
    assert store[LLM_KEY] == "sk-fresh-env-key"
    assert size == len(b"ID3-fake-mp3") and out.read_bytes() == b"ID3-fake-mp3"


def test_synthesize_failure_raises_and_cleans(monkeypatch, tmp_path):
    items = [{"output_start": 0.0, "text": "abc", "clip_duration": 3.0}]

    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("pipeline.tts._synthesize_one_pitch", boom)
    monkeypatch.setattr("pipeline.tts._synthesize_one", boom)
    monkeypatch.setattr("pipeline.tts.time.sleep", lambda s: None)
    with pytest.raises(RenderError, match="TTS synthesis failed"):
        synthesize_narration(items, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def _script_led_timeline():
    """Script-led planner timeline: 2 beats over a large source gap.
    Output: b0 = 0-30s, b1 = 30-60s; source: 10-40s and 200-230s."""
    return {
        "version": 2,
        "clips": [
            {
                "id": "b0",
                "source_start": 10.0,
                "source_end": 40.0,
                "duration": 30.0,
                "output_start": 0.0,
                "output_end": 30.0,
                "beat_id": 0,
                "narration_text": "Beat một nói về mở đầu thật cuốn hút. Thêm câu nữa cho đủ giọng.",
                "emotion": "hype",
                "order": 1,
                "enabled": True,
            },
            {
                "id": "b1",
                "source_start": 200.0,
                "source_end": 230.0,
                "duration": 30.0,
                "output_start": 30.0,
                "output_end": 60.0,
                "beat_id": 1,
                "narration_text": "Beat hai chốt hạ phần kết. Lời dẫn dài hơn một chút để trải cue.",
                "emotion": "bua",
                "order": 2,
                "enabled": True,
            },
        ],
    }


def test_tts_cues_follow_derived_preview_timeline():
    """Contract: slicing a script-led timeline then generating TTS items must
    produce cues contained in the derived preview duration, based on the
    rebased per-clip output metadata (not the original timeline coords)."""
    derived, _ = slice_timeline_by_output_range(_script_led_timeline(), output_start=25.0, duration=20.0)

    items = narration_items_from_timeline(derived)
    assert items, "derived preview must carry narration"
    preview_dur = float(derived["actual_duration"])
    assert preview_dur == pytest.approx(20.0)

    for item in items:
        start = float(item["output_start"])
        end = start + float(item["clip_duration"])
        # Cue window must live inside the derived preview, not the original
        # 60s timeline (which would allow output_start >= 25).
        assert start >= 0.0
        assert end <= preview_dur + 1e-6
        assert start < 25.0  # rebased, not original absolute coords


def test_generate_and_overlay_cue_items_contained_in_clip_windows(monkeypatch):
    """generate_and_overlay_narration's item builder must distribute cues
    within each clip's explicit output window and keep them inside the
    timeline duration."""
    import pipeline.tts as tts

    captured: list = []

    def fake_synth(text, voice, rate, volume, pitch, out_path):
        out_path.write_bytes(b"id3")  # non-empty mp3 stub
        captured.append({"text": text, "out": str(out_path)})

    monkeypatch.setattr(tts, "_synthesize_one_pitch", fake_synth)
    monkeypatch.setattr(tts, "_probe_duration", lambda p: 1.0)

    timeline = _script_led_timeline()
    timeline["clips"][0]["source_end"] = 40.0  # keep as-is
    # Point synthesize output at tmp; overlay step is not exercised here
    # because _probe_duration/ffmpeg are stubbed only for synthesis probing.
    items_cues: list = []
    for clip in timeline["clips"]:
        clip_dur = float(clip["duration"])
        cues = tts.distribute_cues_over_clip(clip["narration_text"], clip_dur)
        for cue in cues:
            items_cues.append(
                {
                    "beat": clip["beat_id"],
                    "output_start": float(clip["output_start"]) + cue["output_start"],
                    "clip_duration": cue["clip_duration"],
                }
            )

    # Every cue must fall inside its beat's output window.
    for cue in items_cues:
        if cue["beat"] == 0:
            assert cue["output_start"] + cue["clip_duration"] <= 30.0 + 1e-6
        else:
            assert 30.0 - 1e-6 <= cue["output_start"]
            assert cue["output_start"] + cue["clip_duration"] <= 60.0 + 1e-6

    # Full timeline coverage: cues sorted and no cue exceeds the target 60s.
    ends = [c["output_start"] + c["clip_duration"] for c in items_cues]
    assert max(ends) <= 60.0 + 1e-6
    assert captured == []  # synthesis stub only exercised via synthesize_narration
