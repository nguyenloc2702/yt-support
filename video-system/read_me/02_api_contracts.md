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
