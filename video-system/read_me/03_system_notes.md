# Ghi Chú Vận Hành & Log Hệ Thống (`System Notes`)

## 1. Hạ tầng Production
- **Domain:** `https://vidfac.gacdero.vn` (Nginx -> `127.0.0.1:8090`, systemd service `video-system`).
- **Thư mục chạy thật:** `/opt/video-system` (code sync từ `/opt/support-video-edit/video-system/`).
- **Database:** `/opt/video-system/data/video_system.sqlite3`.
- **Kho BGM:** `/opt/video-system/assets/music/bgm` (đồng bộ `LOUDNESS.json`).
- **Kho `read_me`:** `/opt/video-system/read_me` (có thể chỉnh sửa trực tiếp qua API `/api/v1/read-me`).

## 2. Quy tắc Audio & Loudness (`audio_config.yaml`)
- Master loudness: `-16 LUFS`, True Peak: `-1.5 dBTP`.
- BGM bed target: `-38.0 LUFS`, duck: `-20.0 dB`.

## 3. Quy tắc YouTube Data API v3
- Quota mặc định: `10.000 units/ngày` (reset 00:00 giờ Pacific `America/Los_Angeles`).
- `videos.insert` tốn `1.600 units` (~6 video/ngày dưới ngưỡng guard `9.000`).
- Luôn dùng `playlistItems.list` (1 unit), **không dùng `search.list`** (100 units).

## 4. Nhật ký thay đổi & Log gen clip
*(Agent bên ngoài có thể gọi `PATCH /api/v1/read-me/03_system_notes.md` với `mode="append"` để ghi thêm log tại đây.)*
- `2026-11-14`: Bắt buộc `kind=thumbnail` trước khi hoàn thành job `#/todos`, bật watermark `@StickSto`, tích hợp YouTube OAuth & Publish, thêm BGM Library API (`/api/v1/bgm`) và `read_me` CRUD API (`/api/v1/read-me`).
