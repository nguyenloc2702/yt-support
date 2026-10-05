"""Tests for utils.disk free-space guard."""

import pytest

from utils.disk import ensure_free_space


def test_ensure_free_space_passes(tmp_path):
    # Any small requirement must pass on a normal dev machine
    ensure_free_space(tmp_path, required_bytes=1024)


def test_ensure_free_space_raises_for_absurd_requirement(tmp_path):
    import shutil

    free = shutil.disk_usage(tmp_path).free
    with pytest.raises(Exception):
        ensure_free_space(tmp_path, required_bytes=free * 10, margin=1.0)


def test_ensure_free_space_missing_dir_raises(tmp_path):
    # On Windows shutil.disk_usage raises FileNotFoundError for a missing path
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        ensure_free_space(missing, required_bytes=1)
    # Once the directory exists it must pass
    missing.mkdir()
    ensure_free_space(missing, required_bytes=1)
