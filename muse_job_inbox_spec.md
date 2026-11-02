# SPEC: Hệ thống VPS giao việc cho Muse (Job Inbox API)

- Phiên bản: 1.0 — 2026-11-02
- Bên A (phía VPS): hệ thống gen video của Joseph — tạo job, lưu hàng đợi, nhận kết quả.
- Bên B (phía Muse): agent Muse — chủ động lấy job, thực thi, trả kết quả, đồng thời báo tóm tắt cho Joseph trong chat.
- Mục đích của tài liệu này: để đội code VPS triển khai đúng giao diện mà Muse sẽ gọi. Mọi ví dụ đều là mẫu minh họa, không chứa dữ liệu thật.

---

## 1. Bối cảnh và nguyên tắc thiết kế

Muse không có API công khai để hệ thống bên ngoài "đẩy" việc vào, và cũng không cấp API key cho agent. Vì vậy luồng giao việc được đảo chiều:

1. Hệ thống VPS tạo job và lưu vào hàng đợi (database hoặc file).
2. Muse chủ động gọi API của VPS theo lịch (polling) để lấy job đang chờ.
3. Muse "claim" job để khóa, tránh hai tiến trình xử lý trùng một job.
4. Muse thực thi job (viết kịch bản, chia cảnh, gọi hệ thống render, kiểm tra chất lượng... tùy `type`).
5. Muse POST kết quả về VPS, đồng thời nhắn tóm tắt cho Joseph trong chat.

Nguyên tắc bắt buộc:

- **Idempotency**: mọi thao tác tạo job và trả kết quả đều an toàn khi gọi lặp lại. Job được định danh bằng `job_id` duy nhất do phía VPS cấp. Gửi lại cùng `job_id` không tạo job mới.
- **Claim có thời hạn (lease)**: job được claim sẽ bị khóa trong một khoảng thời gian (mặc định 15 phút). Nếu Muse chết giữa chừng và không gia hạn, job tự trở lại `pending` để được xử lý lại.
- **At-least-once**: một job có thể được Muse xử lý hơn một lần trong trường hợp lỗi mạng/sập tiến trình. Phía VPS phải chấp nhận kết quả trùng (ghi đè bằng kết quả mới nhất) và phía Muse phải thiết kế bước thực thi sao cho chạy lại không gây hại (ví dụ: không render hai lần nếu job render đã có `output` — kiểm tra trạng thái trước khi làm).
- **Allowlist loại việc**: Muse chỉ tự động chạy các `type` nằm trong danh sách Joseph duyệt trước. Job có `type` lạ sẽ bị Muse từ chối (fail với lý do `unsupported_type`) thay vì tự ý thực thi.
- **Không nhúng bí mật vào job**: payload không chứa mật khẩu, token, khóa riêng. Token xác thực API nằm ở biến môi trường phía VPS và trong kho bảo mật phía Muse.

---

## 2. Thông tin kết nối

| Hạng mục | Giá trị |
|---|---|
| Base URL (public) | `https://<domain-cua-ban>/api/claude-jobs` (khuyến nghị) |
| Base URL (nội bộ) | `http://<tailnet-host>:<port>/api/claude-jobs` nếu đi qua Tailscale |
| Giao thức | HTTPS bắt buộc nếu public; HTTP chỉ chấp nhận trong mạng Tailscale |
| Định dạng | JSON, UTF-8, `Content-Type: application/json` |
| Xác thực | Header `Authorization: Bearer <TOKEN>` cho mọi request từ Muse |
| Token | Chuỗi ngẫu nhiên ≥ 32 ký tự, lưu trong biến môi trường `CLAUDE_JOBS_TOKEN` phía VPS. TUYỆT ĐỐI không ghi token vào tài liệu này, vào code, vào log, hay vào chat |
| Múi giờ | Mọi timestamp dùng ISO 8601 có offset, ví dụ `2026-11-02T15:30:00+07:00` |

Mã lỗi HTTP dùng chung:

