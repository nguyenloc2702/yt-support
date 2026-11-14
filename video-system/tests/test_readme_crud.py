"""Tests for read_me folder CRUD API (/api/v1/read-me)."""
from __future__ import annotations

import pytest


@pytest.fixture()
def isolated_read_me_dir(tmp_path, monkeypatch):
    d = tmp_path / "read_me"
    d.mkdir(parents=True, exist_ok=True)
    (d / "seed.md").write_text("# Seed Doc\nInitial line.\n", encoding="utf-8")
    monkeypatch.setenv("VIDEO_READ_ME_DIR", str(d))
    return d


def test_read_me_full_crud_and_path_safety(client, ingest, read_key, isolated_read_me_dir):
    # 1. List docs
    r_list = client.get("/api/v1/read-me", headers=read_key)
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 1
    assert r_list.json()["docs"][0]["path"] == "seed.md"

    # 2. Create nested doc via POST
    r_post = client.post(
        "/api/v1/read-me",
        headers=ingest,
        json={
            "path": "logs/clip_gen_2026_11_14.md",
            "content": "# Clip Gen Log\n- Shot 1 OK\n",
        },
    )
    assert r_post.status_code == 201, r_post.text
    assert r_post.json()["path"] == "logs/clip_gen_2026_11_14.md"

    # Duplicate POST without overwrite -> 409
    r_dup = client.post(
        "/api/v1/read-me",
        headers=ingest,
        json={"path": "logs/clip_gen_2026_11_14.md", "content": "dup"},
    )
    assert r_dup.status_code == 409

    # 3. PATCH append + replace_text
    r_app = client.patch(
        "/api/v1/read-me/logs/clip_gen_2026_11_14.md",
        headers=ingest,
        json={"mode": "append", "content": "- Shot 2 OK\n"},
    )
    assert r_app.status_code == 200
    assert "- Shot 2 OK" in r_app.json()["content"]

    r_rep = client.patch(
        "/api/v1/read-me/logs/clip_gen_2026_11_14.md",
        headers=ingest,
        json={"mode": "replace_text", "target_text": "Shot 1 OK", "replacement": "Shot 1 DONE"},
    )
    assert r_rep.status_code == 200
    assert "Shot 1 DONE" in r_rep.json()["content"]

    # 4. PUT overwrite
    r_put = client.put(
        "/api/v1/read-me/logs/clip_gen_2026_11_14.md",
        headers=ingest,
        json={"content": "# Replaced\nFresh content.\n"},
    )
    assert r_put.status_code == 200
    assert r_put.json()["title"] == "Replaced"

    # 5. GET single doc & search query
    r_get = client.get("/api/v1/read-me/logs/clip_gen_2026_11_14.md", headers=read_key)
    assert r_get.status_code == 200
    assert "Fresh content." in r_get.json()["content"]

    r_search = client.get("/api/v1/read-me?q=Fresh", headers=read_key)
    assert r_search.status_code == 200
    assert len(r_search.json()["docs"]) == 1

    # 6. Path traversal & unsupported extension must be rejected (422)
    r_bad1 = client.post("/api/v1/read-me", headers=ingest, json={"path": "../evil.md", "content": "x"})
    assert r_bad1.status_code == 422
    r_bad2 = client.post("/api/v1/read-me", headers=ingest, json={"path": "run.py", "content": "x"})
    assert r_bad2.status_code == 422

    # 7. DELETE (admin)
    r_del = client.delete(
        "/api/v1/read-me/logs/clip_gen_2026_11_14.md",
        headers={"Authorization": "Bearer test-admin"},
    )
    assert r_del.status_code == 200
    assert not (isolated_read_me_dir / "logs" / "clip_gen_2026_11_14.md").exists()
