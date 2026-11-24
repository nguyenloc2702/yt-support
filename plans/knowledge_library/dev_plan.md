# Knowledge Library — Kế hoạch triển khai chi tiết

Mục tiêu: một page "Knowledge Library" đọc được trên web, quản lý kho tài liệu Markdown toàn hệ thống (nạp / sửa / xóa / tìm kiếm / phân loại), **không phá** `/api/v1/read-me` hiện hữu.

---

## 1. Gap analysis

**Đã có (tái dùng, không viết lại)**

| Thành phần | Vị trí | Dùng cho |
|---|---|---|
| `_resolve_safe_path` (chặn `..`, allowlist ext, chống escape root) | `service_readme.py:29` | Path safety cho Knowledge |
| `_meta` (title từ heading, size, sha256, mtime) | `service_readme.py:43` | Metadata cơ bản, `content_hash` |
| `get_read_me_dir()` + env `VIDEO_READ_ME_DIR` | `service_readme.py:15` | Root cấu hình được |
| Scope `read < ingest < admin`, session PIN | `auth.py` | Auth endpoint |
| `_idempotent` / `_idempotent_call` + bảng `idempotency` | `main.py:187-313` | POST/PATCH replay an toàn |
| `Store` (SQLite WAL, `execute/query_one/cas_update`) | `store.py` | Index + version + metadata |
| `apiFetch`, `esc`, `showToast`, `showStatusBanner`, CSS `panel/toolbar/search-input` | `frontend/index.html` | Toàn bộ UI |
| Hash router + `setActiveNav` + `pageX`/`goXPage` | `index.html:3904-3942`, `7365-7410` | Route `#/knowledge` |

**Thiếu**
- UI: không có page, nav, reader, editor, tree.
- Metadata: category, tags, summary, owner, status (draft/published), `content_hash` (đã có sha256 nhưng không dùng làm ETag).
- Versioning, soft-delete/trash, audit log.
- Search: `list_docs` đọc **toàn bộ file** mỗi request (`service_readme.py:77`) — O(n) và không index; không phân trang, không sort, không lọc category/tag.
- Concurrency: ghi trực tiếp `write_text` — race mất nội dung; không `If-Match`, không lock.
- Markdown render an toàn, TOC, backlink, download `.md`, sync/reindex.

**Quyết định tái dùng**: giữ `ReadMeService` là lớp I/O thô. Tạo **`KnowledgeService`** (composition, `self.readme = ReadMeService()`) chịu trách nhiệm index/metadata/version/search. Chỉ refactor nhỏ: tách `_resolve_safe_path` thành hàm module `resolve_doc_path(rel, exts)` để hai service dùng chung — hành vi `read-me` không đổi.

---

## 2. Phases (mỗi phase ship được độc lập)

| Phase | Nội dung | Ship khi |
|---|---|---|
| **P0 – Nền** | Tách `resolve_doc_path`; thêm bảng `knowledge_docs`, `knowledge_versions`, `knowledge_fts`; `KnowledgeService.index_all()/upsert()/search()`; CLI reindex | `pytest tests/test_readme_crud.py` vẫn xanh + index đúng số file |
| **P1 – Read API** | `GET /tree`, `GET /docs`, `GET /docs/{path}`, `/raw`, `/tags`; pagination cursor; ETag | Curl đọc được 8 file `read_me/*.md` |
| **P2 – Write API** | `POST/PUT/PATCH/DELETE` + `If-Match` + `Idempotency-Key` + version snapshot + trash | Postman CRUD + 412/409 đúng |
| **P3 – UI read** | Nav "📚 Knowledge", `#/knowledge`, list + tree + search + reader (TOC, render an toàn), empty/loading/error, mobile drawer | Đọc 8 doc trên điện thoại |
| **P4 – UI write** | Editor modal (textarea + preview), tạo/sửa/xóa, xử lý 412 (báo "đã bị sửa"), upload `.md` | Sửa 1 doc, version tăng |
| **P5 – Sync/ops** | `POST /knowledge/sync`, version list/restore, backfill prod, tài liệu agent | Reindex prod không downtime |

---

## 3. File cần tạo/sửa

