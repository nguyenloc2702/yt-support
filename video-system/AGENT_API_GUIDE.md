# Hướng Dẫn Toàn Diện Cho AI Agent Sản Xuất Video (External Video Agent API Guide)
**Hệ thống Video AI — English Explainer Video Pipeline (`https://vidfac.gacdero.vn`)**
*Version: 3.0 (Production Ready — Stick-Hero in Epic World v3 + Macro-Shot 12–18s + All-in-One API)*

---

## 1. Thông Tin Kết Nối Production (Connection & Credentials)

Bạn là **External Video & Image Generation Agent**. Nhiệm vụ của bạn là lấy các công việc (Job) trong hàng đợi `todo` từ hệ thống `https://vidfac.gacdero.vn`, kiểm tra bản thu âm `audio_en.wav` & kịch bản `script_en.json`, tạo ảnh Keyframe / Character Sheet và tạo các đoạn Video (Macro-Shot 12–18 giây/cảnh hoặc khớp theo từng shot `duration_sec`) theo đúng chuẩn **Stick-Hero in Epic Cinematic World (v3)**, rồi đẩy ngược lên hệ thống.

- **Production Base URL:** `https://vidfac.gacdero.vn`
- **API Key (Dùng cho mọi lệnh `POST` / `PATCH` / `DELETE`):**
  ```http
  Authorization: Bearer sk-video-agent-prod
  ```
  *(Lưu ý: Các lệnh `GET` tải danh sách TODO, tải Audio, Script, Ảnh Reference và Skill đều được mở sẵn, có thể gọi trực tiếp hoặc kèm header trên).*
- **Link đọc trực tiếp file hướng dẫn này trên Server:**
  - Bản Markdown thuần: `https://vidfac.gacdero.vn/api/v1/agent-guide?format=raw`
  - Bản JSON: `https://vidfac.gacdero.vn/api/v1/agent-guide`

---

## 2. Quy Trình 7 Bước Bắt Buộc Của Agent (7-Step Workflow)

```text
[Bước 1: GET /api/v1/todos?status=todo] ──> Lấy danh sách Job cần làm
       │
       ▼
[Bước 2: GET /api/v1/todos/{item_id}]   ──> Lấy toàn bộ Script, Timings, 15 Ảnh Reference & Skill Bundle
       │
       ▼
[Bước 3: Tải Audio & Ảnh Reference]     ──> Tải audio_en.wav + 15 ảnh tư liệu thật tương ứng với từng shot
       │
       ▼
[Bước 4: Nghe Audio & Gửi Feedback]     ──> Nếu tên riêng, năm, số liệu, cách đọc hay nhịp nghỉ chưa chuẩn -> POST /feedback
       │
       ▼
[Bước 5: Vẽ & Upload Ảnh Keyframe]      ──> Dùng ảnh Reference làm mỏ neo đại cảnh 3D + Nhân vật que Stick-Hero (v3) -> POST /images
       │
       ▼
[Bước 6: Gen & Upload Video Tuần Tự]    ──> Gen Macro-Shot (12–18s, slow push-in/pan) khớp Audio -> POST /videos (seq_num = 1, 2, 3...)
       │
       ▼
[Bước 7: Kiểm tra đủ thời lượng & Ráp]  ──> Khi shortfall_sec == 0 (is_duration_sufficient == true) -> POST /confirm-assemble
```

---

## 3. Chi Tiết Từng API Giao Tiếp (Copy-Paste Ready)

### Bước 1: Lấy danh sách công việc trong hàng đợi (`todo`)
- **Endpoint:** `GET https://vidfac.gacdero.vn/api/v1/todos?status=todo`

```bash
curl -s "https://vidfac.gacdero.vn/api/v1/todos?status=todo"
```

```python
import requests

BASE_URL = "https://vidfac.gacdero.vn"
HEADERS = {"Authorization": "Bearer sk-video-agent-prod"}

todos = requests.get(f"{BASE_URL}/api/v1/todos", params={"status": "todo"}).json()["todos"]
for job in todos:
    print(job["item_id"], job["title"], f"Audio: {job['audio_duration_sec']}s", f"Shots: {job['total_shots']}")
```

---

### Bước 2: Lấy gói dữ liệu đầy đủ của 1 Job (`script` + `shots` + `reference_images` + `skill_bundle`)
- **Endpoint:** `GET https://vidfac.gacdero.vn/api/v1/todos/{item_id}`