| Mã | Ý nghĩa |
|---|---|
| 200 / 201 | Thành công |
| 400 | Request sai định dạng / thiếu field bắt buộc |
| 401 | Thiếu hoặc sai Bearer token — request không có token hợp lệ phải luôn nhận 401 |
| 404 | Không tồn tại `job_id` |
| 409 | Xung đột trạng thái (ví dụ claim job đã bị claim, hoặc trả kết quả cho job đã `done`) |
| 422 | Payload hợp lệ về cú pháp nhưng sai nghiệp vụ (ví dụ `type` không được hỗ trợ) |

Định dạng lỗi thống nhất:

```json
{
  "ok": false,
  "error": {
    "code": "job_already_claimed",
    "message": "Job job-20261102-001 is already claimed by another worker"
  }
}
```

---

## 3. Mô hình dữ liệu Job

### 3.1. Các field của job

| Field | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `job_id` | string | Có | ID duy nhất do VPS cấp, ví dụ `job-20261102-001`. Là khóa idempotency |
| `type` | string | Có | Loại việc, thuộc danh sách ở mục 4 |
| `priority` | integer | Không | 1 (cao nhất) – 5 (thấp nhất), mặc định 3. Muse lấy job theo priority cao trước, cùng priority thì theo `created_at` cũ trước |
| `payload` | object | Có | Dữ liệu đầu vào, cấu trúc tùy theo `type` (mục 4) |
| `status` | string | Server quản lý | Một trong: `pending`, `claimed`, `running`, `done`, `failed`, `cancelled` |
| `attempts` | integer | Server quản lý | Số lần job đã được claim, bắt đầu từ 0 |
| `max_attempts` | integer | Không | Số lần thử tối đa, mặc định 3. Hết lượt mà vẫn lỗi → `failed` hẳn |
| `created_at` | string (ISO 8601) | Server quản lý | Thời điểm tạo job |
| `claimed_at` | string \| null | Server quản lý | Thời điểm claim gần nhất |
| `claim_expires_at` | string \| null | Server quản lý | Thời điểm hết hạn lease của claim hiện tại |
| `result` | object \| null | Server quản lý | Kết quả Muse trả về (mục 6) |
| `last_error` | object \| null | Server quản lý | Lỗi gần nhất `{code, message}` |
| `source` | string | Không | Hệ thống con nào tạo job, ví dụ `video-web-ui`, để tiện truy vết |
| `note` | string \| null | Không | Ghi chú tự do cho Joseph đọc |

### 3.2. Máy trạng thái (state machine)

```
                 ┌──────────┐   claim còn hiệu lực    ┌───────────┐
   tạo job ────► │ pending  │ ──────────────────────► │  claimed  │
                 └──────────┘                         └───────────┘
                      ▲                                     │ Muse bắt đầu làm
                      │ lease hết hạn /                     ▼
                      │ fail còn lượt retry           ┌───────────┐
                      └────────────────────────────── │  running  │
                                                      └───────────┘
                                                        │        │
                                          trả kết quả ──┘        └── fail hết lượt
                                                        ▼                 ▼
                                                   ┌────────┐       ┌──────────┐
                                                   │  done  │       │  failed  │
                                                   └────────┘       └──────────┘

   pending/claimed/running ──(Joseph hoặc hệ thống hủy)──► cancelled
```

Quy tắc chuyển trạng thái:

- `pending → claimed`: chỉ qua endpoint claim, khi job đang `pending` hoặc claim cũ đã hết hạn.
- `claimed → running`: Muse gọi heartbeat lần đầu hoặc ngầm định khi claim thành công (chọn một cách, ghi rõ trong triển khai; khuyến nghị: claim thành công = `running` luôn, bỏ trạng thái `claimed` trung gian nếu muốn đơn giản — nhưng phải giữ field `claim_expires_at`).
- `running → done`: chỉ qua endpoint result.
- `running → pending`: tự động khi lease hết hạn mà chưa có kết quả, hoặc Muse gọi fail khi `attempts < max_attempts`.
- `running → failed`: Muse gọi fail khi `attempts >= max_attempts`, hoặc lỗi không thể retry (ví dụ `unsupported_type`, `invalid_payload`).
- `* → cancelled`: Joseph/hệ thống hủy; Muse tôn trọng hủy ngay khi thấy trạng thái đổi.

