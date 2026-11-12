# Phụ lục G — Short Studio (kênh dọc 9:16, 2 mode)

> **Trạng thái:** appendix phạm vi hẹp, theo tiền lệ Phụ lục F — **KHÔNG sửa plan lõi
> v3.2** (`support-video-edit/plans/de_xuat_he_thong_video_v3_1_hop_nhat.md`). Mọi điểm
> đụng nội dung đóng băng (geometry, QC, assets.kind, Item/Shot) được giữ nguyên; Short
> Studio là domain riêng song song. Quyết định sau ngày 2026-11-10 phải sửa vào file này,
> không quyết trong chat.

## G.1 Mục tiêu

Tạo video Short dọc **1080×1920 (9:16)** có giọng đọc AI tiếng Việt + phụ đề karaoke
cháy chữ, phục vụ 2 mảng nội dung:

| Mode | Mã | Thời lượng | Ngân sách từ | Công thức kịch bản |
|---|---|---|---|---|
| Tin nóng | `news` | 25–40s | 80–110 từ | Hook giật gân 0–3s → 3 beat diễn biến → câu hỏi mở kích bình luận |
| Giải mã tiền bạc | `decode` | 40–60s | 110–160 từ | Nghịch lý mở đầu → cơ chế ngầm + ví dụ đời thường → chốt nối vòng lặp |

Chính sách mảng Tin nóng (bắt buộc): **0% ảnh lấy từ báo** — mọi khung hình tự tạo
(image-gen hoặc bank template); bắt buộc có `source_note` (tên nguồn tin) ghi trên video;
prompt tự động né chủ đề nhạy cảm (chính trị, y tế, tai nạn máu me).

## G.2 Domain mới (song song Item/Shot — không đụng Phụ lục A)

### Bảng (store.py `_SCHEMA`)

```
shorts:        id, mode(news|decode), title, status, script_json (bản kịch bản
               đầy đủ title/hook/beats/closing_question/hashtags/description/source_note),
               source_text, source_note, target_seconds, audio_asset_id,
               words_asset_id, srt_asset_id, ass_asset_id, video_asset_id,
               duration_s, publish_md_asset_id, error_detail, created_at, updated_at
short_scenes:  short_id, seq_num, text, start_sec, end_sec, image_asset_id,
               image_prompt, UNIQUE(short_id, seq_num)
```

Artifacts lưu bảng `assets` hiện có với `item_id = NULL` (cột đã nullable),
`storage_key = "shorts/{short_id}/<tên>"` — **không** thêm cột mới vào assets.
File nằm ở `data_dir()/objects/shorts/{short_id}/`.

### Trạng thái (`ShortStatus` — house style như `ShotStatus`)

```
draft ──► scripted ──► producing ──► ready
  │           │  ▲        │  │
  │           │  └────────┘  └──► scripted (produce lại)
  │           └──► failed ──► scripted|producing
  └──► cancelled (terminal)          ready ──► producing (produce lại)
```

Label VI (`SHORT_LABELS_VI`, map 1-1 với UI theo DESIGN.md §3):
`draft→Nháp · scripted→Đã có kịch bản · producing→Đang tổng hợp · ready→Đã có video ·
failed→Hỏng · cancelled→Đã hủy`. `producing` → màu `--run`, `ready` → `--ok`,
`failed` → `--held`.

Luật: mọi chuyển trạng thái qua `assert_short_transition` (không UPDATE thô).
Không có trạng thái `done`; `ready` mới là terminal nghiệp vụ (vẫn cho produce lại).

## G.3 Geometry + QC riêng (không đụng Phụ lục B)

`config/render_spec_short.yaml` (file MỚI, không sửa `render_spec.yaml`):

```yaml
version: 1
video: {width: 1080, height: 1920, fps: 30, codec: h264, pix_fmt: yuv420p, crf: 20, container: mp4}
audio: {rate: 48000, channels: 2, codec: aac, bitrate: 192k}
subtitle: {safe_margin_ratio: 0.15, font_size: 64, max_line_words: 6, highlight_color: "&H0000A5FF&"}
pacing: {news_min_scene_sec: 1.5, news_max_scene_sec: 8.0, decode_min_scene_sec: 3.0, decode_max_scene_sec: 12.0}
qc: {duration_tolerance_sec: 0.25}
```

