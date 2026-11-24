# Knowledge Library — bản chốt định hướng

> Ngày: 2026-11-24 · Trạng thái: PROPOSED, chờ owner duyệt scope · Đầu vào: PO/BA, Architecture, Senior Dev.

## 1. Kết luận một câu

**Không xây lại từ đầu.** Hệ thống đã có `/api/v1/read-me` CRUD file và `ReadMeService`; thiếu là **tầng thư viện**: index toàn bộ tài liệu, metadata, tìm kiếm, phân loại, version/audit, UI đọc và một vùng ghi an toàn cho agent.

**Chốt phương án B — Filesystem là source of truth, SQLite là index + sổ version/audit tái tạo được.**

- Tài liệu trong repo (`read_me/`, `docs/`, `skills/`, `plans/`, root `*.md`): **index để đọc và tìm, mặc định read-only**.
- Kho thư viện do agent tổng hợp: **một root riêng, ghi được qua API** (`data/knowledge/`), có `source_refs` trỏ về tài liệu gốc.
- API mới `/api/v1/knowledge`; giữ `/api/v1/read-me` làm bề mặt tương thích, không phá agent đang chạy.
- UI mới `#/library` trong SPA hiện tại.

Lý do: không thể lấy DB làm nguồn duy nhất vì repo đang dùng git + rsync; cũng không thể chỉ dùng filesystem vì search hiện tại quét full text từng file, không ranking, không version, không audit, không dedupe.

## 2. Ba vai trò đã thống nhất gì

| Vai trò | Kết luận chính |
|---|---|
| **PO/BA** | Vấn đề không phải thiếu trang Markdown mà là thiếu curation + provenance: không biết bản nào chuẩn, bản nào chết, ai được sửa. MVP phải có `source_root`, `status`, `owner`, `sha256`, soft delete và audit. |
| **Architecture** | Chọn FS + SQLite index. Thêm bounded context `/api/v1/knowledge`, giữ `/api/v1/read-me` làm alias. Version/audit/trash nằm trong DB; bản sống vẫn trên FS. |
| **Senior Dev** | Tái dùng `ReadMeService` làm lớp I/O thô, bọc `KnowledgeService`; không viết lại. Ước lượng ~9 người-ngày, MVP đọc + ghi cơ bản khoảng 5,5–7 ngày. |

### Điểm phải sửa so với đề xuất ban đầu

1. **Không cho agent sửa trực tiếp `docs/`, `skills/`, `plans/` trên prod.** App chạy ở `/opt/video-system`, các thư mục này được rsync từ repo; sửa trên prod sẽ bị deploy ghi đè. Chỉ `read_me/` (legacy) và `data/knowledge/` (mới) là writable.
2. **Không tự viết Markdown parser 120 dòng.** Dùng `marked` + `DOMPurify` vendor trong `frontend/vendor/`, không dùng CDN. Tự viết parser sẽ vỡ bảng, code fence, link, HTML.
3. **MVP chưa cần FTS5.** Kho hiện tại chỉ hơn 130 file; metadata + content cache + `LIKE`/`instr` là đủ (<1s). FTS5 để Phase 2 khi >500 doc hoặc search p95 >300ms.
4. **Không copy tràn lan tài liệu gốc vào kho.** Chỉ tạo bản tổng hợp khi agent/owner thực sự cần, có `source_refs`; tránh hai bản lệch nhau.

## 3. Kiến trúc đích

```
Agents / Owner
      │
      ▼
┌──────────────────────── FastAPI :8090 ────────────────────────┐
│  /api/v1/knowledge        /api/v1/read-me (legacy alias)      │
│        │                          │                           │
│        ▼                          ▼                           │
│  KnowledgeService  ── resolver · indexer · search · ledger    │
└───────┬───────────────────────────────────────────┬───────────┘
        │                                           │
        ▼                                           ▼
  FS = source of truth                    SQLite WAL (data/)
  ├─ read_me/        (rw legacy)           ├─ knowledge_docs
  ├─ data/knowledge/ (rw library)          ├─ knowledge_versions
  ├─ docs/           (ro)                  ├─ knowledge_events
  ├─ skills/         (ro)                  └─ knowledge_sync_runs
  ├─ plans/          (ro)
  └─ root *.md       (ro)
```

