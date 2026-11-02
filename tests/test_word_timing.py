"""Unit tests for word-level scene windows (pipeline.word_timing)."""

from typing import Any, Dict, List

import pytest

from pipeline.word_timing import (
    MIN_PHYSICAL_DUR,
    build_scene_windows,
    split_overband_windows,
    summarize_windows,
)


def test_cut_lands_in_the_middle_of_the_pause():
    tokens = ["a", "b", "c", "d"]
    # Scene 1 speaks until 1.0s, scene 2 starts at 1.6s: the 0.6s pause is split.
    spans = [(0.0, 0.5), (0.5, 1.0), (1.6, 2.1), (2.1, 2.6)]
    timeline = build_scene_windows([(0, 2), (2, 4)], tokens, spans, 3.0)
    first, second = timeline["scenes"]
    assert first["end"] == pytest.approx(1.3)
    assert second["start"] == pytest.approx(1.3)
    assert first["speech_end"] == 1.0
    assert second["speech_start"] == 1.6


def test_windows_tile_the_whole_audio():
    tokens = ["a", "b", "c", "d"]
    spans = [(0.0, 0.5), (0.5, 1.0), (1.6, 2.1), (2.1, 2.6)]
    timeline = build_scene_windows([(0, 2), (2, 4)], tokens, spans, 3.2)
    windows = timeline["scenes"]
    assert windows[0]["start"] == 0.0
    assert windows[-1]["end"] == pytest.approx(3.2)
    assert timeline["total"] == pytest.approx(3.2)
    assert sum(w["duration"] for w in windows) == pytest.approx(3.2, abs=0.01)
    assert all(
        abs(left["end"] - right["start"]) < 0.001
        for left, right in zip(windows, windows[1:])
    )


def test_touching_scenes_cut_on_the_next_word_onset():
    tokens = ["a", "b", "c", "d"]
    spans = [(0.0, 0.4), (0.4, 0.9), (0.9, 1.4), (1.4, 1.9)]
    timeline = build_scene_windows([(0, 2), (2, 4)], tokens, spans, 1.9)
    # No pause to hide in: the new picture starts exactly on the next word.
    assert timeline["scenes"][0]["end"] == pytest.approx(0.9)


def test_words_carry_absolute_and_relative_timings():
    tokens = ["a", "b", "c"]
    spans = [(0.2, 0.6), (0.7, 1.1), (1.5, 1.9)]
    timeline = build_scene_windows([(0, 2), (2, 3)], tokens, spans, 2.4)
    words = timeline["scenes"][0]["words"]
    assert [w["w"] for w in words] == ["a", "b"]
    assert [round(w["rel"], 3) for w in words] == [0.2, 0.7]
    assert words[1]["start"] == pytest.approx(0.7)


def test_warnings_call_out_scenes_outside_the_shot_laws():
    windows = [
        {"index": 1, "duration": 2.5},
        {"index": 2, "duration": 5.0},
        {"index": 3, "duration": 9.5},
        {"index": 4, "duration": 12.0},
    ]
    text = "\n".join(summarize_windows(windows))
    assert "dưới ngưỡng vật lý" in text and "Cảnh 1" in text
    assert "trên ngưỡng vật lý" in text and "Cảnh 4" in text
    assert "ngoài band" in text and "Cảnh 3" in text and "Cảnh 2" not in text


def test_scene_without_spoken_words_owns_the_silence():
    tokens = ["a", "b", "c"]
    spans = [(0.0, 0.5), (1.2, 1.2), (2.0, 2.5)]
    timeline = build_scene_windows([(0, 1), (1, 2), (2, 3)], tokens, spans, 3.0)
    windows = timeline["scenes"]
    assert windows[1]["words"] == []
    assert windows[1]["duration"] > 0
    assert sum(w["duration"] for w in windows) == pytest.approx(3.0, abs=0.01)


def test_no_scenes_returns_empty_timeline():
    timeline = build_scene_windows([], [], [], 5.0)
    assert timeline["scenes"] == []
    assert timeline["total"] == 0.0


def _sentence_window(sentence_seconds: float) -> dict:
    """One scene window of ~`sentence_seconds` with evenly spoken words."""
    words: List[Dict[str, Any]] = []
    t = 0.0
    # 3.5 words/s of speech, with the requested total owned by the scene.
    while t < sentence_seconds - 0.28:
        words.append({"w": "từ", "start": round(t, 3), "end": round(t + 0.28, 3), "rel": 0.0})
        t += 0.28
    window = {
        "index": 7,
        "start": 0.0,
        "end": round(sentence_seconds, 3),
        "duration": round(sentence_seconds, 3),
        "speech_start": 0.0,
        "speech_end": round(t - 0.28 + 0.28, 3),
        "words": words,
    }
    return window