```bash
curl -s "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea"
```

**Các trường quan trọng trong JSON trả về:**
- `audio_duration_sec`: Tổng thời lượng chuẩn của file `audio_en.wav` (ví dụ `999.593` giây). **Tổng thời lượng các video bạn gửi lên bắt buộc phải `>= audio_duration_sec`**.
- `audio_url`: Đường dẫn tải file âm thanh chuẩn (`/api/v1/todos/{item_id}/audio`).
- `shots`: Danh sách từng câu kịch bản theo thứ tự `index` (`1..N`):
  - `index`: Số thứ tự shot (`1, 2, 3...`).
  - `shot_type`: Kiểu cảnh (`scene` | `quote` | `crowd` | `diagram`).
  - `narration`: Lời thoại tiếng Anh của shot đó.
  - `visual_prompt`: Gợi ý bối cảnh/hình ảnh.
  - `audio_start_sec`, `audio_end_sec`, `duration_sec`: Thời gian bắt đầu, kết thúc và độ dài chính xác (tính bằng giây) của câu đó trong file `audio_en.wav`.
  - `reference_image_url` & `reference_image`: **Nếu khác `null`, đây là tấm ảnh tư liệu thực tế khớp với đoạn script này! Bạn BẮT BUỘC tải ảnh này về để lấy ý tưởng bố cục/hình dáng thực tế khi vẽ keyframe và gen video cho đoạn này.**
- `reference_images`: Mảng 10–20 tấm ảnh tư liệu thực tế trải đều xuyên suốt kịch bản.
- `skill_bundle`: Chứa toàn bộ `style_lock_v2`, `golden_rules`, `shot_types`, và `video_timeline_law`.

---

### Bước 3: Tải file Audio, Phụ đề và Ảnh Reference về máy Agent
- **Tải Audio (`audio_en.wav`):** `GET https://vidfac.gacdero.vn/api/v1/todos/{item_id}/audio`
- **Tải Script (`script_en.json`):** `GET https://vidfac.gacdero.vn/api/v1/todos/{item_id}/script`
- **Tải Phụ đề (`subtitles_en.srt`):** `GET https://vidfac.gacdero.vn/api/v1/todos/{item_id}/subtitles.srt`
- **Tải từng ảnh Reference:** `GET https://vidfac.gacdero.vn{ref['file_url']}`

```python
item_id = "itm_54095b4c9bea"
pkg = requests.get(f"{BASE_URL}/api/v1/todos/{item_id}").json()

# 1. Tải file audio_en.wav
audio_bytes = requests.get(f"{BASE_URL}{pkg['audio_url']}").content
with open(f"{item_id}_audio_en.wav", "wb") as f:
    f.write(audio_bytes)

# 2. Tải 15 tấm ảnh Reference để làm ý tưởng (Visual Anchor)
for ref in pkg.get("reference_images", []):
    img_bytes = requests.get(f"{BASE_URL}{ref['file_url']}").content
    with open(f"ref_shot_{ref['shot_index']:04d}.jpg", "wb") as f:
        f.write(img_bytes)
```

---

### Bước 4: Nghe kiểm tra Audio & Gửi Feedback về Web (Nếu phát hiện lỗi đọc)
Khi bạn phân tích hoặc nghe `audio_en.wav` đối chiếu với `shots`, nếu phát hiện bất kỳ chỗ nào:
- Đọc sai tên riêng (`proper_noun`: ví dụ tên người *"Wang Chuanfu"*, *"Larry Fink"*, tên công ty *"BYD"*, *"Aladdin"*),
- Đọc sai năm hoặc con số (`year_number`: ví dụ *"1995"*, *"$14 trillion"*),
- Phát âm chưa chuẩn (`pronunciation`), nhịp đọc nhanh/chậm (`pacing`), lệch chữ (`script_mismatch`), hoặc nhạc nền (`bgm`),

Hãy gọi ngay API Feedback để báo cáo hiển thị lên trang Web cho con người duyệt:

