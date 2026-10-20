"""Unit tests for StorageUploader and DocumentaryAssembler."""

from pathlib import Path
import subprocess
import pytest

from services.storage_uploader import StorageUploader
from pipeline.documentary_assembler import assemble_documentary


def test_storage_uploader_rclone_missing(tmp_path: Path, monkeypatch):
    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = None  # simulate rclone not in PATH

    dummy_file = tmp_path / "test_shot.mp4"
    dummy_file.write_bytes(b"dummy video data")

    ok, path = uploader.upload_and_clean(dummy_file)
    # Reviewer gap: Must return (False, None) when rclone is unavailable
    assert ok is False
    assert path is None
    # Local file must not be deleted if upload failed
    assert dummy_file.exists()


def test_storage_uploader_success_verified(tmp_path: Path, monkeypatch):
    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = "/mock/bin/rclone"

    dummy_file = tmp_path / "test_shot.mp4"
    dummy_file.write_bytes(b"dummy video data")

    def mock_subprocess_run(cmd, *args, **kwargs):
        class MockResult:
            returncode = 0
            stdout = "test_shot.mp4\n"
            stderr = ""
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # 1. Test clean_local=True
    ok, path = uploader.upload_and_clean(dummy_file, clean_local=True)
    assert ok is True
    assert path == "gdrive://test_folder/test_shot.mp4"
    assert not dummy_file.exists()

    # 2. Test clean_local=False (retain for assembly)
    dummy_file2 = tmp_path / "test_shot2.mp4"
    dummy_file2.write_bytes(b"dummy video data 2")

    ok2, path2 = uploader.upload_and_clean(dummy_file2, clean_local=False)
    assert ok2 is True
    assert dummy_file2.exists()

    # 3. Test 0-byte remote failure
    def mock_subprocess_run_zero_byte(cmd, *args, **kwargs):
        class MockResult:
            returncode = 0
            stdout = "0\n"  # 0 bytes
            stderr = ""
        return MockResult()

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run_zero_byte)
    ok3, path3 = uploader.upload_and_clean(dummy_file2, clean_local=True)
    assert ok3 is False
    assert path3 is None
    assert dummy_file2.exists()  # local file preserved on failure


def test_documentary_assembler_missing_dir(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        assemble_documentary(job_id="test_job", project_dir=tmp_path / "non_existent")


def test_documentary_assembler_empty_shots(tmp_path: Path):
    proj_dir = tmp_path / "proj"
    (proj_dir / "shots").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No shot clips found"):
        assemble_documentary(job_id="test_job", project_dir=proj_dir)