def test_mid_clause_splits_a_long_sentence_into_windows_inside_the_band():
    """P2: a 12s sentence must become >=2 windows, each >=3.0s, total intact."""
    window = _sentence_window(12.0)
    parts = split_overband_windows([window])

    assert len(parts) >= 2
    assert all(p["duration"] >= MIN_PHYSICAL_DUR - 0.01 for p in parts)
    assert sum(p["duration"] for p in parts) == pytest.approx(12.0, abs=0.05)
    # Parts tile the parent span and stay attached to its scene index.
    assert parts[0]["start"] == pytest.approx(0.0)
    assert parts[-1]["end"] == pytest.approx(12.0)
    assert {p["index"] for p in parts} == {7}
    assert [p["part"] for p in parts] == list(range(1, len(parts) + 1))
    # No word is lost or duplicated by the split.
    total_words = sum(len(p["words"]) for p in parts)
    assert total_words == len(window["words"])
    assert [w["start"] for w in parts[0]["words"]] == [
        w["start"] for w in window["words"][: len(parts[0]["words"])]
    ]


def test_mid_clause_prefers_the_comma_breath():
    """A cut lands after a clause separator before any other gap."""
    words = [
        {"w": "Năm", "start": 0.0, "end": 0.5, "rel": 0.0},
        {"w": "1602,", "start": 0.5, "end": 1.0, "rel": 0.5},
        {"w": "công", "start": 1.1, "end": 1.6, "rel": 1.1},
        {"w": "ty", "start": 1.6, "end": 2.1, "rel": 1.6},
        {"w": "đông", "start": 2.2, "end": 2.7, "rel": 2.2},
        {"w": "Ấn", "start": 2.7, "end": 3.2, "rel": 2.7},
        {"w": " Hà", "start": 3.2, "end": 3.7, "rel": 3.2},
        {"w": "Lan", "start": 3.7, "end": 4.2, "rel": 3.7},
        {"w": "huy", "start": 4.2, "end": 4.7, "rel": 4.2},
        {"w": "động", "start": 4.7, "end": 5.2, "rel": 4.7},
        {"w": "vốn", "start": 5.2, "end": 5.7, "rel": 5.2},
        {"w": "lớn,", "start": 5.7, "end": 6.2, "rel": 5.7},
        {"w": "và", "start": 6.3, "end": 6.8, "rel": 6.3},
        {"w": "thế", "start": 6.8, "end": 7.3, "rel": 6.8},
        {"w": "mà", "start": 7.3, "end": 7.8, "rel": 7.3},
        {"w": "cỗ", "start": 7.8, "end": 8.3, "rel": 7.8},
        {"w": "máy", "start": 8.3, "end": 8.8, "rel": 8.3},
        {"w": "vẫn", "start": 8.8, "end": 9.3, "rel": 8.8},
        {"w": "chạy", "start": 9.3, "end": 9.8, "rel": 9.3},
        {"w": "miệt", "start": 9.8, "end": 10.3, "rel": 9.8},
        {"w": "mài.", "start": 10.3, "end": 10.8, "rel": 10.3},
        {"w": "không", "start": 10.8, "end": 11.3, "rel": 10.8},
        {"w": "ai", "start": 11.3, "end": 11.8, "rel": 11.8},
        {"w": "hay.", "start": 11.8, "end": 12.3, "rel": 11.8},
    ]
    window = {
        "index": 3,
        "start": 0.0,
        "end": 12.5,
        "duration": 12.5,
        "speech_start": 0.0,
        "speech_end": 12.3,
        "words": words,
    }

    parts = split_overband_windows([window])

    assert len(parts) >= 2
    # At least one internal boundary must be a comma breath — the narrator's
    # own pause after "lớn," outranks the silent 0.1s word gaps around it.
    assert any(
        p["words"][-1]["w"].endswith(",") for p in parts[:-1]
    )
    assert all(p["duration"] >= MIN_PHYSICAL_DUR - 0.01 for p in parts)
    assert sum(p["duration"] for p in parts) == pytest.approx(12.5, abs=0.05)


def test_band_break_is_a_warning_never_an_error():
    """P2: the advisory band only ever produces warnings for Gate 2."""
    windows = [
        {"index": 1, "duration": 3.2},   # under the 3.5s band, above 3.0s floor
        {"index": 2, "duration": 7.0},   # over the 6.0s band, under 10.5s cap
    ]
    text = "\n".join(summarize_windows(windows))
    assert "ngoài band" in text and "Cảnh 1" in text and "Cảnh 2" in text
    assert "dưới ngưỡng vật lý" not in text
    assert "trên ngưỡng vật lý" not in text


def test_build_scene_windows_mid_clause_policy_flags_through():
    tokens = ["a", "b", "c", "d"]
    spans = [(0.0, 0.5), (0.5, 1.0), (1.6, 2.1), (2.1, 2.6)]
    timeline = build_scene_windows([(0, 2), (2, 4)], tokens, spans, 3.0, cut_policy="mid_clause")
    assert timeline["cut_policy"] == "mid_clause"
    # Short windows survive the policy untouched.
    assert len(timeline["scenes"]) == 2