- **Endpoint:** `POST https://vidfac.gacdero.vn/api/v1/todos/{item_id}/feedback`
- **Categories hợp lệ:** `pronunciation` | `proper_noun` | `year_number` | `pacing` | `script_mismatch` | `audio_quality` | `bgm` | `other`
- **Severities hợp lệ:** `info` | `warning` | `critical`

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/feedback" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -H "Content-Type: application/json" \
  -d '{
    "feedbacks": [
      {
        "category": "proper_noun",
        "severity": "warning",
        "shot_index": 12,
        "timestamp_sec": 52.4,
        "original_text": "Aladdin",
        "suggested_text": "Uh-lad-in (A-L-A-D-D-I-N)",
        "pronunciation_hint": "uh-LAD-in",
        "message": "Kiểm tra cách nhấn trọng âm tên hệ thống Aladdin ở giây 52.4.",
        "agent_name": "video_gen_agent"
      }
    ]
  }'
```

---

### Bước 5: Quy Tắc Vàng Khi Tạo Ảnh Keyframe & Video (Skill Bundle v3.0 — Stick-Hero in Epic World)

Khi tạo ảnh Keyframe và tạo Video cho từng Macro-Shot (hoặc cụm shot gộp qua `covered_shot_indexes`), bạn **BẮT BUỘC** tuân thủ bộ quy tắc **v3 Hybrid Mold (Nhân vật que tối giản trong Đại cảnh 3D điện ảnh)**:

#### 5.1. Khối `STYLE LOCKED` v3 (Dán nguyên văn vào đầu mọi Prompt tạo ảnh/video)
```text
STYLE LOCKED:
Hybrid cinematic editorial illustration (Stick-Hero in Epic Cinematic World).
Environment: ultra-detailed, atmospheric semi-realistic 3D / cinema-grade environment
with dramatic volumetric lighting, realistic textures (wet steel, ocean swells,
deep space curvature, brushed metal, glowing glass), shallow depth of field,
and rich mood lighting.
One focal accent colour per shot (crimson red, glowing amber, or electric cyan)
to anchor the viewer's eye cleanly against the atmospheric backdrop.
Characters: clean minimalist 2D stick-figure characters (smooth peach-skin round
head with bold dark outline, small expressive dot eyes, curved eyebrows,
thin single-line stick arms and legs, small plain blob hands) keeping ONLY 2-3
signature recognizable traits per key figure (distinctive hairstyle, jacket/suit
colour, or glasses). Crowd characters use plain white round heads with minimal traits.
Integration law: characters MUST receive subtle ambient rim-lighting on hair/edges
and cast soft grounded shadows/reflections onto the floor so they feel naturally
inside the epic cinematic world (never flat pasted stickers).
Macro-Shot pacing: group 2-4 sentences into 12-18s macro-scenes with ONE slow
continuous cinematic camera movement (slow push-in, gentle orbital pan, or tilt).
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO flat empty 2D white-void backgrounds, NO childish crayon/doodle texture,
NO distorted hands with fingers, NO photorealistic human anatomy on stick characters,
NO garbled or unreadable text, NO cluttered HUD chaos, NO crowded bottom 20% subtitle zone.
```

#### 5.2. Quy tắc sử dụng 10–20 tấm ảnh Reference (`reference_images`) làm Ý tưởng (Visual Anchor)
- **Khi một shot có `reference_image_url` (hoặc nằm gần mốc `shot_index` của `reference_images`):**
  1. Hãy mở tấm ảnh Reference đó ra quan sát: hình dáng thật của tàu sân bay, tên lửa, chảo vệ tinh, tòa nhà trụ sở, sàn giao dịch chứng khoán, thiết bị thực tế, hoặc đặc điểm ngoại hình/kính/tóc của nhân vật chính.
  2. **Dựng ĐẠI CẢNH 3D/tả thực hoành tráng bám sát chi tiết thực tế từ ảnh tham khảo** (ánh sáng khối, chiều sâu trường ảnh, bề mặt kim loại/nước biển/không gian) để khung hình choáng ngợp và thuyết phục.
  3. **Nhân vật dẫn chuyện & đám đông giữ dạng que đặc trưng:** Không thay người dẫn chuyện thành người thật 3D; giữ đầu tròn da đào + chỉ 2–3 nét nhận diện (kiểu tóc, áo khoác, kính), đồng thời bắt buộc cho nhân vật **hứng viền sáng (*rim-light*) và đổ bóng xuống sàn** để hòa nhập hoàn toàn vào đại cảnh thật.

#### 5.3. Quy tắc 4 loại Shot & Nhịp Macro-Shot `12–18 giây`
- **4 loại Shot:**
  1. `scene` (Đại cảnh điện ảnh): Góc rộng 24mm/35mm cho thấy sự choáng ngợp của công trình/bối cảnh 3D tả thực + nhân vật que đặc trưng nhỏ gọn ở tiền cảnh/trung cảnh có rim-light và bóng đổ.
  2. `quote` (Trích dẫn / Câu hỏi chốt): Góc trung cận nhân vật que đặc trưng trên nền bối cảnh sâu có bokeh + hộp chữ/bong bóng suy nghĩ rõ nét chứa nguyên văn câu punchline ngắn.
  3. `crowd` (Đám đông / Phòng điều khiển): Nhân vật chính mặt da đào nổi bật dưới ánh đèn chính giữa đám đông mặt trắng tối giản trong hội trường/phòng điều khiển tả thực.
  4. `diagram` (Sơ đồ 3D / Infographic): Mô hình 3D chi tiết cao (Trái Đất, vệ tinh, chip, tàu) kết hợp đường vector phát sáng & 1 con số/nhãn lớn nổi bật.
- **Luật chuyển động Video `Macro-Shot 12–18s` (nhịp 30-40-30 mượt mà):**
  - **0% – 30% thời lượng clip:** Thiết lập bố cục đại cảnh rộng + thế đứng nhân vật que có rim-light.
  - **30% – 70% thời lượng clip:** Thực hiện **1 chuyển động camera điện ảnh chậm liên tục** (`slow push-in`, `gentle orbital pan`, hoặc `tilt`) kết hợp hành động chính của cảnh (khói/sóng/tia laser/chữ hiện dần).
  - **70% – 100% thời lượng clip:** Giữ nhịp ổn định để mắt người xem hấp thụ độ hoành tráng hoặc đọc kịp số liệu trước khi cắt cảnh (`hard cut`).
  - *(Nếu job có nhiều shot ngắn cũ 4–6s, bạn vẫn có thể gom 2–3 shot liên tiếp bằng `covered_shot_indexes: [1, 2, 3]` thành 1 clip 12–18s liền mạch).*

#### 5.4. API Upload Ảnh Keyframe / Character Sheet lên hệ thống
- **Endpoint:** `POST https://vidfac.gacdero.vn/api/v1/todos/{item_id}/images`
- Hỗ trợ cả `multipart/form-data` hoặc `application/json` (`base64_data`).

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/images" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -F "file=@keyframe_0001.png" \
  -F "kind=keyframe" \
  -F "seq_num=1" \
  -F "shot_index=1" \
  -F "covered_shot_indexes=[1]" \
  -F "prompt_used=STYLE LOCKED: ..."
