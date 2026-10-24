"""Unit tests for the deterministic narration audit (pipeline.audio_audit)."""

import json
from pathlib import Path

import pytest

import pipeline.audio_audit as audit_mod
from pipeline.audio_audit import (
    AudioAuditResult,
    audit_audio,
    load_audio_audit,
    normalize_tokens,
    save_audio_audit,
)

WORD_DUR = 0.29
SCENE_GAP = 0.4


def _scene_text(index: int, filler: int = 8) -> str:
    return f"cảnh số {index} " + " ".join(["nội dung"] * filler)


def _plan(n=2, duration=5.51):
    return [
        {"index": i + 1, "text": _scene_text(i + 1), "duration": duration}
        for i in range(n)
    ]


def _fake_timings(texts, word_dur=WORD_DUR, scene_gap=SCENE_GAP):
    words = []
    t = 0.0
    for si, text in enumerate(texts):
        if si:
            t += scene_gap
        for token in text.split():
            words.append({"word": token, "start": round(t, 3), "end": round(t + word_dur, 3)})
            t += word_dur
    return words, round(t, 3)


def _transcriber(words, duration):
    def _fn(audio_path, language):
        return words, duration
    return _fn


@pytest.fixture(autouse=True)
def _no_ffprobe(monkeypatch):
    monkeypatch.setattr(audit_mod, "_probe_audio_duration", lambda p: 0.0)


def _audio(tmp_path: Path) -> Path:
    p = tmp_path / "narration_vi.mp3"
    p.write_bytes(b"RIFF" + b"\x00" * 64)
    return p


def test_normalize_tokens_masks_numbers_and_punctuation():
    assert normalize_tokens("Năm 1602, vốn 6.5 triệu!") == ["năm", "<num>", "vốn", "<num>", "<num>", "triệu"]


def test_clean_audio_matches_plan(tmp_path):
    plan = _plan()
    words, duration = _fake_timings([sc["text"] for sc in plan])
    result = audit_audio(
        _audio(tmp_path), plan, "vi",
        transcriber=_transcriber(words, duration),
    )

    assert result.wer == 0.0
    assert result.word_accuracy == 1.0
    assert len(result.scenes) == 2
    for sc in result.scenes:
        assert sc.missing is False
        assert abs(sc.drift) < 0.02
    assert 195 <= result.local_wpm_mean <= 214
    assert result.pause_count == 1
    assert result.pause_histogram["0.30-0.60"] == 1
    # Scene 1 holds through the 0.4s inter-scene pause (audio-first timing).
    assert [t["duration_sec"] for t in result.timing_map] == [5.91, 5.51]
    assert sum(t["hold_sec"] for t in result.timing_map) == pytest.approx(duration, abs=0.02)
    assert result.errors == []
    assert not any("WER" in w for w in result.warnings)


def test_word_error_rate_detects_substitution(tmp_path):
    plan = _plan(n=1)
    words, duration = _fake_timings([plan[0]["text"]])
    words[3]["word"] = "sai"
    result = audit_audio(
        _audio(tmp_path), plan, "vi",
        transcriber=_transcriber(words, duration),
    )
    assert 0.0 < result.wer <= 0.1
    assert result.word_accuracy < 1.0
    assert result.matched_words == len(plan[0]["text"].split()) - 1


def test_missing_scene_is_flagged(tmp_path):
    plan = _plan(n=2)
    words, duration = _fake_timings([plan[0]["text"]])
    result = audit_audio(
        _audio(tmp_path), plan, "vi",
        transcriber=_transcriber(words, duration),
    )
    assert result.scenes[0].missing is False
    assert result.scenes[1].missing is True
    assert any("không khớp" in w for w in result.warnings)
    assert result.wer >= 0.4


def test_total_drift_and_timing_map_clamp(tmp_path):
    plan = _plan(n=2, duration=5.2)
    words, _ = _fake_timings([plan[0]["text"]], word_dur=0.5)
    result = audit_audio(
        _audio(tmp_path), plan, "vi",
        transcriber=_transcriber(words, 100.0),
    )
    # 36 words * 0.5 = 18s vs 10.4s plan -> drift beyond the error threshold
    assert result.duration_drift > 0.25
    assert any("lệch plan" in e for e in result.errors)
    assert all(4.0 <= t["duration_sec"] <= 8.0 for t in result.timing_map)


