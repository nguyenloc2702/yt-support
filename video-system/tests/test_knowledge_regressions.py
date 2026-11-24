"""Regression cho 8 lỗi subagent tìm thấy sau khi deploy lần đầu.

Mỗi test tái hiện đúng bug đã xác nhận (có bug từng trả 500 trên prod) rồi khoá
hành vi đúng. Không xoá test nào ở đây khi refactor — đây là bằng chứng.
"""
from __future__ import annotations

import gzip

import pytest


def _create(client, headers, path, content="# Doc\nbody\n", **extra):
    r = client.post("/api/v1/knowledge/docs", headers=headers,
                    json={"source": "library", "path": path, "content": content, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_raw_unicode_title_is_200_and_header_cannot_be_injected(client, ingest, read_key,
                                                                knowledge_env):
    """Prod từng trả 500: title tiếng Việt → latin-1 encode trong header."""
    vi = _create(client, ingest, "raw/vi.md", "# Tiêu đề\nNội dung.\n",
                 title="Hướng dẫn vận hành — bản tổng hợp")
    r = client.get(f"/api/v1/knowledge/docs/{vi['id']}/raw", headers=read_key)
    assert r.status_code == 200, r.text
    disp = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in disp
    assert "H%C6%B0%E1%BB%9Bng" in disp          # RFC 5987, không phải byte thô
    assert "\r" not in disp and "\n" not in disp

    # Ingest control title → không được tách thành filename thứ hai / inject header.
    evil = _create(client, ingest, "raw/evil.md", "# Evil\n",
                   title='ev"; filename="pwn.md')
    r2 = client.get(f"/api/v1/knowledge/docs/{evil['id']}/raw", headers=read_key)
    assert r2.status_code == 200
    assert 'filename="pwn.md"' not in r2.headers["content-disposition"]
    assert r2.headers["content-disposition"].count("filename=") == 1

    crlf = _create(client, ingest, "raw/crlf.md", "# CRLF\n",
                   title="x\r\nX-Injected: 1")
    r3 = client.get(f"/api/v1/knowledge/docs/{crlf['id']}/raw", headers=read_key)
    assert r3.status_code == 200
    disp3 = r3.headers["content-disposition"]
    # CRLF đã bị thay bằng '-': chữ "X-Injected" chỉ là một phần tên file,
    # không còn là header mới. Điều cần khoá là không có control char nào sống sót.
    assert "\r" not in disp3 and "\n" not in disp3
    assert disp3.count("attachment;") == 1


@pytest.mark.parametrize("bad_etag", [
    '""', "-v0", '"-v0"', "a", "0123456789abcdef", "0123456789abcdef-v0",
    "ffffffffffffffff-v1",
])
def test_malformed_if_match_cannot_bypass_optimistic_lock(client, ingest, read_key,
                                                          knowledge_env, bad_etag):
    doc = _create(client, ingest, "etag/lock.md", "# Lock\n")
    r = client.put(f"/api/v1/knowledge/docs/{doc['id']}",
                   headers={**ingest, "If-Match": bad_etag},
                   json={"content": "# Overwritten\n"})
    assert r.status_code == 412, (bad_etag, r.text)
    assert r.json()["code"] == "hash_mismatch"


def test_secret_in_front_matter_metadata_is_redacted_everywhere(client, ingest, read_key,
                                                                knowledge_env):
    token = "sk-" + "q" * 30
    target = knowledge_env["library"] / "meta" / "leak.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\ntitle: {token}\ncategory: {token}\n---\n# Safe body\n",
                      encoding="utf-8")

    docs = client.get("/api/v1/knowledge/docs?source=library", headers=read_key).json()["docs"]
    doc_id = docs[0]["id"]
    assert token not in str(docs)

    detail = client.get(f"/api/v1/knowledge/docs/{doc_id}", headers=read_key).json()
    assert token not in str(detail)
    assert "[REDACTED]" in detail["title"]
    assert "[REDACTED]" in detail["category"]

    raw = client.get(f"/api/v1/knowledge/docs/{doc_id}/raw", headers=read_key)
    assert token not in raw.headers["content-disposition"]
    assert token not in raw.text


def test_writes_to_trashed_doc_are_rejected(client, ingest, read_key, admin, owner,
                                            knowledge_env):
    doc = _create(client, ingest, "trash/locked.md", "# Locked\n")
    doc_id = doc["id"]
    assert owner.delete(f"/api/v1/knowledge/docs/{doc_id}", headers=admin).status_code == 200

    # Ba đường ghi đều không được hồi sinh file trong khi row vẫn là trashed.
    r_put = client.put(f"/api/v1/knowledge/docs/{doc_id}",
                       headers={**ingest, "If-Match": "*"},
                       json={"content": "# Resurrect\n"})
    assert r_put.status_code == 410
    assert r_put.json()["code"] == "doc_trashed"

    r_create = client.post("/api/v1/knowledge/docs", headers=ingest,
                           json={"source": "library", "path": "trash/locked.md",
                                 "content": "# Resurrect\n", "overwrite": True})
    assert r_create.status_code == 410

    r_pub = owner.post(f"/api/v1/knowledge/docs/{doc_id}/publish", headers=admin,
                       json={"status": "published"})
    assert r_pub.status_code == 410

    # File vẫn nằm yên trong trash và restore vẫn hoạt động.
    assert not (knowledge_env["library"] / "trash" / "locked.md").exists()
    trashed = list((knowledge_env["library"] / ".trash" / doc_id).iterdir())
    assert len(trashed) == 1
    assert owner.post(f"/api/v1/knowledge/docs/{doc_id}/restore",
                      headers=admin).status_code == 200


def test_version_snapshots_store_redacted_text(client, read_key, owner, admin,
                                               knowledge_env, store_fixture):
    token = "ghp_" + "z" * 32
    target = knowledge_env["library"] / "ver" / "secret.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Ver\n{token}\n", encoding="utf-8")

    docs = client.get("/api/v1/knowledge/docs?source=library", headers=read_key).json()["docs"]
    doc_id = docs[0]["id"]
    rows = store_fixture.query(
        "SELECT content_z FROM knowledge_versions WHERE doc_id=?", (doc_id,))
    assert rows
    assert token not in gzip.decompress(rows[0]["content_z"]).decode("utf-8")

    # Restore cũng trả nội dung đã che, không tạo ra bản chứa secret.
    r = owner.post(f"/api/v1/knowledge/docs/{doc_id}/versions/1/restore", headers=admin)
    assert r.status_code == 200, r.text
    assert token not in r.json()["content"]
    assert "[REDACTED]" in r.json()["content"]


