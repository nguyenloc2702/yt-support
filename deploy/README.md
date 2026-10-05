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