---

## 4. Các loại job (type) và payload

Danh sách khởi điểm. Chỉ thêm `type` mới sau khi Joseph duyệt, vì Muse chỉ chạy type trong allowlist.

### 4.1. `write_script` — Viết kịch bản từ ý tưởng

```json
{
  "idea": "Câu chuyện về một chú mèo lạc vào thành phố tương lai",
  "target_duration_sec": 1200,
  "language": "vi",
  "style": "kể chuyện ấm áp, có cao trào",
  "audience": "người lớn trẻ tuổi"
}
```

Kết quả kỳ vọng: kịch bản đầy đủ dạng text/markdown, ước lượng thời lượng đọc, lưu trong `result.outputs.script`.

### 4.2. `split_scenes` — Chia kịch bản thành cảnh + prompt

```json
{
  "script": "<toàn văn kịch bản>",
  "max_scene_sec": 10,
  "visual_style": "cinematic, ánh sáng vàng ấm",
  "aspect_ratio": "16:9"
}
```

Kết quả kỳ vọng: danh sách cảnh trong `result.outputs.scenes`, mỗi cảnh gồm `scene_no`, `duration_sec`, `narration` (lời thoại/lời dẫn), `image_prompt`, `video_prompt`, `negative_prompt` (nếu hệ thống render hỗ trợ).

### 4.3. `make_video` — Trọn gói: kịch bản → cảnh → gọi render

```json
{
  "story": "Câu chuyện / ý tưởng gốc...",
  "target_duration_sec": 1200,
  "language": "vi",
  "visual_style": "cinematic",
  "aspect_ratio": "16:9",
  "voice": "nữ miền Nam, ấm",
  "music_mood": "piano nhẹ"
}
```

Luồng Muse thực thi: viết kịch bản → chia cảnh → gọi API render của chính hệ thống VPS (theo spec kết nối riêng) → chờ render → kiểm tra file đầu ra → trả `video_url` trong kết quả. Đây là job dài; Muse cập nhật tiến độ qua heartbeat (mục 5.4).

### 4.4. `qc_video` — Kiểm tra chất lượng video đã render

```json
{
  "video_url": "https://<domain>/outputs/abc123.mp4",
  "checklist": ["đúng kịch bản", "không lỗi chính tả trong chữ trên hình", "âm thanh rõ", "đúng thời lượng ±10%"]
}
```

Kết quả kỳ vọng: `result.outputs.qc` gồm `pass` (true/false), điểm từng hạng mục, danh sách vấn đề phát hiện kèm mốc thời gian trong video.

### 4.5. `custom_instruction` — Việc tự do do Joseph soạn

```json
{
  "instruction": "Nội dung việc bằng ngôn ngữ tự nhiên..."
}
```

Lưu ý: loại này chỉ bật khi Joseph yêu cầu rõ; mặc định KHÔNG nằm trong allowlist tự chạy, vì nội dung tự do khó kiểm soát phạm vi.

---

## 5. Các endpoint chi tiết

### 5.1. Tạo job — `POST /jobs` (phía hệ thống của Joseph gọi, không phải Muse)

Request:

```json
{
  "job_id": "job-20261102-001",
  "type": "make_video",
  "priority": 2,
  "max_attempts": 3,
  "source": "video-web-ui",
  "payload": { "...": "theo mục 4" }
}
```

Response `201`:

```json
{
  "ok": true,
  "job_id": "job-20261102-001",
  "status": "pending",
  "duplicate": false
}
```

Nếu gửi lại cùng `job_id`: trả `200` với `"duplicate": true`, không tạo job mới, không đổi payload cũ.

### 5.2. Lấy job đang chờ — `GET /jobs/pending?limit=10` (Muse gọi)

Chỉ trả các job `status = pending` (bao gồm job có claim đã hết hạn), đã sắp xếp theo priority + `created_at`. Không trả payload của job đã `done/failed/cancelled`.

Response `200`:

