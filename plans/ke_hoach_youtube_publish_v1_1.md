# Kế hoạch v1.1: Liên kết Kênh YouTube & Đăng video trực tiếp (`#/youtube` + `#/todos`)

> Nâng cấp từ v1.0 sau review code thật: khắc phục các bẫy runtime của YouTube API
> (quota, `videos.update` merge, thumbnail timing), chốt bảo mật OAuth (HMAC state,
> Fernet token-at-rest), bền vững hoá resumable upload qua restart, và gắn sâu vào
> hệ thống auth/webhook/config sẵn có của backend.

## 0. Changelog v1.0 → v1.1

| # | Vấn đề ở v1.0 | Nâng cấp v1.1 |
|---|---|---|
| 1 | Chưa nhắc quota API | QuotaLedger đếm units/ngày, chặn upload khi cạn (~6 video/ngày quota mặc định) |
| 2 | Dùng `search`/`playlistItems` lẫn lộn | Chốt `playlistItems.list` trên uploads playlist (1 unit); **cấm `search.list`** (100 unit) |
| 3 | PATCH metadata đơn giản | **Bắt buộc GET-merge-UPDATE** (giữ `categoryId`), tránh mất snippet |
| 4 | OAuth `state` chưa rõ lưu đâu | HMAC-signed state từ `session_secret` sẵn có, expiry 10 phút, one-time |
| 5 | Chưa khai scopes | Chốt `youtube.upload` + `youtube.force-ssl` |
| 6 | Upload BackgroundTask "chạy và cầu nguyện" | Persist upload session URI + resume từ offset, xử lý HTTP 308, startup sweep record treo |
| 7 | Gọi `thumbnails.set` ngay sau upload | Retry sau poll `processingStatus`, kèm lưu ý kênh phải phone-verified |
| 8 | Mã hoá token "đối xứng" chung chung | Fernet key riêng `YOUTUBE_TOKEN_ENCRYPTION_KEY`, fail-fast nếu thiếu |
| 9 | Chống đăng trùng chỉ dựa vào `youtube_url` | Idempotency guard qua record `youtube_uploads` đang chạy |
| 10 | publishAt validate ở UI | Validate bắt buộc ở backend (tương lai ≥ 10 phút, force `private`) |
| 11 | (mới) Tách rời hệ event | Bắn event `youtube.*` vào hệ webhooks/events sẵn có của backend |
| 12 | (mới) Auth chưa đề cập | Toàn bộ endpoint mới đi qua lớp PIN/Bearer hiện có; publish/disconnect/patch cần scope `admin` |

---

## 1. Mục tiêu

Kết nối trực tiếp **kênh YouTube** vào `https://vidfac.gacdero.vn` qua **Google OAuth 2.0** (1-click) và thao tác toàn bộ vòng đời xuất bản trên web:

1. **Trang `#/youtube`**: kết nối/ngắt kết nối kênh, xem info kênh (avatar, tên, `@handle`, subs, tổng video), danh sách video trên kênh (views/likes/comments, `private`/`unlisted`/`public`), sửa nhanh metadata (title, description, tags, privacy).
2. **Đăng video từ `#/todos` (và `#/shorts`)**: khi job có `final_en.mp4` + ảnh thumbnail (`kind=thumbnail`), bấm **📺 Đăng lên YouTube** → hệ thống tự:
   - Resumable upload theo chunk (không tốn RAM VPS), có **resume qua restart**.
   - Gắn thumbnail của job sau khi video xử lý xong.
   - Tùy chọn đính kèm `subtitles_en.srt` của job.
   - Chọn `private`/`unlisted`/`public` hoặc hẹn giờ (`publishAt`).
   - Thanh tiến độ `%`, lưu `youtube_video_id`/`youtube_url` vào job, **chặn đăng trùng**, bắn webhook khi xong.

---

## 2. Thiết kế kiến trúc

### 2.1 Database ([store.py](../video-system/backend/app/store.py))

3 bảng mới (pattern `CREATE TABLE IF NOT EXISTS` + `_ensure_columns` hiện có):

