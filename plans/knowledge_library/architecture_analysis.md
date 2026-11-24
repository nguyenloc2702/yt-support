# Knowledge Library — Phân tích & thiết kế kiến trúc

**Ngày:** 2026-11-24 · **Trạng thái:** PROPOSED · **Phạm vi:** `video-system` (FastAPI 1 process, SQLite WAL, SPA `index.html`). Không sửa code.

## 0. Chốt một câu

> **Filesystem là source of truth; SQLite là index + sổ version/audit tái tạo được.** Thêm `/api/v1/knowledge`; giữ `/api/v1/read-me` làm alias mỏng.

## 1. Ba phương án & tradeoff

| | A. FS-only | B. FS + index (**chọn**) | C. DB-only |
|---|---|---|---|
| Search 208 file | quét full text mỗi lần | FTS5 + metadata | tốt |
| Agent ghi liên tục | tốt | ghi FS, index theo sau | mất đường sửa file |
| rsync deploy | tốt | tốt + reconcile | xấu: DB ở `data/` bị loại |
| Version/audit/trash | không | có | có |
| Dedupe 2 bản `skills/` | không | content hash | có |
| Backup Drive | file ngoài snapshot | snapshot gồm index+version | gồm hết |

A thiếu tag/version/ranking; C phá git+rsync+agent. B giữ cả ba.

- **WAL 1 process**: `BEGIN IMMEDIATE` khi ghi index; rebuild 208 file <1s → phao "xoá index, scan lại".
- **rsync + backup**: `deploy.ps1` loại `data/`, `logs/` nhưng sync `read_me/`, `docs/`, `skills/` → index không thể là nguồn duy nhất. `service_dbbackup.py` chỉ snapshot DB → blob version để trong DB, bản sống trên FS.

## 2. Kiến trúc đích

```
   Agents (Muse) ──PATCH append──┐        Owner ──#/library──┐
                                 ▼                          ▼
        ┌─────────────── FastAPI :8090 (1 process) ───────────────┐
        │ KnowledgeService: resolver → reader → indexer →          │
        │ ledger → sync  (safe path · hash · FTS · audit)          │
        └────────────┬──────────────────────────────┬────────────┘
                     ▼                              ▼
        FS = source of truth              SQLite WAL (data/)
        read_me · docs · skills           index · versions · blobs
        plans · root *.md                 FTS5 · trash · sync_runs
```

- **Root registry** `knowledge_sources`: alias, abs_path, mode, priority, globs. `read_me`(rw,10); `docs`(8), `skills`(7), `plans`(6), `repo_*`(5), `root_md`(4) đều ro. Chỉ `read_me` ghi mặc định.
- **Sync**: lazy reconcile khi GET (stat mtime+size, lệch thì hash lại) + sweep 10 phút + `POST /knowledge/sync`. **Không inotify**: rsync ghi đè, restart mất event.
- **Dedupe**: cùng `sha256` → canonical theo priority, đánh `duplicate_of`.
- **Conflict**: `ETag = "sha[:16]-v{n}"`, so `current_sha256`.

## 3. Mô hình dữ liệu

| Bảng | Cột chính | Khóa/index |
|---|---|---|
| `knowledge_sources` | alias, abs_path, mode, priority, globs | PK(alias) |
| `knowledge_docs` | id, source, rel_path, canonical_path, title, category, tags_json, owner, visibility, status, current_sha256, size, mtime, duplicate_of, version_count, deleted_at | PK(id); UNIQUE(source,rel_path); idx(sha256) |
| `knowledge_versions` | id, doc_id, version_no, sha256, title, author, source, note, created_at | UNIQUE(doc_id, version_no) |
| `knowledge_blobs` | sha256 PK, content_z (gzip), size, refcount | PK(sha256) |
| `knowledge_events` | id, doc_id, actor, action, from_sha, to_sha, meta_json, at | idx(doc_id, at) |
| `knowledge_sync_runs` | id, trigger, times, counts, errors_json | idx(started_at) |
| `knowledge_fts` | FTS5(title, body, tags, path, doc_id UNINDEXED) | `unicode61 remove_diacritics 2` |

**FTS5: có.** Search hiện tại là substring `in content_low`, không ranking, không tiếng Việt không dấu. Dùng `bm25()` + `snippet()`; fallback `LIKE`.

**Content trong DB?** Không lưu bản sống; chỉ **blob version** (gzip, content-addressed, cap 5MB) để diff/restore và dedupe.

## 4. API boundary

Giữ `/api/v1/read-me` (đã có trong `02_api_contracts.md`) làm **alias** root=`read_me`.