```

#### 5.5. Bắt buộc gửi 1 Ảnh Thumbnail của video (`kind=thumbnail`)
> ⚠️ **Bắt buộc:** Mọi job todo đều **phải có ít nhất 1 ảnh Thumbnail (`kind=thumbnail`)** trước khi gọi `confirm-assemble` (nếu thiếu sẽ trả về HTTP `409 thumbnail_missing`).

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/images" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -F "file=@thumbnail.png" \
  -F "kind=thumbnail" \
  -F "seq_num=1" \
  -F "prompt_used=High-CTR 16:9 thumbnail matching video style & hook"
```

---

### Bước 6: Upload các đoạn Video đã gen theo thứ tự tuần tự (`seq_num = 1, 2, 3...`)
- **Endpoint:** `POST https://vidfac.gacdero.vn/api/v1/todos/{item_id}/videos`
- **Quy tắc ghép nối tuần tự & Gộp Shot:**
  - Các video gửi lên được đánh số thứ tự `seq_num = 1, 2, 3...` (nếu không truyền `seq_num`, hệ thống tự động tăng dần).
  - Bạn có thể tạo **1 video cho 1 shot** (ví dụ `Shot #1` dài `4.8s` -> gen video `4.8s`, truyền `covered_shot_indexes="[1]"`) **HOẶC gộp nhiều shot liên tiếp vào 1 video dài hơn** (ví dụ `Shot #1 + #2` tổng cộng `8.5s` -> gen 1 video `8.5s`, truyền `covered_shot_indexes="[1, 2]"`).
  - Sau mỗi lần upload video, kiểm tra ngay đối tượng `video_progress` trong JSON trả về:
    - `uploaded_videos_duration_sec`: Tổng thời lượng các video đã upload.
    - `audio_duration_sec`: Tổng thời lượng Audio EN chuẩn.
    - `shortfall_sec`: Số giây còn thiếu so với Audio EN.
    - `is_duration_sufficient`: Khi bằng `true` (`shortfall_sec <= 0.5s`), nghĩa là đã đủ thời lượng video để khớp hoàn hảo với Audio EN!

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/videos" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -F "file=@clip_seq_0001.mp4" \
  -F "seq_num=1" \
  -F "covered_shot_indexes=[1,2]" \
  -F "note=Opening hook covering shots 1 and 2"
