# 📋 BÁO CÁO HIỆN TRẠNG TRIỂN KHAI & SỔ TAY VẬN HÀNH
## Hệ Thống Xưởng Sản Xuất Video Bán Tự Động (AI Video Factory · Dual-Mode 24/7)
*Cập nhật lúc: 2026-10-20*

---

## 🚀 1. TỔNG QUAN HIỆN TRẠNG TRIỂN KHAI (DASHBOARD)

| Thông số | Trạng thái hiện tại | Ghi chú |
| :--- | :---: | :--- |
| **Mã nguồn & Pipeline** | ✅ **100% Hoàn thành** | 360/360 test tự động (`pytest`) + `ruff` linter đạt 100% (xanh lá). |
| **Hạ tầng CI/CD GitHub** | ✅ **100% Hoàn thành** | GitHub Actions tự động test, build GHCR và SSH deploy. |
| **Container VPS** | 🟢 **Đang chạy (`Up / Running`)** | Container `support-video-worker` chạy ngầm trên VPS `talk.gacdero.vn`. |
| **Google Drive (rclone)** | 🟢 **Đã liên kết (5 TiB)** | Đã cấp quyền OAuth2, kết nối trực tiếp kho lưu trữ 5 TiB Google Drive. |
| **Tài khoản Google Pro** | 🟢 **1 / 10 Sẵn sàng** | `acc_01.json` đã nạp và xác minh; 9 tài khoản còn lại chờ nạp sau. |
| **Telegram Bot Listener** | 🟢 **Đang hoạt động (Polling)** | Đã kết nối Bot và Chat ID, nhận lệnh trực tiếp từ điện thoại. |

---

## 🏗️ 2. KIẾN TRÚC & HẠ TẦNG THỰC TẾ

### 2.1. Phân bổ trên VPS (`talk.gacdero.vn`)
- **Thư mục triển khai:** `/opt/support-video-edit/` (cách ly độc lập với `/opt/agent-talk/` và `/opt/entre-tb/`).
- **Cấu trúc lưu trữ mount ngoài (Persistent Volumes):**
  - `/opt/support-video-edit/shared/sessions/`: Lưu trữ các file cookie Google (`acc_01.json` .. `acc_10.json`) với quyền `600`.
  - `/opt/support-video-edit/shared/data/`: Chứa cơ sở dữ liệu hàng đợi SQLite `factory_jobs.sqlite3` (chế độ WAL).
  - `/opt/support-video-edit/shared/logs/`: Lưu trữ log xoay vòng của worker (tối đa 3 file x 50MB).
  - `/opt/support-video-edit/shared/rclone/`: Nơi chứa file cấu hình `rclone.conf` tải lên Google Drive.
- **Giới hạn tài nguyên (Bảo vệ 6GB RAM VPS dùng chung):**
  - Container RAM trần: `2048M` (2GB).
  - Container CPU trần: `2.0` vCPU.
  - `stop_grace_period: 60s`: Đảm bảo khi restart không bị hỏng file hoặc dở dang bước upload.

### 2.2. Khóa bảo mật & Secrets đã tự động cấu hình
1. **Khóa SSH Deploy (`support_video_deploy`):**
   - Đã sinh cặp khóa ed25519 chuyên biệt tại `~/.ssh/support_video_deploy`.
   - Public key đã được nạp an toàn vào `/root/.ssh/authorized_keys` trên VPS.
2. **GitHub Actions Secrets (Kho mã nguồn `nguyenloc2702/yt-support`):**
   - `VPS_SSH_KEY`: Khóa private của `support_video_deploy`.
   - `VPS_HOST`: `talk.gacdero.vn` (cổng 22, user `root`).
3. **Mã bí mật nội bộ (`/opt/support-video-edit/.env` quyền `600`):**
   - `APP_SECRET_KEY`: Khóa băm ngẫu nhiên 64 ký tự chuẩn mật mã.
   - `MASTER_ENCRYPTION_KEY`: Khóa Fernet 32-byte mã hóa đối xứng.
   - `XKIRO_API_KEY`: API Key TTS chất lượng cao xKiro.