```json
{
  "ok": true,
  "count": 1,
  "jobs": [
    {
      "job_id": "job-20261102-001",
      "type": "make_video",
      "priority": 2,
      "attempts": 0,
      "max_attempts": 3,
      "created_at": "2026-11-02T15:30:00+07:00",
      "payload": { "...": "theo mục 4" }
    }
  ]
}
```

### 5.3. Claim job — `POST /jobs/:id/claim` (Muse gọi)

Request:

```json
{ "worker": "claude-muse", "lease_sec": 900 }
```

Response `200` khi claim thành công:

```json
{
  "ok": true,
  "job_id": "job-20261102-001",
  "status": "running",
  "attempts": 1,
  "claim_expires_at": "2026-11-02T15:45:00+07:00"
}
```

Response `409` khi job đang bị claim còn hiệu lực hoặc đã `done/failed/cancelled`. Claim phải là thao tác nguyên tử (atomic): trong database dùng transaction / `UPDATE ... WHERE status='pending'` và kiểm tra số row bị ảnh hưởng; với bản file-based dùng cơ chế đổi tên file nguyên tử (mục 8).

### 5.4. Heartbeat / gia hạn lease — `POST /jobs/:id/heartbeat` (Muse gọi khi job chạy lâu)

Request:

```json
{ "worker": "claude-muse", "lease_sec": 900, "progress_note": "Đã chia 40/120 cảnh" }
```

Response `200`: `{ "ok": true, "claim_expires_at": "..." }`. Job `make_video` 20 phút có thể chạy lâu hơn một lease — Muse gọi heartbeat định kỳ (mặc định mỗi 5 phút khi job đang chạy). `progress_note` được lưu vào job để Joseph xem tiến độ.

### 5.5. Trả kết quả — `POST /jobs/:id/result` (Muse gọi)

Request theo schema mục 6. Response `200`: `{ "ok": true, "job_id": "...", "status": "done" }`.
Nếu job đã `done` rồi mà Muse gửi lại cùng kết quả (do lỗi mạng ở lần trước): trả `200` với `"duplicate": true`. Nếu job đã bị hủy (`cancelled`): trả `409`.

### 5.6. Báo lỗi — `POST /jobs/:id/fail` (Muse gọi)

Request:

```json
{
  "worker": "claude-muse",
  "error": { "code": "render_timeout", "message": "Hệ thống render không phản hồi sau 30 phút" },
  "retryable": true
}
```

Hành vi phía server: nếu `retryable = true` và `attempts < max_attempts` → job về `pending`; ngược lại → `failed`. Lưu `last_error`.

### 5.7. Xem một job — `GET /jobs/:id` (cả hai phía)

Trả toàn bộ record của job, gồm `status`, `result`, `last_error`, phục vụ tra cứu/debug.

### 5.8. Hủy job — `POST /jobs/:id/cancel` (phía Joseph/hệ thống gọi)

Đặt `status = cancelled`. Muse kiểm tra trạng thái job trước mỗi bước dài; thấy `cancelled` thì dừng ngay và không trả kết quả nữa.

---

## 6. Schema kết quả (Muse POST về qua endpoint 5.5)

```json
{
  "worker": "claude-muse",
  "finished_at": "2026-11-02T16:05:00+07:00",
  "summary": "Đã hoàn thành video 20 phút: 120 cảnh, render xong, QC đạt 9/10.",
  "outputs": {
    "video_url": "https://<domain>/outputs/job-20261102-001.mp4",
    "duration_sec": 1195,
    "script": "<kịch bản đầy đủ, nếu có>",
    "scenes": [
      {
        "scene_no": 1,
        "duration_sec": 10,
        "narration": "...",
        "image_prompt": "...",
        "video_prompt": "..."
      }
    ],
    "qc": { "pass": true, "score": 9, "issues": [] }
  }
}
```

Quy tắc:

- `summary` là câu tóm tắt ngắn để hiển thị cho Joseph (cũng là nội dung Muse nhắn trong chat). Viết bằng tiếng Việt, nêu con số cụ thể.
- `outputs` chỉ chứa các khóa phù hợp với `type` của job; khóa không áp dụng thì bỏ qua, không để chuỗi rỗng.
- File lớn (MP4) KHÔNG nhúng vào JSON. Muse chỉ trả `video_url` (link do hệ thống VPS cấp) hoặc, nếu Muse là bên giữ file, một link tải riêng sẽ được gửi kèm trong chat — không qua API này.