QC short (chạy trong `pipeline_short/assembler.py`, fail-closed): probe output phải
đúng 1080×1920@30 h264 yuv420p, duration lệch audio ≤ ±0.25s. Sai → `failed` với
`error_detail` tiếng người. **Không** dùng OCR/khung QC 1280×720 của shot.

## G.4 Adapter protocols (providers.py — mở rộng, fail-closed như cũ)

```
LlmScriptAdapter:  chat_json(system, user, schema) -> dict   # JSON mode, schema-validate ở caller
ImageGenAdapter:   generate_image(prompt, out_path, size="1080x1920") -> dict{path, model, size_used}
```

Registry thêm `llm`, `image_gen` + `require_llm()/require_image_gen()`. Không cấu hình
→ `AdapterNotConfigured` → API trả 503 tiếng người "chưa cấu hình adapter", **không**
rơi vào placeholder.

Impl xkiro (`backend/app/adapters/`):
- `llm_xkiro.py`: httpx POST `{base}/chat/completions` (OpenAI-compatible,
  `response_format json_object`, fallback model chain, JSON repair — port tinh gọn từ
  `support-video-edit/pipeline/llm_client.py`).
- `image_xkiro.py`: POST `{base}/images/generations` → 202 → poll
  `GET /images/generations/{id}` → tải file; nếu model từ chối size dọc → thử lại
  `1024x1024` và trả `size_used` để assembler center-crop (port từ
  `support-video-edit/services/xkiro_image_provider.py`).

Env (thêm vào `.env.example`): `VIDEO_XKIRO_API_KEY`, `VIDEO_XKIRO_BASE_URL`
(mặc định `https://api.xkiro.com/v1`), `VIDEO_XKIRO_LLM_MODEL`,
`VIDEO_XKIRO_LLM_FALLBACK_MODELS`, `VIDEO_XKIRO_IMAGE_MODEL`. Unset = adapter không
cắm = fail-closed (đúng §1.4 — không silent downgrade).

## G.5 Pipeline `backend/app/pipeline_short/` (tiền lệ `pipeline_en/`)

| Module | Trách nhiệm |
|---|---|
| `prompts.py` | System prompt + JSON schema cho 2 mode; validate ngân sách từ; sinh `hashtags` (tối đa 6 TikTok/6 FB/5 YT), `description`, `source_note`. Trả kèm `word_budget_used`. |
| `tts_vi.py` | edge-tts voice VI (`short_config.yaml:tts_vi.default_voice`), stream **WordBoundary** → `words.json` (offset/duration/text từng từ). `VIDEO_OFFLINE_TTS=1` → WAV tổng hợp deterministic + timeline từ phân bổ đều (pattern `_write_synthetic_speech_wav`). Ghép cue → 1 audio + timeline từ tuyệt đối. |
| `karaoke.py` | `words.json` → ASS 1080×1920 tag `{\k}` từng từ, safe-margin trên/dưới 15%, nhấn từ chứa số/%/$ bằng màu highlight, tách dòng ≤6 từ. |
| `assembler.py` | Mỗi scene: ảnh → zoompan dọc (1216×2160→1080×1920@30, seed theo short_id để tái lập) → concat → mix voice + BGM bed (tái dùng logic `_mix_bgm_bed` của `pipeline_en`) → burn ASS → mux + QC probe. Tôn trọng `VIDEO_FAKE_FFMPEG` (container fake deterministic như `AssemblerService`) để test không cần ffmpeg. |
| `bank.py` | Bank ảnh template `data_dir()/shorts_bank/` (PNG dọc). Fast mode `news`: scene thường dùng bank theo seed, chỉ 1–2 ảnh hero gen mới (nếu có adapter). Bank trống + không adapter → lỗi tiếng người liệt kê cả hai. |

## G.6 API (`/api/v1/shorts`, scope theo §7.1)

| Endpoint | Scope | Ghi chú |
|---|---|---|
| `POST /api/v1/shorts` | ingest | `{mode, source_text, source_note?, target_seconds?, title?}` → LLM sinh kịch bản (sync) → short `scripted`. Idempotency-Key. |
| `GET /api/v1/shorts`, `GET /api/v1/shorts/{id}` | read | Kèm scenes + durations. |
| `PATCH /api/v1/shorts/{id}/script` | ingest | Sửa title/beats/closing_question (duyệt người) — chỉ khi `scripted`. |
| `POST /api/v1/shorts/{id}/produce` | ingest | TTS → scenes → ảnh → assemble (sync). Lỗi → `failed` + `error_detail` tiếng người. Idempotency-Key. |
| `DELETE /api/v1/shorts/{id}` | admin | Soft-cancel. |
| `GET /shorts/{id}/audio|video|script|subtitles.srt|subtitles.ass|publish.md` | read | FileResponse pattern hiện có. |