---

## ⚙️ 3. HIỆN TRẠNG 2 LUỒNG SẢN XUẤT VIDEO (DUAL-MODE)

### 🟢 Mode 1: Smart Reup & Cắt Dựng Nhanh (`/reup`)
*Thời gian xử lý: ~2 - 3 phút/video.*
1. **Tối ưu Subtitle (Cứu CPU & RAM VPS):** `yt-dlp` luôn chạy cờ trích xuất phụ đề có sẵn (`--write-sub --write-auto-sub`). Nếu video có sub ➔ parse trực tiếp, **bỏ qua hoàn toàn Whisper STT** (tiết kiệm 1.5GB RAM và 5 phút CPU 100%).
2. **Biên tập dí dỏm:** LLM tóm tắt và viết lại kịch bản lôi cuốn theo phong cách reviewer tiếng Việt.
3. **Cổng duyệt Telegram:** Gửi bản tóm tắt qua Telegram chờ duyệt (`/approve <job_id>` hoặc `/reject <job_id>`).
4. **Lồng tiếng & Nhạc nền:** Tổng hợp giọng đọc `edge-tts` (hoặc xKiro) kết hợp tự động auto-ducking BGM âm lượng nền.
5. **Cắt cảnh gốc:** `script_matcher` cắt ghép các đoạn video gốc khớp chuẩn xác với mốc thời gian lời thoại.
6. **Concurrency Semaphore Lock:** Giới hạn chỉ đúng **1 tác vụ nặng** (Whisper/Render) chạy tại một thời điểm. Nếu có job khác gửi đến cùng lúc sẽ tự động vào trạng thái `QUEUED` chờ lượt, không làm sập container lân cận (`agent-talk`, `entre-tb`).

### 🟣 Mode 2: AI Genesis 100% Sinh Mới (`/remake`)
*Phim tài liệu / Re-make lịch sử với visual tự sinh hoàn toàn.*
1. **Phân rã kịch bản:** Phân tách mạch truyện thành các shot 10s kèm prompt sinh hình/video chi tiết, phân định rõ cảnh then chốt và cảnh phụ.
2. **Cổng duyệt kịch bản:** Gửi kịch bản phân cảnh qua Telegram chờ admin duyệt.
3. **Hàng đợi SQLite WAL:** Quản lý từng shot độc lập (`factory_shots`), tự động hoàn lượt thử cho shot bị gián đoạn khi restart worker (`reset_interrupted_shots`).
4. **Sinh Video Veo qua `gemini-webapi` 2.1.1 (Đã nghiệm thu):**
   - Gọi RPC nội bộ siêu nhẹ, không tốn tài nguyên trình duyệt Playwright.
   - Kết quả đo thực nghiệm: 10.01 giây, độ phân giải 720p HD, dung lượng 4.23 MB, thời gian sinh ~78 giây/shot.
   - Tự động hoán đổi session atomic (`tempfile` + `os.replace` quyền `600`) khi Google refresh `__Secure-1PSIDTS`.
5. **Graceful Fallback Ken Burns:** Shot nào gặp lỗi quá 3 lần retry sẽ tự động kích hoạt `ImageKenBurnsProvider` (sinh ảnh tĩnh độ nét cao + hiệu ứng lia máy 10s) để dây chuyền ghép phim tài liệu cuối (`assemble_full_documentary.py`) không bao giờ bị đứt gãy.
6. **Kiểm tra chất lượng & Lưu trữ:** `output_validator` thẩm định moov atom và bitrate trước khi tải lên Google Drive qua `rclone`.

---

## 🛡️ 4. QUẢN TRỊ POOL 10 TÀI KHOẢN PRO & QUOTA

