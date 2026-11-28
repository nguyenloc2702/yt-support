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
  beat_type TEXT, voice TEXT, generator TEXT,
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
  response_json TEXT, created_at REAL, request_hash TEXT, claim_token TEXT
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
  mode TEXT NOT NULL,               -- news | decode | daily_digest | trailer (Phụ lục G)
  lang TEXT DEFAULT 'vi',           -- vi | en (mảng quốc tế)
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
  asset_kind TEXT DEFAULT 'image',  -- image | video_clip (b-roll Pexels)
  attribution TEXT,                 -- credit nguồn ảnh/clip (vd photographer Pexels)
  agent_checked INTEGER DEFAULT 0,  -- G.13: agent ngoài đã submit clip phủ scene
  generated_duration_sec REAL DEFAULT 0.0,
  created_at REAL, updated_at REAL,
  UNIQUE(short_id, seq_num)
);
-- G.13: clip agent ngoài submit cho Short job (pattern todo_videos)
CREATE TABLE IF NOT EXISTS short_clips (
  id TEXT PRIMARY KEY,
  short_id TEXT NOT NULL,
  seq_num INTEGER NOT NULL,
  video_asset_id TEXT,
  filename TEXT,
  sha256 TEXT,
  duration_sec REAL DEFAULT 0.0,
  width INTEGER DEFAULT 0,
  height INTEGER DEFAULT 0,
  fps REAL DEFAULT 0.0,
  covered_scene_indexes TEXT,       -- JSON [1,2,...] — 1 clip phủ nhiều scene
  note TEXT,
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
CREATE INDEX IF NOT EXISTS idx_short_clips_short ON short_clips(short_id, seq_num);
CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY,
  link_hash TEXT UNIQUE,          -- sha1(link) — dedupe tuyệt đối (G.10)
  source TEXT,
  title TEXT NOT NULL,
  link TEXT NOT NULL,
  summary TEXT,
  query TEXT,
  lang TEXT DEFAULT 'vi',         -- vi | en (mảng quốc tế)
  published_at REAL,
  fetched_at REAL,
  status TEXT DEFAULT 'new',      -- new | used | dismissed
  pushed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_news_status ON news_items(status, fetched_at);
CREATE TABLE IF NOT EXISTS youtube_channels (
  id TEXT PRIMARY KEY,
  channel_id TEXT UNIQUE NOT NULL,
  title TEXT,
  custom_url TEXT,
  thumbnail_url TEXT,
  uploads_playlist_id TEXT,
  subscriber_count INTEGER DEFAULT 0,
  video_count INTEGER DEFAULT 0,
  access_token_enc TEXT NOT NULL,
  refresh_token_enc TEXT NOT NULL,
  token_expires_at REAL,
  scopes TEXT,
  status TEXT DEFAULT 'connected', -- connected | disconnected | expired
  is_default INTEGER DEFAULT 1,
  connected_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS youtube_uploads (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  target_type TEXT DEFAULT 'todo',
  channel_id TEXT NOT NULL,
  youtube_video_id TEXT,
  youtube_url TEXT,
  title TEXT,
  description TEXT,
  tags_json TEXT,
  privacy_status TEXT DEFAULT 'private',
  publish_at TEXT,
  upload_status TEXT DEFAULT 'queued', -- queued | uploading | processing | uploaded | failed | canceled
  progress_pct REAL DEFAULT 0.0,
  bytes_uploaded INTEGER DEFAULT 0,
  total_bytes INTEGER DEFAULT 0,
  upload_session_uri TEXT,
  attempt_count INTEGER DEFAULT 0,
  thumbnail_synced INTEGER DEFAULT 0,
  captions_synced INTEGER DEFAULT 0,
  error_message TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_yt_uploads_item ON youtube_uploads(item_id, created_at);
CREATE TABLE IF NOT EXISTS youtube_quota_ledger (
  day TEXT PRIMARY KEY,
  units_used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS youtube_oauth_nonces (
  nonce TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  used_at REAL
);
CREATE TABLE IF NOT EXISTS bgm_tracks (
  id TEXT PRIMARY KEY,
  filename TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  mood TEXT NOT NULL,
  track_type TEXT NOT NULL DEFAULT 'bgm',
  beat_role TEXT,
  genre TEXT,
  bpm INTEGER,
  energy TEXT,
  tags_json TEXT,
  artist TEXT,
  license TEXT,
  source_url TEXT,
  note TEXT,
  duration_sec REAL DEFAULT 0.0,
  lufs REAL,
  true_peak_dbtp REAL,
  active INTEGER DEFAULT 1,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_bgm_mood ON bgm_tracks(mood, track_type, active);
-- Instagram Reels publishing (Instagram API with Instagram Login)
CREATE TABLE IF NOT EXISTS ig_accounts (
  id TEXT PRIMARY KEY,
  ig_user_id TEXT UNIQUE NOT NULL,
  username TEXT,
  account_type TEXT,
  media_count INTEGER DEFAULT 0,
  access_token_enc TEXT NOT NULL,
  token_expires_at REAL,
  status TEXT DEFAULT 'connected',   -- connected | disconnected | expired
  is_default INTEGER DEFAULT 1,
  connected_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS ig_publishes (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  target_type TEXT DEFAULT 'todo',   -- todo | short
  account_id TEXT NOT NULL,
  caption TEXT,
  share_to_feed INTEGER DEFAULT 1,
  is_ai_generated INTEGER DEFAULT 1,
  affiliate_slug TEXT,
  container_id TEXT,
  ig_media_id TEXT,
  permalink TEXT,
  publish_at TEXT,
  status TEXT DEFAULT 'queued',      -- queued | scheduled | creating | processing | published | failed | canceled
  attempt_count INTEGER DEFAULT 0,
  error_message TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ig_publishes_item ON ig_publishes(item_id, created_at);
CREATE TABLE IF NOT EXISTS ig_oauth_nonces (
  nonce TEXT PRIMARY KEY,
  expires_at REAL NOT NULL,
  used_at REAL
);
-- Affiliate: products -> links (slug redirect) -> conversions (sync từ reporting API)
CREATE TABLE IF NOT EXISTS affiliate_products (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL,             -- clickbank | ebay
  label TEXT NOT NULL,
  cb_promoter TEXT,                  -- ClickBank vendor nickname
  ebay_item_url TEXT,
  is_active INTEGER DEFAULT 1,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS affiliate_links (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  item_id TEXT,
  network TEXT NOT NULL,
  deeplink TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  subid TEXT,
  clicks INTEGER DEFAULT 0,
  last_click_at REAL,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_aff_links_item ON affiliate_links(item_id);
CREATE TABLE IF NOT EXISTS affiliate_conversions (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL,
  network_txn_id TEXT NOT NULL,
  subid TEXT,
  item_id TEXT,
  product_label TEXT,
  amount REAL DEFAULT 0.0,
  currency TEXT DEFAULT 'USD',
  status TEXT,                       -- pending | cleared | returned
  event_date TEXT,
  synced_at REAL,
  UNIQUE(network, network_txn_id)
);
CREATE TABLE IF NOT EXISTS bio_pages (
  handle TEXT PRIMARY KEY,
  title TEXT,
  items_json TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS digest_submissions (
  id TEXT PRIMARY KEY,
  date TEXT UNIQUE,                 -- YYYY-MM-DD theo timezone của digest (1 kỳ/ngày)
  status TEXT NOT NULL,             -- requested | submitted | building | built | approved | rejected | failed
  stories_json TEXT,                -- list tin đã chốt (payload submit, đã normalize)
  short_id TEXT,                    -- short render cho kỳ này
  critic_issues_json TEXT,          -- issue cuối của critic (nếu fail)
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS chat_threads (
  id TEXT PRIMARY KEY,              -- new_id("thr")
  title TEXT,                       -- chủ đề hội thoại
  created_by TEXT NOT NULL,         -- participant 1 (key-name của agent tạo thread)
  participant_b TEXT,               -- participant 2 — NULL = slot trống, agent thứ 2 tự claim (CAS)
  item_id TEXT,                     -- optional: gắn thread vào 1 item đang làm
  status TEXT DEFAULT 'open',       -- open | locked (30 tin) | closed (admin)
  last_message_seq INTEGER,
  last_message_at REAL,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- cursor tuyệt đối — AUTOINCREMENT để retention DELETE không rewind seq
  thread_id TEXT NOT NULL,
  sender TEXT NOT NULL,             -- key-name của agent / "owner"
  body TEXT NOT NULL,               -- <= 2000 ký tự (chặn tầng service)
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_chat_msg_thread ON chat_messages(thread_id, seq);
CREATE INDEX IF NOT EXISTS idx_chat_msg_sender ON chat_messages(sender, created_at);

-- Authoring flow (2026-11-23): brief -> script duel -> agreement -------------
CREATE TABLE IF NOT EXISTS job_briefs (
  id TEXT PRIMARY KEY,                  -- new_id("brf")
  title TEXT NOT NULL,
  angle TEXT,
  target_duration_sec REAL DEFAULT 780,
  language TEXT DEFAULT 'en',
  style_lock_version TEXT DEFAULT 'v3',
  source_links_json TEXT,
  deadline_at REAL,
  budget_json TEXT,
  status TEXT NOT NULL DEFAULT 'draft', -- draft | approved | in_progress | done | cancelled
  approval_mode TEXT NOT NULL DEFAULT 'objection', -- calendar | objection | hard
  sensitive INTEGER DEFAULT 0,
  objection_window_sec INTEGER DEFAULT 1800,
  critic TEXT,                          -- agent được chỉ định review (owner set)
  approved_by TEXT,
  approved_at REAL,
  created_by TEXT NOT NULL,
  created_at REAL,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON job_briefs(status, created_at);

CREATE TABLE IF NOT EXISTS script_proposals (
  id TEXT PRIMARY KEY,                  -- new_id("prp")
  brief_id TEXT NOT NULL,
  parent_id TEXT,
  version INTEGER NOT NULL,
  author TEXT NOT NULL,
  content_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft', -- draft | invalid | under_review | agreed | rejected | superseded | stale
  validator_json TEXT,
  created_at REAL,
  updated_at REAL,
  UNIQUE(brief_id, version)
);
CREATE INDEX IF NOT EXISTS idx_proposals_brief ON script_proposals(brief_id, version);
CREATE INDEX IF NOT EXISTS idx_proposals_hash ON script_proposals(content_hash);

CREATE TABLE IF NOT EXISTS script_findings (
  id TEXT PRIMARY KEY,                  -- new_id("fnd")
  proposal_id TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  category TEXT NOT NULL,               -- fact | structure | style | duration
  severity TEXT NOT NULL,               -- low | medium | high
  evidence_json TEXT,
  blocking INTEGER DEFAULT 0,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_findings_proposal ON script_findings(proposal_id, blocking);

CREATE TABLE IF NOT EXISTS script_signatures (
  proposal_id TEXT NOT NULL,
  signer TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  verdict TEXT NOT NULL,                -- pass | block
  note TEXT,
  signed_at REAL,
  PRIMARY KEY (proposal_id, signer)
);

CREATE TABLE IF NOT EXISTS script_decisions (
  id TEXT PRIMARY KEY,                  -- new_id("dec")
  brief_id TEXT NOT NULL,
  proposal_id TEXT,
  decided_by TEXT NOT NULL,
  decision TEXT NOT NULL,               -- force_agree | reject | owner_override
  reason TEXT,
  content_hash TEXT,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_decisions_brief ON script_decisions(brief_id, created_at);

-- Trạng thái next_action cuối cùng đã phát webhook cho từng brief (dedupe) -----
CREATE TABLE IF NOT EXISTS brief_agreement_state (
  brief_id TEXT PRIMARY KEY,
  next_action TEXT NOT NULL,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS archived_units (
  unit_key TEXT PRIMARY KEY,            -- "itm_xxx" hoặc "shorts/sho_xxx"
  remote_path TEXT NOT NULL,            -- gdrive:video_system_assets/...
  size_bytes INTEGER,
  files INTEGER,
  archived_at REAL,
  last_restored_at REAL,
  restore_count INTEGER DEFAULT 0
);

-- Knowledge Library: FS là source of truth, DB chỉ là index/version/audit -----
CREATE TABLE IF NOT EXISTS knowledge_sources (
  alias TEXT PRIMARY KEY,
  abs_path TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'ro',      -- rw | ro
  category TEXT,
  priority INTEGER DEFAULT 0,
  recursive INTEGER DEFAULT 1,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS knowledge_docs (
  id TEXT PRIMARY KEY,                  -- knd_<sha1(source:path)[:16]>, stable qua reindex
  source_alias TEXT NOT NULL,
  rel_path TEXT NOT NULL,
  title TEXT,
  category TEXT,
  tags_json TEXT,
  status TEXT NOT NULL DEFAULT 'published', -- published | draft | reviewed | canonical | archived | trashed | missing
  owner TEXT,
  generated_by TEXT,
  source_refs_json TEXT,
  current_sha256 TEXT,
  size_bytes INTEGER,
  mtime REAL,
  version_no INTEGER DEFAULT 1,
  read_only INTEGER DEFAULT 1,
  duplicate_of TEXT,
  trashed_path TEXT,
  deleted_at REAL,
  created_at REAL,
  updated_at REAL,
  UNIQUE(source_alias, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_sha ON knowledge_docs(current_sha256);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_status ON knowledge_docs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_docs_source ON knowledge_docs(source_alias, rel_path);
CREATE TABLE IF NOT EXISTS knowledge_versions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  title TEXT,
  author TEXT,
  note TEXT,
  content_z BLOB,                       -- gzip(UTF-8); chỉ lưu bản version, không lưu bản sống
  size_bytes INTEGER,
  created_at REAL,
  UNIQUE(doc_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_versions_doc ON knowledge_versions(doc_id, version_no);
CREATE TABLE IF NOT EXISTS knowledge_events (
  id TEXT PRIMARY KEY,
  doc_id TEXT,
  actor TEXT,
  action TEXT,
  from_sha TEXT,
  to_sha TEXT,
  meta_json TEXT,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_events_doc ON knowledge_events(doc_id, created_at);
CREATE TABLE IF NOT EXISTS knowledge_sync_runs (
  id TEXT PRIMARY KEY,
  trigger TEXT,
  started_at REAL,
  finished_at REAL,
  counts_json TEXT,
  errors_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_sync_started ON knowledge_sync_runs(started_at);

CREATE TABLE IF NOT EXISTS shot_reviews (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  shot_id TEXT NOT NULL,
  shot_index INTEGER NOT NULL,
  video_hash TEXT NOT NULL,
  video_version INTEGER NOT NULL,
  reviewer TEXT NOT NULL,
  defect_codes_json TEXT NOT NULL,
  disclose_notes_json TEXT,
  note TEXT,
  pairing_ok INTEGER NOT NULL DEFAULT 1,
  score REAL NOT NULL,
  verdict TEXT NOT NULL,
  regen_brief_json TEXT,
  recurring_defect INTEGER NOT NULL DEFAULT 0,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shot_reviews_shot ON shot_reviews(item_id, shot_index, video_hash);

CREATE TABLE IF NOT EXISTS shot_comments (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  shot_id TEXT NOT NULL,
  shot_index INTEGER NOT NULL,
  video_version INTEGER NOT NULL DEFAULT 0,
  author TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shot_comments_shot ON shot_comments(item_id, shot_index, created_at);

CREATE TABLE IF NOT EXISTS muse_proposals (
  id TEXT PRIMARY KEY,
  proposer TEXT NOT NULL,
  title TEXT NOT NULL,
  title_norm TEXT NOT NULL,
  hook TEXT NOT NULL,
  angle TEXT,
  source_url TEXT,
  source_note TEXT,
  mode TEXT NOT NULL DEFAULT 'news',
  lang TEXT NOT NULL DEFAULT 'en',
  target_seconds REAL NOT NULL DEFAULT 60.0,
  status TEXT NOT NULL DEFAULT 'proposed',
  priority INTEGER NOT NULL DEFAULT 3,
  signals INTEGER NOT NULL DEFAULT 1,
  brief_id TEXT,
  claimed_by TEXT,
  claim_lease_until REAL,
  avg_score REAL,
  review_count INTEGER NOT NULL DEFAULT 0,
  pass_count INTEGER NOT NULL DEFAULT 0,
  decided_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_muse_proposals_status
  ON muse_proposals(status, priority, created_at);

CREATE TABLE IF NOT EXISTS muse_proposal_reviews (
  id TEXT PRIMARY KEY,
  proposal_id TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  score REAL NOT NULL,
  dimensions_json TEXT,
  verdict TEXT NOT NULL,
  comment TEXT,
  created_at REAL NOT NULL,
  UNIQUE(proposal_id, reviewer)
);
CREATE INDEX IF NOT EXISTS idx_muse_proposal_reviews_p
  ON muse_proposal_reviews(proposal_id, created_at);
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
    ("beat_type", "TEXT"),
    ("voice", "TEXT"),
    ("generator", "TEXT"),
    ("board_status", "TEXT"),
    ("video_hash", "TEXT"),
    ("video_version", "INTEGER DEFAULT 0"),
    ("uploaded_by", "TEXT"),
    ("declared_generator", "TEXT"),
    ("claimed_by", "TEXT"),
    ("claim_lease_until", "REAL"),
    ("grandfathered", "INTEGER DEFAULT 0"),
    # B1: soft-claim lease cho đường review (tách khỏi claimed_by = claim gen
    # shot đỏ — hai ngữ nghĩa khác nhau, không dùng chung cột).
    ("review_claimed_by", "TEXT"),
    ("review_claim_lease_until", "REAL"),
]

_SHORTS_MIGRATION_COLS = [
    ("source_link", "TEXT"),  # G.11 — link bài báo để lấy ảnh gốc
    ("lang", "TEXT DEFAULT 'vi'"),  # mảng quốc tế EN
]

_SHORT_SCENES_MIGRATION_COLS = [
    ("asset_kind", "TEXT DEFAULT 'image'"),   # image | video_clip
    ("attribution", "TEXT"),
    ("agent_checked", "INTEGER DEFAULT 0"),   # G.13: clip agent phủ scene
    ("generated_duration_sec", "REAL DEFAULT 0.0"),
]

_NEWS_MIGRATION_COLS = [
    ("lang", "TEXT DEFAULT 'vi'"),  # mảng quốc tế EN
]

_IDEMPOTENCY_MIGRATION_COLS = [
    ("request_hash", "TEXT"),  # scoped idempotency: chống reuse key khác body/path
    # claim_token: chủ sở hữu key đang xử lý. Cho phép CAS thật khi reclaim row
    # `status_code=0` bị bỏ rơi, và chặn owner cũ ghi đè kết quả của người reclaim.
    ("claim_token", "TEXT"),
]

# Muse proposals (đã deploy trước khi có brief_id) — ALTER bổ sung khi thiếu.
_MUSE_PROPOSALS_MIGRATION_COLS = [
    ("brief_id", "TEXT"),  # link sang job_briefs khi script-writer convert (Q6)
    ("claimed_by", "TEXT"),  # B1: soft-claim lease khi dispatch review
    ("claim_lease_until", "REAL"),
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
        existing_scene_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(short_scenes)").fetchall()
        }
        for col, col_def in _SHORT_SCENES_MIGRATION_COLS:
            if existing_scene_cols and col not in existing_scene_cols:
                conn.execute(f"ALTER TABLE short_scenes ADD COLUMN {col} {col_def}")
        existing_news_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(news_items)").fetchall()
        }
        for col, col_def in _NEWS_MIGRATION_COLS:
            if existing_news_cols and col not in existing_news_cols:
                conn.execute(f"ALTER TABLE news_items ADD COLUMN {col} {col_def}")
        existing_idem_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(idempotency)").fetchall()
        }
        for col, col_def in _IDEMPOTENCY_MIGRATION_COLS:
            if existing_idem_cols and col not in existing_idem_cols:
                conn.execute(f"ALTER TABLE idempotency ADD COLUMN {col} {col_def}")
        existing_prop_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(muse_proposals)").fetchall()
        }
        for col, col_def in _MUSE_PROPOSALS_MIGRATION_COLS:
            if existing_prop_cols and col not in existing_prop_cols:
                conn.execute(f"ALTER TABLE muse_proposals ADD COLUMN {col} {col_def}")
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
        # Flag phải set SAU khi BEGIN thành công. Nếu BEGIN ném "database is
        # locked", __exit__ không bao giờ chạy — để flag True ở trên sẽ làm thread
        # này không commit nữa và mọi transaction sau đó fail ("cannot start a
        # transaction within a transaction") -> 500 dưới contention.
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except BaseException:
            self.local_state.in_tx = False
            raise
        self.local_state.in_tx = True
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.local_state.in_tx = False
