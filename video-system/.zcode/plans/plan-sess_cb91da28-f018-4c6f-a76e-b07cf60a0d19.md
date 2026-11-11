# Kế hoạch: UI audit + ⚡ Short Video Studio trên repo `video-system`

## Context
Kênh Short cần 2 mảng: 🔥 Tin nóng (kéo view) + 💰 Giải mã tiền bạc (giữ chân). Repo `video-system` là lõi Item-centric (FastAPI + SQLite WAL + frontend `index.html` 6.578 dòng vanilla) có kỷ luật khóa: `config/DESIGN.md` là luật, mọi mở rộng domain sau đóng băng phải ghi thành appendix kế hoạch (tiền lệ Phụ lục F) rồi mới code/test. Thứ tự đã chốt: **UI trước → Short Studio sau**; phạm vi **2 mode** (✂️ Tách từ video dài → vòng sau).

## PHASE A — Audit + sửa UI hiện tại (frontend/index.html)
`docs/UI_REVIEW.md` audit file ~200 dòng cũ, còn file hiện tại 6.578 dòng đã dựng lại 2026-11-06 → audit cũ lỗi thời (bug held_count đã fix sẵn tại `service_items.py:72-75`).
1. Chạy audit **mới** theo 5 cửa `.agents/skills/ui-review/SKILL.md` trên file hiện tại (token DESIGN.md, số tabular + mọi số từ API, held first-class, a11y floor, bare-structure).
2. Fix các điểm trượt cửa blocker dự kiến: pill hiển thị enum thô (thêm map nhãn VI theo §3 cho mọi trạng thái xuất hiện trên UI), hàng thẻ click được nhưng thiếu `tabindex`/Enter-Space, hành động chưa bắt lỗi (thông báo tiếng người + nút hành động tại chỗ, cấm lỗi thô HTTP), poller race (cờ `inFlight` + không poll khi `document.hidden`) nếu còn.
3. Cập nhật `docs/UI_REVIEW.md` với audit mới + chụp bằng chứng vào `docs/screenshots/`.

## PHASE B — ⚡ Short Video Studio (2 mode)

### B0. Appendix kế hoạch (bắt buộc theo luật repo)
Tạo `docs/SHORT_STUDIO_PLAN.md` — appendix phạm vi hẹp, đánh dấu "KHÔNG sửa plan lõi v3.2" (theo tiền lệ Phụ lục F), chốt: domain shorts (bảng, status + transitions + nhãn VI), geometry 9:16 riêng, hàng QC, scopes, adapter protocols, công thức kịch bản, chính sách tin nóng (ghi nguồn tin, chủ đề né, 0% ảnh báo).

### B1. Domain + Store
- `backend/app/domain.py`: `ShortStatus(str,Enum)` + `SHORT_TRANSITIONS` + `assert_short_transition` + `SHORT_LABELS_VI` (đúng house style của ItemStatus/HELD_LABELS_VI).
- `backend/app/store.py`: 2 bảng mới `_SCHEMA`: `shorts` (id, mode, title, status, source_text, target_seconds, duration_s, error_detail, timestamps), `short_scenes` (short_id, seq, text, est_seconds, image_asset_id, words_asset_id); artifacts lưu bảng `assets` hiện có với `storage_key = shorts/{short_id}/...` (mở rộng cột nullable `short_id` cho assets).

### B2. Adapters (Protocol + Registry + fail-closed, đúng pattern `providers.py`)
- `providers.py` thêm: `LlmScriptAdapter` (`chat_json(system, user, schema) -> dict`), `ImageGenAdapter` (`generate_image(prompt, out_path, size) -> path`). Không cấu hình → `registry.require_llm()` raise → UI báo honestly "chưa cấu hình adapter".
- Impl xkiro: `backend/app/adapters/llm_xkiro.py` (port tinh gọn logic `chat_json` + fallback model từ `support-video-edit/pipeline/llm_client.py`), `backend/app/adapters/image_xkiro.py` (port `generate_xkiro_image`, size dọc + fallback square). Wire trong `main.py` khi có env `VIDEO_XKIRO_API_KEY` (đưa vào `.env.example`).

