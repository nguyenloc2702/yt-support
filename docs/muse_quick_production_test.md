# Hướng dẫn production đầy đủ: Job Inbox nội bộ cho Muse

## 1. Mục tiêu

Repository này cung cấp một Job Inbox API nội bộ trên VPS. Local app tạo job `make_video`; Muse chủ động kết nối vào API, lấy job, claim, xử lý video, heartbeat và trả kết quả.

Luồng chuẩn:

```text
Local app
  └─ POST /jobs
       └─ Job Inbox API trên VPS
            ├─ Muse GET /jobs/pending
            ├─ Muse POST /jobs/:id/claim
            ├─ Muse POST /jobs/:id/heartbeat
            ├─ Muse POST /jobs/:id/result hoặc /fail
            └─ Local app đọc kết quả và hoàn tất local job
```

Muse chịu trách nhiệm orchestration AI: viết/chốt script theo payload, chia cảnh, tạo prompt, gọi Render API của VPS, chờ render, QC và trả kết quả.

## 2. Thành phần đã có trong repository

- FastAPI service: [`services/job_inbox_api.py`](../services/job_inbox_api.py)
- SQLite state store: [`services/job_inbox_store.py`](../services/job_inbox_store.py)
- Client tạo job: [`services/muse_job_client.py`](../services/muse_job_client.py)
- Systemd unit: [`deploy/muse-job-inbox.service`](../deploy/muse-job-inbox.service)
- Contract test: [`tests/test_job_inbox_api.py`](../tests/test_job_inbox_api.py)
- Hướng dẫn kiến trúc: [`huong_dan_production_v2.md`](../huong_dan_production_v2.md)

Các trạng thái Job Inbox:

```text
pending → running → done
                  ├→ failed
                  └→ cancelled
```

Lease của job `running` hết hạn sẽ được đưa lại về `pending` ở lần gọi tiếp theo tới `GET /jobs/pending`.

## 3. Cài đặt trên VPS

```bash
cd /opt/support-video-edit
python3 -m venv .venv
.venv/bin/pip install -e .
```

Dependency FastAPI và Uvicorn được khai báo trong [`pyproject.toml`](../pyproject.toml).

Kiểm tra import:

```bash
.venv/bin/python -c "from services.job_inbox_api import app; print(app.title)"
```

## 4. Cấu hình environment

Tạo file `/opt/support-video-edit/.env`:

```env
# Token Muse dùng khi gọi Job Inbox API.
# Không commit và không ghi token vào log.
CLAUDE_JOBS_TOKEN=THAY_BANG_TOKEN_DAI_NGAU_NHIEN

# Database riêng cho Job Inbox.
JOB_INBOX_DB_PATH=/opt/support-video-edit/data/muse_job_inbox.sqlite3

# URL local app dùng để gọi API tạo job.
# Nếu local app chạy cùng VPS:
JOB_INBOX_INTERNAL_URL=http://127.0.0.1:8787

# Feature flag gửi video job sang Muse.
MUSE_VIDEO_ENABLED=false

# Timeout HTTP của client.
MUSE_JOBS_TIMEOUT_SECONDS=30
```

Có thể dùng `MUSE_JOBS_BASE_URL` thay cho `JOB_INBOX_INTERNAL_URL` để tương thích ngược, nhưng cấu hình mới nên dùng `JOB_INBOX_INTERNAL_URL`.

## 5. Chạy FastAPI bằng systemd

Unit file là [`deploy/muse-job-inbox.service`](../deploy/muse-job-inbox.service):

```bash
sudo cp deploy/muse-job-inbox.service /etc/systemd/system/muse-job-inbox.service
sudo systemctl daemon-reload
sudo systemctl enable --now muse-job-inbox
sudo systemctl status muse-job-inbox
```

Xem log:

```bash
sudo journalctl -u muse-job-inbox -f
```

Service hiện bind tại `127.0.0.1:8787`. Không expose trực tiếp Uvicorn ra Internet. Dùng Nginx/Caddy hoặc Tailscale để đưa API tới Muse, có TLS và firewall.

Ví dụ chạy thủ công để debug:

```bash
JOB_INBOX_DB_PATH=/opt/support-video-edit/data/muse_job_inbox.sqlite3 \
CLAUDE_JOBS_TOKEN=THAY_TOKEN \
.venv/bin/uvicorn services.job_inbox_api:app --host 127.0.0.1 --port 8787
```

## 6. Reverse proxy và URL Muse sử dụng

URL nội bộ cho local app có thể là:

```env
JOB_INBOX_INTERNAL_URL=http://127.0.0.1:8787
```

URL cấp cho Muse phải truy cập được từ máy Muse, ví dụ:

```text
https://your-domain.example.com/api/claude-jobs
```

Reverse proxy phải chuyển tiếp prefix `/api/claude-jobs` tới service Uvicorn và giữ nguyên các path:

```text
POST /api/claude-jobs/jobs
GET  /api/claude-jobs/jobs/pending
GET  /api/claude-jobs/jobs/{job_id}
POST /api/claude-jobs/jobs/{job_id}/claim
POST /api/claude-jobs/jobs/{job_id}/heartbeat
POST /api/claude-jobs/jobs/{job_id}/result
POST /api/claude-jobs/jobs/{job_id}/fail
POST /api/claude-jobs/jobs/{job_id}/cancel
```