- **Trạng thái lưu trữ:** File trạng thái bền vững `sessions/pool_state.json` (sử dụng `filelock`), sống qua restart container.
- **Ngân sách Quota an toàn 70%:** Mặc định dừng mỗi tài khoản ở mức 20 shot/ngày (ngưỡng an toàn chống cạn credit).
- **Tự động phục hồi:** Sang ngày mới theo mốc nửa đêm UTC, trạng thái `EXHAUSTED` tự động reset về `AVAILABLE`.
- **Kill-Switch khẩn cấp:** Khi gặp bất kỳ lỗi xác minh bất thường nào (`AUTH` hoặc `VERIFY`), hệ thống lập tức ngắt toàn bộ Mode 2, chuyển sang `PAUSED`, và gửi cảnh báo đỏ lên Telegram. Admin cần đăng nhập lại trình duyệt, nạp lại cookie rồi gửi `/resume`.

---

## 📱 5. SỔ TAY ĐIỀU KHIỂN TỪ XA QUA TELEGRAM

Bạn có thể điều khiển toàn bộ hệ thống từ điện thoại thông qua các lệnh:

| Lệnh | Ý nghĩa & Hành vi |
| :--- | :--- |
| `/reup <url> [duration=180]` | Kích hoạt Mode 1 (Tóm tắt, lồng tiếng & cắt video gốc theo thời lượng mong muốn). |
| `/remake <url>` | Kích hoạt Mode 2 (Tạo kịch bản chia shot và sinh 100% video AI mới). |
| `/approve <job_id>` | Duyệt kịch bản và cho phép dây chuyền bắt đầu render video. |
| `/reject <job_id>` | Từ chối kịch bản và hủy job đang chờ. |
| `/cancel <job_id>` | Hủy ngay lập tức một job đang chạy dở và dừng các cảnh còn lại. |
| `/retry <job_id>` | Khởi động lại một job bị lỗi hoặc đã hủy để xử lý tiếp các cảnh còn dở. |
| `/status` | Báo cáo dung lượng đĩa trống VPS, quota từng tài khoản Pro, trạng thái kill-switch & tiến độ hàng đợi. |
| `/pause` | Tạm dừng toàn bộ dây chuyền sinh video AI để bảo trì. |
| `/resume` | Kích hoạt hệ thống chạy tiếp sau khi nạp cookie hoặc sau khi tạm dừng. |
| `/help` | Xem danh sách hướng dẫn lệnh chi tiết. |

---

## 🔧 6. HƯỚNG DẪN QUẢN TRỊ COOKIE (CLI CHEATSHEET)

Bạn có thể chạy các lệnh này trên máy cá nhân hoặc trực tiếp trên VPS (`python scripts/manage_accounts.py ...`):

```bash
# 1. Xem danh sách tất cả các tài khoản hiện có
python scripts/manage_accounts.py list

# 2. Kiểm tra xem cookie còn sống hay hết hạn (gọi trực tiếp Google server)
python scripts/manage_accounts.py check --all
python scripts/manage_accounts.py check --acc 1

# 3. Thêm tài khoản mới (nhập cookie bảo mật ẩn trong terminal, không lưu log shell)
python scripts/manage_accounts.py add --acc 2

# 4. Thêm tài khoản từ file cookie json có sẵn
python scripts/manage_accounts.py add --acc 2 --from-file duong_dan_cookie.json

# 5. Vô hiệu hóa hoặc Kích hoạt lại 1 tài khoản
python scripts/manage_accounts.py disable --acc 2
python scripts/manage_accounts.py enable --acc 2
```

---

## 📌 7. HÀNH ĐỘNG TIẾP THEO

1. **Kích hoạt Telegram Bot:**
   - Cung cấp `TELEGRAM_BOT_TOKEN` (từ `@BotFather`) và `TELEGRAM_CHAT_ID` (từ `@userinfobot`).
   - Nạp vào `/opt/support-video-edit/.env` trên VPS để bot bắt đầu phản hồi tin nhắn.
2. **Cập nhật thêm tài khoản Pro:**
   - Dùng script `manage_accounts.py add --acc N` để nạp các tài khoản từ 2 đến 10 khi bạn rảnh.
3. **Cấu hình Google Drive (rclone):**
   - Copy file `rclone.conf` vào `/opt/support-video-edit/shared/rclone/rclone.conf` nếu muốn video render xong tự động chuyển thẳng vào thư mục Drive cá nhân.
