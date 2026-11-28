# Mode `cinematic` — short kể chuyện, không đưa tin

Owner yêu cầu 2026-11-28: thêm 1 dạng short "không thuộc dạng đưa tin", "giống dạng shot
của video dài", "dựa vào script audio để gen ra". Link tham chiếu bị login wall nên 4 câu
hỏi thiết kế chưa được trả lời — dưới đây là **mặc định đã chọn**, sửa bằng config chứ
không phải sửa code.

## Quyết định mặc định

| Điểm | Mặc định | Sửa ở đâu |
|---|---|---|
| Tên mode | `cinematic` | `prompts.MODES` |
| Visual | Editorial cinematic lấy từ style lock video dài (`STYLE_LOCK_V2`), chuyển 9:16 | `cinematic.style` trong `short_config.yaml` |
| Nhân vật | Không ràng buộc 1 nhân vật xuyên suốt (tránh lỗi nhất quán mặt) | prompt scene |
| Audio | TTS đọc script (như 4 mode cũ). Audio owner-upload để đợt sau | — |
| Overlay | Karaoke only: **không** badge "TIN NÓNG QUỐC TẾ", **không** headline split-screen | `render_presets.cinematic` |
| Độ dài | 30–60s, mục tiêu 45s; VI 100–140 từ, EN 85–120 từ | `modes.cinematic` |

## Khác biệt so với `news` (lý do phải là mode mới, không phải config)

1. **Giọng kể** thay vì giọng tin: kể chuyện có mạch, không cần "chốt hạ" bằng câu hỏi
   kích bình luận; hook vẫn giữ 0–3s nhưng là mở cảnh, không phải giật tít.
2. **Style lock** khác: news dùng `STYLE_GLOBAL_HYBRID_916` (HUD, lane tactical map,
   accent đỏ báo động). Cinematic dùng style video dài: editorial illustration / cinematic
   footage, một accent màu mỗi shot, không HUD.
3. **Overlay** khác: news burn badge + headline; cinematic chỉ karaoke.
4. **Shot grammar** khác: news 3 beat dồn dập; cinematic macro-scene 5–8s, một chuyển
   động camera liên tục mỗi shot (30-40-30), ưu tiên mạch liền chứ không cắt nhanh.

## Việc phải làm

1. `prompts.py`: thêm `"cinematic"` vào `MODES`, tên VI/EN, system prompt kể chuyện
   (VI + EN), giữ nguyên schema `{title, hook, beats[], closing, hashtags, description,
   source_note}` và `narration_texts`.
2. `short_config.yaml`: block `modes.cinematic` (target/words/voice/fast_mode) +
   `render_presets.cinematic: "clean_karaoke"`.
3. `skill_bundle_short.py`:
   - `STYLE_CINEMATIC_916` (adapt `STYLE_LOCK_V2` sang 1080x1920, bỏ yêu cầu 16:9,
     bỏ stick-figure nếu `style=live_action`).
   - `build_detailed_shot_prompt(..., style="news")` — tham số mới, **mặc định giữ
     hành vi cũ** để 4 mode kia không đổi output.
   - `get_short_skill_summary(style="news")`.
4. `service_shorts.py`:
   - `get_job` truyền `mode`/style vào prompt + `template`.
   - `_image_prompt_for(..., mode)` — cinematic dùng prompt editorial/cinematic thay vì
     "news editorial".
   - `assemble_job` chọn preset theo mode (`cinematic` → `clean_karaoke`).
5. `overlay_renderer.py`: preset `clean_karaoke` — chỉ vẽ karaoke theo word, không badge,
   không headline, không source pill. Nếu preset lạ thì giữ hành vi cũ.
6. `service_proposals.ALLOWED_MODES` **không đổi** (đề xuất tự động vẫn chỉ news/decode/
   trailer).

## Nghiệm thu

- `pytest tests/ -q` ≥ 406 passed, 0 failed.
- Test mới:
  - `create(mode="cinematic")` → status `scripted`, scenes = hook+beats+closing.
  - `publish_job` → `queued`; `get_job().scenes[i].visual_prompt` chứa style cinematic và
    **không** chứa badge/headline news.
  - `get_job().template` phản ánh style cinematic.
  - `assemble_job` chọn preset `clean_karaoke` cho mode cinematic (assert qua mock/spy).
  - 4 mode cũ: `visual_prompt` vẫn chứa style news (chống regression).
- `ruff check` sạch.
- Không đổi shape response của `get_job` (thêm field phải optional).
