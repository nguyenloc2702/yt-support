# PLAN — Workflow 5 bước đơn giản

> Trạng thái: **ĐÃ HOÀN THÀNH TRIỂN KHAI VÀ PASS TOÀN BỘ TEST XUYÊN TẦNG** (Unit, Smoke, Regression, E2E).
>
> Tất cả các giai đoạn từ 1 đến 5 đã được thực thi và nghiệm thu tự động nghiêm ngặt bằng Pytest + Ruff + Subprocess runners.

---

## 1. Luồng mục tiêu

```
1. Dán URL YouTube → Clone
2. Nhập thời lượng mong muốn → Tổng hợp
3. Xem mô tả chi tiết theo timestamp (cảnh gì, ai làm gì, thoại gì, âm thanh gì)
4. Dán script chuẩn → Feedback theo mốc thời gian (miss audio ở đoạn nào)
5. Preview + nhạc nền tùy chọn → Xuất
```

---

## 2. Phân loại file: SỬA / UPDATE / THÊM MỚI / KHÔNG ĐỔI

### 2.1 THÊM MỚI

| File | Mục đích | Phụ thuộc |
|---|---|---|
| `pages/00_workflow.py` | Entry point duy nhất điều phối 5 bước, hiển thị stepper trạng thái | `services/project_service.py`, `services/job_service.py`, `services/render_service.py` |
| `pipeline/timeline_report.py` | Báo cáo theo timestamp output: mô tả hình ảnh, thoại, audio state, confidence | `pipeline/timeline_builder.py`, `pipeline/transcribe.py` |
| `pipeline/script_matcher.py` | Đối chiếu script chuẩn với report, trả `matched/partial/miss` + severity + message | `pipeline/timeline_report.py` |
| `tests/test_timeline_report.py` | Unit test cho timeline report | pytest |
| `tests/test_script_matcher.py` | Unit test cho script matcher | pytest |
| `tests/test_workflow_smoke.py` | Smoke test entry point 5 bước | pytest + streamlit |

> Ghi chú: 3 file code trên **đã tồn tại** do triển khai nhầm trước đó. Khi được duyệt, cần **review lại** thay vì tạo mới từ đầu.

### 2.2 UPDATE (sửa nội dung hiện có)

| File | Sửa gì | Chi tiết |
|---|---|---|
| `services/analysis_service.py` | Tích hợp sinh `timeline_report.json` sau khi build timeline | Gọi `build_timeline_report(timeline, transcript, scenes, silences)` rồi ghi vào `projects/<id>/analysis/timeline_report.json`. **Đã patch lần trước — cần review lại diff** |
| `pages/01_projects.py` | Gọn lại: bỏ mode upload, chỉ còn URL; di chuyển advanced ra riêng | Thực chất để nguyên, chỉ ẩn khỏi sidebar mặc định |
| `pages/02_analysis.py` | ẩn khỏi sidebar, chuyển thành "advanced" | Chỉ để tái dùng khi user muốn cấu hình kỹ thuật |
| `pages/07_export.py` | Tối giản UI: 2 nút Preview/Xuất + toggle nhạc nền duy nhất | Ẩn gain/offset/fade/loop vào `st.expander("Advanced audio")` |
| `pipeline/timeline_report.py` | Mở rộng mô tả hình ảnh bằng keyframe + shot analysis | Gọi `pipeline/keyframes.py`, `pipeline/shot_analysis.py` để sinh mô tả hành động, chủ thể |
| `pipeline/script_matcher.py` | Thêm phát hiện "script yêu cầu thoại nhưng vùng đó chỉ silence/music" → miss severity high | Mở rộng logic hiện có, thêm规则 phân loại |
| `app.py` hoặc config sidebar | Ẩn các trang dư thừa khỏi sidebar mặc định, gộp vào folder "Advanced" | Dùng `.streamlit/pages.toml` hoặc rename tiền tố |

### 2.3 CẦN BỎ khỏi luồng chính (giữ file, ẩn khỏi sidebar)

| File | Lý do |
|---|---|
| `pages/04_transcript.py` | Gộp vào bước 3 |
| `pages/05_script_review.py` | Gộp vào bước 4 |
| `pages/06_audio_delivery.py` | Gate dư thừa |
| `pages/06_timeline.py` | User không cần chỉnh timeline tay trong luồng đơn giản |
| `pages/05_media_analysis.py` | Gate dư thừa |

### 2.4 KHÔNG ĐỔI (giữ nguyên backend)

- `pipeline/youtube.py`
- `pipeline/transcribe.py`
- `pipeline/timeline_builder.py`
- `pipeline/renderer.py`
- `pipeline/audio_mix.py`
- `pipeline/keyframes.py`
- `pipeline/shot_analysis.py`
- `services/job_service.py`
- `services/project_service.py`
- `core/models.py`, `core/schemas.py`, `core/enums.py`
- `config/settings.py`

---

## 3. Thiết kế chi tiết từng bước

### Bước 1 — Clone
- Reuse `create_project_from_url` trong `services/project_service.py`
- Giao diện: `[URL]` + `[Tên project]` + `[☑ Quyền sử dụng]` + `[🚀 Clone]`
- Hiện progress bar + lỗi rõ ràng khi thiếu `yt-dlp` / URL private / video invalid

