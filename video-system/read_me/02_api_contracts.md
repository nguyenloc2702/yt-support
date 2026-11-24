# Toàn Bộ Contract API Hệ Thống (`https://vidfac.gacdero.vn`)

> Mọi request cần header `Authorization: Bearer <API_KEY>` (hoặc cookie phiên web PIN).
> Phân quyền: `read` < `ingest` < `admin`.

---

## 1. TODO Jobs & Assembly (`#/todos`)
| Method | Endpoint | Scope | Mô tả |
|---|---|---|---|
| `GET` | `/api/v1/todos?status=...&limit=100` | `read` | Liệt kê jobs (`todo`, `ready_to_assemble`, `assembled`, `completed`) |
| `GET` | `/api/v1/todos/{item_id}` | `read` | Chi tiết job, `shots[]`, `video_progress`, `has_thumbnail`, `youtube_upload` |
| `POST` | `/api/v1/todos/from-script` | `ingest` | Tạo job EN mới từ `script_text` hoặc `shots[]` + tự synth TTS & BGM |
| `POST` | `/api/v1/todos/{item_id}/images` | `ingest` | Upload ảnh `keyframe` / `character` / `reference` / **`thumbnail`** (`multipart/form-data` hoặc JSON `base64_data`/`file_url`) |
| `GET` | `/api/v1/todos/{item_id}/images/{seq_num}/file?kind=thumbnail` | `read` | Xem/tải file ảnh theo `kind` |
| `POST` | `/api/v1/todos/{item_id}/videos` | `ingest` | Upload clip MP4 cho shot (`seq_num`, `covered_shot_indexes`) |
| `POST` | `/api/v1/todos/{item_id}/confirm-assemble` | `ingest` | Ráp thành phẩm `final_en.mp4` (`burn_subtitles`, `allow_shortfall`, `watermark_enabled`, `watermark_text`, `allow_missing_thumbnail`) |
| `GET` | `/api/v1/todos/{item_id}/final-video` | `read` | Tải `final_en.mp4` |

### Lưu ý Thumbnail bắt buộc (`kind=thumbnail`)
- Gửi `POST /api/v1/todos/{item_id}/images` với `kind="thumbnail"` (mặc định `seq_num=1`).
- Nếu chưa có thumbnail, `confirm-assemble` trả về **HTTP `409`** (`{"error": "thumbnail_missing"}`), trừ khi truyền `allow_missing_thumbnail: true`.

---

## 2. Bộ Sưu Tập Nhạc Nền (`BGM Library`)
| Method | Endpoint | Scope | Mô tả |
|---|---|---|---|
| `POST` | `/api/v1/bgm` | `ingest` | Thêm nhạc vào kho (`multipart/form-data` hoặc JSON `base64_data`/`file_url`/`file_path`). Tự convert sang `.mp3`, tự đo `duration_sec`, `lufs`, `true_peak_dbtp` và ghi `LOUDNESS.json`. |
| `GET` | `/api/v1/bgm` | `read` | Liệt kê & lọc nhạc (`?mood=&track_type=&beat_role=&genre=&q=`) |
| `GET` | `/api/v1/bgm/{track_id}/file` | `read` | Nghe/tải file `.mp3` |
| `DELETE` | `/api/v1/bgm/{track_id}` | `admin` | Xóa track khỏi DB, đĩa và `LOUDNESS.json` |

**Các field của `POST /api/v1/bgm`:**
- `title` *(bắt buộc)*, `mood` *(bắt buộc: `mysterious | nostalgic | gentle | energetic | quirky | ominous | triumphant | celebratory`, hoặc `allow_custom_mood=true`)*
- `track_type`: `bgm` *(mặc định)* · `sfx` · `ambient` · `stinger`
- `beat_role`: `hook` · `setup` · `rising` · `crisis` · `breakthrough` · `payoff`
- `genre`, `bpm` (20–300), `energy`, `tags`, `artist`, `license`, `source_url`, `note`, `lufs`, `true_peak_dbtp`, `overwrite`

---

