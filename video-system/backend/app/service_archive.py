"""Archive unit trong data/objects lên Google Drive để giải phóng đĩa VPS.

Đơn vị archive là một "unit" dưới ``data/objects``:

    itm_xxxxxxxx/          -> item (video dài)
    shorts/sho_xxxxxxxx/   -> short

Một unit chỉ bị xoá local khi đã nằm đủ trên Drive: ``rclone copy`` rồi
``rclone check --one-way`` trả về 0. Không có bước xác nhận thì không xoá.

Khi phục vụ file mà bản local đã archive, ``ensure_local()`` tự kéo lại từ Drive
(một lần cho mỗi unit, có lock để 5 request đồng thời không mở 5 lần rclone).

Vì sao không mount Drive làm filesystem: FUSE thêm 100-300ms mỗi lần đọc, video
player seek liên tục -> mỗi range request thành một round-trip mạng. Drive ở đây
chỉ là tầng lạnh.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from .config import data_dir, db_path
from .store import SQLiteStore, Store, now

logger = logging.getLogger("archive")

DEFAULT_ARCHIVE_AFTER_DAYS = 30
# Ngưỡng chặn: unit quá lớn mà đẩy lên Drive ở 100 KiB/s sẽ chiếm timer nhiều giờ.
DEFAULT_MAX_UNIT_BYTES = 4 * 1024**3
# Thời gian tối đa một request chờ restore trước khi trả 503 để client thử lại.
RESTORE_BUDGET_SEC = 120.0

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(unit_key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lk = _LOCKS.get(unit_key)
        if lk is None:
            lk = threading.Lock()
            _LOCKS[unit_key] = lk
        return lk


def objects_root() -> Path:
    return data_dir() / "objects"


def unit_of(path: Path | str) -> str | None:
    """Suy ra unit_key từ một đường dẫn tuyệt đối nằm dưới data/objects.

    ``objects/itm_a/exports/final_en.mp4``      -> ``itm_a``
    ``objects/shorts/sho_b/final_sho_b.mp4``    -> ``shorts/sho_b``
    """
    try:
        rel = Path(path).resolve().relative_to(objects_root().resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if not parts:
        return None
    if parts[0] == "shorts":
        return f"shorts/{parts[1]}" if len(parts) > 1 else None
    return parts[0] if parts[0].startswith("itm_") else None


def remote_for(unit_key: str) -> str:
    from .storage_gdrive import get_gdrive_remote

    return f"{get_gdrive_remote().rstrip('/')}/{unit_key}"


def local_for(unit_key: str) -> Path:
    return objects_root() / unit_key


def _unit_stats(unit_dir: Path) -> tuple[int, int, float]:
    """(tổng byte, số file, mtime mới nhất)."""
    total = 0
    count = 0
    newest = 0.0
    for root, _dirs, files in os.walk(unit_dir):
        for name in files:
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            count += 1
            if st.st_mtime > newest:
                newest = st.st_mtime
    return total, count, newest


def iter_units() -> Iterator[tuple[str, Path]]:
    root = objects_root()
    if not root.exists():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith("itm_"):
            yield child.name, child
    shorts = root / "shorts"
    if shorts.is_dir():
        for child in sorted(shorts.iterdir()):
            if child.is_dir() and child.name.startswith("sho_"):
                yield f"shorts/{child.name}", child


def _run_rclone(args: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["rclone", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, ((proc.stderr or "") + (proc.stdout or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"rclone timed out sau {int(timeout)}s"
    except FileNotFoundError:
        return 127, "không tìm thấy rclone trong PATH"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _already_remote(remote: str) -> bool:
    """Unit đã có sẵn trên Drive chưa (thư mục có ít nhất 1 file)."""
    code, _out = _run_rclone(["lsf", remote, "--max-depth", "2"], timeout=120)
    if code != 0:
        return False
    code, out = _run_rclone(["lsf", remote, "--recursive", "--files-only"], timeout=180)
    return code == 0 and bool(out.strip())


def archive_unit(
    store: Store,
    unit_key: str,
    *,
    dry_run: bool = False,
    keep_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
) -> dict[str, Any]:
    """Đẩy một unit lên Drive rồi xoá local. Trả về dict kết quả."""
    unit_dir = local_for(unit_key)
    if not unit_dir.is_dir():
        return {"unit": unit_key, "status": "missing_local"}

    size, files, newest = _unit_stats(unit_dir)
    age_days = (time.time() - newest) / 86400 if newest else 0.0
    if age_days < keep_days:
        return {"unit": unit_key, "status": "too_new", "age_days": round(age_days, 1)}

    remote = remote_for(unit_key)

    if dry_run:
        return {
            "unit": unit_key,
            "status": "would_archive",
            "size_mb": round(size / 1024**2, 1),
            "files": files,
            "age_days": round(age_days, 1),
            "remote": remote,
        }

    # Copy dư thời gian: đo thực tế ~100 KiB/s khi client_id rclone bị rate-limit.
    copy_timeout = max(600.0, min(14400.0, (size / (100 * 1024)) * 1.5))
    code, out = _run_rclone(["copy", str(unit_dir), remote, "--transfers", "4"], copy_timeout)
    if code != 0:
        logger.warning("archive %s: copy lỗi (%s) %s", unit_key, code, out[:200])
        return {"unit": unit_key, "status": "copy_failed", "detail": out[:300]}

    code, out = _run_rclone(
        ["check", str(unit_dir), remote, "--one-way", "--size-only"], 600
    )
    if code != 0:
        logger.warning("archive %s: check lệch, KHÔNG xoá local: %s", unit_key, out[:200])
        return {"unit": unit_key, "status": "verify_failed", "detail": out[:300]}

    ts = now()
    store.execute(
        "INSERT INTO archived_units (unit_key, remote_path, size_bytes, files, archived_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(unit_key) DO UPDATE SET "
        "remote_path=excluded.remote_path, size_bytes=excluded.size_bytes, "
        "files=excluded.files, archived_at=excluded.archived_at",
        (unit_key, remote, size, files, ts),
    )
    shutil.rmtree(unit_dir, ignore_errors=True)
    logger.info("archive %s: xong, giải phóng %.1f MB", unit_key, size / 1024**2)
    return {
        "unit": unit_key,
        "status": "archived",
        "size_mb": round(size / 1024**2, 1),
        "files": files,
    }


def restore_unit(store: Store, unit_key: str, *, timeout: float = 3600.0) -> bool:
    """Kéo unit từ Drive về local. Có lock để không chạy song song."""
    lk = _lock_for(unit_key)
    if not lk.acquire(timeout=timeout):
        return False
    try:
        unit_dir = local_for(unit_key)
        if unit_dir.is_dir() and any(unit_dir.rglob("*")):
            return True

        row = store.query_one(
            "SELECT remote_path FROM archived_units WHERE unit_key=?", (unit_key,)
        )
        if not row:
            return False

        unit_dir.mkdir(parents=True, exist_ok=True)
        code, out = _run_rclone(["copy", row["remote_path"], str(unit_dir)], timeout)
        if code != 0:
            logger.warning("restore %s thất bại (%s): %s", unit_key, code, out[:200])
            return False

        store.execute(
            "UPDATE archived_units SET last_restored_at=?, "
            "restore_count=COALESCE(restore_count,0)+1 WHERE unit_key=?",
            (now(), unit_key),
        )
        logger.info("restore %s: xong", unit_key)
        return True
    finally:
        lk.release()


def ensure_local(store: Store, path: Path) -> bool:
    """Bảo đảm file có mặt local; nếu đã archive thì kéo đúng file đó về.

    Kéo từng file (``rclone copyto``) thay vì cả unit: một request subtitle 2 KB
    không nên bắt chờ tải lại 300 MB. Nếu copyto lỗi mới fallback kéo cả unit.
    """
    if path.exists():
        return True
    unit_key = unit_of(path)
    if not unit_key:
        return False
    row = store.query_one(
        "SELECT remote_path FROM archived_units WHERE unit_key=?", (unit_key,)
    )
    if not row:
        return False

    # remote_path trỏ tới thư mục unit, và nội dung unit được copy vào đó (không
    # phải copy cả thư mục unit vào trong). Vì vậy đường dẫn tương đối phải tính
    # từ thư mục unit, KHÔNG phải từ objects/ — nếu tính từ objects/ thì
    # "shorts/sho_x/final.mp4" sẽ thành ".../sho_x/shorts/sho_x/final.mp4".
    unit_dir = local_for(unit_key)
    try:
        rel = path.resolve().relative_to(unit_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return False

    remote_file = f"{row['remote_path'].rstrip('/')}/{rel}"
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = _run_rclone(["copyto", remote_file, str(path)], timeout=1800)
    if code != 0:
        logger.warning("restore file %s lỗi (%s): %s", rel, code, out[:200])
        if not restore_unit(store, unit_key):
            return False
        return path.exists()

    store.execute(
        "UPDATE archived_units SET last_restored_at=?, "
        "restore_count=COALESCE(restore_count,0)+1 WHERE unit_key=?",
        (now(), unit_key),
    )
    logger.info("restore file %s từ %s", rel, row["remote_path"])
    return path.exists()


def run_archive(
    store: Store,
    *,
    keep_days: int = DEFAULT_ARCHIVE_AFTER_DAYS,
    dry_run: bool = False,
    max_unit_bytes: int = DEFAULT_MAX_UNIT_BYTES,
    limit: int | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    freed = 0
    for unit_key, unit_dir in iter_units():
        size, _files, newest = _unit_stats(unit_dir)
        age_days = (time.time() - newest) / 86400 if newest else 0.0
        if age_days < keep_days:
            continue
        if size > max_unit_bytes:
            results.append(
                {"unit": unit_key, "status": "too_large",
                 "size_mb": round(size / 1024**2, 1)}
            )
            continue
        res = archive_unit(store, unit_key, dry_run=dry_run, keep_days=keep_days)
        results.append(res)
        if res.get("status") == "archived":
            freed += int(res.get("size_mb", 0) * 1024**2)
        if limit and len([r for r in results if r.get("status") == "archived"]) >= limit:
            break
    return {
        "keep_days": keep_days,
        "dry_run": dry_run,
        "archived": len([r for r in results if r.get("status") == "archived"]),
        "freed_mb": round(freed / 1024**2, 1),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Archive objects cũ lên Google Drive")
    ap.add_argument("--days", type=int, default=int(
        os.environ.get("VIDEO_ARCHIVE_AFTER_DAYS", DEFAULT_ARCHIVE_AFTER_DAYS)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="số unit tối đa mỗi lần chạy")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    store = SQLiteStore(db_path())
    res = run_archive(store, keep_days=args.days, dry_run=args.dry_run, limit=args.limit)
    for r in res["results"]:
        print(f"{r['unit']:<28} {r['status']:<16} {r.get('size_mb', '')}")
    print(f"--- archived={res['archived']} freed={res['freed_mb']}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
