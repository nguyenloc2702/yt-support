"""Google Drive Storage Uploader via rclone.

Uploads rendered raw shots directly to Google Drive, then deletes
the local temporary video to conserve VPS disk space (Zero Disk Waste).
"""

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Optional, Tuple

logger = logging.getLogger("storage_uploader")

DEFAULT_RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
DEFAULT_REMOTE_FOLDER = os.environ.get(
    "RCLONE_REMOTE_FOLDER", "Video_Projects/history_of_microsoft/raw_shots"
)

VERIFY_DELAYS = (2.0, 5.0, 15.0, 30.0)


class StorageUploader:
    def __init__(self, remote_name: str = DEFAULT_RCLONE_REMOTE, remote_folder: str = DEFAULT_REMOTE_FOLDER):
        self.remote_name = remote_name
        self.remote_folder = remote_folder
        self.rclone_bin = shutil.which("rclone")
        self._last_probe_error = "missing"

    @property
    def is_rclone_available(self) -> bool:
        return self.rclone_bin is not None

    def _remote_size(self, remote_path: str, timeout: int = 30) -> Optional[int]:
        """Size of one remote object, or None when it is absent/unreadable.

        Exact-path lookups on the Drive remote retry a not-found with
        exponential backoff, so `lsf <file>` hangs past the timeout and the
        upload never reaches `copyto`. `size --json` with capped retries
        surfaces not-found in seconds.
        """
        cmd = [
            self.rclone_bin,
            "size",
            remote_path,
            "--json",
            "--retries", "1",
            "--low-level-retries", "1",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("rclone size timed out after %ds for %s", timeout, remote_path)
            return None
        if res.returncode != 0:
            stderr = (getattr(res, "stderr", "") or "").lower()
            self._last_probe_error = (
                "rate_limited"
                if "quota" in stderr or "ratelimitexceeded" in stderr
                else "missing"
            )
            return None
        try:
            payload = json.loads((res.stdout or "").strip() or "{}")
            count = int(payload.get("count") or 0)
            size = int(payload.get("bytes") or 0)
        except (TypeError, ValueError):
            return None
        return size if count == 1 else None

    def _await_remote_size(self, remote_path: str) -> Optional[int]:
        """Confirm the object landed, tolerating Drive's 403 quota bursts.

        rclone resolves the remote root on every call, so a throttled Drive API
        answers like a missing object. Back off instead of declaring failure.
        """
        for delay in (0.0,) + VERIFY_DELAYS:
            if delay:
                time.sleep(delay)
            size = self._remote_size(remote_path)
            if size is not None:
                if delay:
                    logger.info("Remote object confirmed after %.0fs of retries: %s", delay, remote_path)
                return size
        return None

    def upload_and_clean(
        self,
        local_path: Path,
        remote_filename: Optional[str] = None,
        remote_folder: Optional[str] = None,
        clean_local: bool = True,
        timeout: int = 300,
    ) -> Tuple[bool, Optional[str]]:
        """
        Uploads local video file to Google Drive via rclone.
        Upon verified upload, deletes the local file if clean_local=True.
        Returns (success: bool, url_or_path: Optional[str]).
        """
        local_file = Path(local_path)
        if not local_file.exists():
            logger.error("Local file not found: %s", local_file)
            return False, None

        local_size = local_file.stat().st_size
        dest_name = remote_filename or local_file.name
        dest_folder = remote_folder or self.remote_folder
        remote_dest = f"{self.remote_name}:{dest_folder}"

        if not self.is_rclone_available:
            logger.error(
                "rclone is not installed or not in PATH. Cannot upload to cloud: %s",
                local_file,
            )
            return False, None

        # Calculate dynamic timeout based on local file size (min 500 KB/s transfer + 60s base)
        effective_timeout = max(timeout, int(local_size / (500 * 1024)) + 60)
        logger.info("Uploading %s (%d bytes) to %s/%s via rclone (timeout: %ds)...",
                    local_file.name, local_size, remote_dest, dest_name, effective_timeout)

        import hashlib

        def _get_local_md5(p: Path) -> str:
            h = hashlib.md5()
            with open(p, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            return h.hexdigest().lower()

        local_md5: Optional[str] = None

        try:
            # 1. Idempotency Check: Pre-check if remote file already exists with EXACT size and MD5
            remote_path = f"{remote_dest}/{dest_name}"
            existing_size = self._remote_size(remote_path)
            if existing_size is not None and existing_size == local_size:
                # Check MD5 if possible
                local_md5 = _get_local_md5(local_file)
                md5_res = subprocess.run(
                    [self.rclone_bin, "md5sum", remote_path, "--retries", "1", "--low-level-retries", "1"],
                    capture_output=True, text=True, timeout=30,
                )
                remote_md5 = md5_res.stdout.strip().split()[0].lower() if (md5_res.returncode == 0 and md5_res.stdout.strip()) else None

                if not remote_md5 or remote_md5 == local_md5:
                    logger.info("⚡ Remote object %s/%s exists with exact size (%d bytes) & matching MD5. Skipping re-upload.",
                                dest_folder, dest_name, existing_size)
                    if clean_local:
                        try:
                            local_file.unlink()
                        except Exception:
                            pass
                    return True, f"gdrive://{dest_folder}/{dest_name}"

            # 2. Execute 'rclone copyto'
            cmd = [
                self.rclone_bin,
                "copyto",
                str(local_file),
                f"{remote_dest}/{dest_name}",
                "--retries", "3",
                "--low-level-retries", "10",
                "--stats-one-line",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=effective_timeout)
            if result.returncode != 0:
                logger.error("rclone upload failed (code %d): %s", result.returncode, result.stderr)
                return False, None

            # 3. Verify remote file exists and size matches local file EXACTLY (P1.7)
            remote_size = self._await_remote_size(remote_path)
            if remote_size is None:
                if self._last_probe_error == "rate_limited":
                    logger.error(
                        "rclone remote verification inconclusive for %s/%s: Drive API quota/rate limit exhausted; upload will be retried",
                        dest_folder, dest_name,
                    )
                else:
                    logger.error("rclone remote verification failed for %s/%s (missing or unreadable)", dest_folder, dest_name)
                return False, None

            if remote_size == 0:
                logger.error("rclone verified file is 0 bytes (empty/corrupted): %s/%s", dest_folder, dest_name)
                return False, None
            if remote_size != local_size:
                logger.error("rclone size mismatch! Local: %d bytes, Remote: %d bytes", local_size, remote_size)
                return False, None

            # Check MD5 hash post-upload
            if local_md5 is None:
                local_md5 = _get_local_md5(local_file)
            post_md5_res = subprocess.run(
                [self.rclone_bin, "md5sum", remote_path, "--retries", "1", "--low-level-retries", "1"],
                capture_output=True, text=True, timeout=30,
            )
            if post_md5_res.returncode == 0 and post_md5_res.stdout.strip():
                post_remote_md5 = post_md5_res.stdout.strip().split()[0].lower()
                if len(post_remote_md5) == 32 and post_remote_md5 != local_md5:
                    logger.error("rclone MD5 mismatch! Local: %s, Remote: %s", local_md5, post_remote_md5)
                    return False, None

            logger.info("Uploaded and verified successfully on Google Drive: %s/%s (%d bytes)", dest_folder, dest_name, remote_size)

            # Delete local file only if requested
            if clean_local:
                try:
                    local_file.unlink()
                    logger.info("Cleaned local temporary file to free disk space: %s", local_file.name)
                except Exception as e:
                    logger.warning("Could not delete temporary file %s: %s", local_file, e)
            else:
                logger.info("Retained local file for downstream processing: %s", local_file.name)

            # Return virtual drive path (internal cloud locator, not public shareable link)
            virtual_path = f"gdrive://{dest_folder}/{dest_name}"
            return True, virtual_path


        except subprocess.TimeoutExpired:
            logger.error("rclone upload timed out after %ds for %s", effective_timeout, local_file)
            return False, None
        except Exception as e:
            logger.error("Unexpected error during rclone upload: %s", e)
            return False, None


if __name__ == "__main__":
    uploader = StorageUploader()
    print(f"rclone available: {uploader.is_rclone_available}")
