import shutil
from pathlib import Path

from core.exceptions import InsufficientDiskSpaceError


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(str(path)).free


def ensure_free_space(path: Path, required_bytes: int, margin: float = 1.5) -> None:
    """
    Raise InsufficientDiskSpaceError when the free space at `path` is below
    required_bytes * margin. Used to fail fast before long renders/analysis
    on large (1-2 hour) source files.
    """
    free = free_bytes(path)
    needed = int(required_bytes * margin)
    if free < needed:
        raise InsufficientDiskSpaceError(
            f"Not enough free disk space at {path}. "
            f"Need ~{needed / (1024 ** 3):.2f} GB, have {free / (1024 ** 3):.2f} GB."
        )