### Vì sao tách `data/knowledge/`

- Không bị rsync/deploy ghi đè.
- Là nơi agent tạo bản tổng hợp, owner review/publish.
- Có thể backup lên Drive cùng DB sau này.
- Tài liệu gốc vẫn là chân lý kỹ thuật; bản trong library là “bản biên soạn”, có provenance.

## 4. Mô hình dữ liệu tối thiểu

| Bảng | Cột chính | Ghi chú |
|---|---|---|
| `knowledge_sources` | `alias`, `abs_path`, `mode`, `priority`, `globs` | `read_me` rw, `library` rw, `docs/skills/plans/root` ro |
| `knowledge_docs` | `id`, `source_alias`, `rel_path`, `title`, `category`, `tags_json`, `status`, `owner`, `generated_by`, `source_refs_json`, `current_sha256`, `size_bytes`, `mtime`, `version_no`, `duplicate_of`, `deleted_at` | `UNIQUE(source_alias, rel_path)`; index `sha256`, `status` |
| `knowledge_versions` | `id`, `doc_id`, `version_no`, `sha256`, `content_z`, `author`, `note`, `created_at` | Giữ 20 bản gần nhất; `UNIQUE(doc_id, version_no)` |
| `knowledge_events` | `id`, `doc_id`, `actor`, `action`, `from_sha`, `to_sha`, `meta_json`, `created_at` | Audit mọi create/update/delete/restore/publish |
| `knowledge_sync_runs` | `id`, `trigger`, `started_at`, `finished_at`, `counts_json`, `errors_json` | Theo dõi index drift sau deploy |

`content_z` chỉ lưu **bản version** (gzip), không lưu bản sống; bản sống đọc từ FS. DB có thể xoá và rebuild index bất cứ lúc nào.

## 5. API đề xuất

Base `/api/v1/knowledge`. Auth giữ nguyên `read < ingest < admin`; browser dùng session PIN.

| Method | Path | Scope | Mục đích |
|---|---|---|---|
| `GET` | `/sources` | read | Danh sách root, root nào read-only |
| `GET` | `/docs?q=&root=&category=&status=&tag=&limit=&cursor=` | read | List/search có phân trang |
| `GET` | `/docs/{doc_id}` | read | Metadata + content + `ETag` |
| `GET` | `/docs/{doc_id}/raw` | read | Tải `.md` |
| `POST` | `/docs` | ingest | Tạo bản library mới (`path`, `content`, `source_refs`) |
| `POST` | `/docs/{doc_id}/fork` | ingest | Tạo bản library từ tài liệu repo read-only |
| `PUT` | `/docs/{doc_id}` | ingest | Ghi đè, bắt buộc `If-Match` |
| `PATCH` | `/docs/{doc_id}` | ingest | `append/prepend/replace_text/overwrite` |
| `DELETE` | `/docs/{doc_id}` | admin | Soft delete vào trash 30 ngày |
| `POST` | `/docs/{doc_id}/restore` | admin | Khôi phục từ trash |
| `GET` | `/docs/{doc_id}/versions` | read | Lịch sử version |
| `POST` | `/docs/{doc_id}/versions/{n}/restore` | admin | Restore version cũ |
| `POST` | `/docs/{doc_id}/publish` | admin | draft → reviewed/canonical |
| `POST` | `/sync` | admin | Quét lại FS, reconcile index |
| `GET` | `/stats` | read | Tổng doc, orphan, duplicate, hash mismatch |

### Quy ước lỗi

`400 bad_path`, `403 forbidden_scope`, `404 doc_not_found`, `409 doc_exists|version_conflict`, `412 hash_mismatch`, `413 doc_too_large`, `415 unsupported_media_type`, `422 validation_error`, `503 knowledge_disabled`.

### Concurrency

- `ETag = "sha256[:16]-v{n}"`.
- `PUT`/`PATCH` sửa nội dung phải gửi `If-Match`; thiếu → `428`, lệch → `412` kèm ETag mới.
- Ghi atomic: temp file → `os.replace()`; lock theo `doc_id`; hash tính từ bytes đã ghi.
- `POST` dùng `Idempotency-Key` sẵn có.