### Bước 2 — Thời lượng
- Input số giây (hoặc phút)
- Ẩn các options kỹ thuật; dùng giá trị mặc định trong `config/settings.py`
- Nút `🚀 Tổng hợp & phân tích` → `job_service.submit_analysis(project_id, options)`
- Hiển thị progress của job + nút hủy

### Bước 3 — Mô tả theo timestamp
- Sau khi analysis xong, đọc `analysis/timeline_report.json`
- Hiển thị dạng bảng/expander: mỗi dòng gồm
  - `output_start → output_end`
  - `visual_description` (cảnh, hành động, chủ thể)
  - `transcript` (thoại nếu có)
  - `audio_state` (`speech_detected` / `silence` / `noise_or_music_or_unknown`)
  - `confidence`
- Nâng cấp `visual_description` bằng keyframe + shot analysis (không dùng LLM trong giai đoạn đầu, giữ deterministic)

### Bước 4 — Đối chiếu script
- Textarea để user dán script chuẩn
- Nút `🔎 Đối chiếu` → `match_script_to_report(script_text, report)`
- Kết quả hiển thị dạng bảng:
  - Trạng thái: `✅ matched` / `🟡 partial` / `❌ miss`
  - Timestamp: `từ X đến Y`
  - Message: ví dụ "từ 12.3s đến 18.7s đang miss audio — script yêu cầu thoại nhưng vùng này chỉ có silence"
  - Severity: `low/medium/high`

### Bước 5 — Preview / Xuất
- Toggle 1 dòng: `🎵 Nhạc nền (âm lượng nhỏ)` với file picker
- Nút `👁 Preview` → `submit_preview_render`
- Nút `⬇️ Xuất` → `submit_final_render`
- Ẩn advanced audio settings vào expander

---

## 4. Plan TEST — làm đến đâu test đến đó

### Giai đoạn 1 — Skeleton `pages/00_workflow.py` (Bước 1-2)
**Code:** tạo skeleton + stepper + clone + duration + gọi analysis.
**Test song song:**
- `tests/test_workflow_smoke.py` — chạy `streamlit run` headless, check load page không lỗi.
- Manual test: dán URL công khai → clone OK; dán URL private → hiện lỗi đúng.

### Giai đoạn 2 — `pipeline/timeline_report.py` (Bước 3)
**Code:** mở rộng report, thêm keyframe/shot description.
**Test song song:**
- `tests/test_timeline_report.py`
  - Timestamp remapping output → source đúng
  - Phân chia theo transcript boundary
  - `audio_state` đúng cho speech/silence/music
  - Empty timeline → trả report rỗng, không crash
  - Nhiều clip → `row_id` unique, thứ tự đúng

### Giai đoạn 3 — `pipeline/script_matcher.py` (Bước 4)
**Code:** hoàn thiện matcher, thêm audio-missing detection.
**Test song song:**
- `tests/test_script_matcher.py`
  - Câu script khớp → `matched`
  - Câu script khớp một phần → `partial`
  - Câu script không khớp gì → `miss`
  - Câu yêu cầu thoại nhưng vùng đó silence → `miss` + severity `high`
  - Script rỗng / report rỗng → trả kết quả hợp lệ, không crash

### Giai đoạn 4 — Export tối giản (Bước 5)
**Code:** gộp export vào `pages/00_workflow.py`, ẩn advanced controls.
**Test song song:**
- Manual: preview render OK, final render OK, bật/tắt nhạc nền không crash.
- Đảm bảo `pipeline/audio_mix.py` hoạt động đúng với gain thấp mặc định.
- `pytest tests/test_pages_smoke.py` — regression các trang cũ.

### Giai đoạn 5 — Regression tổng
- `pytest tests/ -x` toàn bộ
- Ruff trên các file mới/sửa
- `python -m compileall` các file đã chỉnh
- Manual end-to-end: URL → duration → tổng hợp → xem report → dán script → nhận feedback → preview → xuất

---

## 5. Rủi ro & lưu ý

| Rủi ro | Giảm thiểu |
|---|---|
| Đã có code triển khai nhầm từ phiên trước | Review diff trước khi tiếp tục, không tạo lại từ đầu |
| LLM/VLM cho mô tả hình ảnh | Giai đoạn đầu dùng keyframe + shot analysis deterministic; LLM để phase sau |
| `script_matcher` hiện là lexical, chưa semantic | Ghi chú rõ trong UI đây là "first-pass", không phải match cuối cùng |
| Workflow cũ có thể đang được test tự động dựa vào trang cũ | Giữ nguyên trang cũ (chỉ ẩn khỏi sidebar), không xóa file |
| Sidebar Streamlit tự sinh theo `pages/` | Dùng tiền tố số hoặc `.streamlit/pages.toml` để nhóm "Advanced" |

---

## 6. Thứ tự triển khai khi được duyệt

1. Review lại diff các file đã patch nhầm từ phiên trước
2. Skeleton `pages/00_workflow.py` + stepper (Giai đoạn 1) → test smoke
3. Hoàn thiện `pipeline/timeline_report.py` (Giai đoạn 2) → unit test
4. Hoàn thiện `pipeline/script_matcher.py` (Giai đoạn 3) → unit test
5. Gộp export, ẩn advanced (Giai đoạn 4) → manual test + pages smoke
6. Sidebar grouping + regression tổng (Giai đoạn 5)
