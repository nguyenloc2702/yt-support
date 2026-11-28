"""Tests — MPA page routes: mỗi feature 1 trang, / redirect về /todos.

Chạy ungated (routes tĩnh không đụng board): `pytest tests/test_web_pages.py -q`
"""
from __future__ import annotations

from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"


@pytest.mark.parametrize("path", ["/todos", "/shorts", "/youtube", "/publish", "/chat"])
def test_feature_pages_served(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    # mỗi page load đủ module JS dùng chung
    for src in ("core.js", "todos.js", "shorts.js", "youtube.js",
                "publish.js", "chat.js", "router.js"):
        assert f"/static/js/{src}" in r.text, f"{path} thiếu script {src}"


def test_root_redirects_to_todos(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307, r.text
    assert r.headers["location"] == "/todos"


def test_page_scripts_have_no_store_cache(client):
    """B2 lesson: JS/CJS phải no-store — heuristic cache từng làm agent chạy
    code cũ sau deploy (syncRouteFromHash is not defined)."""
    r = client.get("/static/js/core.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"


def test_youtube_oauth_callback_redirects_to_youtube_page(client):
    """OAuth callback phải về `/youtube#/youtube?...` — trước đây là `/#/youtube`
    và sau MPA bị 307 sang /todos, mất toast + trạng thái kết nối."""
    r = client.get("/api/v1/youtube/auth/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/youtube#/youtube?yt=error")


def test_instagram_oauth_callback_redirects_to_publish_page(client):
    r = client.get("/api/v1/instagram/auth/callback", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/publish#/publish?ig=error")


def test_frontend_monolith_index_archived():
    """Monolith 10.5k dòng đã archive — không được quay lại frontend/."""
    assert not (FRONTEND_DIR / "index.html").exists(), (
        "frontend/index.html (monolith cũ) không còn được serve; "
        "giữ bản backup ở _archive/, không đặt lại trong frontend/"
    )