Bắt buộc:

- HTTPS nếu mở public.
- Firewall chỉ cho IP/Tailscale của Muse nếu có thể.
- Header `Authorization: Bearer <CLAUDE_JOBS_TOKEN>`.
- Không ghi token vào access log.
- Giới hạn request body và rate limit phù hợp.

## 7. Kiểm tra authentication và health bằng curl

Thiếu token phải bị từ chối:

```bash
curl -i https://your-domain.example.com/api/claude-jobs/jobs/pending
```

Kỳ vọng: HTTP `401`.

Có token:

```bash
curl -i \
  -H "Authorization: Bearer $CLAUDE_JOBS_TOKEN" \
  -H "Accept: application/json" \
  "https://your-domain.example.com/api/claude-jobs/jobs/pending?limit=1"
```

Kỳ vọng: HTTP `200` và response envelope hợp lệ:

```json
{"ok": true, "count": 0, "jobs": []}
```

## 8. API contract

### Tạo job

```http
POST /jobs
Authorization: Bearer <token>
Content-Type: application/json
```

Body mẫu:

```json
{
  "job_id": "video-remake_123-abc1234567",
  "type": "make_video",
  "priority": 3,
  "max_attempts": 3,
  "source": "video-web-ui",
  "payload": {
    "story": "Approved video script",
    "script_source": "approved_script",
    "script": "[CẢNH 001]\nLời thoại...",
    "target_duration_sec": 180,
    "language": "vi",
    "visual_style": "cinematic documentary",
    "aspect_ratio": "16:9",
    "voice": "vi-VN-NamMinhNeural",
    "music_mood": "subtle documentary"
  }
}
```

Cùng một `job_id` và cùng payload sẽ trả lại job cũ, không tạo duplicate. Cùng `job_id` nhưng payload khác sẽ trả conflict `409`.

### Muse lấy job pending

```http
GET /jobs/pending?limit=10
Authorization: Bearer <token>
```

Response chuẩn:

```json
{"ok": true, "count": 1, "jobs": [{"job_id": "video-...", "status": "pending"}]}
```

Jobs được sắp xếp theo `priority ASC` (thang 1–5: 1 = cao nhất / khẩn nhất, 5 = thấp nhất; mặc định 3), sau đó `created_at ASC` (cùng priority thì job tạo trước lấy trước).

### Claim

```http
POST /jobs/{job_id}/claim
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{"worker": "claude-muse", "lease_sec": 900}
```

Claim lần hai khi job đang `running` phải trả `409`.

### Heartbeat

```http
POST /jobs/{job_id}/heartbeat
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "worker": "claude-muse",
  "lease_sec": 900,
  "progress_note": "Đã render 3/5 cảnh"
}
```

### Result

```http
POST /jobs/{job_id}/result
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "worker": "claude-muse",
  "finished_at": "2026-11-02T16:05:00+07:00",
  "summary": "Hoàn thành video, QC đạt.",
  "outputs": {
    "video_url": "https://your-domain.example.com/outputs/video.mp4",
    "duration_sec": 180,
    "script": "...",
    "scenes": [],
    "qc": {"pass": true, "score": 9, "issues": []}
  }
}
```

### Fail

```http
POST /jobs/{job_id}/fail
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "worker": "claude-muse",
  "error": {
    "code": "render_timeout",
    "message": "Render API timeout"
  },
  "retryable": true
}
```

Nếu `retryable=true` và `attempts < max_attempts`, job quay lại `pending`. Nếu không, job chuyển `failed`. Lease expiry cũng được tính theo `attempts`; sau khi đạt `max_attempts`, job chuyển `failed` với lỗi `lease_expired`, tránh vòng lặp vô hạn.

### Result duplicate và response envelope

Gửi lại result sau khi job đã `done` trả HTTP `200`:

```json
{"ok": true, "job_id": "video-...", "status": "done", "duplicate": true}
```

`GET /jobs/pending` trả `ok`, `count`, `jobs`; jobs được sắp xếp theo `priority ASC` (1 là cao nhất, 5 là thấp nhất), sau đó `created_at ASC`. Lỗi dùng HTTP status phù hợp và mã `unauthorized`, `not_found` hoặc `conflict`.

### Cancel

```http
POST /jobs/{job_id}/cancel
Authorization: Bearer <token>
```

## 9. Chạy test trước khi mời Muse

```bash
.venv/bin/python -m pytest tests/test_job_inbox_api.py tests/test_muse_job_client.py tests/test_settings_service.py -q
.venv/bin/python -m ruff check services/job_inbox_store.py services/job_inbox_api.py services/muse_job_client.py tests/test_job_inbox_api.py
```

Kết quả kiểm tra gần nhất trong workspace hiện tại: **19 passed**, Ruff pass. Có 2 warning từ Starlette/httpx về deprecation của test client; đây không phải test failure.

## 10. Test thực tế 3–5 cảnh