**Tạo**
- `video-system/backend/app/service_knowledge.py` — `KnowledgeService`: index, search (FTS5 → LIKE fallback), metadata/front-matter, version snapshot (giữ 20 bản), trash, sync, `content_hash`.
- `video-system/backend/app/service_docpath.py` — `resolve_doc_path()`, `sha256_bytes()`, `MAX_DOC_BYTES` dùng chung.
- `video-system/tests/test_knowledge_api.py` — CRUD + scope + lỗi.
- `video-system/tests/test_knowledge_search.py` — FTS, fallback, phân trang, sort, filter.
- `video-system/tests/test_knowledge_security.py` — traversal, symlink, XSS payload.
- `video-system/tests/test_knowledge_concurrency.py` — 2 PATCH song song, 412, atomic write.
- `video-system/read_me/09_knowledge_library.md` — hướng dẫn agent dùng API mới.

**Sửa**
- `backend/app/store.py` — thêm 3 bảng + index vào `_SCHEMA` (idempotent, không cần ALTER).
- `backend/app/main.py` — block route sau read-me (dòng ~2822), khởi tạo `knowledge = KnowledgeService(store)`.
- `backend/app/schemas.py` — `KnowledgeCreate/Put/Patch/MetaUpdate/Restore`.
- `frontend/index.html` — nav button, `#pageKnowledge`, `goKnowledgePage/openKnowledgeReader`, `renderMarkdownSafe`, CSS `.kb-*`.
- `read_me/02_api_contracts.md` — mục "4b. Knowledge Library".
- `plans/knowledge_library/dev_plan.md` — file này.

---

## 4. API contract

Base `/api/v1/knowledge`. Auth như cũ; browser cần session PIN.

| Method | Path | Scope | Request | Response |
|---|---|---|---|---|
| GET | `/tree?depth=3` | read | — | `{nodes:[{path,name,is_dir,doc_count,children?}]}` |
| GET | `/docs?q=&category=&tag=&status=&sort=updated_desc&limit=50&cursor=` | read | — | `{total,next_cursor,docs:[{path,title,summary,category,tags,size_bytes,content_hash,updated_at,status}]}` |
| GET | `/docs/{path}?version=` | read | — | `{...meta, content, content_hash}` + `ETag` |
| GET | `/docs/{path}/raw` | read | — | `text/markdown` attachment |
| POST | `/docs` | ingest | `{path,content,category?,tags?,summary?,overwrite=false}` + `Idempotency-Key` | `201` meta; `409 doc_exists` |
| PUT | `/docs/{path}` | ingest | `{content,meta?}` + `If-Match: <hash>` (bắt buộc) | `200` meta mới; `412 hash_mismatch`; `428` thiếu header |
| PATCH | `/docs/{path}` | ingest | `{mode:append\|prepend\|replace_text\|overwrite,content,target_text?,replacement?}` + `If-Match` (khuyến nghị) | `200`; `409 target_not_found` |
| DELETE | `/docs/{path}?hard=false` | admin | — | `{deleted:true,trashed_to}` |
| GET | `/docs/{path}/versions` | read | — | `{versions:[{version,content_hash,author,created_at,size_bytes}]}` |
| POST | `/docs/{path}/versions/{v}/restore` | admin | — | `200` meta (tạo version mới) |
| POST | `/sync` | admin | `{prune_missing=true}` | `{scanned,added,updated,removed}` |
| GET | `/tags` `/stats` | read | — | tag counts / tổng doc, size, category |

**Lỗi** (shape `{detail, error}` như `ApiError`): `400 bad_path`, `404 doc_not_found`, `409 doc_exists|target_not_found|version_conflict`, `412 hash_mismatch`, `413 doc_too_large`, `415 unsupported_media_type`, `422 validation_error`, `423 doc_locked`, `503 knowledge_disabled`.

**Idempotency**: POST dùng `_idempotent_call` (claim trước, chốt sau). PUT/PATCH idempotent theo `content_hash`; replay cùng hash trả `200` + `Idempotency-Replayed: true`.

---

## 5. Thiết kế UI

Route: `#/knowledge` (list), `#/knowledge/<path>` (reader, path URL-encoded), `#/knowledge/new`, `#/knowledge/edit/<path>`.

Layout desktop 3 cột: **sidebar 240px** (tree theo category + tag filter) · **list/reader giữa** · **TOC 200px** (reader). Toolbar trên: search (debounce 300ms), chip category, select sort, nút "＋ Tài liệu mới", toggle "Chỉ của tôi".

Components: `kb-shell`, `kb-tree`, `kb-card` (title, summary 2 dòng, tag pills, thời gian, size), `kb-reader` (breadcrumb, h1, meta row, nút Sửa/Xóa/Tải `.md`/Lịch sử), `kb-editor` (split textarea/preview), `kb-empty` (icon + "Chưa có tài liệu — tạo mới hoặc đồng bộ"), skeleton 6 card khi loading, error panel dùng `showStatusBanner` + nút thử lại, `showToast` sau save/delete.