`publish.md` = gói đăng bài: title + description + hashtag theo nền + nguồn tin.

## G.7 Frontend (trang thứ 3 của `frontend/index.html`)

Route hash `#/shorts` (danh sách) + `#/shorts/{id}` (chi tiết). Layout 2 cột:
trái = điều khiển (mode → nhập tin → Tạo kịch bản → duyệt beat → Tạo Voice & Ảnh →
video); phải = khung điện thoại 9:16 (beat kèm giây tabular, audio player, ảnh, video).
Đúng DESIGN.md: token hiện có, tabular-nums, nhãn VI từ `SHORT_LABELS_VI`, empty-state
có hướng dẫn hành động, lỗi tiếng người, không emoji thay icon.

## G.8 Tests (luật mới phải có test — TEST_PLAN §3)

`tests/test_shorts_flow.py`: schema kịch bản + ngân sách từ (FakeLlm); WordBoundary
offline → timeline từ; ASS chứa `\k` + geometry 1080×1920; assembler fake-ffmpeg ra
container đúng geometry; transitions (draft→ready, illegal→409); scopes (read POST →
403); publish.md đủ title/hashtag/nguồn. Conftest thêm `FakeLlm`/`FakeImageGen` +
fixture `short_adapters` (save/restore registry như `adapters`).

## G.11 Sửa đổi 2026-11-11 — ảnh gốc từ bài báo + push từng tin (yêu cầu user)

Ba quyết định mới (ghi vào đây theo luật, không quyết trong chat):

1. **Người dùng CHỌN tin — không tự đăng.** Sync RSS chỉ *thu thập* vào hộp tin
   (`news_items`); **không tự đẩy Telegram hàng loạt** (`auto_push: false` mặc
   định trong `news_config.yaml`). Telegram chỉ gửi tin mà user bấm chọn
   (`POST /api/v1/news/{id}/push` hoặc nút "Gửi Telegram" trong UI). Chỉ tin user
   chọn mới được làm Short và đưa đi.
2. **Bỏ style crayon/vector của bên video dài.** Prompt image-gen fallback chuyển
   sang phong cách tin tức trung tính (editorial, không khóa crayon). Bank ảnh
   không còn bắt buộc crayon.
3. **Ưu tiên Ảnh gốc của bài báo.** Khi short có `source_link` (link bài báo, kể
   cả link Google News), pipeline **tự lấy ảnh gốc trong bài** làm minh họa:

   - `pipeline_short/article_images.py`: `resolve_article_url()` nhiều tầng —
     (1) decode base64 link Google News format cũ; (2) batchexecute nội bộ
     (Google đã 400-endpoint này từ 2026-11 — giữ làm best-effort, bỏ qua im lặng);
     (3) follow-redirect; (4) tìm bài theo tiêu đề qua DuckDuckGo HTML
     (bị anti-bot 202 → bỏ qua); fallback cuối = dùng nguyên link (collect được
     0 ảnh → rơi xuống bank/AI-gen, không bao giờ chặn produce).
   - **Nguồn link tin cậy nhất: RSS trực tiếp của báo** (`news_config.yaml:feeds`
     — VnExpress / Tuổi Trẻ / Dân Trí, thêm 2026-11-11): link bài là LINK THẬT,
     ảnh gốc lấy được không cần decode. Tin từ Google News query vẫn về nhưng
     có thể không resolve được ảnh.
   - Thứ tự nguồn ảnh mỗi scene: **ảnh gốc bài báo (theo thứ tự og-first)** →
     bank → AI-gen → thẻ màu (honest). Thiếu adapter + bank trống + không có ảnh
     bài → lỗi tiếng người (giữ nguyên).
   - Ảnh ngang/square được ghép **blur-pad dọc**: nền = ảnh phóng to + blur lấp
     đầy 1080×1920, ảnh gốc nét contain giữa (không cắt mất nội dung tin);
     ảnh dọc giữ scale+crop. Implement ở `assembler._prepare_scene_image()`.
   - Schema: `shorts.source_link TEXT` (migration cột cho DB cũ),
     `ShortCreate.source_link`, UI ô "Link bài báo"; "Dùng tin này" tự điền link.
   - Bản quyền: ảnh gốc mang tính minh họa tin tức kèm `source_note` ghi nguồn
     trên video — trách nhiệm duyệt thuộc người làm kênh (bước duyệt kịch bản
     trước render vẫn là cổng bắt buộc).