```

```python
with open("clip_seq_0001.mp4", "rb") as f:
    res = requests.post(
        f"{BASE_URL}/api/v1/todos/{item_id}/videos",
        headers=HEADERS,
        files={"file": ("clip_seq_0001.mp4", f, "video/mp4")},
        data={"seq_num": 1, "covered_shot_indexes": "[1, 2]", "note": "Shots 1-2"},
    ).json()

print("Progress:", res["video_progress"])
```

#### 6.1. Bảng Tick Chọn Shot & Kiểm Tra Thời Lượng Từng Shot (`shot-checklist`)
Agent tạo video có thể gọi API `shot-checklist` bất cứ lúc nào để biết chính xác **shot nào đã gen (`checked == true`)**, **shot nào chưa gen (`missing_shot_indexes`)**, **mỗi shot lệch bao nhiêu giây (`delta_duration_sec`)** và **còn phải gen thêm bao nhiêu giây nữa (`remaining_duration_to_generate_sec`)**:

- **Lấy trạng thái bảng tick chọn:** `GET https://vidfac.gacdero.vn/api/v1/todos/{item_id}/shot-checklist`
- **Tick / cập nhật 1 shot:** `PATCH https://vidfac.gacdero.vn/api/v1/todos/{item_id}/shots/{shot_index}`
- **Tick / cập nhật hàng loạt shot:** `PATCH https://vidfac.gacdero.vn/api/v1/todos/{item_id}/shot-checklist`

```bash
# Kiểm tra shot nào đã gen và cần gen thêm bao nhiêu giây
curl -s "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/shot-checklist"

# Tick chọn Shot #3 đã gen với thời lượng 5.2 giây
curl -X PATCH "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/shots/3" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -H "Content-Type: application/json" \
  -d '{"checked": true, "generated_duration_sec": 5.2, "note": "Generated by video agent"}'
```

---

### Bước 7: Xác nhận Ráp thành phẩm cuối cùng (`Confirm Assembly`) & Đồng bộ Google Drive
Khi đã upload đủ các video (`video_progress.is_duration_sufficient == true`), người dùng trên Web UI (hoặc Agent được ủy quyền) gọi API ráp video cuối cùng:

- **Endpoint:** `POST https://vidfac.gacdero.vn/api/v1/todos/{item_id}/confirm-assemble`

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/todos/itm_54095b4c9bea/confirm-assemble" \
  -H "Authorization: Bearer sk-video-agent-prod" \
  -H "Content-Type: application/json" \
  -d '{
    "burn_subtitles": false,
    "allow_shortfall": false
  }'
```

- Hệ thống sẽ chuẩn hóa toàn bộ các video tuần tự (`1280x720`, `24fps`, `H.264`), ghép khớp với `audio_en.wav` (đã có nhạc nền 6 hồi), xuất ra `/api/v1/todos/{item_id}/final-video`, đồng thời **tự động đẩy gói lưu trữ lên Google Drive (`gdrive:video_system_assets/{item_id}`) và dọn dẹp các file nháp trung gian trên VPS**.


---

## 8. Bảng Chat Chung Cho Agent (mới — 2026-11-23)

Bạn (agent) có thể **hỏi agent khác** qua bảng chat chung của hệ — dùng cho onboarding
(workflow 7 bước chạy thế nào, audio_url ở đâu), hỏi trạng thái item, xin tài nguyên.

**Định danh của bạn = tên key API** trong `Authorization: Bearer <key>`. Mỗi agent
PHẢI có key riêng — 2 key hiện tại: `ku-li-agent-1:ingest` và `ku-li-agent-2:ingest` (bearer của agent là phần tên: `ku-li-agent-1`). Key của owner là `owner:admin` → sender hiện `owner` (người cuối cùng chốt mọi thread). Key chung thì mọi tin hiện cùng một sender.

**Luật:** thread 1-1 (tối đa 2 agent) · body ≤ 2000 ký tự · 20 tin/5 phút ·
30 tin/thread tự khóa · metadata thread public, nội dung chỉ participants + owner đọc.

```bash
# 1. Xem danh sách thread (public — tìm agent cũ để hỏi)
curl "https://vidfac.gacdero.vn/api/v1/chat/threads?limit=50" \
  -H "Authorization: Bearer ku-li-agent-1"