### B3. `backend/app/pipeline_short/` (package mới, tiền lệ `pipeline_en/`)
- `prompts.py`: 2 công thức — **Tin nóng** (25–40s, 80–110 từ: hook giật gân 0–3s → 3 beats → câu hỏi mở; kèm `source_note`, `hashtags{tiktok,facebook,youtube}`, `description`) và **Giải mã** (40–60s, 110–160 từ: nghịch lý → cơ chế + ví dụ đời thường → chốt loop); chấm hook tái dùng ý tưởng `hook_scoring`.
- `tts_vi.py`: edge-tts voice VI (`vi-VN-HoaiMyNeural`) stream **WordBoundary** → mp3 + `words.json` (word timestamps cho karaoke); offline fallback deterministic giống `pipeline_en/audio_generator_en.py` (`VIDEO_OFFLINE_TTS`).
- `karaoke.py`: word-level ASS `\k` 1080×1920, font lớn, safe-margin trên/dưới ~15% cho UI nền tảng, nhấn từ số/$/%.
- `assembler.py`: zoompan dọc (ảnh 1216×2160 → 1080×1920@30), concat, BGM bed + duck (tái dùng pattern `_mix_bgm_bed`), burn ASS, mux; QC riêng short (geometry + duration ±0.25s vs audio) — KHÔNG đụng QC 1280×720.
- `bank.py`: image bank template `data/shorts_bank/` cho Fast mode (chỉ gen 1–2 ảnh hero/video, phần còn lại dùng bank) — mảng tin nóng cần tốc độ.

### B4. Service + API (`backend/app/service_shorts.py` + endpoints trong `main.py`)
- `POST /api/v1/shorts` (mode + source_text + target_seconds → LLM sinh kịch bản, sync — đúng tiền lệ `create_from_script`), `GET /shorts` + `/shorts/{id}`, `PATCH /shorts/{id}/script` (duyệt/sửa beat), `POST /shorts/{id}/produce` (TTS → ảnh → assemble; sync với Fast-mode bank; events + webhook fire theo `_event_with_webhooks`), `GET /shorts/{id}/audio|video|script|subtitles.ass|publish.md` (FileResponse pattern sẵn có). Auth scope: POST = ingest, xóa = admin, đúng §7.1; Idempotency-Key cho POST.

### B5. Frontend (`frontend/index.html`, trang thứ 3)
- Route hash mới `#/shorts` + `#/shorts/{id}` (mở rộng `syncRouteFromHash`), page div `#pageShortStudio`.
- Layout 2 cột: **trái** = chọn mode (Tin nóng | Giải mã) → nhập tin/chủ đề + nguồn tin → Tạo kịch bản → duyệt từng beat (sửa được, kèm số giây tabular) → Tạo Voice & Ảnh → Xuất MP4 9:16 (busy state + không spinner giả) → gói đăng bài (Title + hashtag theo nền + description, nút tải .md); **phải** = khung điện thoại 9:16: beat kèm giây, audio player, lưới ảnh, video khi xong.
- Đúng DESIGN.md: token hiện có, IBM Plex, tabular-nums, map nhãn VI, empty-state có hướng dẫn hành động, lỗi tiếng người; **không dùng emoji làm icon** (ban của DESIGN.md — nhãn dạng chữ "Tin nóng"/"Giải mã tiền bạc").

### B6. Config + docs
- `config/render_spec_short.yaml` (1080×1920@30, crf, safe-margin, pacing tin nóng 1 ảnh/beat) + `config/short_config.yaml` (voice, ngân sách từ, tỷ lệ, thư mục bank) — load qua `backend/app/config.py` pattern, không sửa `render_spec.yaml` bị đóng băng.
- Cập nhật `README.md` (mục đã triển khai), `docs/TEST_PLAN.md` (thêm dòng ma trận), `docs/E2E_PLAN.md` (E2E-9: short flow).

### B7. Tests (kỷ luật TEST_PLAN: luật mới phải có test)
- `tests/conftest.py`: thêm fake `FakeLlm` (trả JSON đúng schema) + `FakeImageGen`; tái dùng `fast_offline_tts`.
- `tests/test_shorts_flow.py`: schema kịch bản + ngân sách từ 80–110; word timeline → ASS có `\k`; assembler 1080×1920 (ffmpeg lavfi thật như test_todo_en_flow); transitions; scopes (read POST → 403); publish pack đủ title/hashtag; offline TTS path.

## Verification
1. `pytest -q` toàn bộ xanh (76 test cũ không gãy) + test mới.
2. Chạy `uvicorn backend.app.main:app` + seed → audit 5 cửa lại toàn UI (cũ + trang Short Studio mới), chụp `docs/screenshots/`.
3. E2E tay với 1 tin mẫu thật: dán tin → kịch bản 80–110 từ → MP4 **1080×1920** có karaoke burn (extract frame kiểm tra) + file publish.md; đo thời gian paste→MP4 (mục tiêu chế độ bank < 5 phút).

## Rủi ro
- xkiro từ chối size dọc → fallback square + center-crop ở assembler (đã có sẵn hành vi fallback).
- edge-tts cần mạng → offline fallback đã là pattern chuẩn repo.
- Không có job queue → v1 giữ sync như tiền lệ repo (TTS from-script); nếu gen ảnh chậm quá thì chuyển produce sang background thread + poll status (đã ghi sẵn trong appendix B0 như phương án B).