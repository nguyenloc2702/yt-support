# Kế hoạch Triển khai: Nâng cấp VLM & Tùy chọn Model theo Provider Riêng (Song song Code & Test)

> **Mục tiêu:** 
> 1. Cho phép người dùng tùy chọn hoặc nhập endpoint nhà cung cấp riêng (OpenAI, Google Gemini, OpenRouter, Ollama cục bộ, Custom/xKiro), API key và model thị giác (VLM) mong muốn.
> 2. Tích hợp pipeline VLM captioning dựa trên keyframe để tự động trích xuất ngữ cảnh hình ảnh thực tế (chủ thể, hành động, bối cảnh) vào mốc thời gian của `timeline_report.json`.
> 3. Cơ chế graceful fallback: Nếu không có API key hoặc provider lỗi, tự động trở về mô tả tất định (deterministic), không làm gián đoạn tiến trình.
> 4. Kế hoạch Code và Kế hoạch Test song song xuyên suốt từng giai đoạn.

---

## 1. Thiết kế Kiến trúc & Luồng Dữ liệu

```
[Video Clip gốc]
       │
       ▼
[Shot Detection (PySceneDetect)]
       │
       ▼
[Keyframe Extractor (FFmpeg)] ──► [Ảnh .jpg sắc nét 768px]
                                           │
                                           ▼
[Custom Provider VLM Gateway] ◄── [Base URL, API Key, Model tùy chọn]
  (OpenAI / Gemini / OpenRouter / Ollama / Custom)
       │
       ▼ (Prompt: Ai làm gì? Bối cảnh nào?)
[Mô tả hình ảnh chân thực (Visual Description)]
       │
       ▼
[Timeline Report (output timestamps + visual_desc + transcript + audio_state)]
       │
       ▼
[Script Matcher (Đối chiếu ngữ nghĩa + Phát hiện miss audio)]
       │
       ▼
[UI Workflow Bước 3: Xem & Sửa trực quan / Bước 4: Nhận Feedback]
```

---

## 2. Phân loại File & Module

### 2.1 File THÊM MỚI
| File | Mục đích | Phụ thuộc |
|---|---|---|
| `pipeline/vlm_captioning.py` | Điều phối trích xuất keyframe, đóng gói payload đa phương thái (multimodal), gọi VLM theo batch với cơ chế retry và fallback | `pipeline/keyframes.py`, `pipeline/llm_client.py` |
| `tests/test_vlm_captioning.py` | Unit test cho module VLM captioning (mock API, xử lý batch, timeout, fallback) | pytest |
| `tests/test_custom_provider.py` | Unit test lưu/đọc cấu hình provider, giải mã key, test connection tới provider giả lập | pytest |
| `tests/test_vlm_workflow_integration.py` | Integration test kiểm thử trọn vẹn từ keyframe -> VLM -> timeline report | pytest |

### 2.2 File CẬP NHẬT (Sửa đổi)
| File | Nội dung thay đổi |
|---|---|
| `services/settings_service.py` | Mở rộng cấu hình provider: hỗ trợ Preset (OpenAI, Gemini, OpenRouter, Ollama, Custom), trường `vlm_model`, hàm `test_provider_connection` |
| `pipeline/llm_client.py` | Nâng cấp hàm `call_vision_completion` để tương thích chuẩn payload của nhiều provider khác nhau (Gemini, OpenRouter, Ollama v1, OpenAI) |
| `pages/00_settings.py` | Bổ sung UI chọn nhanh Preset nhà cung cấp, nhập custom Base URL, nhập VLM model, nút "⚡ Test kết nối & thị giác" trực tiếp |
| `pages/00_workflow.py` | Tại Bước 2: thêm toggle `[x] Nhận diện bối cảnh bằng VLM`; Tại Bước 3: nút `✨ Phân tích lại hình ảnh bằng VLM` và cho phép sửa tay mô tả nếu muốn |
| `pipeline/timeline_report.py` | Tích hợp dữ liệu mô tả từ VLM vào trường `visual_description` và tính toán `confidence` dựa trên VLM output |
| `pipeline/script_matcher.py` | Tận dụng mô tả chi tiết của VLM để nâng cao độ chính xác so khớp ngữ nghĩa giữa script và hình ảnh |

---

## 3. Chi tiết Kế hoạch Code & Test Song song theo 4 Giai đoạn

---

### GIAI ĐOẠN 1: Quản lý Provider Riêng & Kiểm tra Kết nối Đa phương thái