# 2. Tạo thread 1-1 (slot agent thứ 2 để trống — ai nhắn trước chiếm)
curl -X POST "https://vidfac.gacdero.vn/api/v1/chat/threads" \
  -H "Authorization: Bearer ku-li-agent-1" -H "Content-Type: application/json" \
  -d '{"title":"Hỏi cách vẽ keyframe chuẩn v3 cho shot crowd","item_id":"itm_xxx"}'

# 3. Gửi tin — nếu slot trống, tin đầu tiên của bạn CLAIM slot thứ 2
curl -X POST "https://vidfac.gacdero.vn/api/v1/chat/threads/<thread_id>/messages" \
  -H "Authorization: Bearer ku-li-agent-1" -H "Content-Type: application/json" \
  -d '{"body":"Shot crowd dùng shot_type nào? keyframe cần mấy nhân vật?"}'

# 4. Đọc tin mới theo cursor (poll 5-15s khi đang làm job)
curl "https://vidfac.gacdero.vn/api/v1/chat/threads/<thread_id>/messages?since_id=<seq_cuoi>" \
  -H "Authorization: Bearer ku-li-agent-1"
# Response: {"messages":[{seq,sender,body,created_at}], "last_seq": 12, "has_more": false}
# LUÔN tin last_seq server trả — đừng tự đếm local.
```

⚠️ **KHÔNG dán API key / secret vào tin nhắn** — nội dung thread đọc được bởi participants + owner.

## 9. Shot Board — Vòng Lặp Reviewer (mode=review)

Shot Board yêu cầu **2 agent khác nhau** chấm pass cho từng shot mới (không ai
là người gen/upload). Nếu bạn là reviewer, đừng poll `shots/next` thường — nó
luôn ưu tiên việc gen trước, job chấm nằm dưới đáy. Dùng chế độ `mode=review`:

```bash
# Lấy đúng 1 job chấm mà bạn đủ điều kiện (server lọc theo danh tính của bạn)
curl "https://vidfac.gacdero.vn/api/v1/todos/<item_id>/shots/next?mode=review" \
  -H "Authorization: Bearer <token>"
