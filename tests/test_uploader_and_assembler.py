"""Unit tests for StorageUploader and DocumentaryAssembler."""

import hashlib
import json
from pathlib import Path
import subprocess
import pytest

from services.storage_uploader import StorageUploader
from pipeline.documentary_assembler import assemble_documentary


def _size_json(size: int) -> str:
    return json.dumps({"count": 1, "bytes": size, "sizeless": 0})


def test_remote_size_returns_bytes_for_single_object(monkeypatch):
    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = "/mock/bin/rclone"
    captured = []

    def mock_run(cmd, *args, **kwargs):
        captured.append(cmd)

        class Res:
            returncode = 0
            stdout = _size_json(2522310)
            stderr = ""
        return Res()

    monkeypatch.setattr(subprocess, "run", mock_run)

    assert uploader._remote_size("dummy_gdrive:test_folder/a.mp4") == 2522310
    # Exact-path lookups must not inherit rclone's exponential not-found backoff
    assert captured[0][1] == "size"
    assert captured[0][captured[0].index("--low-level-retries") + 1] == "1"


def test_remote_size_none_when_missing_or_unparsable(monkeypatch):
    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = "/mock/bin/rclone"

    calls = []

    def missing(cmd, *args, **kwargs):
        calls.append(cmd)

        class Res:
            returncode = 3
            stdout = ""
            stderr = "directory not found"
        return Res()

    monkeypatch.setattr(subprocess, "run", missing)
    assert uploader._remote_size("dummy_gdrive:test_folder/missing.mp4") is None
    assert uploader._last_probe_error == "missing"

    def quota_exhausted(cmd, *args, **kwargs):
        class Res:
            returncode = 1
            stdout = ""
            stderr = (
                "googleapi: Error 403: Quota exceeded for quota metric 'Queries', "
                "reason: RATE_LIMIT_EXCEEDED"
            )
        return Res()

    monkeypatch.setattr(subprocess, "run", quota_exhausted)
    assert uploader._remote_size("dummy_gdrive:test_folder/a.mp4") is None
    # A throttled Drive API must not be reported as a missing file
    assert uploader._last_probe_error == "rate_limited"

    def empty_dir(cmd, *args, **kwargs):
        class Res:
            returncode = 0
            stdout = json.dumps({"count": 0, "bytes": 0})
            stderr = ""
        return Res()

    monkeypatch.setattr(subprocess, "run", empty_dir)
    assert uploader._remote_size("dummy_gdrive:test_folder/prefix_a") is None

    def timed_out(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert uploader._remote_size("dummy_gdrive:test_folder/slow.mp4") is None


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

    remote_sizes: dict[str, int] = {}

    def fake_remote_size(remote_path, timeout=30):
        # None before the copyto, real size afterwards
        return remote_sizes.get(remote_path)

    def mock_subprocess_run(cmd, *args, **kwargs):
        class MockResult:
            returncode = 0
            stdout = ""
            stderr = ""

        res = MockResult()
        if cmd[1] == "copyto":
            local, remote = cmd[2], cmd[3]
            remote_sizes[remote] = Path(local).stat().st_size
        elif cmd[1] == "md5sum":
            target = cmd[2].rsplit("/", 1)[-1]
            res.stdout = f"{hashlib.md5((tmp_path / target).read_bytes()).hexdigest()}  {target}\n"
        return res

    monkeypatch.setattr(uploader, "_remote_size", fake_remote_size)
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
    monkeypatch.setattr(uploader, "_remote_size", lambda *a, **k: 0)
    ok3, path3 = uploader.upload_and_clean(dummy_file2, clean_local=True)
    assert ok3 is False
    assert path3 is None
    assert dummy_file2.exists()  # local file preserved on failure


def test_upload_verification_retries_after_copyto(tmp_path: Path, monkeypatch):
    """Drive throttling answers `rclone size` like a missing object; upload
    verification must back off and retry instead of failing the shot."""
    import services.storage_uploader as storage_uploader_mod

    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = "/mock/bin/rclone"

    dummy_file = tmp_path / "test_shot.mp4"
    dummy_file.write_bytes(b"dummy video data")
    size = dummy_file.stat().st_size
    md5 = hashlib.md5(b"dummy video data").hexdigest()

    probes = {"count": 0, "copied": False}

    def fake_remote_size(remote_path, timeout=30):
        probes["count"] += 1
        if not probes["copied"]:
            return None  # pre-check: not uploaded yet
        return size if probes["count"] >= 4 else None

    def mock_run(cmd, *args, **kwargs):
        class Res:
            returncode = 0
            stdout = ""
            stderr = ""
        res = Res()
        if cmd[1] == "copyto":
            probes["copied"] = True
        elif cmd[1] == "md5sum":
            res.stdout = f"{md5}  test_shot.mp4\n"
        return res

    monkeypatch.setattr(uploader, "_remote_size", fake_remote_size)
    monkeypatch.setattr(storage_uploader_mod, "VERIFY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(subprocess, "run", mock_run)

    ok, path = uploader.upload_and_clean(dummy_file, clean_local=False)

    assert ok is True
    assert path == "gdrive://test_folder/test_shot.mp4"
    # 1 pre-check + at least 3 post-upload probes
    assert probes["count"] >= 4


def test_storage_uploader_skips_reupload_when_remote_already_matches(tmp_path: Path, monkeypatch):
    uploader = StorageUploader(remote_name="dummy_gdrive", remote_folder="test_folder")
    uploader.rclone_bin = "/mock/bin/rclone"

    dummy_file = tmp_path / "test_shot.mp4"
    dummy_file.write_bytes(b"dummy video data")
    size = dummy_file.stat().st_size
    md5 = hashlib.md5(b"dummy video data").hexdigest()

    monkeypatch.setattr(uploader, "_remote_size", lambda *a, **k: size)

    ran = []

    def mock_run(cmd, *args, **kwargs):
        ran.append(cmd[1])

        class Res:
            returncode = 0
            stdout = f"{md5}  test_shot.mp4\n" if cmd[1] == "md5sum" else ""
            stderr = ""
        return Res()

    monkeypatch.setattr(subprocess, "run", mock_run)

    ok, path = uploader.upload_and_clean(dummy_file, clean_local=False)
    assert ok is True
    assert path == "gdrive://test_folder/test_shot.mp4"
    assert "copyto" not in ran
    assert dummy_file.exists()


def test_documentary_assembler_missing_dir(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        assemble_documentary(job_id="test_job", project_dir=tmp_path / "non_existent")


def test_documentary_assembler_empty_shots(tmp_path: Path):
    proj_dir = tmp_path / "proj"
    (proj_dir / "shots").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="No shot clips found"):
        assemble_documentary(job_id="test_job", project_dir=proj_dir)