---

## 7. Hành vi phía Muse (để đội VPS biết trước)

- **Lịch polling**: Muse kiểm tra `GET /jobs/pending` mỗi 5 phút (có thể chỉnh). Ngoài lịch, Joseph nhắn "check việc" trong chat thì Muse kiểm tra ngay lập tức.
- **Thứ tự làm việc**: mỗi vòng polling, Muse claim tối đa 1 job chạy dài (ví dụ `make_video`) hoặc vài job ngắn, theo priority.
- **Báo cáo cho Joseph**: mỗi khi một job chuyển sang `done` hoặc `failed`, Muse nhắn một tin tóm tắt trong chat (dùng đúng nội dung `summary` / `last_error.message`).
- **Việc ngoài allowlist**: Muse fail job với `error.code = "unsupported_type"` và nhắn Joseph xin ý kiến, không tự thực thi.
- **Việc cần duyệt**: nếu payload yêu cầu hành động có tác động lớn (đăng công khai, tốn tiền, xóa dữ liệu...), Muse tạm dừng job đó và hỏi Joseph trước, dù job thuộc allowlist.
- **Mất kết nối**: nếu API của VPS không gọi được, Muse ghi nhận và thử lại ở vòng polling sau; không coi là lỗi của job, không tăng `attempts` của job.

---

## 8. Phiên bản đơn giản: thư mục inbox/outbox (khi chưa kịp code API)

Dùng khi muốn chạy thử nhanh qua SSH hoặc Tailscale, chưa có API/database.

Cấu trúc trên VPS:

```
/srv/claude-jobs/
  inbox/        <- hệ thống thả file job vào đây: <job_id>.json
  claimed/      <- Muse đổi tên file sang đây khi bắt đầu làm (khóa job)
  outbox/       <- Muse ghi kết quả: <job_id>.result.json
  failed/       <- job lỗi hẳn: <job_id>.error.json
  done/         <- job xong được chuyển vào đây để lưu trữ
```

Quy tắc:

- File job dùng đúng schema mục 3 và 4 (bỏ các field server quản lý, trừ `job_id`, `type`, `priority`, `payload`, `created_at`).
- Claim bằng cách đổi tên/di chuyển file `inbox/<job_id>.json` → `claimed/<job_id>.json`. Trên cùng filesystem, rename là nguyên tử nên hai tiến trình không claim trùng được; ai rename thành công thì được việc.
- Ghi kết quả: ghi vào file tạm `outbox/.tmp-<job_id>` rồi rename thành `outbox/<job_id>.result.json` để phía đọc không bao giờ thấy file ghi dở.
- Hệ thống của Joseph theo dõi `outbox/` và `failed/` để cập nhật trạng thái hiển thị.
- Nhược điểm so với API: không có lease/heartbeat tự động; job kẹt ở `claimed/` quá lâu (ví dụ > 60 phút) cần người hoặc script chuyển trả về `inbox/`. Vì vậy bản thư mục chỉ nên là giai đoạn thử nghiệm.

---

## 9. Bảo mật

- Token chỉ lưu ở: biến môi trường phía VPS, và kho bảo mật (Secure Vault / connector) phía Muse. Không hard-code trong source, không commit lên git, không ghi vào log.
- Nếu public ra internet: bắt buộc HTTPS + token; khuyến nghị thêm giới hạn IP hoặc chữ ký HMAC cho request tạo job.
- Nếu qua Tailscale: API chỉ bind vào địa chỉ tailnet (100.x) hoặc localhost + proxy nội bộ, không mở port ra public.
- Phía VPS nên ghi log tối thiểu cho mỗi thao tác: thời gian, endpoint, `job_id`, mã trạng thái — KHÔNG log header Authorization và KHÔNG log toàn bộ payload nếu payload có nội dung nhạy cảm.
- Joseph có thể thu hồi quyền bất cứ lúc nào bằng cách đổi token phía VPS; Muse sẽ báo lỗi 401 và dừng polling cho tới khi được cấp token mới qua form bảo mật.