## 6. UI `#/library`

- Nav mới: **📚 Thư viện**.
- Desktop 3 cột: cây nguồn + filter (240px) · list/reader · TOC + metadata (200px).
- Reader: title, badge `repo/read-only` hoặc `library/draft/reviewed/canonical`, owner, updated, `source_refs`, copy path, tải `.md`, sửa/xóa/lịch sử.
- Editor: split textarea/preview; lưu kèm `If-Match`; 412 hiện banner “tài liệu đã bị sửa”, cho xem bản mới hoặc ghi đè có xác nhận.
- Search debounce 250–300ms; empty/loading/error state rõ; mobile dùng drawer + single column.
- Markdown: `marked` + `DOMPurify` vendor, `html:false`, link allowlist `http(s)`, `mailto`, `#/library/...`; không render raw HTML của agent.
- Deep link: `#/library`, `#/library/{doc_id}`; back/forward hoạt động.

## 7. Phân quyền & an toàn

| Hành động | Ai | Điều kiện |
|---|---|---|
| Đọc/tìm | mọi scope `read` | Không index `.env`, credential, binary; redact secret patterns |
| Tạo bản library | `ingest` | Chỉ `data/knowledge/`; bắt buộc title, category, `generated_by` |
| Sửa bản library | author/owner | `If-Match`; audit; không sửa bản `published` nếu không phải owner |
| Sửa tài liệu repo | owner/dev qua git | UI chỉ đọc; muốn sửa thì `fork` sang library hoặc commit vào repo |
| Xóa | admin/owner | Soft delete + restore 30 ngày |
| Publish/canonical | owner | Bắt buộc với contract, skill, onboarding |
| Sync/reindex | admin | Có `sync_runs` + stats |

Chặn path traversal: chuẩn hoá `\` → `/`, chặn `..`, absolute path, NUL, symlink; `resolve()` + `is_relative_to(root)`.

## 8. Phạm vi triển khai

### MVP (nên làm trước, ~6–7 người-ngày)

1. Schema + `KnowledgeService` + index các root read-only.
2. API read: sources, list/search, get, raw, stats.
3. UI `#/library`: tree, search, reader, TOC, badge read-only.
4. API write chỉ cho `data/knowledge/`: create/fork/update/delete mềm, audit, `If-Match`.
5. UI editor + trash/restore cơ bản.
6. Giữ `/api/v1/read-me` nguyên vẹn; test regression.

### Phase 2 (~3 người-ngày)

- Version history + diff + restore.
- Publish/review workflow draft → reviewed → canonical.
- Dedupe/canonical cho `skills/` vs `video-system/skills/`.
- FTS5 + ranking/snippet nếu search chậm.
- `stats`/doctor, sync sau deploy, rate limit, backup library lên Drive.

### Non-goals

- Không thành wiki/CMS đầy đủ.
- Không upload binary/ảnh/video.
- Không đồng bộ hai chiều git.
- Không sửa `plans/archive/`.
- Không thay git làm lịch sử chuẩn của tài liệu kỹ thuật.

## 9. Rủi ro lớn nhất

1. **Hai nguồn sự thật**: sửa tài liệu repo trên prod sẽ bị deploy ghi đè. Giải pháp: repo docs read-only, library root riêng.
2. **Trùng lặp `skills/` và `video-system/skills/`**: đã lệch nội dung. Giải pháp: hash + badge duplicate, owner chọn canonical, không tự xóa.
3. **Rò rỉ secret**: kho “tất tần tật” rất dễ index `.env`, token, log. Giải pháp: allowlist extension, block pattern, redact trước khi trả content.
4. **Markdown XSS**: agent có thể tạo HTML độc hại. Giải pháp: DOMPurify, `html:false`, link allowlist.
5. **Agent ghi rác**: cần quota, rate limit, draft mặc định, owner publish.
6. **SPA phình to**: `index.html` đã 380KB. Giải pháp: tách `knowledge.js`/`knowledge.css`, mount static; không nhồi thêm 1.000 dòng vào file chính.