Không bắt đầu bằng script dài. Thực hiện theo thứ tự:

1. Deploy API nhưng giữ `MUSE_VIDEO_ENABLED=false`.
2. Xác nhận Muse gọi được endpoint pending với token.
3. Chuẩn bị một job test 3–5 cảnh.
4. Bật `MUSE_VIDEO_ENABLED=true` cho một lần test.
5. Approve Mode 2.
6. Xác nhận local app tạo đúng một job `make_video`.
7. Xác nhận Muse thấy job ở pending.
8. Xác nhận Muse claim job.
9. Xác nhận heartbeat xuất hiện khi chạy lâu.
10. Xác nhận Muse POST result hoặc fail.
11. Kiểm tra `outputs.video_url`, file MP4, duration, resolution, audio và QC.
12. Ghi lại job ID, thời điểm, log và lỗi nếu có.

## 11. Payload do local factory gửi

Flow [`AutomatedFactory.execute_approved_reup()`](../pipeline/automated_factory.py) chọn script đã duyệt theo thứ tự:

1. `job.script_file` nếu file tồn tại.
2. `kich_ban_VI_<job_id>.txt`.
3. Không dùng `shots_plan.json` làm raw script.

Client tạo job ổn định bằng factory job ID và SHA-256 của script. Vì vậy retry cùng job/script không tạo job Muse trùng.

## 12. Render API là điều kiện của `make_video`

Job Inbox chỉ giao việc; nó không tự render video. Để Muse làm `make_video` end-to-end, VPS phải có Render API mà Muse gọi được.

Render API cần thống nhất tối thiểu:

- endpoint submit render;
- payload scene/prompt/voice/style;
- endpoint status;
- heartbeat/progress nếu render lâu;
- endpoint hoặc URL lấy MP4;
- authentication riêng;
- giới hạn timeout và retry;
- tiêu chuẩn resolution, codec, audio và subtitle.

Nếu Render API chưa sẵn sàng, chỉ nên test `write_script`/`split_scenes` hoặc test lifecycle Job Inbox. Không tuyên bố `make_video` production hoàn tất.

## 13. Synchronizer local job còn phải hoàn thiện

Sau khi Job Inbox trả `done`, local app cần synchronizer:

1. Đối chiếu local job `RENDERING` với `job_id` trong Inbox.
2. Đọc `result.outputs.video_url`.
3. Tải video về thư mục artifact kiểm soát được.
4. Dùng ffprobe/validator kiểm tra MP4, duration, resolution và audio.
5. Nếu đạt: cập nhật local job `COMPLETED`.
6. Nếu lỗi: cập nhật local job `FAILED` và lưu nguyên nhân.
7. Retry/restart phải idempotent, không tải/hoàn tất trùng nguy hiểm.

Bản code hiện tại đã có API Job Inbox và submit flow, nhưng synchronizer này chưa hoàn thiện. Vì vậy không đánh dấu local job `COMPLETED` chỉ vì Inbox có status `done`.

## 14. Monitoring và vận hành

Theo dõi:

- systemd service status;
- HTTP 401/403/409/5xx;
- số job pending/running/failed;
- job quá lease;
- job running quá thời lượng dự kiến;
- heartbeat cuối cùng;
- disk usage của SQLite và video output;
- lỗi Render API;
- tỷ lệ QC pass.

Backup database:

```bash
sqlite3 /opt/support-video-edit/data/muse_job_inbox.sqlite3 ".backup '/opt/backups/muse_job_inbox-$(date +%F).sqlite3'"
```

## 15. Rollback

Tắt tạo job mới:

```env
MUSE_VIDEO_ENABLED=false
```

Sau đó restart local worker/app. Không xóa job database hoặc pipeline Gemini cũ ngay. Các job đã gửi Muse cần được xử lý theo chính sách rõ ràng: để Muse tiếp tục, cancel, hoặc retry sau khi khắc phục.

Nếu token bị lộ, thay token ở API và Muse connector, rồi restart service:

```bash
sudo systemctl restart muse-job-inbox
```

## 16. Checklist production cuối

- [ ] FastAPI/Uvicorn cài trên VPS.
- [ ] `JOB_INBOX_DB_PATH` nằm trên disk persistent.
- [ ] `CLAUDE_JOBS_TOKEN` là secret mạnh và không nằm trong Git.
- [ ] HTTPS/reverse proxy/firewall đã cấu hình.
- [ ] Muse truy cập được `GET /jobs/pending`.
- [ ] Tất cả endpoint lifecycle đã test.
- [ ] Idempotency và claim atomic đã test.
- [ ] Lease expiry và heartbeat đã test.
- [ ] Render API đã sẵn sàng.
- [ ] Job test 3–5 cảnh đã pass.
- [ ] `video_url` tải được từ VPS.
- [ ] MP4/QC/resolution/audio đã validate.
- [ ] Synchronizer local job đã chạy idempotent.
- [ ] Job dài thật đã pass.
- [ ] Rollback flag đã thử.
- [ ] Backup và log monitoring đã có.

Chỉ sau khi toàn bộ checklist trên đạt mới bật rộng `MUSE_VIDEO_ENABLED=true` cho production.