## 3. YouTube Channel & Publish (`#/youtube`)
| Method | Endpoint | Scope | Mô tả |
|---|---|---|---|
| `GET` | `/api/v1/youtube/status` | `read` | Trạng thái cấu hình, kênh đang kết nối, quota hôm nay |
| `GET` | `/api/v1/youtube/auth/start` | `read` | Lấy OAuth URL Google |
| `GET` | `/api/v1/youtube/auth/callback` | public (HMAC state) | Callback Google OAuth |
| `DELETE` | `/api/v1/youtube/channel` | `admin` | Ngắt kết nối & revoke token |
| `GET` | `/api/v1/youtube/videos` | `read` | Video trên kênh + lịch sử publish |
| `PATCH` | `/api/v1/youtube/videos/{video_id}` | `admin` | Sửa `title`, `description`, `tags`, `privacy_status` (GET-merge-UPDATE) |
| `POST` | `/api/v1/todos/{item_id}/youtube/publish` | `admin` | Resumable upload `final_en.mp4` + thumbnail + `.srt` (`publish_at` >= 10 phút tương lai) |
| `GET` | `/api/v1/youtube/uploads/{upload_id}` | `read` | Poll tiến độ `%` upload |
| `POST` | `/api/v1/youtube/uploads/{upload_id}/retry` | `admin` | Thử lại upload lỗi |
| `POST` | `/api/v1/youtube/uploads/{upload_id}/cancel` | `admin` | Hủy upload đang chạy |

---

## 4. Kho Tài Liệu & Log `read_me` (CRUD)
| Method | Endpoint | Scope | Mô tả |
|---|---|---|---|
| `GET` | `/api/v1/read-me?prefix=&q=` | `read` | Liệt kê tất cả file trong `read_me/` |
| `GET` | `/api/v1/read-me/{doc_path}` | `read` | Đọc nội dung file |
| `POST` | `/api/v1/read-me` | `ingest` | Tạo file mới (`path`, `content`, `overwrite=false`) |
| `PUT` | `/api/v1/read-me/{doc_path}` | `ingest` | Tạo mới hoặc ghi đè toàn bộ file (`content`) |
| `PATCH` | `/api/v1/read-me/{doc_path}` | `ingest` | Chỉnh sửa / nối thêm log (`mode`: `append \| prepend \| replace_text \| overwrite`) |
| `DELETE` | `/api/v1/read-me/{doc_path}` | `admin` | Xóa file |

---

## 4b. Knowledge Library (`/library`)

> Kho tổng hợp **toàn bộ** tài liệu Markdown của hệ thống (read_me, docs, skills, plans, root).
> FS là source of truth; DB chỉ là index + version + audit. Đọc `/read_me/09_knowledge_library.md`
> trước khi dùng — contract dưới đây chỉ là bảng tra cứu nhanh.
> **Endpoint `/api/v1/read-me` ở mục 4 vẫn giữ nguyên**, không bị thay thế.
> Contract gọn, tự đọc được qua URL cho agent: `GET /api/v1/knowledge/contract?format=raw`
> (JSON index khi không có `?format=raw`). Nguồn: `KNOWLEDGE_AGENT_CONTRACT.md`.

