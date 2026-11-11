"""Image bank fast-mode (Phụ lục G.5): template dọc tại data_dir()/shorts_bank/.

Fast mode Tin nóng: scene thường dùng ảnh bank theo seed (tái lập được),
chỉ 1-2 ảnh hero gen mới nếu có adapter. Bank trống + không adapter → lỗi
tiếng người (honest, không placeholder).
"""
from __future__ import annotations

import zlib
from pathlib import Path

from ..config import data_dir

_BANK_DIRNAME = "shorts_bank"
_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def bank_dir() -> Path:
    d = data_dir() / _BANK_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_bank_images() -> list[Path]:
    files = [p for p in bank_dir().iterdir()
             if p.suffix.lower() in _EXTENSIONS and p.is_file()]
    files.sort()
    return files


def assign_for(short_id: str, seq: int) -> Path | None:
    """Chọn 1 ảnh bank deterministically theo short_id + số scene. None nếu bank trống."""
    pool = list_bank_images()
    if not pool:
        return None
    idx = zlib.crc32(f"{short_id}|scene_{seq}".encode("utf-8")) % len(pool)
    return pool[idx]


def assign_bank_images(short_id: str, count: int, skip: int = 0) -> list[Path | None]:
    """Chọn `count` ảnh bank deterministically theo short_id + vị trí scene.

    skip: số scene đầu đã dùng ảnh gen (hero). Trả None cho slot thiếu bank.
    """
    pool = list_bank_images()
    if not pool:
        return [None] * max(0, count)
    out: list[Path | None] = []
    for i in range(count):
        scene_seq = skip + i
        key = f"{short_id}|scene_{scene_seq}"
        idx = zlib.crc32(key.encode("utf-8")) % len(pool)
        out.append(pool[idx])
    return out