def test_legacy_delete_prunes_ghost_row(client, ingest, read_key, admin, knowledge_env):
    r = client.post("/api/v1/read-me", headers=ingest,
                    json={"path": "ghost.md", "content": "# Ghost\n"})
    assert r.status_code == 201
    doc_id = next(d["id"] for d in client.get(
        "/api/v1/knowledge/docs?source=read_me", headers=read_key).json()["docs"]
        if d["path"] == "ghost.md")

    assert client.delete("/api/v1/read-me/ghost.md", headers=admin).status_code == 200
    client.post("/api/v1/knowledge/sync", headers=admin)   # TTL=0 cũng tự sync

    remaining = client.get("/api/v1/knowledge/docs?source=read_me",
                           headers=read_key).json()["docs"]
    assert [d["path"] for d in remaining] == []
    assert client.get(f"/api/v1/knowledge/docs/{doc_id}",
                      headers=read_key).status_code == 404


@pytest.mark.parametrize("bad_path", ["a\rb.md", "a\nb.md", "a\x00b.md", "a" * 181 + ".md"])
def test_control_chars_and_long_paths_are_422_not_500(client, ingest, knowledge_env,
                                                      bad_path):
    r = client.post("/api/v1/knowledge/docs", headers=ingest,
                    json={"source": "library", "path": bad_path, "content": "# x\n"})
    assert r.status_code == 422, (repr(bad_path[:20]), r.text)
    assert r.json()["code"] == "bad_path"


def test_trashed_rows_survive_prune_and_restore(client, ingest, admin, read_key, owner,
                                                knowledge_env):
    doc = _create(client, ingest, "trash/keep.md")
    doc_id = doc["id"]
    assert owner.delete(f"/api/v1/knowledge/docs/{doc_id}", headers=admin).status_code == 200
    counts = client.post("/api/v1/knowledge/sync", headers=admin).json()
    assert counts["removed"] == 0
    assert client.get(f"/api/v1/knowledge/docs/{doc_id}",
                      headers=read_key).status_code == 410
    assert owner.post(f"/api/v1/knowledge/docs/{doc_id}/restore",
                      headers=admin).status_code == 200


@pytest.mark.parametrize("path", [
    "assets/x.md", "data/x.md", "logs/x.md", "vendor/x.md",
    "frontend/x.md", ".hidden/x.md", ".trash/x.md",
])
def test_indexer_skipped_dirs_are_rejected_on_write(client, ingest, knowledge_env, path):
    """Regression của regression: rw prune và indexer phải dùng chung rule.

    Nếu API cho tạo `assets/x.md` mà indexer bỏ qua `assets/`, sync kế tiếp
    sẽ thấy path không có trong `seen` và xoá row — doc 201 rồi 404.
    """
    r = client.post("/api/v1/knowledge/docs", headers=ingest,
                    json={"source": "library", "path": path, "content": "# x\n"})
    assert r.status_code == 422, (path, r.text)
    assert r.json()["code"] == "bad_path"


def test_valid_doc_is_not_pruned_by_sync(client, ingest, read_key, admin, knowledge_env):
    doc = _create(client, ingest, "notes/keep.md", "# Keep\nNội dung.\n")
    client.post("/api/v1/knowledge/sync", headers=admin)
    r = client.get(f"/api/v1/knowledge/docs/{doc['id']}", headers=read_key)
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "# Keep\nNội dung.\n"
