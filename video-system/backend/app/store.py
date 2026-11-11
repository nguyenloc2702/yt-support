"""Repository abstraction + SQLite WAL implementation (plan §9).

Services depend on the Store interface only. Swapping to Postgres means
implementing another Store, not touching services. All writes are
parameterized SQL; all CAS guards go through `cas_update`.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, type TEXT NOT NULL,
  status TEXT NOT NULL, language TEXT, style_lock_version TEXT,
  source_url TEXT, bgm_enabled INTEGER DEFAULT 1, subtitles_burned INTEGER DEFAULT 0,
  total_shots INTEGER DEFAULT 0, appended_count INTEGER DEFAULT 0,
  held_count INTEGER DEFAULT 0,
  audio_duration_sec REAL DEFAULT 0.0,
  uploaded_images_count INTEGER DEFAULT 0,
  uploaded_videos_count INTEGER DEFAULT 0,
  uploaded_videos_duration_sec REAL DEFAULT 0.0,
  audio_asset_id TEXT, script_asset_id TEXT,
  subtitle_srt_asset_id TEXT, subtitle_ass_asset_id TEXT,
  final_video_asset_id TEXT, error_detail TEXT,
  cumulative_duration_sec REAL DEFAULT 0.0,
  cumulative_manifest_hash TEXT,
  gdrive_status TEXT, gdrive_remote_path TEXT, gdrive_synced_at REAL,
  purged_bytes INTEGER DEFAULT 0, feedbacks_count INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, item_id TEXT, kind TEXT, storage_key TEXT NOT NULL,
  sha256 TEXT, size INTEGER, duration_s REAL, provenance_engine TEXT,
  meta_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS scripts (
  id TEXT PRIMARY KEY, item_id TEXT, version INTEGER, language TEXT,
  text TEXT, scenes_json TEXT, hash TEXT, shots_hash TEXT,
  status TEXT DEFAULT 'active', created_at REAL,
  UNIQUE(item_id, version)
);
CREATE TABLE IF NOT EXISTS audios (
  id TEXT PRIMARY KEY, item_id TEXT, version INTEGER, kind TEXT,
  audio_asset_id TEXT, duration_sec REAL, loudness_lufs REAL, true_peak REAL,
  word_timeline_asset_id TEXT, voice_id TEXT, hash TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS character_sheets (
  id TEXT PRIMARY KEY, item_id TEXT, version INTEGER, name TEXT,
  role TEXT, reference_asset_id TEXT, crowd_ref_asset_id TEXT,
  hash TEXT, status TEXT DEFAULT 'draft', approved_by TEXT, approved_at REAL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS shots (
  id TEXT PRIMARY KEY, item_id TEXT, script_version INTEGER, batch_id TEXT,
  index_num INTEGER, shot_type TEXT, narration TEXT,
  audio_start_sec REAL, audio_end_sec REAL, visual_prompt TEXT,
  on_screen_text TEXT, on_screen_type TEXT,
  keyframe_asset_id TEXT, keyframe_hash TEXT,
  video_asset_id TEXT, render_engine TEXT, seed TEXT,
  character_ids TEXT,
  status TEXT NOT NULL, held_reason TEXT, held_detail TEXT,
  downgrade_approved INTEGER DEFAULT 0,
  attempts INTEGER DEFAULT 0, retry_later_count INTEGER DEFAULT 0,
  next_retry_at REAL, provider TEXT, model TEXT,
  agent_checked INTEGER DEFAULT 0,
  generated_duration_sec REAL DEFAULT 0.0,
  agent_note TEXT,
  created_at REAL, updated_at REAL,
  UNIQUE(item_id, script_version, index_num)
);
CREATE TABLE IF NOT EXISTS todo_videos (
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL, seq_num INTEGER NOT NULL,
  video_asset_id TEXT NOT NULL, filename TEXT,
  sha256 TEXT, duration_sec REAL NOT NULL,
  width INTEGER, height INTEGER, fps REAL,
  covered_shot_indexes TEXT, note TEXT,
  created_at REAL, updated_at REAL,
  UNIQUE(item_id, seq_num)
);
CREATE TABLE IF NOT EXISTS todo_images (
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'keyframe',
  seq_num INTEGER NOT NULL,
  shot_index INTEGER,
  name TEXT,
  image_asset_id TEXT NOT NULL, filename TEXT,
  sha256 TEXT, width INTEGER, height INTEGER,
  prompt_used TEXT, covered_shot_indexes TEXT, note TEXT,
  created_at REAL, updated_at REAL,
  UNIQUE(item_id, kind, seq_num)
);
CREATE TABLE IF NOT EXISTS batches (
  id TEXT PRIMARY KEY, item_id TEXT, shot_indexes TEXT, status TEXT,
  created_at REAL, approved_at REAL
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, item_id TEXT, gate TEXT, version INTEGER,
  decision TEXT, reason TEXT, approver TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, item_id TEXT, shot_id TEXT, type TEXT,
  actor TEXT, payload_json TEXT, at REAL
);
CREATE TABLE IF NOT EXISTS webhooks (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, secret TEXT NOT NULL,
  events TEXT, item_id TEXT, active INTEGER DEFAULT 1, created_at REAL
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id TEXT PRIMARY KEY, webhook_id TEXT, event_id TEXT, attempts INTEGER,
  next_attempt_at REAL, last_status TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY, method TEXT, path TEXT, status_code INTEGER,
  response_json TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS todo_feedbacks (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  shot_index INTEGER,
  timestamp_sec REAL,
  category TEXT NOT NULL DEFAULT 'pronunciation',
  severity TEXT NOT NULL DEFAULT 'warning',
  original_text TEXT,
  suggested_text TEXT,
  pronunciation_hint TEXT,
  message TEXT NOT NULL,
  agent_name TEXT DEFAULT 'external_video_agent',
  status TEXT NOT NULL DEFAULT 'open',
  resolution_note TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS shorts (
  id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,               -- news | decode (Phụ lục G)
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  script_json TEXT,
  source_text TEXT,
  source_note TEXT,
  target_seconds REAL,
  audio_asset_id TEXT,
  words_asset_id TEXT,
  srt_asset_id TEXT,
  ass_asset_id TEXT,
  video_asset_id TEXT,
  publish_md_asset_id TEXT,
  duration_s REAL DEFAULT 0.0,
  word_count INTEGER DEFAULT 0,
  error_detail TEXT,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS short_scenes (
  id TEXT PRIMARY KEY,
  short_id TEXT NOT NULL,
  seq_num INTEGER NOT NULL,
  text TEXT NOT NULL,
  start_sec REAL DEFAULT 0.0,
  end_sec REAL DEFAULT 0.0,
  image_asset_id TEXT,
  image_prompt TEXT,
  bank_used INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL,
  UNIQUE(short_id, seq_num)
);
CREATE INDEX IF NOT EXISTS idx_shots_item ON shots(item_id, script_version, index_num);
CREATE INDEX IF NOT EXISTS idx_shots_status ON shots(item_id, status);
CREATE INDEX IF NOT EXISTS idx_todo_videos_item ON todo_videos(item_id, seq_num);
CREATE INDEX IF NOT EXISTS idx_todo_images_item ON todo_images(item_id, kind, seq_num);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id, at);
CREATE INDEX IF NOT EXISTS idx_todo_feedbacks_item ON todo_feedbacks(item_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_shorts_status ON shorts(status, created_at);
CREATE INDEX IF NOT EXISTS idx_short_scenes_short ON short_scenes(short_id, seq_num);
CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY,
  link_hash TEXT UNIQUE,          -- sha1(link) — dedupe tuyệt đối (G.10)
  source TEXT,
  title TEXT NOT NULL,
  link TEXT NOT NULL,
  summary TEXT,
  query TEXT,
  published_at REAL,
  fetched_at REAL,
  status TEXT DEFAULT 'new',      -- new | used | dismissed
  pushed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_news_status ON news_items(status, fetched_at);
"""

