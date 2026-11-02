# Hướng dẫn production v2: Hệ thống VPS giao việc cho Muse qua Job Inbox

- Phiên bản: 2.0 — 2026-11-02 (bản sửa đúng chiều kiến trúc, thay thế bản hướng dẫn trước)
- Tài liệu gốc về giao diện API: `muse_job_inbox_spec.md` (v1.0) — khi có mâu thuẫn, spec là chuẩn.
- Đối tượng đọc: đội code VPS của Joseph.

---

## 0. Điểm sửa quan trọng nhất so với bản trước

Bản hướng dẫn trước giả định Muse host một "API Job Inbox" tại một domain của Muse (ví dụ `muse.example.com`) để hệ thống VPS POST job sang. **Giả định này sai và không thể triển khai**: Muse không có API công khai, không cấp base URL/token cho bên ngoài gọi vào, và không nhận `POST /jobs` từ internet.

Kiến trúc đúng (đã chốt trong spec):

- **API Job Inbox chạy trên VPS của Joseph.** Hàng đợi job nằm trong database của chính hệ thống.
- Hệ thống tạo job vào hàng đợi của mình (job ở trạng thái `pending`).
- **Muse chủ động gọi vào VPS**: polling `GET /jobs/pending` → claim job → xử lý → POST kết quả về `POST /jobs/:id/result` (hoặc lỗi về `POST /jobs/:id/fail`).
- Local app cập nhật trạng thái job của mình bằng cách đọc **chính database nội bộ** (xem mục 7), không cần poller gọi ra hệ thống bên ngoài.

Mọi mục dưới đây đã được viết lại theo chiều này.

---

## 1. Hiểu đúng luồng end-to-end

```
[Local app]                [Job Inbox API @ VPS]              [Muse]
    │                              │                              │
    │ 1. Tạo job make_video        │                              │
    │    (local job: APPROVED)     │                              │
    ├─────────────────────────────►│ job: pending                 │
    │                              │                              │
    │                              │ 2. GET /jobs/pending         │
    │                              │◄─────────────────────────────┤ (polling 5 phút/lần)
    │                              │ 3. POST /jobs/:id/claim      │
    │                              │◄─────────────────────────────┤ job: running
    │  (local job: RENDERING)      │ 4. POST /jobs/:id/heartbeat  │
    │                              │◄─────────────────────────────┤ (khi job chạy lâu)
    │                              │                              │
    │                              │      Muse xử lý job:         │
    │                              │      viết/chốt script →      │
    │                              │      chia cảnh → gọi Render  │
    │                              │      API của VPS → QC        │
    │                              │                              │
    │                              │ 5. POST /jobs/:id/result     │
    │                              │◄─────────────────────────────┤ job: done
    │ 6. Synchronizer nội bộ đọc   │    (hoặc /fail → failed)     │
    │    job done → validate →     │                              │
    │    local job: COMPLETED      │                              │
```

Trạng thái code hiện tại được hiểu lại như sau:

- Bước "submit job" hiện có = tạo job record vào Job Inbox của chính VPS (không phải gửi sang Muse). Sau khi tạo, local job chuyển `RENDERING` là hợp lệ **nếu** định nghĩa `RENDERING` = "đã vào hàng đợi / đang được Muse xử lý".
- Chưa coi là production end-to-end cho đến khi xác nhận đủ 5 điểm ở mục 6 và lớp đồng bộ ở mục 7 chạy được.

---

## 2. VPS cần cung cấp cho Muse trước khi test

Đảo ngược so với bản cũ — đây là những thứ **phía VPS chuẩn bị**, Muse là bên tiêu thụ:

1. **Base URL của Job Inbox API trên VPS**, ví dụ:
   `https://<domain-cua-ban>/api/claude-jobs`
   (hoặc địa chỉ Tailscale `http://<tailnet-host>:<port>/api/claude-jobs` nếu không mở public).
