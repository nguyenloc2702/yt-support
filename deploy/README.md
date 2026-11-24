# VPS deployment skeleton

This directory contains a minimal, resource-conscious systemd deployment template. It deliberately keeps video data outside the release directory.

## Installation

```bash
sudo install -d -o videoedit -g videoedit /opt/local-video-editor/{releases,shared/{data,projects,logs,model-cache}}
sudo python3.11 -m venv /opt/local-video-editor/venv
sudo /opt/local-video-editor/venv/bin/pip install --upgrade pip
```

Set `/etc/local-video-editor/app.env` from `.env.example`; for production use an absolute `DATA_DIR`, `PROJECTS_DIR`, and `LOG_DIR` under `/opt/local-video-editor/shared`. Set `APP_HOST=127.0.0.1` and keep `MAX_CONCURRENT_JOBS=1`.

Install [`local-video-editor.service`](local-video-editor.service) to `/etc/systemd/system/`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now local-video-editor
sudo systemctl status local-video-editor
```

A reverse proxy such as Nginx or Caddy should terminate TLS and proxy to `127.0.0.1:8501`. Do not expose port 8501 publicly.

## Release layout

```text
/opt/local-video-editor/
├── current -> releases/<commit>
├── releases/<commit>/       # source code only
├── venv/                    # shared virtualenv
└── shared/
    ├── data/
    ├── projects/
    ├── logs/
    └── model-cache/
```

Use an atomic symlink switch during deployment and retain the previous release for rollback. Never copy [`data/`](../data:1), [`projects/`](../projects:1), or [`logs/`](../logs:1) into a release.

## Disk hygiene (bắt buộc trên VPS 30 GB)

Đĩa 29 GB đã từng lên 96% vì ba nguồn: image Docker của `entre-tb` (build 3–4
image/ngày, ~7.8 GB/lần), rác build video trong `/tmp`, và `data/objects` tăng
~330 MB/ngày. Hai timer dưới đây giữ mức sử dụng ổn định:

| Unit | Chạy | Làm gì |
|---|---|---|
| `video-tmp-sweep.timer` | 03:30 hằng ngày | Xoá **thư mục** trong `/tmp` quá 2 ngày. Bỏ qua thư mục ẩn (`.X11-unix`, `systemd-private-*`) và thư mục đang được process dùng làm cwd. |
| `docker-disk-prune.timer` | 04:10 hằng ngày | `docker image prune -af --filter until=72h` + build cache. Chỉ xoá image **không container nào dùng** và cũ hơn 72h, nên vẫn đủ bản để rollback. |

Cài đặt:

```bash
sudo install -m 755 deploy/video-tmp-sweep.sh deploy/docker-disk-prune.sh /usr/local/bin/
sudo install -m 644 deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now video-tmp-sweep.timer docker-disk-prune.timer
```

Kiểm tra chế độ không xoá gì: `DRY_RUN=1 video-tmp-sweep 2`.
Log: `/var/log/video-tmp-sweep.log`, `/var/log/docker-disk-prune.log`.

**Không dùng `/tmp` để chứa thành phẩm** — hệ điều hành tự xoá khi reboot.
Video final phải nằm trong `data/exports/` hoặc `data/objects/`.

## vidfac.gacdero.vn đi qua Caddy của project `entre-tb` (dễ vỡ)

`video-system` chạy bằng systemd trên host, bind `0.0.0.0:8090`. TLS cho
`vidfac.gacdero.vn` **không** do nginx (đang `inactive`, disabled) mà do container
`entre-tb-prod-caddy-1`, đọc `/opt/entre-tb/infra/caddy/Caddyfile` (bind mount
read-only từ repo khác).

Caddy chỉ đọc file lúc **khởi động**. Vì vậy nếu block `vidfac` bị xoá khỏi file
mà container chưa restart, mọi thứ vẫn chạy bình thường — sự cố chỉ lộ ra khi
container restart, lúc đó HTTPS của vidfac chết (`alert 80` trong TLS handshake).
Đúng chuyện này đã xảy ra ngày 2026-11-24.

`vidfac-caddy-guard.timer` (5 phút/lần) kiểm tra và tự thêm lại block nếu thiếu,
rồi reload Caddy (graceful, không downtime site khác). Upstream lấy gateway của
network `entre-tb-prod_frontend` tại thời điểm chạy, không hardcode.

```bash
sudo install -m 755 deploy/vidfac-caddy-guard.sh /usr/local/bin/
sudo install -m 644 deploy/vidfac-caddy-guard.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vidfac-caddy-guard.timer
```

**Sửa tận gốc:** thêm block này vào repo `entre-tb`. Guard chỉ là lưới an toàn —
nếu repo `entre-tb` có block thì guard không làm gì cả.

## Archive lên Google Drive + backup DB

Hai tầng lưu trữ, không phải "đẩy hết lên Drive":

| Tầng | Nơi lưu | Dùng cho |
|---|---|---|
| Nóng | `data/objects` trên VPS | File đang được phục vụ — `FileResponse` cần file local, seek liên tục |
| Lạnh | `gdrive:video_system_assets/<unit>` | Unit cũ, chỉ tải lại khi có người thực sự mở |

**Vì sao không mount Drive làm filesystem:** FUSE thêm 100–300 ms mỗi lần đọc; video
player seek liên tục nên mỗi range request thành một round-trip mạng. Đo thực tế
upload VPS → Drive chỉ **97 KiB/s** (do rclone dùng client_id mặc định bị Google
rate-limit 403; băng thông VPS thật là 2.5 MB/s).

### `video-archive.timer` — 04:40 hằng ngày

Đơn vị archive là một **unit** dưới `data/objects`: `itm_*` hoặc `shorts/sho_*`.
Chỉ archive unit có file mới nhất cũ hơn `--days` (mặc định 14). Quy trình:

1. `rclone copy` unit → `gdrive:video_system_assets/<unit>`
2. `rclone check --one-way --size-only` — **không khớp thì KHÔNG xoá local**
3. Xoá local, ghi vào bảng `archived_units`

Khi có request cho file đã archive, `ensure_local()` tự `rclone copyto` **đúng file
đó** về (không kéo cả unit), nên mở 1 subtitle 2 KB không phải chờ tải 300 MB.

Chạy tay / xem trước:

```bash
cd /opt/video-system
.venv/bin/python -m backend.app.service_archive --days 14 --dry-run
.venv/bin/python -m backend.app.service_archive --days 7 --limit 2
```

### `video-dbbackup.timer` — 02:40 hằng ngày

DB là thứ duy nhất không dựng lại được từ code. Dùng API `sqlite3.Connection.backup()`
(không copy file) vì DB đang chạy ở WAL — copy thô sẽ ra bản không nhất quán.
Snapshot được `PRAGMA integrity_check` trước khi upload; giữ 14 bản gần nhất trên
`gdrive:video_system_backups/db/`.

`.env` **không** được đưa lên Drive (chứa API key). Config yaml thì có.

```bash
.venv/bin/python -m backend.app.service_dbbackup --dry-run
.venv/bin/python -m backend.app.service_dbbackup --list
```
