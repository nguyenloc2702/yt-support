"""Tests for pipeline.subtitles: SRT + karaoke ASS generation."""
import json
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from pipeline.subtitles import (
    MAX_LINE_WORDS,
    build_ass_from_word_timeline,
    build_karaoke_ass,
    build_srt,
    caption_coverage,
    caption_intervals,
)


def _items(tmp_path: Path, with_words: bool):
    items = []
    for i, (text, start) in enumerate(
        [("Xin chào thế giới.", 0.0), ("Hết rồi nhé.", 3.0)]
    ):
        it = {
            "output_start": start,
            "raw_duration": 2.0,
            "text": text,
            "tempo": 1.0,
            "audio_path": "",
        }
        if with_words:
            p = tmp_path / f"w{i}.mp3"
            p.write_bytes(b"x")
            words = [
                {"offset": 0.0 + j * 0.5, "duration": 0.4, "text": w}
                for j, w in enumerate(text.split())
            ]
            p.with_suffix(".words.json").write_text(
                json.dumps(words), encoding="utf-8"
            )
            it["audio_path"] = str(p)
        items.append(it)
    return items


def test_build_srt_basic(tmp_path):
    out = build_srt(_items(tmp_path, with_words=False), tmp_path / "a.srt")
    content = out.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,000" in content
    assert "Xin chào thế giới." in content
    assert "Hết rồi nhé." in content


def test_karaoke_ass_with_word_timing(tmp_path):
    out = build_karaoke_ass(_items(tmp_path, with_words=True), tmp_path / "a.ass")
    content = out.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "\\k" in content  # karaoke tags present
    assert "Xin" in content


def test_karaoke_ass_falls_back_to_plain_text(tmp_path):
    out = build_karaoke_ass(_items(tmp_path, with_words=False), tmp_path / "a.ass")
    content = out.read_text(encoding="utf-8")
    lines = [l for l in content.splitlines() if l.startswith("Dialogue")]
    assert lines and "\\k" not in lines[0]
    assert "Xin chào thế giới." in lines[0]


def test_karaoke_scales_by_tempo(tmp_path):
    items = _items(tmp_path, with_words=True)
    for it in items:
        it["tempo"] = 2.0
    out = build_karaoke_ass(items, tmp_path / "a.ass")
    content = out.read_text(encoding="utf-8")
    # tempo 2 halves word durations: 0.4s -> 20cs
    assert "{\\k20}" in content


# ------------------------------------------------- word-timeline captions


def _steady(words, start=0.0, step=0.30, length=0.24):
    """Timed words at a constant cadence, as the audio-first timing produces."""
    return [{"w": w, "start": start + i * step, "end": start + i * step + length}
            for i, w in enumerate(words)]


def _timeline(scenes, total_sec=20.0):
    return {"language": "en", "total_sec": total_sec, "scenes": scenes}


def _cue_texts(ass_path: Path):
    """The visible words of every Dialogue line, tags stripped."""
    lines = [l for l in ass_path.read_text(encoding="utf-8").splitlines()
             if l.startswith("Dialogue:")]
    out = []
    for line in lines:
        text = line.split(",", 9)[9]
        out.append(re.sub(r"\{\\[^}]*\}", " ", text).split())
    return out


def _burn(ass_path: Path, out_clip: Path, seconds: int = 5) -> Path:
    """Render the cue file over a black 1080p frame, the way the assembler does."""
    escaped = str(Path(ass_path).resolve()).replace("\\", "/").replace(":", "\\:")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:r=30:d={seconds}",
        "-vf", f"subtitles=filename='{escaped}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_clip),
    ], check=True, capture_output=True)
    return out_clip


def _gray_frame(clip_path: Path, at_s: float) -> np.ndarray:
    raw = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", str(at_s), "-i", str(clip_path),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "bmp", "-",
    ], capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def test_long_scene_splits_into_short_captions(tmp_path: Path):
    timeline = _timeline([{"index": 1, "words": _steady(list("abcdefghijklm"))}])
    out = build_ass_from_word_timeline(timeline, tmp_path / "a.ass")
    cues = _cue_texts(out)
    assert len(cues) == 2                       # 7 words + 6 words
    assert all(len(c) <= MAX_LINE_WORDS for c in cues)
    assert cues[0] == list("abcdefg") and cues[1] == list("hijklm")


