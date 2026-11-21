# Hướng Dẫn Toàn Diện Cho AI Agent Sản Xuất Video (External Video Agent API Guide)
**Hệ thống Video AI — English Explainer Video Pipeline (`https://vidfac.gacdero.vn`)**
*Version: 2.3 (Production Ready — All-in-One API + Shot Checklist + Skill + Visual Anchor Guide)*

---

## 1. Thông Tin Kết Nối Production (Connection & Credentials)

Bạn là **External Video & Image Generation Agent**. Nhiệm vụ của bạn là lấy các công việc (Job) trong hàng đợi `todo` từ hệ thống `https://vidfac.gacdero.vn`, kiểm tra bản thu âm `audio_en.wav` & kịch bản `script_en.json`, tạo ảnh Keyframe / Character Sheet và tạo các đoạn Video hoạt họa 2D theo đúng thời lượng từng phân cảnh, rồi đẩy ngược lên hệ thống.

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
[Bước 5: Vẽ & Upload Ảnh Keyframe]      ──> Dùng ảnh Reference làm ý tưởng (Visual Anchor) + Khuôn Crayon Vector v2 -> POST /images
       │
       ▼
[Bước 6: Gen & Upload Video Tuần Tự]    ──> Gen video theo nhịp 30-40-30 khớp thời lượng Audio -> POST /videos (seq_num = 1, 2, 3...)
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

### Bước 5: Quy Tắc Vàng Khi Tạo Ảnh Keyframe & Video (Skill Bundle v2.1)

Khi tạo ảnh Keyframe và tạo Video cho từng shot (hoặc cụm shot gộp), bạn **BẮT BUỘC** tuân thủ bộ quy tắc **Crayon Capital Vector Mold v2**:

#### 5.1. Khối `STYLE LOCKED` (Dán nguyên văn vào đầu mọi Prompt tạo ảnh/video)
```text
STYLE LOCKED:
Simple flat 2D cartoon explainer, clean sharp professional line art.
Crisp, smooth, bold black outlines, clean vector edges.
Flat colours, minimal shading: exactly one flat grey side shadow
on the shaded side of a head or body, no gradient, no detailed shading.
Characters are stick figures: oversized round heads, very small dot eyes,
simple curved eyebrows, wide toothy grins OR shocked open mouths,
a small oval or tube body, thin single-line stick arms and legs,
hands as small plain blobs, no fingers, no detailed clothing folds.
Main character: peach skin face, detailed hair, dark or coloured outfit,
sharp and high contrast. Crowd characters: plain white faces,
no skin tone, one flat grey side shadow, simple flat hair,
repeated identical oval bodies, lower contrast than the main character.
Muted-but-clear palette, slightly desaturated: dusty blue, muted navy,
muted red, off-white, slate grey, warm earth tones.
One accent colour per shot, muted red, reserved for the focal object,
number, or stamp. Everything else stays muted so the accent punches.
Bright, simple backgrounds, one clear scene, no clutter, still bright and readable.
Aspect ratio: 16:9, unless the format requires 9:16.
Negative: NO crayon texture, NO grain, NO paper texture,
NO rough sketchy wobbly lines, NO childish doodle texture,
NO overly vivid candy colours, NO dark grey muddy look,
no photorealism, no 3D render, no detailed shading, no gradient,
no blurry, no extra limbs, no distorted hands,
no crowded background, no tiny unreadable text, no garbled text,
no realistic torso, shirt, or jacket with folds and buttons,
no crowd character with the same skin tone and detail as the main character.
```

#### 5.2. Quy tắc sử dụng 10–20 tấm ảnh Reference (`reference_images`) làm Ý tưởng (Visual Anchor)
- **Khi một shot có `reference_image_url` (hoặc nằm gần mốc `shot_index` của `reference_images`):**
  1. Hãy mở tấm ảnh Reference đó ra quan sát: hình dáng thật của chiếc xe, cấu trúc viên pin, kiến trúc tòa nhà trụ sở, sàn giao dịch chứng khoán lịch sử, biểu đồ thực tế, hoặc đặc điểm ngoại hình/kính/tóc của nhân vật chính.
  2. **Lấy đúng ý tưởng, bố cục và đặc trưng thực tế từ bức ảnh đó** để đưa vào cảnh vẽ giúp video sống động, thú vị và có chiều sâu thực tế.
  3. **Tuyệt đối không chèn nguyên ảnh chụp thật (photorealistic)** vào video — hãy **chuyển thể (stylize / vectorize)** chủ thể trong bức ảnh đó sang nét vẽ 2D Vector của `STYLE LOCKED` (viền mực đen đậm, nhân vật chính mặt da đào `#E8A88A`, đám đông mặt trắng `#FFFFFF`, 1 điểm nhấn màu đỏ `#D93829`).

#### 5.3. Quy tắc 4 loại Shot & Nhịp chuyển động Video `30-40-30`
- **4 loại Shot:**
  1. `scene` (Cảnh rộng / Cảnh kể): Khung hình 3 lớp (Tiền cảnh bị cắt viền mép khung -> Trung cảnh nhân vật chính da đào -> Hậu cảnh đơn giản lặp lại).
  2. `quote` (Trích dẫn / Bong bóng suy nghĩ): Bong bóng mây trắng viền đen lớn ở trung tâm chứa nguyên văn câu punchline ngắn, mặt nhân vật nói là tiêu điểm thứ hai.
  3. `crowd` (Đám đông / Phòng họp): Chỉ duy nhất nhân vật chính có mặt màu da đào và tóc chi tiết; toàn bộ đám đông xung quanh mặt trắng phẳng + 1 bóng xám bên cạnh.
  4. `diagram` (Sơ đồ / Infographic): Chia 2 dải màu phẳng bằng đường kẻ ngang đen đậm, bên trái là silhouette vật lớn (chỉ 3–4 chi tiết bên trong), bên phải là con số/con dấu màu đỏ nhấn (`muted red`).
- **Luật chuyển động Video `30-40-30` & Máy quay tĩnh (`Static Camera`):**
  - **0% – 30% thời lượng clip:** Giữ nguyên bố cục mở đầu để mắt người xem kịp định hình.
  - **30% – 70% thời lượng clip:** Thực hiện **duy nhất 1 chuyển động chậm, dứt khoát** (ví dụ: nhân vật đổi biểu cảm/vung tay, bong bóng chữ hiện ra, đường biểu đồ đi lên, con dấu đóng xuống).
  - **70% – 100% thời lượng clip:** Giữ tĩnh khung hình cuối để người xem đọc kịp con số/chữ trước khi chuyển cảnh cắt cứng (`hard cut`).

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
