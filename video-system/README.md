# video-system — Item-centric video production core (plan v3.2)

> **Vị trí canonical (từ 2026-11-11):** thư mục con `video-system/` trong repo
> `yt-support` (`https://github.com/nguyenloc2702/yt-support.git`). Bản gốc
> `C:\app\work\video-system` chỉ còn là runtime/venv cũ cho tới khi retire —
> **mọi thay đổi code/core v3.2 làm ở đây từ nay.**

**1 Item = 1 video.** Repo này là kiến trúc + nghiệp vụ lõi theo plan v3.2
đóng băng (`plans/de_xuat_he_thong_video_v3_1_hop_nhat.md` ở root repo này —
kể từ khi gộp, plan đã cùng repo với code). Không chứa data thật; seed chỉ là
test data gán nhãn rõ.

## Đã triển khai (xuyên tầng)

- **Domain:** bảng transition shot (Phụ lục A — không `done`, không nhảy QC,
  held-chặn-prefix) + item (§2.3) + enum held_reason có label VI (Phụ lục C).
- **Store:** repository abstraction trên SQLite WAL — đổi Postgres = đổi Store,
  không đổi service.
- **Import gate chuẩn Shot (§3.2):** canonical SHA256 (§3.2.5), probe audio
  bắt buộc (không tin payload), ±1.0s/±0.25s, on_screen allowlist quote/diagram,
  422 report kiểu Phụ lục D, idempotent re-import, version mới → `superseded`.
- **QC tự động (§4.B.5):** chỉ hình học + chữ, ngưỡng đọc từ `render_spec.yaml`;
  OCR chưa cắm → held `QC_TOOL_UNAVAILABLE` (fail-closed, không âm thầm pass);
  màu nhân vật/biểu cảm = QC NGƯỜI ở duyệt batch.
- **Batches + cumulative append-only (§4.C):** prefix liên tục, held chặn,
  manifest hash (shot + audio + style version) rebuild sau mỗi batch.
- **Assets:** mọi file qua bảng `assets` với storage_key mẫu; bảng khác giữ
  `asset_id`, không URL.
- **API:** FastAPI, Idempotency-Key trên POST, scope read/ingest/admin
  (ingest không approve được — §7.1), deny-by-default khi chưa set key,
  webhook HMAC-SHA256 (Phụ lục E).
- **UI:** `frontend/index.html` theo `config/DESIGN.md` — IBM Plex, tabular
  nums, Held-first với lý do tiếng người + nút retry tại chỗ, mọi số từ API.
- **Skill rà UI:** `.agents/skills/ui-review/SKILL.md` (tổng hợp từ
  frontend-design-deslop + hallmark + awesome-design-md).

## Chưa triển khai — adapters cắm sau (protocols sẵn trong providers.py)

- Generation thật (Veo/KB), YouTube download, Whisper, OCR thật
  (TTS + ffmpeg concat thật đã chạy: `pipeline_en` + AssemblerService).
- Postgres/Redis — đổi Store khi ≥2 worker vật lý.

## Short Studio (Phụ lục G — `docs/SHORT_STUDIO_PLAN.md`)

Tin nóng & Giải mã tiền bạc → video dọc **1080×1920** voice VI + phụ đề karaoke
cháy chữ + gói đăng bài (title/hashtag/nguồn). Domain riêng (`shorts`/`short_scenes`,
status `ShortStatus` có nhãn VI) — không đụng luật Item/Shot của plan v3.2.

- `POST /api/v1/shorts` (mode `news`|`decode` + tin dán vào) → LLM viết kịch bản
  theo công thức (hook → beats → câu hỏi mở / chốt loop), validate ngân sách từ;
  adapter xkiro **fail-closed** khi thiếu `VIDEO_XKIRO_API_KEY` (503 tiếng người).
- `PATCH /shorts/{id}/script` duyệt/sửa từng câu → `POST /shorts/{id}/produce`:
  edge-tts VI (WordBoundary → timeline từ) → karaoke ASS `{\k}` 1080×1920 safe-margin
  → zoompan dọc (bank fast-mode cho Tin nóng) → mix BGM → burn + QC fail-closed
  (±0.25s, sai geometry = `failed` + lý do tiếng người).
- Hộp tin nóng (G.10): Google News RSS + push Telegram, scheduler daemon 60 phút
  (bật `VIDEO_NEWS_SYNC_ENABLED=1`; nguồn/chu kỳ trong `config/news_config.yaml`),
  trigger tay `POST /api/v1/news/sync`; tin hiện thẳng trong UI, bấm "Dùng tin này"
  để làm Short.
- UI: trang thứ 3 `#/shorts` (nút "Short Studio" trên topbar) — 2 cột, khung điện
  thoại 9:16, timeline từng câu, gói đăng bài `.md`, tải MP4/.ASS/.SRT.

## Chạy

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
VIDEO_DATA_DIR=data .venv/bin/uvicorn backend.app.main:app --port 8000
# UI: http://127.0.0.1:8000/   API docs: /docs
VIDEO_DATA_DIR=data .venv/bin/python -m backend.app.seed   # data demo (gán nhãn SEED)
.venv/bin/pytest -q                                        # 26 test
```

## Cấu hình (một nguồn sự thật)

- `config/render_spec.yaml` — geometry/codec/QC numbers; domain + qc ĐỌC file này.
- `config/render_spec_short.yaml` + `config/short_config.yaml` — số liệu Short Studio (G).
- `config/news_config.yaml` — nguồn tin nóng + chu kỳ Telegram (G.10).
- `config/audio_config.yaml` — loudness, DUY NHẤT nơi giữ.
- `config/style_lock_v3.yaml` — **STUB**: paste khối v3 verbatim trước khi gen
  production; không bao giờ gõ lại từ trí nhớ.
- `config/DESIGN.md` — UI lock.
- `.env.example` — VIDEO_DATA_DIR, VIDEO_API_KEYS (key:scope), allowlist hosts,
  xKiro (LLM/ảnh), Telegram + news sync (G.10).

## Production notes

- Set `VIDEO_API_KEYS` trước khi expose; unset = chỉ localhost.
- Backup SQLite **kèm -wal** cùng lúc.
- Không chạy 2 writer trên cùng SQLite file từ 2 máy — đó là lý do có bước Postgres.
- Xóa Item là soft delete + giữ audit trail; purge là job nền riêng sau 7 ngày.

## Docs

- `docs/SHORT_STUDIO_PLAN.md` — Phụ lục G: domain/API/QC/tin nóng (bổ sung sau đóng băng, theo tiền lệ Phụ lục F).
- `docs/TEST_PLAN.md` — ma trận plan→test, ngưỡng chặn merge.
- `docs/E2E_PLAN.md` — E2E-1..9 (E2E-7 migration BlackRock, E2E-8 generation thật, E2E-9 short flow).
