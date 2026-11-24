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
