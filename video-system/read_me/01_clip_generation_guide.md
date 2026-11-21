# Hướng Dẫn Gen Clip & Quy Chuẩn Sản Xuất Video (`#/todos` & `#/shorts`)

## 1. Quy trình chuẩn 1 Job Video dài 16:9 (`#/todos`)
1. **Tạo Job từ kịch bản:** `POST /api/v1/todos/from-script` (hoặc lấy từ queue `GET /api/v1/todos?status=todo`).
2. **Đọc gói shot & timing:** `GET /api/v1/todos/{item_id}` trả về `shots[]` kèm `duration_sec`, `start_ms`, `end_ms`, `narration`, `visual_prompt`, và `thumbnail_prompt_hint`.
3. **Chuẩn bị ảnh nhất quán theo Khuôn v3 (`Stick-Hero in Epic World`):**
   - Đại cảnh 3D/tả thực hoành tráng (ánh sáng khối, chiều sâu trường ảnh, bám chi tiết từ `reference_images`) + nhân vật que đặc trưng StickSto (chỉ giữ 2–3 nét nhận diện: tóc, áo khoác, kính) có *rim-light* và bóng đổ xuống sàn.
   - Character sheet: `kind="character"`
   - Keyframe theo shot: `kind="keyframe"`, `shot_index=<n>`
   - **Ảnh Thumbnail (BẮT BUỘC 1 ảnh):** `kind="thumbnail"`, `seq_num=1` (`1280x720`, 16:9). Thiếu ảnh này sẽ không hoàn thành được job (`409 thumbnail_missing`).
4. **Gen & Upload Video Clip Macro-Shot (`12–18s`, `1280x720`, `24fps`, H.264 `.mp4`):**
   - Khuyến khích dùng **Macro-Shot 12–18 giây/cảnh** (`slow push-in / orbital pan / tilt`), upload qua `POST /api/v1/todos/{item_id}/videos` kèm `seq_num` và `covered_shot_indexes` (ví dụ `[1]` hoặc `[1,2,3]` nếu job có shot ngắn).
   - Mỗi clip nên dài **>= tổng `duration_sec`** của các shot được cover (hệ thống tự trim chính xác theo millisecond của kịch bản để không lệch audio).
5. **Ráp Video Thành Phẩm (`final_en.mp4`):**
   - Gọi `POST /api/v1/todos/{item_id}/confirm-assemble` với `watermark_enabled=true`, `watermark_text="@StickSto"`.
   - Hệ thống tự ghép các shot, mux audio EN + BGM + watermark chuyển động chống re-up `@StickSto`.

## 2. Quy chuẩn Watermark chống re-up
- Mặc định bật `watermark_enabled=true`, chữ ký `@StickSto` mờ chuyển động theo quỹ đạo Lissajous across frame để tránh cắt/che logo.

## 3. Quy chuẩn Short 9:16 (`#/shorts`)
- Dùng `/api/v1/shorts` (`1080x1920`, `30fps`), chi tiết xem tại `AGENT_SHORT_GUIDE.md`.