---

## 10. Kiểm thử và tiêu chí nghiệm thu

Chạy lần lượt bằng curl (thay `<BASE>` và `<TOKEN>` bằng giá trị thật ở môi trường test):

```bash
# 0) Không có token phải bị chặn
curl -s -o /dev/null -w "%{http_code}\n" <BASE>/jobs/pending        # kỳ vọng: 401

# 1) Tạo job test
curl -s -X POST <BASE>/jobs -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"job_id":"job-test-001","type":"write_script","priority":3,
       "payload":{"idea":"Mèo lạc vào thành phố tương lai","target_duration_sec":60,"language":"vi"}}'
# kỳ vọng: 201, status pending, duplicate false

# 2) Tạo lại đúng job đó
# kỳ vọng: 200, duplicate true, tổng số job không tăng

# 3) Lấy pending
curl -s "<BASE>/jobs/pending?limit=10" -H "Authorization: Bearer <TOKEN>"
# kỳ vọng: thấy job-test-001

# 4) Claim
curl -s -X POST <BASE>/jobs/job-test-001/claim -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" -d '{"worker":"claude-muse","lease_sec":900}'
# kỳ vọng: 200, status running, attempts 1

# 5) Claim lại ngay -> kỳ vọng 409

# 6) Trả kết quả
curl -s -X POST <BASE>/jobs/job-test-001/result -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"worker":"claude-muse","summary":"Xong kịch bản test","outputs":{"script":"..."}}'
# kỳ vọng: 200, status done
```

Nghiệm thu đạt khi:

- [ ] Request thiếu/sai token luôn nhận 401.
- [ ] Tạo job trùng `job_id` không sinh job mới (idempotency).
- [ ] Hai lần claim đồng thời chỉ một lần thành công (atomic claim).
- [ ] Job bị claim mà không có kết quả thì tự về `pending` sau khi lease hết hạn.
- [ ] Fail với `retryable=true` còn lượt thì về `pending`; hết lượt thì `failed`.
- [ ] Kết quả gửi trùng cho job đã `done` không ghi đè sai (trả `duplicate: true`).
- [ ] Muse kết nối được từ môi trường của Muse (public HTTPS hoặc Tailscale) và hoàn thành trọn vòng đời của một job test: pending → claim → result → done.

---

## 11. Checklist triển khai

Phía VPS (đội code của Joseph):

- [ ] Chọn phương án: API (mục 5) hoặc thư mục (mục 8) cho giai đoạn đầu.
- [ ] Tạo bảng/file lưu job đúng schema mục 3, có index theo `status` + `priority` + `created_at`.
- [ ] Sinh token, đặt vào biến môi trường `CLAUDE_JOBS_TOKEN`.
- [ ] Cài job nền (cron/worker) quét lease hết hạn mỗi phút để trả job về `pending`.
- [ ] Chạy bộ test mục 10 ở môi trường test trước khi mở cho Muse.

Phía Muse (Sunny tự làm sau khi VPS sẵn sàng):

- [ ] Tạo connector riêng cho API này; Joseph nhập token qua form bảo mật.
- [ ] Test một vòng job thật nhỏ (`write_script` 60 giây) và đối chiếu kết quả hai phía.
- [ ] Bật lịch polling mỗi 5 phút + cơ chế báo cáo vào chat.
- [ ] Chốt allowlist `type` được tự chạy với Joseph.

---

## 12. Câu hỏi mở cần Joseph chốt trước khi code

1. Giai đoạn đầu dùng API hay thư mục inbox/outbox?
2. API public HTTPS hay chỉ qua Tailscale?
3. Danh sách `type` được phép tự chạy ngay: chỉ `write_script` + `split_scenes`, hay gồm cả `make_video` và `qc_video`?
4. Chu kỳ polling mong muốn: 5 phút có đủ nhanh không, hay cần 1–2 phút?
5. Khi job `failed` hẳn, hệ thống của bạn muốn nhận thêm thông báo qua kênh nào ngoài API (email/webhook nội bộ...)?
