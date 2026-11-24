"""Backup DB SQLite + config lên Google Drive.

DB là thứ duy nhất trong hệ thống không thể dựng lại từ code. Trước module này
không có backup nào cả.

Dùng API backup của sqlite3 (không copy file): DB đang chạy ở WAL, copy file thô
sẽ ra bản không nhất quán. `sqlite3.Connection.backup()` tạo snapshot đúng ngay
cả khi đang có ghi.

Không đưa `.env` lên Drive: file đó chứa API key, và Drive là tài khoản có thể bị
chiếm. Nếu cần, backup riêng bằng kênh khác.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, db_path

logger = logging.getLogger("dbbackup")

DEFAULT_KEEP_DAYS = 14


def _remote_base() -> str:
    base = os.environ.get("VIDEO_GDRIVE_BACKUP_REMOTE", "gdrive:video_system_backups")
    return base.rstrip("/")


def snapshot_db(dest: Path) -> None:
    """Snapshot nhất quán của DB đang chạy (WAL) sang `dest`."""
    src = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def _rclone(args: list[str], timeout: float = 600.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(["rclone", *args], capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, ((proc.stderr or "") + (proc.stdout or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"rclone timed out sau {int(timeout)}s"
    except FileNotFoundError:
        return 127, "không tìm thấy rclone trong PATH"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def run_backup(*, keep_days: int = DEFAULT_KEEP_DAYS, dry_run: bool = False) -> dict[str, Any]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    remote_dir = f"{_remote_base()}/db"
    name = f"video_system-{stamp}.sqlite3"
    remote_file = f"{remote_dir}/{name}"

    if dry_run:
        return {"status": "dry_run", "source": str(db_path()), "target": remote_file}

    tmpdir = Path(tempfile.mkdtemp(prefix="dbbackup-"))
    snap = tmpdir / name
    try:
        snapshot_db(snap)
        size = snap.stat().st_size

        # Kiểm tra snapshot mở được và không hỏng trước khi đẩy lên.
        chk = sqlite3.connect(str(snap))
        try:
            integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
        if integrity != "ok":
            return {"status": "integrity_failed", "detail": integrity}

        code, out = _rclone(["copyto", str(snap), remote_file], timeout=1800)
        if code != 0:
            logger.warning("upload backup lỗi (%s): %s", code, out[:200])
            return {"status": "upload_failed", "detail": out[:300]}

        # Config đọc lúc chạy (không gồm .env).
        cfg = CONFIG_DIR
        cfg_status = "skipped"
        if cfg.is_dir():
            code, out = _rclone(
                ["copy", str(cfg), f"{_remote_base()}/config", "--max-age", "24h"],
                timeout=300,
            )
            cfg_status = "ok" if code == 0 else f"failed: {out[:120]}"

        pruned = 0
        code, out = _rclone(
            ["delete", remote_dir, "--min-age", f"{keep_days}d", "--include", "*.sqlite3"],
            timeout=300,
        )
        if code == 0:
            pruned = len([ln for ln in out.splitlines() if ln.strip()])

        logger.info("backup %s (%.1f MB) -> %s", name, size / 1024**2, remote_file)
        return {
            "status": "ok",
            "file": name,
            "size_mb": round(size / 1024**2, 1),
            "remote": remote_file,
            "config": cfg_status,
            "prune_keep_days": keep_days,
            "pruned_lines": pruned,
        }
    finally:
        for p in tmpdir.glob("*"):
            p.unlink(missing_ok=True)
        tmpdir.rmdir()


def list_backups() -> list[str]:
    code, out = _rclone(["lsf", f"{_remote_base()}/db"], timeout=180)
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backup DB SQLite lên Google Drive")
    ap.add_argument("--keep-days", type=int,
                    default=int(os.environ.get("VIDEO_BACKUP_KEEP_DAYS", DEFAULT_KEEP_DAYS)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list:
        for name in list_backups():
            print(name)
        return 0

    res = run_backup(keep_days=args.keep_days, dry_run=args.dry_run)
    for k, v in res.items():
        print(f"{k}: {v}")
    return 0 if res.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    sys.exit(main())