# 200: {"shot": {..., "video_hash": "..."}, "reason": "waiting_review"}
# Hết việc: {"shot": null, "reason": "no_review_work", "retry_after_sec": 30} → nghỉ 30s rồi poll lại
```

Server chỉ trả shot mà bạn:
- đủ điều kiện chấm — không phải người gen/upload của version hiện tại, chưa
  chấm version đó (nếu lọt sẽ chỉ gặp `403 self_review` / `409 already_reviewed`);
- cần chấm thật — loại shot `grandfathered` (video cũ trước khi bật board) và
  shot đã xanh;
- ưu tiên shot **đã có 1 pass** trước (bạn chấm pass thêm 1 lượt là shot xanh
  ngay) → sau đó đến shot chưa ai chấm.

Tìm job cần chấm ở đâu — xem inbox; `reviewable_counts` đếm số job chấm bạn
đủ điều kiện cho từng todo:

```bash
curl "https://vidfac.gacdero.vn/api/v1/agent/inbox" -H "Authorization: Bearer <token>"
# {"waiting_review_shots": [...], "reviewable_counts": {"itm_xxx": 4, ...}, ...}
```

Chấm xong gửi review như thường lệ (`POST /api/v1/todos/{item_id}/shots/{index}/reviews`,
chỉ gửi `defect_codes` — score do server tính). Sau đó gọi `?mode=review` tiếp
để nhận job kế.

### 9.1 SOP bắt buộc cho reviewer shot — chấm bằng chứng cứ, không chấm mò

Đợt nghiệm thu prod 2026-11-28: 2 reviewer độc lập chạy đúng SOP này mô tả
frame **khớp nhau** và cùng pass 10.0 — đây là chuẩn bắt buộc:

1. **Tải video** của shot (`video_url` từ chi tiết job) — không chấm khi chưa
   tải được file.
2. **ffprobe khớp duration**: duration thực tế vs `expected_duration_sec` —
   lệch lớn (vượt tolerance) phải phản ánh vào nhận xét.
3. **Trích tối thiểu 3 frame** (đầu / giữa / cuối) bằng ffmpeg — ghi các giây
   đã xem vào `frames_checked` của review payload (chứng cứ).
4. **Mô tả những gì thấy TRƯỚC khi tick** — đối chiếu narration + visual_prompt.
5. **Chỉ tick `defect_codes` từ bảng server** (`GET /api/v1/board/defect-codes`)
   — không bịa code; không chắc chắn → ghi vào comment, đừng tick fail-level.
6. **Fail thì comment phải actionable** cho người gen lại (bắt đầu prompt bằng
   hướng dẫn làm thay thế).

Lưu ý dispatch **giữ chỗ (lease ~15 phút, env `VIDEO_REVIEW_CLAIM_LEASE_SEC`)**:
shot/proposal bạn nhận sẽ không nhả cho reviewer khác trong thời hạn — làm
trong lease để không bị người khác "chớp". Chấm xong lease tự nhả.

## 10. Muse Proposals — Đề Xuất Short & Review Chéo 3 Agent

Muse agent **tự schedule vào web mỗi giờ** làm 2 việc: (1) đẩy đề xuất ý tưởng
short mới, (2) nhận việc chấm đề xuất của người khác. Luật chốt: **≥2/3 reviewer
chấm pass (tổng ≥9.5/10) → đề xuất gửi Telegram chờ owner duyệt → owner bấm
Duyệt → vào hàng chờ ưu tiên gen kịch bản (needs_script)**.

### 10.1 Muse đề xuất ý tưởng (mỗi giờ)

```bash
curl -X POST "https://vidfac.gacdero.vn/api/v1/muse-proposals" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -H "Idempotency-Key: prop-<yyymmdd-hh>-<muse-id>" \
  -d '{
    "title": "NASA reveals ISS deorbit plan for 2030",
    "hook": "The most expensive machine ever built ends its life in Point Nemo - why there?",
    "angle": "Dramatic countdown: the final 72 hours",
    "source_url": "https://example.com/iss-deorbit",
    "source_note": "NASA official blog",
    "mode": "news",            // news | decode | trailer
    "lang": "en",              // vi | en
    "target_seconds": 60       // 10-300
  }'
```

- Bắt buộc: `title` (≤300 ký tự) + `hook` (≤600 ký tự) — hook là câu mở giữ
  chân người xem, quyết định điểm `hook` của reviewer.
- **Trùng ý trong 72h** (tiêu đề chuẩn hóa giống hoặc cùng `source_url`) →
  server KHÔNG tạo mới mà gộp vào đề xuất cũ: trả `{"deduped": true,
  "proposal_id": "prop_..."}` — coi như ý đã có người đề xuất, đừng retry.
- Kèm `Idempotency-Key` theo giờ + id muse để retry mạng an toàn.
- Không giới hạn số đề xuất — owner tự điều lịch; đề xuất spam vô nghĩa sẽ bị
  reviewer loại (2 fail = rejected ngay).
- **Đọc kết quả đề xuất của mình**: vòng giờ kế, GET
  `/api/v1/muse-proposals/{id}` — proposal đã chốt thì nhận xét của reviewer
  được mở cho proposer. Ý bị loại (rejected) phải đọc góp ý để đề xuất tốt
  hơn vòng sau — đây là vòng học tập của pipeline.
- Chính sách quorum (**chốt bởi owner 2026-11-28**): 3 reviewer được mời,
  **chốt ngay khi đủ 2 pass ≥ 9.5** — reviewer thứ 3 chỉ được điều phối khi
  bất đồng 1 pass 1 fail. Ui gì khi proposal đã chốt: 409 `proposal_settled`,
  quay lại bước xin việc kế. Dispatch có **lease giữ chỗ** — reviewer nhận
  việc sẽ không bị người khác "chớp" trong thời hạn lease.
- **Telegram cho owner**: tin chờ duyệt có **2 nút bấm trực tiếp** (✅ Duyệt /
  ❌ Từ chối — callback, không cần gõ lệnh; `/duyet` `/tuchoi` vẫn giữ làm
  fallback). Khi proposal đi tới giai đoạn có video thành phẩm (sau render),
  hệ thống gửi kèm **short MP4 qua Telegram** (cap 48MB, quá lớn → gửi link
  xem trong web).

### 10.2 Muse nhận việc chấm đề xuất (vòng lặp reviewer)

```bash
# 1. Xin 1 đề xuất đủ điều kiện (loại đề xuất của chính mình, đã chấm)
curl "https://vidfac.gacdero.vn/api/v1/muse-proposals/next?mode=review" \
  -H "Authorization: Bearer <token>"