```sql
CREATE TABLE IF NOT EXISTS youtube_channels (
  id TEXT PRIMARY KEY,
  channel_id TEXT UNIQUE NOT NULL,
  title TEXT, custom_url TEXT, thumbnail_url TEXT,
  subscriber_count INTEGER, video_count INTEGER,
  access_token_enc TEXT NOT NULL,        -- Fernet, KHÔNG lưu plaintext
  refresh_token_enc TEXT NOT NULL,       -- Fernet
  token_expires_at REAL, scopes TEXT,
  is_default INTEGER DEFAULT 1,
  connected_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS youtube_uploads (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,                 -- todo job
  channel_id TEXT NOT NULL,
  youtube_video_id TEXT, youtube_url TEXT,
  title TEXT, description TEXT, tags_json TEXT,
  privacy_status TEXT DEFAULT 'private', publish_at TEXT,
  upload_status TEXT DEFAULT 'queued',   -- queued|uploading|processing|uploaded|failed|canceled
  progress_pct REAL DEFAULT 0,
  bytes_uploaded INTEGER DEFAULT 0, total_bytes INTEGER,
  upload_session_uri TEXT,               -- để resume qua restart
  attempt_count INTEGER DEFAULT 0,       -- số lần retry chunk
  thumbnail_synced INTEGER DEFAULT 0, captions_synced INTEGER DEFAULT 0,
  error_message TEXT,
  created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_yt_uploads_item ON youtube_uploads(item_id);
CREATE TABLE IF NOT EXISTS youtube_quota_ledger (
  day TEXT PRIMARY KEY,                  -- 'YYYY-MM-DD' (giờ VN)
  units_used INTEGER DEFAULT 0
);
```

- `youtube_uploads.upload_session_uri` → **resume được** khi process restart (một session URI của Google sống ~7 ngày).
- `youtube_quota_ledger` → đếm units tiêu mỗi ngày vì **YouTube API không trả header quota** trong response.

### 2.2 Config & bảo mật ([config.py](../video-system/backend/app/config.py))

Env mới, theo đúng pattern hàm env hiện có:

| Env | Bắt buộc | Ý nghĩa |
|---|---|---|
| `YOUTUBE_CLIENT_ID` | để bật tính năng | OAuth client (thiếu → API trả `configured: false`, UI hiện "chưa cấu hình") |
| `YOUTUBE_CLIENT_SECRET` | để bật tính năng | OAuth secret |
| `YOUTUBE_TOKEN_ENCRYPTION_KEY` | **fail-fast khi publish/connect nếu thiếu** | Fernet key (`cryptography.fernet`, 32-byte base64). Sinh: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `YOUTUBE_UPLOAD_CHUNK_MB` | không, default `4` | Kích thước chunk (1–16, bội của 256KB) |

- **Dependency mới**: thêm `cryptography>=42` vào [pyproject.toml](../video-system/pyproject.toml) (duy nhất; `httpx>=0.27` đã có sẵn).
- **Scopes chốt**: `https://www.googleapis.com/auth/youtube.upload` (upload video) + `https://www.googleapis.com/auth/youtube.force-ssl` (đủ cho `channels.list`, `videos.update`, `thumbnails.set`, `captions.insert`). Không xin scope rộng hơn cần thiết.
- **OAuth `state` chống CSRF**: HMAC-SHA256 ký bằng `session_secret()` đã có trong auth.py, format `nonce.exp.sig`, **expiry 10 phút + one-time** (nonce đã dùng lưu set trong DB kv / in-process với TTL). Không lưu state trong RAM thuần (gãy khi restart/multi-worker).
- Callback URL suy từ `public_base_url()` hiện có → `https://vidfac.gacdero.vn/api/v1/youtube/auth/callback`.

### 2.3 Backend — [service_youtube.py](../video-system/backend/app/service_youtube.py) (file mới)

Dùng `httpx.AsyncClient`/`httpx.Client` gọi thẳng Google APIs.

