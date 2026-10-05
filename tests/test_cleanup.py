"""Tests for utils.cleanup housekeeping helpers."""

import os
import time
from pathlib import Path

from utils.cleanup import (
    cleanup_analysis_artifacts,
    cleanup_render_temp,
    cleanup_stale_previews,
    cleanup_stale_projects,
)


def _make_proj(tmp_path: Path, name: str = "p1") -> Path:
    proj = tmp_path / name
    proj.mkdir(parents=True)
    return proj


def test_cleanup_analysis_deletes_wav_only_when_transcript_exists(tmp_path):
    proj = _make_proj(tmp_path)
    audio = proj / "audio"
    audio.mkdir()
    wav = audio / "speech.wav"
    wav.write_bytes(b"x" * 10)

    # No transcript yet → keep WAV
    assert cleanup_analysis_artifacts(proj) == 0
    assert wav.exists()

    # Transcript appears → WAV removed
    analysis = proj / "analysis"
    analysis.mkdir()
    (analysis / "transcription.json").write_text("{}", encoding="utf-8")
    assert cleanup_analysis_artifacts(proj) == 1
    assert not wav.exists()

    # Idempotent
    assert cleanup_analysis_artifacts(proj) == 0


def test_cleanup_render_temp(tmp_path):
    proj = _make_proj(tmp_path)
    temp_dir = proj / "previews" / "temp_render_abc"
    temp_dir.mkdir(parents=True)
    (temp_dir / "clip.mp4").write_bytes(b"x")
    keep_file = proj / "previews" / "preview_v1.mp4"
    keep_file.write_bytes(b"y")

    assert cleanup_render_temp(proj) == 1
    assert not temp_dir.exists()
    assert keep_file.exists()
    assert cleanup_render_temp(proj) == 0


def test_cleanup_stale_previews_keeps_newest(tmp_path):
    proj = _make_proj(tmp_path)
    previews = proj / "previews"
    previews.mkdir()
    for i in range(5):
        f = previews / f"preview_v{i}.mp4"
        f.write_bytes(b"x")
        os.utime(f, (time.time() - 10_000 * (5 - i),) * 2)  # increasing mtime

    # keep=3 but all files fresh (max_age high) → nothing removed
    assert cleanup_stale_previews(proj, keep=3, max_age_hours=1000) == 0

    # short max_age → the 2 oldest removed
    removed = cleanup_stale_previews(proj, keep=3, max_age_hours=0.1)
    assert removed == 2
    remaining = sorted(p.name for p in previews.glob("*.mp4"))
    assert remaining == ["preview_v2.mp4", "preview_v3.mp4", "preview_v4.mp4"]


def test_cleanup_stale_previews_ignores_partial(tmp_path):
    proj = _make_proj(tmp_path)
    previews = proj / "previews"
    previews.mkdir()
    partial = previews / "x.partial.mp4"
    partial.write_bytes(b"x")
    assert cleanup_stale_previews(proj, keep=0, max_age_hours=0.1) == 0
    assert partial.exists()


def test_cleanup_stale_projects_finds_old_dirs(tmp_path):
    old = _make_proj(tmp_path, "old")
    new = _make_proj(tmp_path, "new")
    very_old = time.time() - 60 * 86400
    os.utime(old, (very_old, very_old))

    stale = list(cleanup_stale_projects(tmp_path, max_age_days=30))
    assert old in stale
    assert new not in stale