#### 1.1 Kế hoạch Code:
1. Mở rộng `services/settings_service.py`:
   - Định nghĩa danh mục `PROVIDER_PRESETS`:
     - **OpenAI**: `https://api.openai.com/v1`, models: `gpt-4o`, `gpt-4o-mini`
     - **Google Gemini**: `https://generativelanguage.googleapis.com/v1beta/openai/`, models: `gemini-2.0-flash`, `gemini-1.5-flash`
     - **OpenRouter**: `https://openrouter.ai/api/v1`, models: `google/gemini-2.0-flash-001`, `openai/gpt-4o-mini`
     - **Ollama Local**: `http://localhost:11434/v1`, models: `qwen2-vl`, `llava`
     - **Custom / xKiro**: `https://api.xkiro.com/v1`, model tự nhập
   - Thêm các key cài đặt: `llm_provider_preset`, `vlm_model`.
   - Hàm `test_provider_connection(base_url, api_key, model_name, test_vision=True)`:
     - Gửi 1 request kiểm tra nhẹ kèm 1 ảnh mẫu 1x1 pixel base64 để xác thực model có hỗ trợ Vision thực sự hay không.
2. Cập nhật giao diện [00_settings.py](file:///c:/app/work/support-video-edit/pages/00_settings.py):
   - Selectbox chọn Provider Preset -> tự động điền Base URL và gợi ý danh sách VLM model phù hợp.
   - Cho phép nhập URL tuỳ chỉnh cho server private / local.
   - Nút `⚡ Test kết nối & Vision`: hiện thông báo xanh nếu kết nối thành công hoặc lỗi chi tiết (sai key, sai url, model không hỗ trợ vision).

#### 1.2 Kế hoạch Test song song (`tests/test_custom_provider.py`):
- [x] `test_provider_preset_defaults`: Kiểm tra các preset trả về đúng Base URL và model mặc định.
- [x] `test_settings_encryption_roundtrip`: Đảm bảo API key của custom provider được mã hóa đúng chuẩn Fernet khi lưu vào DB.
- [x] `test_test_connection_success`: Mock API client phản hồi 200 -> trả về kết quả thành công và latency.
- [x] `test_test_connection_auth_error`: Mock phản hồi 401 Unauthorized -> trả về thông báo lỗi xác thực rõ ràng.
- [x] `test_test_connection_non_vision_model`: Mock phản hồi lỗi khi model không hỗ trợ ảnh -> cảnh báo model không tương thích VLM.

---

### GIAI ĐOẠN 2: Pipeline VLM Captioning cho Keyframe

#### 2.1 Kế hoạch Code:
1. Xây dựng module [pipeline/vlm_captioning.py](file:///c:/app/work/support-video-edit/pipeline/vlm_captioning.py):
   - Hàm `caption_shot_keyframes(shot, keyframe_path, client=None, model=None)`:
     - Đọc ảnh keyframe đã trích xuất từ `pipeline/keyframes.py`.
     - Sử dụng System Prompt tối ưu hóa cho phân tích video:
       ```
       Bạn là chuyên gia phân tích video. Hãy quan sát khung hình và mô tả ngắn gọn bằng tiếng Việt trong 1-2 câu:
       1. Chủ thể chính (ai/cái gì).
       2. Hành động hoặc diễn biến đang diễn ra.
       3. Bối cảnh không gian/môi trường.
       Trả về JSON: {"subject": "...", "action": "...", "context": "...", "summary": "..."}
       ```
     - Hàm batch runner `caption_all_shots_parallel(shots, max_concurrency=3, progress_callback=None)`:
       - Xử lý đa luồng có kiểm soát để không vượt rate limit của provider.
       - Tự động bỏ qua các shot quá ngắn hoặc frame bị lỗi.
       - Bắt toàn bộ ngoại lệ mạng: nếu request thất bại, giữ nguyên fallback tất định.
2. Nâng cấp [pipeline/llm_client.py](file:///c:/app/work/support-video-edit/pipeline/llm_client.py):
   - Tối ưu `call_vision_completion` để tương thích định dạng hình ảnh của cả OpenAI, Gemini OpenAI API, và Ollama.

#### 2.2 Kế hoạch Test song song (`tests/test_vlm_captioning.py`):
- [x] `test_vlm_caption_single_frame_success`: Mock VLM trả về JSON chuẩn -> shot được bổ sung đúng `description`.
- [x] `test_vlm_caption_markdown_fenced_json`: Xử lý trường hợp VLM bọc JSON trong ```json ... ```.
- [x] `test_vlm_caption_graceful_fallback_on_network_error`: Mock timeout hoặc mất mạng -> hàm không crash, trả về fallback evidence.
- [x] `test_vlm_caption_empty_keyframes`: Xử lý shot không có keyframe mà không báo lỗi.
- [x] `test_vlm_batch_rate_limiting`: Kiểm tra cơ chế giới hạn số luồng đồng thời khi chạy batch.

---

### GIAI ĐOẠN 3: Tích hợp VLM vào Timeline Report & Đối chiếu Script

#### 3.1 Kế hoạch Code:
1. Cập nhật [pipeline/timeline_report.py](file:///c:/app/work/support-video-edit/pipeline/timeline_report.py):
   - Tiếp nhận kết quả từ VLM (trường `shot.get("vlm_description")`).
   - Ghép mô tả VLM vào trường `visual_description` của từng mốc thời gian:
     `visual_description = f"{vlm_desc} (Shot {shot_id}, {pace})"`
   - Tăng mức `confidence = "high"` khi có cả VLM description và transcript khớp nhau.
2. Cập nhật [pipeline/script_matcher.py](file:///c:/app/work/support-video-edit/pipeline/script_matcher.py):
   - Trích xuất từ khóa ngữ nghĩa từ mô tả VLM (chủ thể, hành động) để đối chiếu với câu script tương ứng.
   - Phân biệt rõ loại cảnh báo:
     - `Lệch thoại`: Script yêu cầu thoại nhưng là silence.
     - `Lệch hình ảnh`: Script mô tả hành động A nhưng VLM ghi nhận hành động B.

#### 3.2 Kế hoạch Test song song (`tests/test_vlm_workflow_integration.py`):
- [x] `test_timeline_report_incorporates_vlm_descriptions`: Kiểm tra các row trong report mang đầy đủ mô tả từ VLM.
- [x] `test_script_matcher_with_vlm_visual_evidence`: Script mô tả hành động "người thợ hàn kim loại", VLM ghi nhận "thợ hàn trong xưởng" -> trả về `matched` thay vì `miss`.
- [x] `test_script_matcher_detects_visual_mismatch`: Script yêu cầu "cảnh bãi biển mùa hè" nhưng VLM ghi nhận "văn phòng làm việc" -> gắn cờ cảnh báo `partial` hoặc `miss`.

---

### GIAI ĐOẠN 4: Trải nghiệm UI & Nghiệm thu Toàn diện

#### 4.1 Kế hoạch Code:
1. Cập nhật [pages/00_workflow.py](file:///c:/app/work/support-video-edit/pages/00_workflow.py):
   - **Bước 2:** Bổ sung tùy chọn `[x] Bật phân tích bối cảnh AI (VLM)` kèm hiển thị Provider/Model đang dùng và link tắt sang Settings.
   - **Bước 3:** 
     - Hiển thị nhãn `[VLM AI]` cạnh các đoạn đã được làm giàu mô tả.
     - Nút `✨ Làm giàu mô tả bằng VLM` cho phép chạy bổ sung VLM cho video đã tổng hợp mà không cần tổng hợp lại từ đầu.
     - Cho phép người dùng chỉnh sửa nhanh trực tiếp (inline edit) text mô tả nếu muốn can thiệp thủ công.
2. Cập nhật [services/analysis_service.py](file:///c:/app/work/support-video-edit/services/analysis_service.py):
   - Gọi bước VLM captioning sau khi trích xuất keyframes (nếu options `use_vlm=True` và đã có API key).

#### 4.2 Kế hoạch Test song song (`tests/test_pages_smoke.py` & Smoke Tests):
- [x] `test_workflow_page_smoke_with_vlm_state`: Chạy headless Streamlit với trạng thái đã bật VLM.
- [x] `test_settings_page_smoke_with_presets`: Chạy headless Streamlit trang Settings với preset mới.
- [x] `pytest tests/`: Regression toàn bộ test suite (smoke, unit, integration) đảm bảo 100% pass.
- [x] `ruff check`: Đảm bảo toàn bộ code sạch sẽ, không lỗi cú pháp hoặc typing.

---

## 4. Bảng So sánh Trước và Sau khi Nâng cấp VLM

| Tiêu chí | Trước khi nâng cấp | Sau khi nâng cấp VLM |
|---|---|---|
| **Nguồn ngữ cảnh hình ảnh** | Chỉ dựa vào metadata: mã cảnh, độ dài shot, phán đoán từ thoại | **Nhìn trực tiếp khung hình thực tế** bằng AI thị giác |
| **Mô tả chi tiết** | *"Cảnh hành động/tư liệu kèm âm thanh nền"* | *"Người đầu bếp đang thái rau củ trên thớt gỗ trong gian bếp ấm cúng"* |
| **Video không có thoại (B-roll/nhạc)** | Gần như không có thông tin ngữ cảnh | **Mô tả chính xác hành động và bối cảnh** |
| **Nhà cung cấp AI** | Cố định 1 cấu hình chung | **Tự do chọn OpenAI, Gemini, OpenRouter, Ollama local, Custom endpoint** |
| **Khả năng kiểm soát chi phí** | Chưa tối ưu | **Batching 1 frame/shot, skip shot quá ngắn, cho phép bật/tắt linh hoạt** |
| **Tính an toàn / Offline** | Có | **Tự động fallback về deterministic nếu mất mạng hoặc không có key** |
