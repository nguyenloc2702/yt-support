"""
Housekeeping helpers that keep runtime artifacts from filling the disk.

All helpers are safe to call repeatedly: missing paths are skipped and
failures are swallowed (cleanup is best-effort, never allowed to break the
main pipeline). These routines matter most on a small VPS where source +
proxy + WAV + preview + temp clips for a single project can easily exceed
several GB.
"""

import shutil
import time
from pathlib import Path
from typing import Iterable

from core.logging_config import get_logger

logger = get_logger(__name__)

# Speech WAV is re-extractable from source; once the transcript JSON exists
# there is no reason to keep hundreds of MB of uncompressed audio around.
_DELETABLE_AFTER_ANALYSIS = ("speech.wav", "speech_resume.wav")
# Renderer scratch directories follow this prefix.
_TEMP_RENDER_PREFIX = "temp_render_"


def _safe_unlink(path: Path) -> bool:
    try:
        if path.exists():
            path.unlink()
            logger.info("Cleanup: removed %s", path)
            return True
    except OSError as exc:
        logger.warning("Cleanup: could not remove %s: %s", path, exc)
    return False


def cleanup_analysis_artifacts(proj_dir: Path) -> int:
    """
    Delete re-extractable intermediate audio once transcript JSON exists.
    Returns number of files removed.
    """
    transcript = proj_dir / "analysis" / "transcription.json"
    if not transcript.exists():
        return 0
    removed = 0
    audio_dir = proj_dir / "audio"
    if audio_dir.is_dir():
        for name in _DELETABLE_AFTER_ANALYSIS:
            if _safe_unlink(audio_dir / name):
                removed += 1
    return removed


def cleanup_render_temp(proj_dir: Path) -> int:
    """
    Remove renderer scratch directories (per-clip encoded files).
    Returns number of directories removed.
    """
    removed = 0
    for sub in ("previews", "exports"):
        base = proj_dir / sub
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith(_TEMP_RENDER_PREFIX):
                try:
                    shutil.rmtree(child, ignore_errors=True)
                    logger.info("Cleanup: removed temp render dir %s", child)
                    removed += 1
                except OSError as exc:
                    logger.warning("Cleanup: rmtree failed for %s: %s", child, exc)
    return removed


def cleanup_stale_previews(proj_dir: Path, keep: int = 3, max_age_hours: float = 72.0) -> int:
    """
    Delete preview files beyond the newest `keep` ones, or older than
    `max_age_hours`. Returns number of files removed.
    """
    previews = proj_dir / "previews"
    if not previews.is_dir():
        return 0
    files = [
        p
        for p in previews.glob("*.mp4")
        if p.is_file() and not p.name.endswith(".partial.mp4")
    ]
    if len(files) <= keep:
        return 0
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for old in files[keep:]:
        try:
            if old.stat().st_mtime < cutoff:
                if _safe_unlink(old):
                    removed += 1
        except OSError:
            continue
    return removed


def cleanup_stale_projects(projects_dir: Path, max_age_days: float = 30.0) -> Iterable[Path]:
    """
    Yield project directories untouched for longer than `max_age_days`.
    Does NOT delete automatically - callers decide (delete_project() already
    wipes rows and files atomically, so reuse it for actual removal).
    """
    cutoff = time.time() - max_age_days * 86400
    if not projects_dir.is_dir():
        return
    for proj in projects_dir.iterdir():
        if not proj.is_dir():
            continue
        try:
            if proj.stat().st_mtime < cutoff:
                yield proj
        except OSError:
            continue
