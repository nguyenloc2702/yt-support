"""Test archive (Drive tầng lạnh) và backup DB.

Không gọi rclone thật: mọi test đều monkeypatch `_run_rclone` hoặc chỉ chạy
dry-run. Test nào chạm vào "xoá local" đều kiểm tra rằng file còn nguyên khi
bước xác nhận thất bại.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.app import service_archive as arch
from backend.app import service_dbbackup as backup
from backend.app.config import data_dir


# -- unit mapping -------------------------------------------------------------

def test_unit_of_item():
    p = data_dir() / "objects" / "itm_abc" / "exports" / "final_en.mp4"
    assert arch.unit_of(p) == "itm_abc"


def test_unit_of_short():
    p = data_dir() / "objects" / "shorts" / "sho_xyz" / "final_sho_xyz.mp4"
    assert arch.unit_of(p) == "shorts/sho_xyz"


def test_unit_of_rejects_paths_outside_objects(tmp_path):
    assert arch.unit_of(tmp_path / "somewhere" / "file.mp4") is None
    # chính thư mục objects không phải một unit
    assert arch.unit_of(data_dir() / "objects") is None
    # thư mục shorts (cha) cũng không
    assert arch.unit_of(data_dir() / "objects" / "shorts") is None


def test_unit_of_rejects_unknown_prefix():
    p = data_dir() / "objects" / "random_dir" / "x.mp4"
    assert arch.unit_of(p) is None


# -- phát hiện unit -----------------------------------------------------------

def _make_unit(rel: str, *, days_old: float, payload: bytes = b"x" * 1024) -> Path:
    d = data_dir() / "objects" / rel
    d.mkdir(parents=True, exist_ok=True)
    f = d / "artifact.bin"
    f.write_bytes(payload)
    ts = time.time() - days_old * 86400
    os.utime(f, (ts, ts))
    return d


def test_iter_units_finds_items_and_shorts():
    _make_unit("itm_iter01", days_old=1)
    _make_unit("shorts/sho_iter01", days_old=1)
    keys = [k for k, _ in arch.iter_units()]
    assert "itm_iter01" in keys
    assert "shorts/sho_iter01" in keys


def test_run_archive_dry_run_only_flags_old_units():
    _make_unit("itm_old01", days_old=40)
    _make_unit("itm_new01", days_old=1)
    res = arch.run_archive(arch_store(), keep_days=30, dry_run=True)
    units = {r["unit"]: r for r in res["results"]}
    assert units["itm_old01"]["status"] == "would_archive"
    assert "itm_new01" not in units
    assert res["archived"] == 0
    # dry-run không được xoá gì
    assert (data_dir() / "objects" / "itm_old01" / "artifact.bin").exists()


def test_archive_skips_unit_over_size_limit():
    _make_unit("itm_big01", days_old=40, payload=b"y" * 4096)
    res = arch.run_archive(arch_store(), keep_days=30, dry_run=True, max_unit_bytes=1024)
    units = {r["unit"]: r for r in res["results"]}
    assert units["itm_big01"]["status"] == "too_large"


# -- archive không xoá khi chưa xác nhận --------------------------------------

def arch_store():
    from backend.app.main import store
    return store


def test_archive_keeps_local_when_verify_fails(monkeypatch):
    unit = _make_unit("itm_verify01", days_old=40)
    calls = []

    def fake(args, timeout):
        calls.append(args)
        return (0, "ok") if args[0] == "copy" else (1, "size mismatch")

    monkeypatch.setattr(arch, "_run_rclone", fake)
    res = arch.archive_unit(arch_store(), "itm_verify01", keep_days=30)

    assert res["status"] == "verify_failed"
    assert (unit / "artifact.bin").exists(), "verify lỗi mà vẫn xoá local"
    # Phải so hash thật, không được chỉ so size — size trùng mà nội dung lệch vẫn mất data.
    check_args = next(args for args in calls if args[0] == "check")
    assert "--size-only" not in check_args
    assert arch_store().query_one(
        "SELECT 1 FROM archived_units WHERE unit_key=?", ("itm_verify01",)) is None


def test_archive_deletes_local_after_verify(monkeypatch):
    _make_unit("itm_ok01", days_old=40)
    monkeypatch.setattr(arch, "_run_rclone", lambda args, timeout: (0, "ok"))
    res = arch.archive_unit(arch_store(), "itm_ok01", keep_days=30)

    assert res["status"] == "archived"
    assert not (data_dir() / "objects" / "itm_ok01").exists()
    row = arch_store().query_one(
        "SELECT * FROM archived_units WHERE unit_key=?", ("itm_ok01",))
    assert row is not None
    assert row["remote_path"].endswith("/itm_ok01")


def test_archive_refuses_young_unit(monkeypatch):
    _make_unit("itm_young01", days_old=3)
    monkeypatch.setattr(arch, "_run_rclone", lambda args, timeout: (0, "ok"))
    res = arch.archive_unit(arch_store(), "itm_young01", keep_days=30)
    assert res["status"] == "too_new"
    assert (data_dir() / "objects" / "itm_young01" / "artifact.bin").exists()


def test_ensure_local_short_remote_path_not_doubled(monkeypatch):
    """Bug thật đã gặp: unit_key đã chứa "shorts/" nên nếu tính rel từ objects/
    thì remote thành .../sho_x/shorts/sho_x/final.mp4."""
    unit = "shorts/sho_dup01"
    arch_store().execute(
        "INSERT OR REPLACE INTO archived_units (unit_key, remote_path, size_bytes, "
        "files, archived_at) VALUES (?,?,?,?,?)",
        (unit, "gdrive:test/" + unit, 1, 1, time.time()))
    target = data_dir() / "objects" / "shorts" / "sho_dup01" / "final_sho_dup01.mp4"
    seen = []

    def fake(args, timeout):
        seen.append(args)
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).write_bytes(b"v")
        return 0, "ok"

    monkeypatch.setattr(arch, "_run_rclone", fake)
    assert arch.ensure_local(arch_store(), target) is True
    assert seen[0][1] == "gdrive:test/shorts/sho_dup01/final_sho_dup01.mp4"


# -- ensure_local -------------------------------------------------------------

def test_ensure_local_true_when_file_exists():
    p = data_dir() / "objects" / "itm_here" / "f.bin"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"1")
    assert arch.ensure_local(arch_store(), p) is True


def test_ensure_local_false_when_unit_never_archived():
    p = data_dir() / "objects" / "itm_never" / "f.bin"
    assert arch.ensure_local(arch_store(), p) is False


def test_ensure_local_restores_single_file(monkeypatch):
    unit = "itm_restore01"
    arch_store().execute(
        "INSERT OR REPLACE INTO archived_units (unit_key, remote_path, size_bytes, "
        "files, archived_at, restore_count) VALUES (?,?,?,?,?,0)",
        (unit, "gdrive:test/" + unit, 10, 1, time.time()))

    target = data_dir() / "objects" / unit / "todo" / "subtitles_en.srt"
    seen = []

    def fake(args, timeout):
        seen.append(args)
        # chỉ khôi phục đúng file được yêu cầu
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        return 0, "ok"

    monkeypatch.setattr(arch, "_run_rclone", fake)
    assert arch.ensure_local(arch_store(), target) is True
    assert target.exists()
    assert seen[0][0] == "copyto"
    # đường dẫn remote phải tính từ thư mục unit: <remote>/todo/subtitles_en.srt
    assert seen[0][1] == f"gdrive:test/{unit}/todo/subtitles_en.srt"
    row = arch_store().query_one(
        "SELECT restore_count FROM archived_units WHERE unit_key=?", (unit,))
    assert row["restore_count"] == 1


def test_ensure_local_replaces_zero_byte_existing_file(monkeypatch):
    """File 0 byte là restore hỏng — phải kéo lại, không được coi là đã có."""
    unit = "itm_zero01"
    arch_store().execute(
        "INSERT OR REPLACE INTO archived_units (unit_key, remote_path, size_bytes, "
        "files, archived_at) VALUES (?,?,?,?,?)",
        (unit, "gdrive:test/" + unit, 5, 1, time.time()))
    target = data_dir() / "objects" / unit / "f.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")

    seen = []

    def fake(args, timeout):
        seen.append(args)
        Path(args[2]).write_bytes(b"restored")
        return 0, "ok"

    monkeypatch.setattr(arch, "_run_rclone", fake)
    assert arch.ensure_local(arch_store(), target) is True
    assert seen and seen[0][0] == "copyto"
    assert target.read_bytes() == b"restored"


def test_ensure_local_does_not_double_restore_under_concurrency(monkeypatch):
    """5 request cùng đòi 1 file đã archive thì chỉ được gọi rclone 1 lần."""
    unit = "itm_racerestore"
    arch_store().execute(
        "INSERT OR REPLACE INTO archived_units (unit_key, remote_path, size_bytes, "
        "files, archived_at) VALUES (?,?,?,?,?)",
        (unit, "gdrive:test/" + unit, 5, 1, time.time()))
    target = data_dir() / "objects" / unit / "f.bin"
    calls = []
    started = threading.Event()
    release = threading.Event()

    def fake(args, timeout):
        calls.append(args)
        started.set()
        assert release.wait(5), "release không được bật — test bị kẹt"
        Path(args[2]).parent.mkdir(parents=True, exist_ok=True)
        Path(args[2]).write_bytes(b"restored")
        return 0, "ok"

    monkeypatch.setattr(arch, "_run_rclone", fake)
    done = []

    def worker():
        done.append(arch.ensure_local(arch_store(), target))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    assert started.wait(5), "rclone không được gọi"
    t2.start()
    time.sleep(0.1)
    assert not done, "request thứ hai phải đợi lock, không được restore song song"
    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    assert done == [True, True]
    assert len(calls) == 1, f"rclone copyto phải gọi 1 lần, được {len(calls)}"


def test_ensure_local_false_when_restore_fails(monkeypatch):
    unit = "itm_fail01"
    arch_store().execute(
        "INSERT OR REPLACE INTO archived_units (unit_key, remote_path, size_bytes, "
        "files, archived_at) VALUES (?,?,?,?,?)",
        (unit, "gdrive:test/" + unit, 1, 1, time.time()))
    monkeypatch.setattr(arch, "_run_rclone", lambda args, timeout: (1, "403 forbidden"))
    p = data_dir() / "objects" / unit / "f.bin"
    assert arch.ensure_local(arch_store(), p) is False


# -- backup DB ----------------------------------------------------------------

def _make_db(path: Path, rows: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def test_snapshot_db_copies_all_committed_rows(tmp_path):
    src = tmp_path / "src.sqlite3"
    _make_db(src, 25)
    dst = tmp_path / "snap.sqlite3"
    import backend.app.service_dbbackup as mod
    old = mod.db_path
    mod.db_path = lambda: src
    try:
        mod.snapshot_db(dst)
    finally:
        mod.db_path = old

    conn = sqlite3.connect(dst)
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 25
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_backup_dry_run_does_not_touch_rclone(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry-run không được gọi rclone")

    monkeypatch.setattr(backup, "_rclone", boom)
    res = backup.run_backup(dry_run=True)
    assert res["status"] == "dry_run"
    assert res["target"].endswith(".sqlite3")


def test_backup_uploads_and_reports_size(monkeypatch, tmp_path):
    import backend.app.service_dbbackup as mod
    src = tmp_path / "live.sqlite3"
    _make_db(src, 5)
    old = mod.db_path
    mod.db_path = lambda: src

    uploaded = {}

    def fake(args, timeout=600.0):
        if args[0] == "copyto":
            uploaded["remote"] = args[2]
            uploaded["size"] = Path(args[1]).stat().st_size
        return 0, ""

    monkeypatch.setattr(backup, "_rclone", fake)
    try:
        res = backup.run_backup(keep_days=7)
    finally:
        mod.db_path = old

    assert res["status"] == "ok"
    assert uploaded["remote"].endswith(".sqlite3")
    assert uploaded["size"] > 0, "file đẩy lên phải có nội dung"