def test_rate_fit_sidecar_drives_naturalness(tmp_path):
    audio = _audio(tmp_path)
    plan = _plan(n=1)
    words, duration = _fake_timings([plan[0]["text"]])
    sidecar = audio.with_name(f"{audio.name}.rate_fit.json")
    sidecar.write_text(
        json.dumps({"atempo": 0.70, "native_wpm": 295.0, "fitted_wpm": 206.5,
                    "slowdown_capped": True, "min_atempo": 0.75}),
        encoding="utf-8",
    )
    result = audit_audio(audio, plan, "vi", transcriber=_transcriber(words, duration))
    assert result.atempo == 0.70
    assert result.naturalness["atempo_ok"] is False
    assert result.naturalness["slowdown_capped"] is True
    assert any("dưới ngưỡng tự nhiên" in e for e in result.errors)

    sidecar.write_text(
        json.dumps({"atempo": 0.92, "native_wpm": 223.0, "fitted_wpm": 205.2,
                    "slowdown_capped": False, "min_atempo": 0.75}),
        encoding="utf-8",
    )
    result2 = audit_audio(audio, plan, "vi", transcriber=_transcriber(words, duration))
    assert result2.naturalness["atempo_ok"] is True
    assert result2.errors == []


def test_naturalness_grades_delivery_rate_not_articulation(tmp_path):
    """T11 must read the rate the listener hears, not speech inside each scene.

    Live case: fitted 206.1 WPM delivery, per-scene articulation 221.7, and the
    check failed the audio the rate-fit had just brought into band.
    """
    plan = _plan(n=20, duration=5.51)
    words, duration = _fake_timings(
        [sc["text"] for sc in plan], word_dur=0.27, scene_gap=0.25
    )
    result = audit_audio(
        _audio(tmp_path), plan, "vi",
        transcriber=_transcriber(words, duration),
    )

    assert result.local_wpm_mean > 214.0  # articulation alone is out of band
    assert 195.0 <= result.global_wpm <= 214.0
    assert result.naturalness["measured_wpm"] == result.global_wpm
    assert result.naturalness["articulation_wpm"] == result.local_wpm_mean
    assert result.naturalness["wpm_in_band"] is True


def test_missing_audio_returns_error_without_raising(tmp_path):
    result = audit_audio(tmp_path / "nope.mp3", _plan(), "vi",
                         transcriber=_transcriber([], 0.0))
    assert isinstance(result, AudioAuditResult)
    assert result.errors
    assert result.wer == 1.0
    assert result.scenes == []


def test_transcriber_failure_is_reported(tmp_path):
    def boom(audio_path, language):
        raise RuntimeError("model exploded")

    result = audit_audio(_audio(tmp_path), _plan(), "vi", transcriber=boom)
    assert any("Transcribe thất bại" in e for e in result.errors)
    assert result.wer == 1.0


def test_save_and_load_roundtrip(tmp_path):
    plan = _plan()
    words, duration = _fake_timings([sc["text"] for sc in plan])
    result = audit_audio(_audio(tmp_path), plan, "vi",
                         transcriber=_transcriber(words, duration))
    out = save_audio_audit(result, tmp_path / "audio_sync_vi.json")
    loaded = load_audio_audit(out)
    assert loaded is not None
    assert loaded.language == "vi"
    assert loaded.wer == result.wer
    assert len(loaded.scenes) == len(result.scenes)
    assert loaded.scenes[1].duration_sec == result.scenes[1].duration_sec
    assert loaded.timing_map == result.timing_map
    assert load_audio_audit(tmp_path / "missing.json") is None


def test_wpm_band_is_language_aware(tmp_path):
    # Same 160-WPM articulation: out of band for VI, natural for EN.
    plan = _plan(n=1)
    words, duration = _fake_timings([plan[0]["text"]], word_dur=0.375)
    audio = _audio(tmp_path)

    vi = audit_audio(audio, plan, "vi", transcriber=_transcriber(words, duration))
    en = audit_audio(audio, plan, "en", transcriber=_transcriber(words, duration))

    assert any("lệch xa band" in e for e in vi.errors)
    assert not any("band" in e for e in en.errors)
    assert not any("band" in w for w in en.warnings)
    assert en.naturalness["band_wpm"] == [145.0, 175.0]
    assert en.naturalness["wpm_in_band"] is True
    assert vi.naturalness["band_wpm"] == [195.0, 214.0]
