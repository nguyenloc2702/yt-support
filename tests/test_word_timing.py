"""Unit tests for word-level scene windows (pipeline.word_timing)."""

import pytest

from pipeline.word_timing import build_scene_windows, summarize_windows


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