2. **Token do VPS cấp** cho Muse dùng trong header `Authorization: Bearer <token>`. Joseph chuyển token cho Muse qua form bảo mật (Secure Vault) — không gửi qua chat, không qua Telegram.
3. **Xác nhận đã implement đủ các endpoint** theo spec mục 5:
   - `POST /jobs` (tạo job, idempotent theo `job_id`)
   - `GET /jobs/pending`
   - `GET /jobs/:id`
   - `POST /jobs/:id/claim` (nguyên tử, có lease)
   - `POST /jobs/:id/heartbeat`
   - `POST /jobs/:id/result`
   - `POST /jobs/:id/fail`
   - `POST /jobs/:id/cancel`
4. **Xác nhận cơ chế nền phía server**: claim nguyên tử chống trùng, quét lease hết hạn mỗi phút để trả job về `pending`, đếm `attempts/max_attempts` (spec mục 3 và 5).
5. **Allowlist loại job** giai đoạn đầu: `write_script`, `split_scenes`, `make_video`, `qc_video` (chốt lại với Joseph trước khi bật).
6. **Render API của VPS** (điều kiện riêng của `make_video`, xem mục 3): endpoint nộp render, endpoint trạng thái render, và cách lấy URL file đầu ra.
7. Một **job test nhỏ 3–5 cảnh** để chạy thử trước script dài.

Phía Muse sẽ tự chuẩn bị: connector riêng cho API này, lưu token trong kho bảo mật, lịch polling 5 phút/lần, và báo cáo tiến độ/kết quả vào chat cho Joseph.

---

## 3. Vai trò render trong job `make_video` (đã chốt)

Muse **không phải** một render farm độc lập trong kiến trúc này. Trong job `make_video`, Muse đóng vai **điều phối**:

1. Viết/chốt kịch bản từ `payload` (hoặc dùng script đã duyệt nếu payload đã có).
2. Chia kịch bản thành cảnh, sinh prompt cho từng cảnh.
3. Gọi **Render API của chính VPS** để render từng cảnh và ghép video.
4. Chờ render xong, lấy `video_url` của file hoàn chỉnh (file nằm trên VPS).
5. QC theo checklist, rồi POST kết quả về Job Inbox.

Hệ quả triển khai: muốn `make_video` chạy trọn gói, VPS phải cung cấp Render API cho Muse gọi (mục 2.6). Nếu Render API chưa sẵn sàng, giai đoạn đầu chỉ bật `write_script` và `split_scenes` — Muse trả script + danh sách cảnh, hệ thống của Joseph tự render theo cách hiện tại.

Schema kết quả Muse POST về (theo spec mục 6 — đây là schema chuẩn duy nhất, thay cho dạng `{"status":"done","result":{...}}` ở bản cũ):

```json
{
  "worker": "claude-muse",
  "finished_at": "2026-11-02T16:05:00+07:00",
  "summary": "Hoàn thành video: 5 cảnh, thời lượng 48 giây, QC đạt.",
  "outputs": {
    "video_url": "https://<domain-cua-ban>/outputs/job-xxx.mp4",
    "duration_sec": 48,
    "script": "<kịch bản đầy đủ>",
    "scenes": [ { "scene_no": 1, "duration_sec": 10, "narration": "...", "image_prompt": "...", "video_prompt": "..." } ],
    "qc": { "pass": true, "score": 9, "issues": [] }
  }
}
```

Server lưu nguyên object này vào field `result` của job và đặt `status = done`.

---

## 4. Cấu hình VPS

Biến môi trường production (đã sửa hướng):

```env
# Token mà Muse dùng để gọi vào Job Inbox API của VPS (VPS là bên cấp)
CLAUDE_JOBS_TOKEN=REPLACE_WITH_TOKEN_DO_VPS_CAP

# Cờ bật/tắt tính năng giao việc cho Muse (rollback nhanh)
MUSE_VIDEO_ENABLED=true

# Timeout cho các thao tác nội bộ liên quan hàng đợi (giây)
MUSE_JOBS_TIMEOUT_SECONDS=30
```

Lưu ý:

- **Không còn biến `MUSE_JOBS_BASE_URL` trỏ tới domain của Muse.** Nếu Job Inbox là một service riêng trong nội bộ VPS, local app gọi nó qua địa chỉ nội bộ (localhost/tên service) — đó là kết nối nội bộ của chính hệ thống, đặt tên biến tùy đội code, ví dụ `JOB_INBOX_INTERNAL_URL`.
- Token không commit vào Git, không ghi vào log, không gửi qua Telegram/chat.
- Base URL public (nếu mở) bắt buộc HTTPS. HTTP chỉ chấp nhận trong mạng Tailscale/localhost có kiểm soát.
- Khởi động lại worker/app sau khi đổi environment. Đổi token = thu hồi quyền truy cập của Muse ngay lập tức.

