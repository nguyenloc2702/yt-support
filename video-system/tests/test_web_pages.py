"""Tests — MPA page routes: mỗi feature 1 trang, / redirect về /todos.

Chạy ungated (routes tĩnh không đụng board): `pytest tests/test_web_pages.py -q`
"""
from __future__ import annotations

import pytest


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
