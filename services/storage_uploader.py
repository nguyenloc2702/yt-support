"""Google Drive Storage Uploader via rclone.

Uploads rendered raw shots directly to Google Drive, then deletes
the local temporary video to conserve VPS disk space (Zero Disk Waste).
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger("storage_uploader")

DEFAULT_RCLONE_REMOTE = os.environ.get("RCLONE_REMOTE", "gdrive")
DEFAULT_REMOTE_FOLDER = os.environ.get(
    "RCLONE_REMOTE_FOLDER", "Video_Projects/history_of_microsoft/raw_shots"
)


class StorageUploader:
    def __init__(self, remote_name: str = DEFAULT_RCLONE_REMOTE, remote_folder: str = DEFAULT_REMOTE_FOLDER):
        self.remote_name = remote_name
        self.remote_folder = remote_folder
        self.rclone_bin = shutil.which("rclone")

    @property
    def is_rclone_available(self) -> bool:
        return self.rclone_bin is not None

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

        try:
            # 1. Idempotency Check: If remote file already exists with matching size, reuse it!
            verify_cmd = [
                self.rclone_bin,
                "lsf",
                "--format", "s",
                f"{remote_dest}/{dest_name}",
            ]
            pre_check = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30)
            if pre_check.returncode == 0 and pre_check.stdout.strip().isdigit():
                existing_size = int(pre_check.stdout.strip())
                if existing_size > 0 and abs(existing_size - local_size) <= max(4096, int(local_size * 0.02)):
                    logger.info("⚡ Remote object %s/%s already exists with matching size (%d bytes). Skipping re-upload.",
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

            # 3. Verify remote file exists and size matches local file within safe tolerance
            v_res = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30)
            remote_size_str = v_res.stdout.strip()
            if v_res.returncode != 0 or not remote_size_str:
                logger.error("rclone remote verification failed for %s/%s: %s", dest_folder, dest_name, v_res.stderr)
                return False, None

            if remote_size_str.isdigit():
                remote_size = int(remote_size_str)
                if remote_size == 0:
                    logger.error("rclone verified file is 0 bytes (empty/corrupted): %s/%s", dest_folder, dest_name)
                    return False, None
                # Size comparison with 2% tolerance for cloud container wrappers/metadata
                size_diff = abs(remote_size - local_size)
                if size_diff > max(4096, int(local_size * 0.05)):
                    logger.error("rclone size mismatch! Local: %d bytes, Remote: %d bytes (diff %d bytes)",
                                 local_size, remote_size, size_diff)
                    return False, None

            logger.info("Uploaded and verified successfully on Google Drive: %s/%s (%s bytes)", dest_folder, dest_name, remote_size_str)

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