**a) TokenManager** — lưu/mã hoá/gia hạn:
- Encrypt `access_token`/`refresh_token` bằng Fernet trước khi ghi DB; decrypt khi dùng.
- Mọi call API tự kiểm tra `token_expires_at`: sắp hết hạn (< 120s) → POST `oauth2.googleapis.com/token` với `refresh_token` (grant_type=refresh_token), ghi đè DB.
- Lock refresh trong-process (tránh refresh song song đốt token lần).
- Google trả `invalid_grant`/401 khi token bị thu hồi → mark `youtube_channels` disconnected, báo UI "kết nối đã hết hạn, kết nối lại".

**b) QuotaLedger** — chống cạn quota im lặng:
- Cost chuẩn: `videos.insert` 1600 · `captions.insert` 50 · `thumbnails.set` 50 · `videos.update` 50 · `videos.list` 1 · `playlistItems.list` 1 · `channels.list` 1.
- Mỗi call thành công cộng vào `youtube_quota_ledger` theo ngày (giờ VN).
- **Ngưỡng cứng 9.000 units/ngày**: khi thiếu units cho `videos.insert` (cần ≥ 1.600) → API publish trả `429` kèm ngày reset (giờ VN 0h), UI hiện cảnh báo. Quota console mặc định 10.000/ngày → ~6 video/ngày.

**c) Channel & video ops:**
- `channels.list?part=snippet,statistics,contentDetails&mine=true` → info kênh **+ uploads playlist ID** (`contentDetails.relatedPlaylists.uploads`).
- Danh sách video: `playlistItems.list` (uploads playlist, 1 unit) → gom video IDs → `videos.list?part=snippet,statistics,status` (1 unit). **Không dùng `search.list`** (100 unit/call).
- Sửa metadata `videos.update`: **luôn GET trước → merge** `snippet` (giữ nguyên `categoryId` + các field không sửa) → UPDATE cả `snippet` + `status`. Không được gửi partial snippet (YouTube replace toàn bộ → lỗi `invalidCategoryId`/mất categoryId).

**d) Publish pipeline — state machine:**

```
queued → uploading → processing → uploaded
   ↓         ↓            ↓
canceled   failed      failed (thumbnail/captions vẫn retry độc lập)
```

1. **Validate** (synchronous, trước khi queued):
   - Job có `final_en.mp4` (`items/{id}/exports/final_en.mp4`) → không có thì `422`.
   - Có ảnh `todo_images` `kind='thumbnail'` → không có thì `422` (đã có sẵn logic `has_thumbnail` ở service_todos.py).
   - **Idempotency guard**: tồn tại record `youtube_uploads` cùng `item_id` ở trạng thái `queued/uploading/processing` → `409` kèm upload_id; đã `uploaded` → trả `200` kèm `youtube_url` + `already_published: true`.
   - `publishAt` (nếu có): phải ≥ now + 10 phút, và **ép `privacy_status='private'`** (YouTube bắt buộc; đến giờ hẹn tự public).
2. **Khởi tạo resumable session**: `POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` với metadata JSON → 200 + `Location` session URI → **persist vào record**.
3. **Chunk loop** (background task, không giữ file trong RAM):
   - Đọc 4MB từ file (offset = `bytes_uploaded`), `PUT` session URI với `Content-Range: bytes X-(X+size-1)/total`.
   - `308 Resume Incomplete` → tiếp chunk kế (parse header `Range` nếu server yêu cầu reset offset).
   - `200/201` → xong, parse `video_id`.
   - `5xx`/timeout → backoff 2s→4s→…→60s, tối đa 5 lần: `PUT` rỗng `Content-Range: bytes */total` để hỏi offset thật → resume từ đó; hết lượt → `failed` (giữ session URI để nút "Thử lại" resume tiếp).
   - Quá trình upload cập nhật `bytes_uploaded`/`progress_pct` sau mỗi chunk.