_ITEM_MIGRATION_COLS = [
    ("source_url", "TEXT"),
    ("bgm_enabled", "INTEGER DEFAULT 1"),
    ("subtitles_burned", "INTEGER DEFAULT 0"),
    ("audio_duration_sec", "REAL DEFAULT 0.0"),
    ("uploaded_images_count", "INTEGER DEFAULT 0"),
    ("uploaded_videos_count", "INTEGER DEFAULT 0"),
    ("uploaded_videos_duration_sec", "REAL DEFAULT 0.0"),
    ("audio_asset_id", "TEXT"),
    ("script_asset_id", "TEXT"),
    ("subtitle_srt_asset_id", "TEXT"),
    ("subtitle_ass_asset_id", "TEXT"),
    ("final_video_asset_id", "TEXT"),
    ("error_detail", "TEXT"),
    ("gdrive_status", "TEXT"),
    ("gdrive_remote_path", "TEXT"),
    ("gdrive_synced_at", "REAL"),
    ("purged_bytes", "INTEGER DEFAULT 0"),
    ("feedbacks_count", "INTEGER DEFAULT 0"),
]

_SHOT_MIGRATION_COLS = [
    ("agent_checked", "INTEGER DEFAULT 0"),
    ("generated_duration_sec", "REAL DEFAULT 0.0"),
    ("agent_note", "TEXT"),
]

_SHORTS_MIGRATION_COLS = [
    ("source_link", "TEXT"),  # G.11 — link bài báo để lấy ảnh gốc
]


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    """Interface (structural). See SQLiteStore below."""

    def cas_update(self, sql: str, params: tuple) -> bool: ...
    def query(self, sql: str, params: tuple = ()) -> list[dict]: ...


class SQLiteStore(Store):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # WAL once per database (not per connection).
        conn = sqlite3.connect(self.path)
        conn.executescript(_SCHEMA)
        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()
        }
        for col, col_def in _ITEM_MIGRATION_COLS:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE items ADD COLUMN {col} {col_def}")
        existing_shot_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(shots)").fetchall()
        }
        for col, col_def in _SHOT_MIGRATION_COLS:
            if col not in existing_shot_cols:
                conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {col_def}")
        existing_shorts_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(shorts)").fetchall()
        }
        for col, col_def in _SHORTS_MIGRATION_COLS:
            if existing_shorts_cols and col not in existing_shorts_cols:
                conn.execute(f"ALTER TABLE shorts ADD COLUMN {col} {col_def}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.commit()
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    # -- generic ------------------------------------------------------------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        conn = self._conn()
        cur = conn.execute(sql, params)
        if not getattr(self._local, "in_tx", False):
            conn.commit()
        return cur.rowcount

    def cas_update(self, sql: str, params: tuple) -> bool:
        """Caller SQL must contain a guarded WHERE; returns True if a row changed."""
        return self.execute(sql, params) > 0

    def transaction(self):
        return _Tx(self._conn(), self._local)


class _Tx:
    def __init__(self, conn: sqlite3.Connection, local_state: threading.local) -> None:
        self.conn = conn
        self.local_state = local_state

    def __enter__(self) -> sqlite3.Connection:
        self.local_state.in_tx = True
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.local_state.in_tx = False
