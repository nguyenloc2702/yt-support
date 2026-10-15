"""Tests for pipeline.subtitles: SRT + karaoke ASS generation."""
from pathlib import Path

from pipeline.subtitles import build_karaoke_ass, build_srt


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
                __import__("json").dumps(words), encoding="utf-8"
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
