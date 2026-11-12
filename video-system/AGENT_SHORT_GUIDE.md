# Agent Guide — Short EN Video Job (Breaking News 9:16)

Bạn là **video generation agent** cho mảng short tin tức tiếng Anh. Hệ thống
lo **audio + timing + karaoke subtitle**; bạn lo **visual per scene** theo
template "Breaking News Alert 9:16".

## Quy trình 5 bước

### Step 1 — Poll job mới
```
GET /api/v1/short-jobs?status=queued
Authorization: Bearer <key:ingest-or-read>
```
Trả `{"jobs": [{short_id, title, status, duration_s, word_count, ...}]}`.

### Step 2 — Tải job package
```
GET /api/v1/shorts/{short_id}/job
```
Payload chứa:
- `audio_url` — voice EN (WAV, edge-tts, đã mix sẵn)
- `subtitles_ass_url` / `subtitles_srt_url` — karaoke timing (hệ thống burn vào final)
- `template` — STYLE LOCK + shot types + golden rules (BẮT BUỘC tuân thủ)
- `scenes[]` — mỗi scene: `{seq, narration, audio_start_sec, audio_end_sec,
  duration_sec, visual_prompt, stock_search_keywords}`

### Step 3 — Gen video per scene
- 1 clip mỗi scene, MP4 **h264 1080x1920 @30fps yuv420p**
- Duration clip = `audio_end_sec - audio_start_sec` (±0.25s — LUẬT)
- Đọc `template.style_lock` trong job payload trước khi gen
- Gợi ý visual thật: dùng `stock_search_keywords` của scene (Pixabay/Pexels)

### Step 4 — Submit clip
```
POST /api/v1/shorts/{short_id}/clips
Authorization: Bearer <key:ingest>
Content-Type: multipart/form-data

file=@scene_001.mp4
seq_num=1
covered_scene_indexes=[1]     # hoặc "1,2" nếu 1 clip gộp 2 scene
note=veo-gen-02
```
Hoặc JSON: `{"file_path": "<đường dẫn trong VIDEO_DATA_DIR>", "seq_num": 1}` /
`{"file_url": "https://..."}`. Submit lại cùng `seq_num` → **overwrite**.
Không cần audio trong clip — hệ thống mix voice + BGM.

### Step 5 — Assemble
Sau khi coverage đủ (response `progress.is_duration_sufficient == true`):
```
POST /api/v1/shorts/{short_id}/assemble
```
Hệ thống: normalize clip → trim đúng scene window → concat → burn karaoke
subtitle → mix voice + BGM → QC (1080x1920@30, duration ±0.25s) → MP4 final.
Thiếu duration → **409** `video_duration_shortfall` kèm `progress` — submit
thêm clip rồi thử lại. Final: `GET /api/v1/shorts/{short_id}/video`.

## Trạng thái job
`scripted → queued (publish-job) → rendering (clip đầu về) → ready (assemble xong)`

## Lỗi thường gặp
| Code | Nghĩa |
|---|---|
| 422 | Sai field / clip duration invalid / seq_num ngoài range |
| 409 | Duration shortfall (chưa đủ coverage) hoặc transition không hợp lệ |
| 404 | short_id / clip không tồn tại |
| 502 | ffmpeg lỗi — submit lại clip chuẩn hơn |

## Quy tắc bất di bất dịch
1. Duration clip = scene window ±0.25s
2. Fill 9:16 toàn khung — không letterbox
3. Bottom 25% để trống (karaoke burn ở đó)
4. Headline overlay 2 dòng uppercase — số liệu/key nouns
5. Không watermark/logo/AI-face lộ rõ
6. Clip có motion (30-40-30 punch-zoom), không đứng yên
