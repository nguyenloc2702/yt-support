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

        dest_name = remote_filename or local_file.name
        dest_folder = remote_folder or self.remote_folder
        remote_dest = f"{self.remote_name}:{dest_folder}"

        if not self.is_rclone_available:
            logger.error(
                "rclone is not installed or not in PATH. Cannot upload to cloud: %s",
                local_file,
            )
            return False, None

        logger.info("Uploading %s to %s/%s via rclone...", local_file.name, remote_dest, dest_name)

        try:
            # Using 'rclone copyto' first
            cmd = [
                self.rclone_bin,
                "copyto",
                str(local_file),
                f"{remote_dest}/{dest_name}",
                "--retries", "3",
                "--low-level-retries", "10",
                "--stats-one-line",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                logger.error("rclone upload failed (code %d): %s", result.returncode, result.stderr)
                return False, None

            # Verify remote file exists on Google Drive before deleting local file
            verify_cmd = [
                self.rclone_bin,
                "lsf",
                f"{remote_dest}/{dest_name}",
            ]
            v_res = subprocess.run(verify_cmd, capture_output=True, text=True, timeout=30)
            if v_res.returncode != 0 or not v_res.stdout.strip():
                logger.error("rclone remote verification failed for %s/%s: %s", dest_folder, dest_name, v_res.stderr)
                return False, None

            logger.info("Uploaded and verified successfully on Google Drive: %s/%s", dest_folder, dest_name)

            # Delete local file only if requested
            if clean_local:
                try:
                    local_file.unlink()
                    logger.info("Cleaned local temporary file to free disk space: %s", local_file.name)
                except Exception as e:
                    logger.warning("Could not delete temporary file %s: %s", local_file, e)
            else:
                logger.info("Retained local file for downstream processing: %s", local_file.name)

            # Return virtual drive path
            virtual_path = f"gdrive://{dest_folder}/{dest_name}"
            return True, virtual_path

        except subprocess.TimeoutExpired:
            logger.error("rclone upload timed out after 180s for %s", local_file)
            return False, None
        except Exception as e:
            logger.error("Unexpected error during rclone upload: %s", e)
            return False, None


if __name__ == "__main__":
    uploader = StorageUploader()
    print(f"rclone available: {uploader.is_rclone_available}")
