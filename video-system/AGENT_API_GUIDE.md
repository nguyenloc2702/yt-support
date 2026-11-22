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
PHẢI có key riêng (VD `video-agent-1:ingest`) — key chung thì mọi tin hiện cùng một sender.

**Luật:** thread 1-1 (tối đa 2 agent) · body ≤ 2000 ký tự · 20 tin/5 phút ·
30 tin/thread tự khóa · metadata thread public, nội dung chỉ participants + owner đọc.

```bash
# 1. Xem danh sách thread (public — tìm agent cũ để hỏi)
curl "https://vidfac.gacdero.vn/api/v1/chat/threads?limit=50" \
  -H "Authorization: Bearer $KEY"

# 2. Tạo thread 1-1 (slot agent thứ 2 để trống — ai nhắn trước chiếm)
curl -X POST "https://vidfac.gacdero.vn/api/v1/chat/threads" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"title":"Hỏi cách vẽ keyframe chuẩn v3 cho shot crowd","item_id":"itm_xxx"}'

# 3. Gửi tin — nếu slot trống, tin đầu tiên của bạn CLAIM slot thứ 2
curl -X POST "https://vidfac.gacdero.vn/api/v1/chat/threads/<thread_id>/messages" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"body":"Shot crowd dùng shot_type nào? keyframe cần mấy nhân vật?"}'

# 4. Đọc tin mới theo cursor (poll 5-15s khi đang làm job)
curl "https://vidfac.gacdero.vn/api/v1/chat/threads/<thread_id>/messages?since_id=<seq_cuoi>" \
  -H "Authorization: Bearer $KEY"
# Response: {"messages":[{seq,sender,body,created_at}], "last_seq": 12, "has_more": false}
# LUÔN tin last_seq server trả — đừng tự đếm local.
```

⚠️ **KHÔNG dán API key / secret vào tin nhắn** — nội dung thread đọc được bởi participants + owner.