Mobile ≤768px: sidebar thành drawer (nút ☰), list full-width, reader single column, TOC ẩn sau nút "Mục lục"; editor chuyển tab Soạn/Xem trước. Sticky action bar dưới cho Save/Hủy.

**Markdown**: không thêm dependency runtime. Viết `renderMarkdownSafe()` (~120 dòng) trong `index.html`: **escape toàn bộ trước** (`esc`), rồi mới áp block rule (heading, list, code fence, table, blockquote, hr, link). Link chỉ cho `http(s)://`, `#/knowledge/...`, `mailto:`; chèn `rel="noopener noreferrer" target="_blank"`. Không render raw HTML. Nếu sau này cần GFM đầy đủ: **vendor** `marked` + `DOMPurify` vào `frontend/vendor/` và thêm route static — tuyệt đối không dùng CDN (prod offline, không CSP).

---

## 6. Test plan

- **Unit**: `resolve_doc_path` (unicode NFD, backslash, absolute, `..%2f`), hash ổn định, parse front-matter, prune version >20, FTS fallback khi thiếu FTS5.
- **API**: full CRUD; `total/next_cursor`; sort/filter; `POST` trùng → 409; `PUT` thiếu `If-Match` → 428; sai hash → 412; >2MB → 413; `.py` → 415; read key gọi POST → 403; ingest gọi DELETE → 403.
- **Frontend smoke**: Playwright (đã có) — login PIN → `#/knowledge` render ≥8 card → mở 1 doc → search → tạo → sửa → xóa; assert không có `Uncaught`.
- **Security**: symlink trỏ ra ngoài root bị từ chối; content chứa `<img onerror>` và `[x](javascript:alert(1))` phải render thành text/link bị loại.
- **Concurrency**: 2 thread PUT cùng hash → 1 thành công, 1 nhận 412; 50 PATCH append song song → không mất dòng (file lock).
- **Regression**: `test_readme_crud.py` giữ nguyên 100%; thêm test `/read-me` không thấy file trong trash, không lộ metadata mới.

---

## 7. Commit / deploy / rollback

1. `feat(store): knowledge tables + fts` → 2. `refactor(readme): shared safe path` → 3. `feat(knowledge): service + index` → 4. `feat(api): knowledge read/write` → 5. `test(knowledge): api/security/concurrency` → 6. `feat(ui): knowledge library page` → 7. `docs(knowledge): contracts + agent guide`.

**Migration/backfill**: bảng tạo bằng `CREATE TABLE IF NOT EXISTS` trong `_SCHEMA` (không ALTER, không downtime). Sau deploy gọi `POST /api/v1/knowledge/sync` (hoặc lazy index ở request đầu) để quét `read_me/` (8 file) + `docs/`. Không copy/duplicate file — file vẫn là source of truth, DB là index rebuild được.

**Rollback**: `VIDEO_KNOWLEDGE_ENABLED=0` → route trả 503, nav ẩn; hoặc revert commit UI. Bảng mới vô hại, `read_me` không đổi ⇒ rollback an toàn tuyệt đối. Version/trash giữ 30 ngày.

---

## 8. Effort & pitfalls

| Hạng mục | Người-ngày |
|---|---|
| Schema + service + index/FTS | 2.0 |
| API (read/write/version/sync) | 1.5 |
| Tests (4 file) | 1.5 |
| Frontend (list/reader/editor/mobile/renderer) | 3.0 |
| Docs + deploy + backfill prod | 1.0 |
| **Tổng** | **~9 người-ngày** (1 dev ≈ 2 tuần; MVP P0–P3 ≈ 5.5 ngày) |

**3 pitfall dễ gặp**
1. **Search tiếng Việt**: FTS5 `unicode61` không bỏ dấu — "tài liệu" ≠ "tai lieu"; có bản SQLite thiếu FTS5. → Chuẩn hóa cột `body_norm` (bỏ dấu, lowercase) + LIKE fallback + test cả hai.
2. **Path traversal qua symlink/encoding**: `resolve()` một mình không đủ nếu file là symlink hoặc `..%2F` được decode sau. → `resolve()` + `is_relative_to(root)` + `not is_symlink()` + từ chối `%2f` trong raw path.
3. **Lệch file ↔ index khi ghi đồng thời**: `write_text` không atomic, hash tính trước khi ghi → client `If-Match` sai. → ghi temp + `os.replace`, lock theo path (`threading.Lock` map), `content_hash` tính từ bytes đã ghi, cập nhật DB sau commit file.