4. **Processing poll**: sau khi có `video_id`, poll `videos.list?part=processingStatus,status` mỗi 15s (tối đa ~10 phút) → `succeeded`/`terminated` mới chuyển `processing → uploaded`. `thumbnails.set` **retry tối đa 3 lần** (cách 15s) vì gọi quá sớm hay bị 400/409; nếu kênh chưa phone-verified → lỗi rõ ràng "kênh cần xác minh số điện thoại trên YouTube Studio để set thumbnail".
5. **Captions** (nếu tick): `captions.insert` với bytes của `items/{id}/todo/subtitles_en.srt`, `language=en`, mime `text/plain`, chỉ khi file tồn tại; fail captions **không** fail toàn bộ upload (log + mark `captions_synced=0`, `error_message` phụ).
6. **Kết thúc**: cập nhật `youtube_video_id` + `youtube_url` vào record (và field hiển thị của todo job), bắn event webhook (mục 2.3e).

**e) Tích hợp hệ events/webhooks sẵn có:**
- Đăng ký event type mới vào registry `EVENTS` của [webhooks.py](../video-system/backend/app/webhooks.py): `youtube.channel.connected`, `youtube.channel.disconnected`, `youtube.upload.completed`, `youtube.upload.failed`.
- `dispatch_event` hiện có sẽ tự giao tới các webhook đã đăng ký — agent ngoài đang poll job queue cũng có thể nhận thông báo thay vì phải poll.

**f) Startup sweep (bền vững qua restart):**
- Lúc app startup: các record `uploading` có `upload_session_uri` → spawn task resume từ offset đã lưu (hỏi `bytes */total` trước); session URI quá 24h tuổi hoặc Google trả 410 → mark `failed` với lý do.
- Record `uploading` **không có** session URI (lỗi giữa chừng khi tạo session) → mark `failed`.
- Record `queued` quăng lại vào hàng upload.

### 2.4 Endpoints ([main.py](../video-system/backend/app/main.py))

Toàn bộ nằm sau lớp auth hiện có (PIN session cookie cho browser / Bearer key cho programmatic). Publish + sửa metadata + ngắt kết nối yêu cầu **scope `admin`**; xem thông tin chỉ cần `read`.

| Method | Path | Scope | Chú thích |
|---|---|---|---|
| GET | `/api/v1/youtube/status` | read | `{configured, connected, channel?, quota_used_today, quota_limit}` — 1 call đầu tiên của trang |
| GET | `/api/v1/youtube/auth/start` | read | Trả `{auth_url}` (state HMAC 10 phút); frontend tự `window.location = auth_url` |
| GET | `/api/v1/youtube/auth/callback` | — (Google redirect) | Đổi code → token (encrypt) → `channels.list mine` → lưu DB → redirect `/#/youtube?yt=connected` hoặc `?yt=error&reason=...` |
| GET | `/api/v1/youtube/channel` | read | Info kênh đang kết nối |
| DELETE | `/api/v1/youtube/channel` | admin | Revoke token qua `oauth2.googleapis.com/revoke` + xoá record; huỷ upload đang chạy |
| GET | `/api/v1/youtube/videos` | read | Danh sách video kênh (playlistItems + videos.list) + lịch sử `youtube_uploads` |
| PATCH | `/api/v1/youtube/videos/{video_id}` | admin | Sửa title/description/tags/privacy — **GET-merge-UPDATE** |
| POST | `/api/v1/todos/{item_id}/youtube/publish` | admin | Body: `{title, description, tags, privacy_status, publish_at?, upload_captions}` → validate + tạo record + chạy background |
| POST | `/api/v1/youtube/uploads/{upload_id}/retry` | admin | Resume/thử lại upload failed (dùng lại session URI nếu còn hạn) |
| POST | `/api/v1/youtube/uploads/{upload_id}/cancel` | admin | Hủy upload đang chạy (status=canceled) |
| GET | `/api/v1/youtube/uploads/{upload_id}` | read | Poll `{upload_status, progress_pct, bytes_uploaded, total_bytes, youtube_url?, error_message?}` |

### 2.5 Frontend ([index.html](../video-system/frontend/index.html))