## G.9 Giới hạn đã biết (v1)

- Produce là sync (tiền lệ `from-script` TTS của repo); nếu gen ảnh thật làm quá
  120s thì chuyển sang background thread + poll — quyết định ghi vào đây trước khi làm.
- Mode ✂️ "Tách từ video dài" **chưa** trong appendix này — làm vòng sau.
- Chưa có TTS nhiều giọng/xKiro TTS; chỉ edge-tts voice VI + fallback voice.

## G.10 Hộp tin nóng: sync RSS + push Telegram (bổ sung 2026-11-10)

**Nguồn:** Google News RSS (`news.google.com/rss/search?q=<query>&hl=vi&gl=VN&ceid=VN:vi`)
— không cần API key. Danh sách query + từ khóa loại nằm trong `config/news_config.yaml`.

**Cơ chế:**
- Bảng `news_items` (store): `id, link_hash UNIQUE, source, title, link, summary,
  published_at, fetched_at, status(new|used|dismissed), pushed_at`. Dedupe theo
  sha1(link) — không bao giờ push/lưu trùng.
- Scheduler daemon thread (khởi động cùng FastAPI khi `VIDEO_NEWS_SYNC_ENABLED=1`),
  chu kỳ `interval_min` (mặc định 60 phút — yêu cầu user 2026-11-10; chỉnh được 15).
  Mỗi kỳ: fetch toàn bộ query → dedupe → INSERT → push tin mới qua Telegram bot API
  (chỉ khi có `VIDEO_TELEGRAM_BOT_TOKEN` + `VIDEO_TELEGRAM_CHAT_ID`; thiếu → vẫn lưu,
  bỏ push, không lỗi).
  **Sửa 2026-11-11 (G.11):** KHÔNG tự push hàng loạt nữa — `auto_push: false`
  mặc định; Telegram chỉ gửi tin user chọn qua `POST /api/v1/news/{id}/push`
  (nút "Gửi Telegram" trong UI).
- Trigger thủ công: `POST /api/v1/news/sync` (scope ingest) — chạy 1 kỳ ngay lập tức.
- Đọc: `GET /api/v1/news?status=` (read); gỡ tin: `POST /api/v1/news/{id}/dismiss`
  (ingest).
- UI: mục "Tin nóng vừa về" trong trang Short Studio — bấm "Dùng tin này" tự điền
  `source_text` + `source_note` vào form tạo kịch bản.

**Sự thật hoá:** RSS poll không phải real-time tuyệt đối; là gần-realtime (RSS Google
News cập nhật mỗi vài phút). Người dùng vẫn phải duyệt kịch bản trước render nên chu kỳ
60 phút không làm mất tin. Tin lỗi fetch một query vẫn tiếp tục các query còn lại
(không fail-cả-kỳ), lỗi ghi vào event `news.sync_failed`.

## G.12 Mảng quốc tế EN + stock Pexels (bổ sung 2026-11-12)

Mục tiêu: short tiếng Anh thuần quốc tế kiểu TikTok news (DailyMail-style) —
chọn tin tay từ hộp tin, nguồn visual đầy đủ (ảnh bài → stock ảnh → stock video
b-roll → bank → AI-gen → thẻ màu).

### Ngôn ngữ (lang='en')
- `ShortCreate.lang: vi|en` (mặc định vi); lưu cột `shorts.lang`.
- Prompt EN: `prompts._SYSTEM_NEWS_EN/_SYSTEM_DECODE_EN` — cùng công thức hook →
  beats → closing, văn nói tự nhiên, cấm bịa số; ngân sách từ EN riêng
  (`modes.*.min_words_en/max_words_en` — EN nói chậm ~2.45 từ/s).
- Voice EN: block `tts_en` trong `short_config.yaml`
  (`en-US-AndrewMultilingualNeural` fallback Christopher); edge-tts free.
- Image prompt scene + publish pack (Duration/Source) sinh bản EN khi lang=en.

### Nguồn tin EN quốc tế (chọn tay, không auto-cron)
- `news_config.yaml` thêm `feeds_en` (BBC Business, CNBC, Al Jazeera, Guardian)
  + `queries_en` (Google News RSS hl=en gl=US) + `exclude_keywords_en`.