def test_a_pause_starts_the_next_caption(tmp_path: Path):
    words = (_steady(["one", "two", "three"])
             + [{"w": w, "start": 1.8 + i * 0.3, "end": 1.8 + i * 0.3 + 0.24}
                for i, w in enumerate(["four", "five", "six"])])
    out = build_ass_from_word_timeline(_timeline([{"index": 1, "words": words}], 6.0),
                                       tmp_path / "a.ass")
    cues = caption_intervals(out)
    assert len(cues) == 2
    assert cues[0][1] == 0.84 and cues[1][0] == 1.8   # the breath stays blank


def test_a_short_gap_keeps_the_caption_on_screen(tmp_path: Path):
    """Holding through a half-second breath is what keeps coverage above 0.85."""
    words = (_steady(["one", "two", "three"])
             + [{"w": w, "start": 1.34 + i * 0.3, "end": 1.34 + i * 0.3 + 0.24}
                for i, w in enumerate(["four", "five", "six"])])
    out = build_ass_from_word_timeline(_timeline([{"index": 1, "words": words}], 3.0),
                                       tmp_path / "a.ass")
    cues = caption_intervals(out)
    assert len(cues) == 2
    assert cues[0][1] == cues[1][0] == pytest.approx(1.34)
    assert caption_coverage(out, 2.18)["caption_share"] == pytest.approx(1.0)


def test_numbers_keep_the_highlight(tmp_path: Path):
    words = _steady(["a", "billion", "dollar", "deal", "percent", "10", "quiet"])
    out = build_ass_from_word_timeline(_timeline([{"index": 1, "words": words}], 4.0),
                                       tmp_path / "a.ass")
    text = out.read_text(encoding="utf-8")
    for word in ("billion", "dollar", "percent", "10"):
        assert "\\c&H0000A5FF&}" + word + "{\\r}" in text
    assert "{\\k24}quiet{\\r}" not in text      # ordinary words are not recoloured


def test_excluded_scenes_carry_no_captions(tmp_path: Path):
    timeline = _timeline([
        {"index": 1, "words": _steady(["alpha", "beta"])},
        {"index": 2, "words": _steady(["gamma", "delta"], start=4.0)},
    ], 8.0)
    out = build_ass_from_word_timeline(timeline, tmp_path / "a.ass", exclude_scene_indexes=[2])
    text = out.read_text(encoding="utf-8")
    assert "alpha" in text and "gamma" not in text


def test_cues_are_ordered_and_never_overlap(tmp_path: Path):
    timeline = _timeline([
        {"index": 1, "words": _steady(list("abcdefghij"))},
        {"index": 2, "words": _steady(list("klmnopqrst"), start=8.0)},
    ], 12.0)
    out = build_ass_from_word_timeline(timeline, tmp_path / "a.ass")
    cues = caption_intervals(out)
    assert len(cues) == 4
    assert all(b[0] >= a[1] - 1e-6 for a, b in zip(cues, cues[1:]))


def test_rendered_captions_stay_inside_the_margins(tmp_path: Path):
    """libass must wrap a deliberately long cue without touching the frame edge."""
    margin = 90
    words = _steady(["international", "collaboration", "unprecedented", "infrastructure",
                     "straightforward", "accountability", "characteristics", "environmentally",
                     "pharmaceuticals", "misunderstanding", "intergovernmental",
                     "commercialisation"], step=0.45, length=0.5)
    timeline = _timeline([{"index": 1, "words": words}], 6.0)
    assert len(words) == 12                          # the plan asks for a 12-word cue
    ass = build_ass_from_word_timeline(timeline, tmp_path / "wide.ass",
                                       margin_lr=margin, max_line_words=len(words),
                                       max_line_seconds=6.0)
    clip = _burn(ass, tmp_path / "wide.mp4")
    img = _gray_frame(clip, 1.0)

    ink_columns = np.where(img.max(axis=0) > 60)[0]
    ink_rows = np.where(img.max(axis=1) > 60)[0]
    assert len(ink_columns) > 100, "no caption pixels rendered"
    assert ink_columns.min() >= margin - 6 and ink_columns.max() <= 1920 - margin + 6
    assert ink_rows.min() > 1080 * 0.5, "caption must sit in the lower part of the frame"