| Method | Endpoint | Scope |
|---|---|---|
| GET | `/docs?root=&category=&tag=&q=&cursor=`; `/tree`; `/search`; `/stats`; `/docs/{id}[/content\|/versions[/{n}]]` | read |
| POST | `/docs` `{source,path,content,overwrite}` → 201+ETag | ingest |
| PUT | `/docs/{id}` (**If-Match bắt buộc**) | ingest |
| PATCH | `/docs/{id}` `mode=append\|prepend\|replace_text\|overwrite` | ingest |
| DELETE | `/docs/{id}` soft→trash; `?hard=1` | admin |
| POST | `/docs/{id}/restore`, `/versions/{n}/restore`, `/publish`, `/knowledge/sync` | ingest/admin |

**Concurrency**: thiếu `If-Match` ở PUT/replace → 428; lệch → **409** + ETag hiện tại. Ghi atomic: temp file → `os.replace()`. `version_no` cấp trong `BEGIN IMMEDIATE` + `UNIQUE`. POST dùng `_idempotent()` sẵn có.

## 5. Tích hợp SPA

- `#/library`, `#/library/{id}`: nhánh mới trong `syncRouteFromHash()` **trước** fallback `setActiveNav('todos')`; thêm `navLibraryBtn`, `pageLibrary`, `hideLibraryPage()`.
- 3 cột: trái cây root + category/tag + Trash; giữa viewer; phải TOC + metadata (owner, version, sha).
- Render: **vendor `marked` + `DOMPurify`** vào `frontend/vendor/`, `app.mount("/static", StaticFiles(...))`; `marked({html:false})` → `DOMPurify.sanitize()`. Không CDN.
- `highlight.js` lazy; TOC scroll-spy `IntersectionObserver`; search debounce 250ms, highlight bằng `snippet()` server.
- Edit modal gửi `If-Match`; 409 → banner + Xem bản mới/Ghi đè. Version drawer: diff 2 cột + Restore.

## 6. Bảo mật

| Rủi ro | Kiểm soát |
|---|---|
| Path traversal + symlink | chuẩn hoá `\`→`/`, chặn `..`/NUL/absolute/`C:`; `realpath` trong root; từ chối segment symlink |
| File size | 5MB/doc (env) → 413; chặn NUL/binary |
| Markdown XSS | DOMPurify allowlist, `html:false` cho doc agent, chặn `javascript:`/`data:`, `rel=noopener` |
| Raw HTML | chỉ doc `owner` + `visibility=team`, vẫn qua DOMPurify |
| Auth | read: list/get/search; ingest: create/update/patch/restore; admin: delete/sync/publish |
| Owner-only | `private`/`append_only` → agent chỉ append; publish = admin |
| Rate limit | 120 read/ph, 30 write/ph, 5 sync/giờ → 429 |

## 7. Vận hành

- **Deploy**: rsync đổi file → index cũ; lazy reconcile xử lý ngay, thêm `POST /knowledge/sync` sau restart trong `deploy.ps1`.
- **Lệch index**: `/knowledge/stats` trả `orphans`, `untracked`, `duplicates`, `hash_mismatch`; doctor `POST /sync?rebuild=1`.
- **Trash**: `data/knowledge_trash/<doc_id>/<v>.md` + `status=trashed`, purge 30 ngày theo pattern `video-tmp-sweep`.
- **Backup**: DB snapshot gồm index+versions+blobs; file gốc qua git + rclone; không đưa `.env` lên Drive.
- **Log**: `knowledge.op action doc actor ms result`; đếm 409/413/429, `sync_duration`, `orphan_count`.

## 8. So sánh & chốt

| Tiêu chí (trọng số) | A | B | C |
|---|---|---|---|
| Search/phân loại (25) | 1 | 5 | 5 |
| Agent+git+rsync (30) | 5 | 5 | 1 |
| Version/audit/trash (20) | 0 | 4 | 5 |
| Backup (15) | 2 | 4 | 5 |
| Vận hành đơn giản (10) | 5 | 3 | 2 |
| **Tổng** | **2.6** | **4.5** | **3.5** |

**Chốt B**, 3 pha: **P1** sources+docs+FTS5+read/list/search+sync+alias read-me, UI đọc & search · **P2** versions/blobs/events, ETag/If-Match, trash/restore, edit modal, history · **P3** dedupe/canonical, publish, stats/doctor, rate limit, sweep.

Xem lại khi >5.000 doc → Postgres + `pg_trgm`; nếu agent không ghi qua FS → write-through.