- `news_items.lang` ('vi'|'en') — dedupe sha1(link) dùng chung cả 2 mảng;
  `GET /api/v1/news?lang=` lọc theo ngôn ngữ.
- UI: filter Tất cả/VI/EN + link "Xem nguồn ↗" mở bài gốc tab mới; chọn tin EN
  tự chuyển ngôn ngữ short sang en.

### Stock media (`pipeline_short/stock_media.py`)
- **Pixabay primary** (env `VIDEO_PIXABAY_API_KEY` — key cấp ngay sau đăng ký;
  Pexels đã tạm dừng cấp key mới từ 2026-11, connector Pexels giữ làm fallback
  cho tài khoản có key cũ qua env `VIDEO_PEXELS_API_KEY`). Thiếu cả hai → skip
  connector, pipeline vẫn chạy bằng nguồn khác (honest, không chết).
- Ảnh dọc (`orientation=portrait`) + video mp4 dọc ≤1080 ngang; download cache
  `data/shorts_bank/pexels/{photos,videos}/` theo sha1(url); attribution
  (photographer) lưu cột `short_scenes.attribution`.
- Scene ghi `asset_kind` ('image'|'video_clip').

### Assembler video b-roll
- Scene có `clip_path` → ffmpeg loop-fill đúng duration scene, scale/crop fill
  9:16, chuẩn 30fps, MUTE audio clip (voice là chính); không zoompan.
- Concat vẫn stream-copy sau chuẩn hóa codec h264 yuv420p.

### Env mới
- `VIDEO_PEXELS_API_KEY` — tùy chọn; không có vẫn produce được.

## G.13 Job queue cho agent video ngoài (bổ sung 2026-11-13)

Bối cảnh: video auto-assemble bằng ảnh tĩnh không đạt chất lượng (user đánh
giá "xấu"). Chuyển kiến trúc theo pattern TODO video dài: hệ thống lo
AUDIO + JOB, agent video ngoài (user kiếm) gen visual per scene.

### Luồng trạng thái (thêm vào ShortStatus)
`scripted --publish-job--> queued --clip đầu--> rendering --assemble--> ready`

### Service (`service_shorts.py`)
- `_ensure_voice_timeline()` — tách bước 1-2+4 của produce (TTS + scene windows
  + karaoke ASS/SRT), dùng chung produce và publish_job.
- `publish_job(id)` — CHỈ lang='en' + scripted; build timeline → `queued`,
  event `short.job_ready`.
- `get_job(id)` — package: audio/subs URLs, scenes (timing + visual_prompt +
  stock_search_keywords), template `skill_bundle_short`, progress coverage.
- `submit_clip(id, seq_num, file, covered_scene_indexes)` — sandbox
  VIDEO_DATA_DIR, probe, sha256, upsert `short_clips`, đánh dấu scene
  `agent_checked` + duration pro-rata (pattern `submit_video` todo); 1 clip có
  thể gộp nhiều scene.
- `assemble_job(id)` — normalize clip (trim theo scene window, pro-rata seek
  cho clip gộp) → concat → burn karaoke + voice + BGM → QC fail-closed →
  `ready`. Thiếu coverage → `ShortfallError` (HTTP 409 `video_duration_shortfall`).
- Legacy `produce()` (ảnh tĩnh) giữ nguyên làm fallback.

### API
`GET /api/v1/short-jobs?status=` · `POST /api/v1/shorts/{id}/publish-job` ·
`GET /api/v1/shorts/{id}/job` (+`/job/audio`) · `POST /api/v1/shorts/{id}/clips`
(multipart/JSON) · `GET|DELETE /api/v1/shorts/{id}/clips/{seq}` ·
`POST /api/v1/shorts/{id}/assemble` · `GET /api/v1/short-agent-guide`.
Webhook mới: `short.job_ready`, `short.clip_submitted`, `short.ready`.

### Template agent
`pipeline_short/skill_bundle_short.py` — STYLE_NEWS_916 "Breaking News Alert
9:16" (headline overlay đè trên, banner đỏ, bottom 25% để trống cho karaoke,
punch-zoom 30-40-30, footage thật + map/satellite, cấm watermark/letterbox).
Guide: `AGENT_SHORT_GUIDE.md`.

### Store
`short_clips` (UNIQUE short_id+seq_num, sha256, duration, covered_scene_indexes
JSON) + `short_scenes.agent_checked/generated_duration_sec`.