---

## 5. Test kết nối trước khi chạy video thật

Test này chạy **trên VPS, gọi vào chính Job Inbox API của VPS** để bảo đảm API + token sẵn sàng trước khi bàn giao cho Muse:

```powershell
# Không có token -> kỳ vọng 401
curl.exe -i "$env:JOB_INBOX_URL/jobs/pending?limit=1"

# Có token -> kỳ vọng 200 + JSON hợp lệ
curl.exe -i `
  -H "Authorization: Bearer $env:CLAUDE_JOBS_TOKEN" `
  -H "Accept: application/json" `
  "$env:JOB_INBOX_URL/jobs/pending?limit=1"
```

(Linux: thay cú pháp biến môi trường PowerShell bằng `$CLAUDE_JOBS_TOKEN` và `$JOB_INBOX_URL`.)

Kỳ vọng:

- Request thiếu token: HTTP `401`.
- Request có token: HTTP `200`, JSON hợp lệ, không lỗi DNS/TLS.
- Chạy thêm bộ test vòng đời job trong spec mục 10 (tạo trùng `job_id`, claim hai lần, lease hết hạn, fail/retry) — tất cả phải đạt trước khi mời Muse test.

Sau bước này, bàn giao base URL cho Joseph để Muse cấu hình connector và tự kiểm tra `GET /jobs/pending` từ phía Muse (kỳ vọng `200`, danh sách rỗng hoặc chỉ chứa job test).

---

## 6. Test job nhỏ và tiêu chí pass

Dùng một job `make_video` với script ngắn 3–5 cảnh (hoặc `write_script` nếu Render API chưa sẵn sàng). Local job ở trạng thái `APPROVED`, bật `MUSE_VIDEO_ENABLED=true`, chạy flow duyệt như production.

Quan sát theo thứ tự, thiếu một mốc là dừng và xử lý, không đi tiếp:

1. Job record được tạo đúng **một lần** trong Job Inbox (retry request không sinh job trùng), local job chuyển `RENDERING`.
2. Muse thấy job ở `GET /jobs/pending` trong vòng một chu kỳ polling (≤ 5 phút).
3. Muse claim thành công; gọi claim lần hai (giả lập) nhận `409`.
4. Job dài có heartbeat gia hạn lease; `progress_note` xem được qua `GET /jobs/:id`.
5. Muse POST `result` đúng schema mục 3 → job `done`; hoặc POST `fail` với `error.code` rõ ràng nếu có lỗi thật.
6. Synchronizer nội bộ (mục 7) xử lý job `done`: validate artifact → local job `COMPLETED`.

Tiêu chí pass test (tất cả phải đúng):

- Tạo job idempotent: retry không tạo trùng.
- Muse nhận đúng payload tiếng Việt đã duyệt (script, `target_duration_sec`, style, `aspect_ratio` 16:9, voice đã thống nhất).
- Số cảnh và thời lượng video hợp lý so với script.
- `video_url` trỏ tới file trên VPS, tải xuống được từ chính VPS và từ máy ngoài (nếu URL public/signed — ghi rõ thời hạn ký).
- File là MP4 hợp lệ: có hình, có tiếng theo contract, độ phân giải đạt yêu cầu.
- `result.outputs.qc.pass = true` hoặc đạt ngưỡng đã thống nhất với Joseph.
- Local job kết thúc ở `COMPLETED` **sau khi** validate artifact xong (không đánh dấu hoàn thành ngay khi thấy `done`).
- Job `failed` mẫu (tạo payload cố ý sai) đi đúng đường: job `failed`, local job `FAILED`, `last_error` đọc được.

---

## 7. Đồng bộ kết quả về local job (lớp bắt buộc, chạy nội bộ VPS)

Khác bản cũ: **không cần** poller gọi `GET /jobs/:id` ra hệ thống bên ngoài, vì Job Inbox nằm ngay trong hệ thống. Cần một synchronizer nội bộ (background worker/cron trong app) làm các việc sau:

1. Quét định kỳ (ví dụ mỗi 30–60 giây) các local job đang `RENDERING` và đối chiếu record job tương ứng trong bảng Job Inbox.
2. Job `done`: lấy `result.outputs.video_url` → kiểm tra file tồn tại/tải được → validate MP4 (đọc được metadata, thời lượng, độ phân giải, có audio track nếu contract yêu cầu) → lưu artifact vào nơi hệ thống quản lý → local job `COMPLETED`.
3. Job `failed`: local job `FAILED`, sao chép `last_error` sang local để Joseph xem.
4. Job `cancelled`: local job về trạng thái hủy tương ứng.
5. Job kẹt: local job `RENDERING` quá ngưỡng thời gian (ví dụ > 2× thời lượng dự kiến) mà job Inbox vẫn `pending` — cảnh báo cho Joseph, vì có thể Muse chưa polling hoặc API đang lỗi phía Muse.
6. **Resume sau restart**: synchronizer không giữ trạng thái trong RAM; mọi quyết định đều suy ra từ database, nên process khởi động lại là tự đồng bộ tiếp, không mất job.
7. Thao tác cập nhật local job phải idempotent: chạy lại synchronizer không làm job `COMPLETED` bị xử lý hai lần (kiểm tra trạng thái hiện tại trước khi validate/tải lại).

Chỉ khi lớp này chạy ổn mới tuyên bố production end-to-end.

---

## 8. Rollback an toàn

Khi Muse phía đối tác lỗi, API chưa ổn định, hoặc cần dừng khẩn cấp:

```env
MUSE_VIDEO_ENABLED=false
```

Khởi động lại worker/app. Cờ này ngăn flow tạo job mới cho Muse; các job đã ở trong hàng đợi vẫn giữ nguyên trạng thái để xử lý tiếp sau, không xóa. Ngoài ra có thể đổi `CLAUDE_JOBS_TOKEN` để cắt quyền truy cập của Muse ngay lập tức. Không xóa pipeline render cũ cho đến khi luồng Muse pass test production và synchronizer chạy ổn định qua ít nhất một script dài thật.

---

## 9. Thứ tự triển khai đề xuất

1. VPS triển khai Job Inbox API theo spec (mục 2–5 của spec), gồm claim nguyên tử + quét lease.
2. Chạy bộ test vòng đời job của spec mục 10 ngay trên VPS; đạt hết mới đi tiếp.
3. Deploy code local app (flow tạo job + synchronizer mục 7) với `MUSE_VIDEO_ENABLED=false`.
4. Cấp token; Joseph chuyển token cho Muse qua form bảo mật; Muse tạo connector và test `GET /jobs/pending` (kỳ vọng 200).
5. Bật cờ cho **một** job test nhỏ 3–5 cảnh; đối chiếu đủ 6 mốc ở mục 6.
6. Nếu Render API chưa sẵn sàng cho Muse gọi: tạm giới hạn allowlist ở `write_script` + `split_scenes`, hoàn thiện Render API rồi mới mở `make_video`.
7. Chạy một script dài thật (ví dụ video 20 phút) với giám sát của Joseph.
8. Đạt hết tiêu chí mục 6 ở job dài → bật production rộng hơn; giữ cờ rollback và pipeline cũ thêm một thời gian theo dõi.

---

## 10. Những điểm bản cũ cần xóa/sửa khi đối chiếu code

- Xóa khái niệm "Muse cung cấp base URL/token" và domain `muse.example.com` khỏi code, env mẫu và tài liệu nội bộ.
- Mọi chỗ hiểu "submit job sang Muse" sửa thành "tạo job vào Job Inbox nội bộ".
- Bỏ kế hoạch poller gọi ra ngoài; thay bằng synchronizer nội bộ ở mục 7.
- Thống nhất schema kết quả theo mục 3 của bản này (= spec mục 6).
- Bổ sung vào server: claim nguyên tử, lease + heartbeat, quét job hết lease, `attempts/max_attempts` — nếu code hiện tại chưa có, đây là phần việc còn thiếu trước production.