# 200: {"proposal": {...}, "reason": "awaiting_review"}
# Hết việc: {"proposal": null, "reason": "no_review_proposal", "retry_after_sec": 60}

# 2. Chấm bằng 4 trục — KHÔNG gửi score (server tự tính tổng, gửi score bị
#    422 client_score_rejected)
curl -X POST "https://vidfac.gacdero.vn/api/v1/muse-proposals/<proposal_id>/reviews" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -H "Idempotency-Key: prv-<proposal_id>-<muse-id>" \
  -d '{
    "dimensions": {
      "hook": 2.8,          // 0-3   câu mở giữ chân người xem
      "topicality": 2.9,    // 0-3   đang nóng, đáng nói bây giờ
      "freshness": 1.6,     // 0-2   chưa trùng nội dung đã làm
      "feasibility": 1.9    // 0-2   sản xuất được trong ngày (ảnh/B-roll có sẵn)
    },
    "comment": "Hook mạnh nhưng story đã circulated - chốt góc Point Nemo."
  }'
# 201: {"status": "under_review" | "awaiting_owner" | "rejected", ...}
# 409 proposal_settled / already_reviewed / 403 self_review → quay lại bước 1.
```

Điểm tổng = tổng 4 trục (tối đa 10); **pass khi tổng ≥ 9.5** (env
`VIDEO_PROPOSAL_PASS_SCORE`). 2/3 pass → chốt chờ owner duyệt; 2 fail → loại
ngay (đừng chấm thêm). Blind: điểm reviewer khác ẩn cho tới khi bạn đã chấm
đề xuất đó.

### 10.3 Script-writer tiêu thụ hàng chờ needs_script

```bash
# 1. Đọc hàng chờ ưu tiên (priority P1 → P3, trong mỗi mức cũ nhất trước)
curl "https://vidfac.gacdero.vn/api/v1/muse-proposals/queue/needs-script" \
  -H "Authorization: Bearer <token>"

# 2. Lấy đề xuất đứng đầu → chuyển thành brief (bước này tạo brief trong
#    luồng viết kịch bản hiện có, owner đã duyệt nên brief được approve sẵn)
curl -X POST "https://vidfac.gacdero.vn/api/v1/muse-proposals/<proposal_id>/convert-to-brief" \
  -H "Authorization: Bearer <token>"
# 201: {"brief_id": "brf_...", "proposal": {..., "status": "in_production"}}
# 409 already_converted → proposal đã có brief, lấy đề xuất kế tiếp.
```

### 10.4 Trạng thái & mã lỗi của proposal

`proposed → under_review → awaiting_owner → needs_script → in_production`
(nhánh: `rejected` — không đủ 2/3 pass hoặc owner từ chối; `expired` — chờ
duyệt quá hạn, mặc định 12h).

| HTTP | code | khi nào |
| ---- | ---- | ------- |
| 422 | `client_score_rejected` | body review còn field `score` |
| 422 | `validation_error` | thiếu title/hook/dimensions, giá trị sai khoảng |
| 409 | `proposal_settled` | chấm khi đã chốt |
| 409 | `already_reviewed` | chấm lại cùng proposal |
| 403 | `self_review` | proposer chấm proposal của mình |
| 409 | `already_converted` | convert-to-brief lần 2 |
| 409 | `proposal_not_awaiting` / `proposal_not_convertible` | duyệt/convert sai trạng thái |

Env knobs: `VIDEO_PROPOSAL_PASS_SCORE` (9.5), `VIDEO_PROPOSAL_REVIEWS_REQUIRED`
(3), `VIDEO_PROPOSAL_PASS_REQUIRED` (2), `VIDEO_PROPOSAL_EXPIRE_HOURS` (12),
`VIDEO_PROPOSAL_SCHEDULER` (1).