## 10. Đề xuất chốt để owner quyết

**Nên duyệt MVP theo hướng A:**

- **A. Hybrid library (khuyến nghị)**: đọc toàn bộ tài liệu repo; chỉ ghi trong `data/knowledge/` + `read_me/` legacy; UI `#/library`; API `/api/v1/knowledge`.
- **B. Full-write mọi root**: đáp ứng “sửa xóa như thật” cho tất cả tài liệu, nhưng chấp nhận rủi ro deploy ghi đè và agent phá contract. Không khuyến nghị.
- **C. UI-only trên read-me cũ**: nhanh nhất (~2 ngày), nhưng không giải quyết phân mảnh, không search toàn kho, không version/audit.

Nếu chọn A, thứ tự triển khai: schema/service → API read → UI read → write library → audit/trash → test → deploy. Không cần migration phá dữ liệu; có thể rollback bằng `VIDEO_KNOWLEDGE_ENABLED=0`.

---

## 11. Kết quả triển khai (cập nhật sau khi code)

Owner chốt **Option A**. MVP đã dựng xong; phần dưới ghi lại những chỗ **code lệch so với
kế hoạch ban đầu** để người sau không ngạc nhiên.

### Đã làm

| Hạng mục | File |
|---|---|
| Schema 5 bảng | `backend/app/store.py` (`knowledge_sources/docs/versions/events/sync_runs`) |
| Service | `backend/app/service_knowledge.py` (~700 dòng) |
| Schema request | `backend/app/schemas.py` (6 model) |
| 15 route + static mount + `/library` | `backend/app/main.py` |
| Trang độc lập | `frontend/library.html`, `library.css`, `library.js` |
| Thư viện vendor | `frontend/vendor/marked.min.js` (12.0.2), `purify.min.js` (3.1.6) |
| Tài liệu agent | `read_me/09_knowledge_library.md`, `read_me/02_api_contracts.md` §4b |
| Test | `tests/test_knowledge_{api,search,security,concurrency}.py` (49 case) |

### Bốn chỗ sửa so với kế hoạch

1. **`published_locked` chỉ áp cho source `library`.**
   Kế hoạch nói "published thì chỉ owner sửa". Nhưng `read_me` lấy status `published` từ
   *index mặc định*, không phải quyết định publish của ai. Áp nguyên tắc sẽ chặn đúng luồng
   agent append log hằng ngày — tức phá compatibility mà Option A hứa giữ.

2. **`status` không đọc từ front-matter.**
   Kế hoạch cho front-matter override metadata. Nhưng nếu file tự khai `status: canonical`
   thì một tài liệu chưa ai duyệt có thể tự phong canonical rồi khoá đường sửa của agent
   khác. Giờ `status` chỉ đến từ `POST /publish` (owner) và trạng thái trash; front-matter
   vẫn override `title`/`category`/`tags`/`source_refs`.

3. **Thêm guard ghi (`_assert_writable`) + khoá read-modify-write.**
   Kế hoạch không nói tới. Phát hiện khi viết test: (a) API ghi được file >5MB hoặc chứa NUL,
   nhưng lần đọc sau đó fail 413/415 → tài liệu thành ngõ cụt; (b) hai agent `append` song
   song nuốt mất một dòng vì đọc-ghi không nguyên tử. Cả hai đã khoá bằng `RLock` + guard.

4. **Không thêm bước sync vào CI.**
   Kế hoạch đề xuất `POST /knowledge/sync` sau deploy. Thực tế không cần: `_last_sync` khởi
   tạo bằng 0 nên **lần đọc đầu tiên sau restart tự sync toàn bộ**. Gọi tay chỉ để làm nóng.
   Thêm vào CI sẽ phải nhét admin key vào script deploy — đổi lợi ích rất nhỏ lấy một secret
   mới trong pipeline, không đáng.

### Còn để Phase 2

FTS5 + ranking, workflow review, UI diff version, dedupe/canonical cho `skills/` trùng,
rate limit, backup `data/knowledge/` lên Drive.