1. **Top nav**: nút **`📺 YouTube`** cạnh `Short Studio` → `#/youtube`.
2. **Trang `#/youtube`**:
   - **Panel Kênh đang liên kết**: avatar, tên kênh, `@handle`, subs, tổng video, badge quota đã dùng hôm nay (`1.600 / 10.000 units`), nút 🔄 Làm mới, 🔗 Kết nối / Đổi kênh, 🔌 Ngắt kết nối (confirm). Chưa cấu hình CLIENT_ID → hiện panel hướng dẫn env thay vì nút kết nối.
   - **Bảng Video trên Kênh**: thumbnail, tiêu đề, trạng thái privacy, views/likes/comments, ngày đăng, ✏️ Sửa (modal Title/Description/Tags/Privacy — disable `public` nếu app chưa verify Google, hiện tooltip giải thích), link mở YouTube.
   - **Bảng Lịch sử Publish từ Video Factory**: job → status (queued/uploading %/processing/uploaded/failed), progress bar realtime (poll 2s), link video, nút Thử lại/Hủy.
3. **`#/todos/{item_id}` — Tab Video Thành Phẩm**:
   - Nút **📺 Đăng lên YouTube**: enable khi có `final_en.mp4` + `has_thumbnail` + kênh đã kết nối; disabled kèm tooltip nêu lý do thiếu gì.
   - **Modal Publish**: Title (điền sẵn tên job), Description (gợi ý template), Tags, Privacy (`private` mặc định), datetime-local Hẹn giờ đăng (chọn này → ép private + disable privacy), checkbox **Đính kèm phụ đề `.srt`** (checked nếu file tồn tại).
   - Sau submit: progress bar `%` poll 2s → xong hiện badge **✅ Đã đăng YouTube** + link; 409 (đang có upload) → toast nhảy tới lịch sử; 429 quota → toast "Hết quota hôm nay, reset lúc 0h".

---

## 3. Google Cloud Console (điều kiện trước khi bấm Kết nối trên Prod)

1. Bật **YouTube Data API v3** cho project.
2. **OAuth consent screen**: External; scopes `youtube.upload`, `youtube.force-ssl`; thêm email quản lý kênh vào **Test users** (nếu app ở Testing).
3. **OAuth 2.0 Client ID** (Web application) — Authorized redirect URI: `https://vidfac.gacdero.vn/api/v1/youtube/auth/callback` (khớp `public_base_url()`).
4. `.env` prod (`/opt/video-system/.env`):
   ```
   YOUTUBE_CLIENT_ID=...
   YOUTUBE_CLIENT_SECRET=...
   YOUTUBE_TOKEN_ENCRYPTION_KEY=<Fernet key>
   ```
5. Kênh YouTube trên Studio phải **phone-verified** (mục Cài đặt → Nâng cao) nếu muốn set thumbnail qua API.

> **Giới hạn đã biết (hiển thị cả trong UI):**
> - App OAuth chưa qua **verification/audit** của Google → video upload qua API bị khóa `private`, không thể chuyển `public` bằng API (phải bật tay trong YouTube Studio, hoặc submit audit form để verify project).
> - Quota mặc định **10.000 units/ngày** ≈ 6 video upload/ngày. Đơn vị tính trong UI từ QuotaLedger.
> - `thumbnails.set` chỉ thành công sau khi video processing xong + kênh đã verify.

---

## 4. Ma trận lỗi & hành vi (edge cases)

| Tình huống | Hành vi hệ thống |
|---|---|
| Server restart giữa lúc upload | Startup sweep → resume từ offset thật (`bytes */total`) với session URI còn hạn |
| Session URI hết hạn (>24h không dùng / Google 410) | Mark `failed`, nút Thử lại tạo session mới từ đầu |
| Google trả `invalid_grant` (token bị thu hồi) | Mark kênh disconnected, UI yêu cầu kết nối lại; upload dở → failed |
| Chunk PUT 5xx lặp 5 lần | `failed` + giữ session URI để retry có kiểm soát (nút Thử lại) |
| Bấm Đăng 2 lần / 2 tab cùng lúc | Idempotency guard → `409` + trả upload_id đang chạy |
| Hết quota (dưới 1.600 units) | `429` + thông báo giờ reset; không tạo record rác |
| publishAt ở quá khứ / ≤ 10 phút | `422`; publishAt có giá trị → ép private |
| Thiếu `final_en.mp4` / thiếu thumbnail | `422` nêu rõ thiếu gì |
| Thumbnail set lỗi vì processing chưa xong | Retry 3 lần cách 15s; vẫn lỗi → video vẫn `uploaded`, thumbnail_synced=0 + ghi chú lỗi phụ |
| Captions lỗi (sai format/kênh không owner) | Không fail upload; ghi `captions_synced=0` + lỗi phụ |
| Kênh bị phone-unverified | Lỗi set thumbnail kèm hướng dẫn verify trên Studio |
| Video bị xoá tay trên YouTube sau khi upload | `youtube_uploads` vẫn giữ lịch sử; `GET /videos` của kênh không còn video đó — chỉ hiển thị, không crash |