| Method | Endpoint | Scope | Mô tả |
|---|---|---|---|
| `GET` | `/api/v1/knowledge/contract` | `read` | Contract agent-facing; `?format=raw` → Markdown thuần |
| `GET` | `/api/v1/knowledge/sources` | `read` | Danh sách source (`alias`, `path`, `mode` `rw`/`ro`, `doc_count`) |
| `GET` | `/api/v1/knowledge/docs?q=&source=&category=&status=&tag=&limit=&offset=` | `read` | Liệt kê / tìm kiếm (`limit` mặc định 50, trần 200) |
| `GET` | `/api/v1/knowledge/stats` | `read` | Tổng số, dung lượng, theo source/status, nhóm trùng lặp |
| `GET` | `/api/v1/knowledge/docs/{doc_id}` | `read` | Đọc tài liệu; trả `ETag` ở header, `content` + `secret_redacted` ở body |
| `GET` | `/api/v1/knowledge/docs/{doc_id}/raw` | `read` | Tải `.md` gốc (`text/markdown`, attachment) |
| `GET` | `/api/v1/knowledge/docs/{doc_id}/versions` | `read` | Lịch sử version (mới nhất trước, tối đa 20) |
| `POST` | `/api/v1/knowledge/docs` | `ingest` | Tạo tài liệu (`source`, `path`, `content`, `title`, `category`, `tags`, `source_refs`, `overwrite`) |
| `POST` | `/api/v1/knowledge/docs/{doc_id}/fork` | `ingest` | Copy tài liệu repo (`ro`) sang `library` để sửa được |
| `PUT` | `/api/v1/knowledge/docs/{doc_id}` | `ingest` | Ghi đè toàn bộ — **bắt buộc `If-Match`** |
| `PATCH` | `/api/v1/knowledge/docs/{doc_id}` | `ingest` | `append`/`prepend` không cần `If-Match`; `replace_text`/`overwrite` thì có |
| `DELETE` | `/api/v1/knowledge/docs/{doc_id}` | **owner** | Xoá mềm vào `.trash/<doc_id>/` |
| `POST` | `/api/v1/knowledge/docs/{doc_id}/restore` | **owner** | Khôi phục từ trash |
| `POST` | `/api/v1/knowledge/docs/{doc_id}/versions/{n}/restore` | **owner** | Khôi phục nội dung version `n` (tạo version mới) |
| `POST` | `/api/v1/knowledge/docs/{doc_id}/publish` | **owner** | `status`: `published \| canonical` |
| `POST` | `/api/v1/knowledge/sync` | `admin` | Re-index từ đĩa; body tuỳ chọn `{"prune_missing": true}` |

**Mã lỗi máy đọc được** (`body.code`):

| HTTP | `code` | Nghĩa / cách xử lý |
|---|---|---|
| `403` | `read_only` | Tài liệu repo không sửa trực tiếp — gọi `/fork` rồi sửa bản fork |
| `403` | `published_locked` | Bản `library` đã publish; chỉ owner sửa |
| `403` | `owner_only` | Thao tác xoá/publish/restore chỉ owner |
| `409` | `doc_exists` | Đã có tài liệu cùng `path`; dùng `overwrite: true` hoặc `/fork` |
| `409` | `target_not_found` | `replace_text` không thấy `target_text` — đọc lại tài liệu |
| `409` | `already_trashed` / `not_trashed` | Trạng thái trash không khớp |
| `410` | `doc_trashed` | Đã xoá mềm — `/restore` nếu cần |
| `412` | `hash_mismatch` | `If-Match` cũ — **đọc lại, hợp nhất, gửi lại** (đừng retry y nguyên) |
| `415` | `unsupported_media_type` | Chỉ `.md`, không NUL/binary |
| `422` | `bad_path` | Đường dẫn thoát root / chứa `..` / absolute / symlink |
| `422` | `secret_detected` | Nội dung chứa token thật — thay bằng placeholder |
| `428` | `missing_if_match` | Thiếu `If-Match` cho PUT/`overwrite`/`replace_text` |
| `404` | `doc_not_found` / `source_not_found` / `version_not_found` / `trash_missing` | ID/source/version/trash không tồn tại |
| `413` | `doc_too_large` | Vượt 5MB |
| `422` | `bad_mode` / `bad_status` / `target_text_required` / `validation_error` | Sai field body |

Bảng này không đầy đủ mọi `code` (Pydantic còn có thêm vài mã), nhưng đây là những mã
agent sẽ gặp thường xuyên nhất.

**ETag** dạng `"<sha256[:16]>-v<n>"`. Gửi lại **nguyên chuỗi** trong header `If-Match`.
Chỉ chấp nhận ETag đúng định dạng hoặc `If-Match: *` (khi chắc chắn mình là writer duy nhất) —
**prefix cắt ngắn, `""` hay `-v0` bị từ chối 412**, nếu không stale writer có thể đè bản mới.

