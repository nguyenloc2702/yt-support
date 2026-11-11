"""Google Drive (rclone) Sync & Local Intermediate File Cleanup Service."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import RENDER_SPEC, data_dir

logger = logging.getLogger("storage_gdrive")


def get_gdrive_remote() -> str:
    """Return the configured rclone remote destination."""
    return os.environ.get(
        "VIDEO_GDRIVE_REMOTE",
        RENDER_SPEC.get("storage", {}).get("gdrive_remote", "gdrive:video_system_assets"),
    ).strip()


def is_gdrive_sync_disabled() -> bool:
    """Check if GDrive sync is explicitly disabled via environment variable."""
    return os.environ.get("VIDEO_DISABLE_GDRIVE_SYNC", "").strip().lower() in {"1", "true", "yes"}


import threading
import time

_RCLONE_CACHE: dict[str, Any] = {
    "key": None,
    "ts": 0.0,
    "reachable": False,
    "error": None,
    "refreshing": False,
}
_RCLONE_CACHE_TTL_SEC = 300.0


def _probe_rclone_remote(remote: str, cache_key: str) -> tuple[bool, str | None]:
    """Run a fast single-attempt rclone about probe and update _RCLONE_CACHE."""
    remote_reachable = False
    remote_error: str | None = None
    remote_name = remote.split(":")[0] if ":" in remote else remote
    try:
        res = subprocess.run(
            [
                "rclone",
                "about",
                f"{remote_name}:",
                "--contimeout",
                "2s",
                "--timeout",
                "2s",
                "--retries",
                "1",
                "--low-level-retries",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            remote_reachable = True
        else:
            err_text = (res.stderr or res.stdout or "").strip()
            if "invalid_grant" in err_text.lower() or "token expired" in err_text.lower():
                remote_error = "rclone OAuth token expired (invalid_grant)"
            else:
                remote_error = err_text[:200] if err_text else f"rclone returned code {res.returncode}"
    except subprocess.TimeoutExpired:
        remote_error = "rclone about command timed out after 2s"
    except Exception as exc:
        remote_error = str(exc)

    _RCLONE_CACHE["key"] = cache_key
    _RCLONE_CACHE["ts"] = time.monotonic()
    _RCLONE_CACHE["reachable"] = remote_reachable
    _RCLONE_CACHE["error"] = remote_error
    _RCLONE_CACHE["refreshing"] = False
    return remote_reachable, remote_error


def check_storage_health(force_refresh: bool = False) -> dict[str, Any]:
    """Check local disk usage and rclone GDrive remote status."""
    d = data_dir()
    try:
        total, used, free = shutil.disk_usage(d)
        total_gb = round(total / (1024**3), 2)
        used_gb = round(used / (1024**3), 2)
        free_gb = round(free / (1024**3), 2)
        pct_used = round((used / total) * 100, 1) if total > 0 else 0.0
    except Exception as exc:
        logger.warning("Failed to check disk usage: %s", exc)
        total_gb, used_gb, free_gb, pct_used = 0.0, 0.0, 0.0, 0.0

    rclone_path = shutil.which("rclone")
    rclone_installed = rclone_path is not None
    remote = get_gdrive_remote()
    storage_cfg = RENDER_SPEC.get("storage", {})

    remote_reachable = False
    remote_error: str | None = None

    if is_gdrive_sync_disabled():
        remote_reachable = False
        remote_error = "GDrive sync disabled via VIDEO_DISABLE_GDRIVE_SYNC"
    elif not rclone_installed:
        remote_reachable = False
        remote_error = "rclone binary not found in PATH"
    else:
        cache_key = f"{remote}|{rclone_path}"
        now_ts = time.monotonic()
        has_cache = _RCLONE_CACHE.get("key") == cache_key and float(_RCLONE_CACHE.get("ts") or 0.0) > 0.0
        cache_fresh = has_cache and (now_ts - float(_RCLONE_CACHE.get("ts") or 0.0)) < _RCLONE_CACHE_TTL_SEC

        if not force_refresh and cache_fresh:
            remote_reachable = bool(_RCLONE_CACHE["reachable"])
            remote_error = _RCLONE_CACHE["error"]
        elif not force_refresh:
            # Serve current/default status immediately (<2ms) and probe rclone in background thread
            remote_reachable = bool(_RCLONE_CACHE["reachable"])
            remote_error = _RCLONE_CACHE["error"] if has_cache else "Đang kiểm tra kết nối GDrive..."
            if not _RCLONE_CACHE.get("refreshing"):
                _RCLONE_CACHE["refreshing"] = True
                threading.Thread(
                    target=_probe_rclone_remote,
                    args=(remote, cache_key),
                    daemon=True,
                ).start()
        else:
            remote_reachable, remote_error = _probe_rclone_remote(remote, cache_key)

    return {
        "disk": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent_used": pct_used,
            "data_dir": str(d),
        },
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "percent_used": pct_used,
        "rclone_installed": rclone_installed,
        "gdrive_remote": remote,
        "remote_reachable": remote_reachable,
        "remote_error": remote_error,
        "auto_sync_on_assemble": bool(storage_cfg.get("auto_sync_on_assemble", True)),
        "purge_intermediates_on_assemble": bool(storage_cfg.get("purge_intermediates_on_assemble", True)),
    }


def cleanup_item_intermediates(
    item_id: str,
    remove_uploaded_clips: bool = False,
    purge_raw_clips: bool = False,
) -> dict[str, Any]:
    """Clean up temporary files and optionally purge raw uploaded video clips to free VPS disk space.

    - Always cleans temporary directories: exports/_tmp_norm, todo/_tmp_shots, todo/_tmp_retime, ingest,
      and temporary files: *.tmp.*, *.voice.wav, *.tmp.
    - If purge_raw_clips=True, or (remove_uploaded_clips=True AND final_en.mp4 exists):
      deletes videos/seq_*.mp4 files while preserving exports/final_en.mp4,
      todo/audio_en.wav, todo/script_en.json, todo/subtitles_en.*, and images/.
    """
    item_dir = data_dir() / "objects" / item_id
    if not item_dir.exists():
        return {"freed_bytes": 0, "removed_paths": []}

    removed_paths: list[str] = []
    freed_bytes = 0

    # 1. Temporary directories to always delete
    temp_dirs = [
        item_dir / "exports" / "_tmp_norm",
        item_dir / "todo" / "_tmp_shots",
        item_dir / "todo" / "_tmp_retime",
        item_dir / "ingest",
    ]
    now_ts = time.time()
    for td in temp_dirs:
        if td.exists() and td.is_dir():
            # Safeguard: never delete _tmp_norm if it was created/modified recently (< 300s)
            # to prevent race conditions during active video assembly.
            if td.name == "_tmp_norm":
                try:
                    mtime = td.stat().st_mtime
                    if now_ts - mtime < 300.0:
                        continue
                except OSError:
                    pass
            for p in td.glob("**/*"):
                if p.is_file():
                    try:
                        sz = p.stat().st_size
                        freed_bytes += sz
                    except OSError:
                        pass
            shutil.rmtree(td, ignore_errors=True)
            removed_paths.append(str(td))

    # 2. Temporary files to always delete (*.tmp.*, *.voice.wav, *.tmp)
    for p in item_dir.rglob("*"):
        if p.is_file():
            name_lower = p.name.lower()
            if ".tmp." in name_lower or name_lower.endswith(".voice.wav") or name_lower.endswith(".tmp"):
                try:
                    sz = p.stat().st_size
                    p.unlink(missing_ok=True)
                    freed_bytes += sz
                    removed_paths.append(str(p))
                except OSError as exc:
                    logger.warning("Could not delete temp file %s: %s", p, exc)

    # 3. Purge raw uploaded clips if explicitly requested or if final_en.mp4 exists
    final_video = item_dir / "exports" / "final_en.mp4"
    can_purge_clips = purge_raw_clips or (remove_uploaded_clips and final_video.exists())
    if can_purge_clips:
        videos_dir = item_dir / "videos"
        if videos_dir.exists() and videos_dir.is_dir():
            for clip in videos_dir.glob("seq_*.mp4"):
                if clip.is_file():
                    try:
                        sz = clip.stat().st_size
                        clip.unlink(missing_ok=True)
                        freed_bytes += sz
                        removed_paths.append(str(clip))
                    except OSError as exc:
                        logger.warning("Could not delete clip %s: %s", clip, exc)

    return {
        "freed_bytes": freed_bytes,
        "removed_paths": removed_paths,
    }


def sync_item_to_gdrive(
    item_id: str,
    purge_raw_clips_after_sync: bool = False,
) -> dict[str, Any]:
    """Sync item package to Google Drive using rclone copy.

    Never crashes assembly on sync failures (handles missing rclone, expired tokens,
    or disabled sync gracefully).
    """
    if is_gdrive_sync_disabled():
        logger.info("GDrive sync skipped for %s (disabled via VIDEO_DISABLE_GDRIVE_SYNC)", item_id)
        return {
            "status": "skipped",
            "remote_path": None,
            "detail": "GDrive sync disabled via VIDEO_DISABLE_GDRIVE_SYNC",
            "freed_bytes": 0,
        }

    item_dir = data_dir() / "objects" / item_id
    if not item_dir.exists():
        return {
            "status": "error",
            "remote_path": None,
            "detail": f"Item directory does not exist: {item_dir}",
            "freed_bytes": 0,
        }

    if shutil.which("rclone") is None:
        logger.info("GDrive sync skipped for %s (rclone not installed)", item_id)
        return {
            "status": "skipped",
            "remote_path": None,
            "detail": "rclone binary not found in system PATH",
            "freed_bytes": 0,
        }

    remote = get_gdrive_remote()
    remote_item_path = f"{remote.rstrip('/')}/{item_id}"

    cmd = [
        "rclone",
        "copy",
        str(item_dir),
        remote_item_path,
        "--exclude",
        "**/_tmp_norm/**",
        "--exclude",
        "**/_tmp_shots/**",
        "--exclude",
        "**/*.tmp.*",
        "--exclude",
        "**/*.voice.wav",
        "-v",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode == 0:
            freed = 0
            if purge_raw_clips_after_sync:
                clean_res = cleanup_item_intermediates(
                    item_id, remove_uploaded_clips=True, purge_raw_clips=True
                )
                freed = clean_res.get("freed_bytes", 0)

            logger.info("Synced item %s to %s (freed %d bytes)", item_id, remote_item_path, freed)
            return {
                "status": "synced",
                "remote_path": remote_item_path,
                "detail": f"Successfully copied item {item_id} to {remote_item_path}",
                "freed_bytes": freed,
            }
        else:
            err_text = (proc.stderr or proc.stdout or "").strip()
            if "invalid_grant" in err_text.lower() or "token expired" in err_text.lower():
                status = "auth_expired"
                detail = "Google Drive rclone token expired (invalid_grant). Re-run 'rclone config reconnect'."
            else:
                status = "error"
                detail = err_text[:300] if err_text else f"rclone returned exit code {proc.returncode}"
            logger.warning("GDrive sync failed for %s: %s", item_id, detail)
            return {
                "status": status,
                "remote_path": remote_item_path,
                "detail": detail,
                "freed_bytes": 0,
            }
    except subprocess.TimeoutExpired:
        logger.warning("GDrive sync timed out for %s", item_id)
        return {
            "status": "error",
            "remote_path": remote_item_path,
            "detail": "rclone copy timed out after 180s",
            "freed_bytes": 0,
        }
    except Exception as exc:
        logger.warning("GDrive sync exception for %s: %s", item_id, exc)
        return {
            "status": "error",
            "remote_path": remote_item_path,
            "detail": str(exc),
            "freed_bytes": 0,
        }