---

## 5. Kế hoạch kiểm thử (pytest, mock Google bằng `httpx.MockTransport`)

1. **OAuth**: `auth/start` trả URL đúng client_id/redirect_uri/scopes + state verify được HMAC & hết hạn 10 phút; `auth/callback`: state sai/hết hạn/tái dùng → 400; happy path → token encrypt roundtrip đúng, record kênh đúng, redirect `/#/youtube`.
2. **TokenManager**: token sắp hết hạn (< 120s) → tự refresh, DB cập nhật; `invalid_grant` → kênh mark disconnected.
3. **QuotaLedger**: cộng units đúng theo ngày VN; vượt ngưỡng → publish trả 429.
4. **GET /youtube/videos**: mock playlistItems + videos.list → merge statistics/status đúng; **assert không gọi `search.list`**.
5. **PATCH /youtube/videos/{id}**: assert chuỗi GET→merge→UPDATE, `categoryId` gốc được giữ.
6. **POST publish**:
   - Thiếu `final_en.mp4` → 422; thiếu thumbnail → 422.
   - Đang có upload queued/uploading → 409; đã uploaded → 200 `already_published`.
   - publishAt quá khứ → 422; publishAt hợp lệ → privacy bị ép `private`.
   - Happy path: resumable POST → session URI; PUT chunk → 308 → PUT kế → 200; assert `Content-Range` từng chunk + bytes_uploaded/progress_pct; xong → poll processingStatus → `thumbnails.set` (retry đúng lúc) → `captions.insert` khi có `.srt` → `youtube_url` trả về; event `youtube.upload.completed` được dispatch.
   - Chunk 5xx → backoff resume từ offset Google báo.
7. **Startup sweep**: record `uploading` có session URI → resume; không có session URI → failed; session quá 24h → failed.
8. **Retry/Cancel endpoint**: retry upload failed → tái dùng session; cancel → status canceled, chunk loop dừng.
9. **Disconnect**: revoke + xoá record; upload đang chạy bị cancel.
10. **Lint + full suite**: `ruff check video-system` + `pytest video-system/tests -q`.

---

## 6. Deploy & rollout

1. Deploy code lên `https://vidfac.gacdero.vn`; restart service.
2. Điền `YOUTUBE_CLIENT_ID`/`YOUTUBE_CLIENT_SECRET`/`YOUTUBE_TOKEN_ENCRYPTION_KEY` vào `/opt/video-system/.env` → restart lại (tính năng tự bật, không cần rollback đặc biệt — thiếu env → UI chỉ hiện "chưa cấu hình").
3. Smoke test: `#/youtube` → Kết nối → kênh hiện đúng; `#/todos` chọn job có final + thumbnail → Publish private → xem progress → video xuất hiện trên kênh (private) → đổi sang public tay trong Studio (nếu app chưa verify Google) → kiểm tra thumbnail/captions/subs.
4. Webhook `youtube.upload.completed` đăng ký thử với 1 endpoint test để xác nhận agent ngoài nhận được thông báo.

**Rollback**: tính năng tự chứa (1 service file mới + block endpoint + 3 bảng SQLite không đụng bảng cũ). Bỏ env `YOUTUBE_CLIENT_ID` + restart = tắt hoàn toàn, không ảnh hưởng luồng xuất bản hiện có.
