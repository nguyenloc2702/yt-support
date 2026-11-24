"""Biên giới an toàn của Knowledge Library.

Ba nhóm rủi ro thật sự ở đây:
  1. Ghi ra ngoài root (path traversal, absolute path, symlink) — API này nhận
     đường dẫn từ agent nên đây là bề mặt tấn công chính.
  2. Rò secret: tài liệu vận hành hay chứa token thật, mà library lại đọc được
     qua HTTP nên phải chặn lúc ghi và che lúc đọc.
  3. Ghi vào vùng read-only (repo docs bị rsync ghi đè khi deploy).
"""
from __future__ import annotations

import os

import pytest

MAX_BYTES = 5 * 1024 * 1024


def _post(client, headers, path, content="# x\n", **extra):
    return client.post("/api/v1/knowledge/docs", headers=headers,
                       json={"source": "library", "path": path,
                             "content": content, **extra})


@pytest.mark.parametrize("bad_path", [
    "../evil.md",
    "a/../../evil.md",
    "/etc/passwd.md",
    "C:/Windows/evil.md",
    "C:\\Windows\\evil.md",
    "sub/./x.md",
    "..",
    "docs/..",
])
def test_path_traversal_rejected(client, ingest, knowledge_env, bad_path):
    r = _post(client, ingest, bad_path)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "bad_path"
    # Không có file nào được tạo ngoài root.
    assert not (knowledge_env["library"].parent / "evil.md").exists()


def test_absolute_and_empty_paths_rejected_by_schema(client, ingest, knowledge_env):
    assert _post(client, ingest, "").status_code == 422      # min_length=1
    assert _post(client, ingest, "   ").status_code == 422   # rỗng sau strip


def test_only_markdown_allowed(client, ingest, knowledge_env):
    for path in ("run.py", "a/b.json", "note.txt", "noext"):
        r = _post(client, ingest, path)
        assert r.status_code == 415, (path, r.text)
        assert r.json()["code"] == "unsupported_media_type"


def test_unknown_source_is_404(client, ingest, knowledge_env):
    r = client.post("/api/v1/knowledge/docs", headers=ingest,
                    json={"source": "khong-co", "path": "x.md", "content": "# x\n"})
    assert r.status_code == 404
    assert r.json()["code"] == "source_not_found"


@pytest.mark.parametrize("payload", [
    "token: ghp_" + "a" * 30,
    "key = sk-" + "b" * 30,
    "VIDEO_API_KEYS=test-admin:admin,real-key:admin",
    "Authorization: Bearer abcdefghijklmnop",
    "VIDEO_WEB_PIN=123456",
])
def test_secret_in_content_blocks_write(client, ingest, knowledge_env, payload):
    r = _post(client, ingest, "leak/secret.md", f"# Leak\n{payload}\n")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "secret_detected"
    # Fail closed: file không được tạo, kể cả khi nội dung đã ghi một nửa.
    assert not (knowledge_env["library"] / "leak" / "secret.md").exists()


def test_secret_is_redacted_when_read(client, read_key, knowledge_env):
    """File đã lỡ nằm trên đĩa (agent ghi tay) → đọc qua API phải bị che."""
    target = knowledge_env["library"] / "handwritten" / "token.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Token\nghp_" + "c" * 32 + "\n", encoding="utf-8")

    docs = client.get("/api/v1/knowledge/docs?source=library",
                      headers=read_key).json()["docs"]
    doc = client.get(f"/api/v1/knowledge/docs/{docs[0]['id']}", headers=read_key)
    assert doc.status_code == 200
    body = doc.json()
    assert body["secret_redacted"] is True
    assert "ghp_cccc" not in body["content"]
    assert "[REDACTED]" in body["content"]

    # Raw download cũng phải đi qua cùng lớp che — không có cửa sau.
    raw = client.get(f"/api/v1/knowledge/docs/{docs[0]['id']}/raw", headers=read_key)
    assert "ghp_cccc" not in raw.text
    assert "[REDACTED]" in raw.text


def test_binary_and_oversize_rejected(client, ingest, knowledge_env):
    r_nul = _post(client, ingest, "bin.md", "# Bin\n\x00binary\n")
    assert r_nul.status_code == 415
    assert r_nul.json()["code"] == "unsupported_media_type"
    assert not (knowledge_env["library"] / "bin.md").exists()

    r_big = _post(client, ingest, "big.md", "x" * (MAX_BYTES + 1))
    assert r_big.status_code == 413
    assert r_big.json()["code"] == "doc_too_large"
    assert not (knowledge_env["library"] / "big.md").exists()


def test_symlink_inside_library_is_not_followed(client, ingest, read_key, knowledge_env):
    outside = knowledge_env["library"].parent / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret = outside / "secret.md"
    secret.write_text("# Outside\nkhông được index\n", encoding="utf-8")
    link = knowledge_env["library"] / "link.md"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("tạo symlink cần quyền đặc biệt trên nền tảng này")

    # Index bỏ qua symlink → không có row nào trỏ ra ngoài.
    docs = client.get("/api/v1/knowledge/docs?source=library", headers=read_key).json()["docs"]
    assert [d["path"] for d in docs] == []

    # Và ghi đè qua đường dẫn symlink cũng bị chặn.
    r = _post(client, ingest, "link.md", "# Overwrite\n")
    assert r.status_code == 422
    assert r.json()["code"] == "bad_path"
    assert secret.read_text(encoding="utf-8").startswith("# Outside")


def test_repo_source_is_read_only(client, ingest, read_key, admin, owner, knowledge_repo_env):
    repo_doc = client.get("/api/v1/knowledge/docs?source=repo_docs",
                          headers=read_key).json()["docs"][0]

    # Tạo mới vào source ro → 403.
    r_create = client.post("/api/v1/knowledge/docs", headers=ingest,
                           json={"source": "repo_docs", "path": "new.md",
                                 "content": "# New\n"})
    assert r_create.status_code == 403
    assert r_create.json()["code"] == "read_only"

    # Xoá tài liệu repo → 403 kể cả với owner (deploy rsync sẽ mang lại).
    r_del = owner.delete(f"/api/v1/knowledge/docs/{repo_doc['id']}", headers=admin)
    assert r_del.status_code == 403
    assert r_del.json()["code"] == "read_only"

    # Publish tài liệu repo → 403.
    r_pub = owner.post(f"/api/v1/knowledge/docs/{repo_doc['id']}/publish",
                       headers=admin, json={"status": "published"})
    assert r_pub.status_code == 403


def test_deleted_file_does_not_resurrect_on_sync(client, ingest, read_key, admin,
                                                 owner, knowledge_env):
    """File trong .trash không được index lại thành tài liệu mới."""
    doc_id = _post(client, ingest, "tmp/gone.md", "# Gone\n").json()["id"]
    assert owner.delete(f"/api/v1/knowledge/docs/{doc_id}",
                        headers=admin).status_code == 200

    client.post("/api/v1/knowledge/sync", headers=admin)
    docs = client.get("/api/v1/knowledge/docs?source=library", headers=read_key).json()["docs"]
    assert docs == []
    # Row vẫn còn (xoá mềm) để restore được.
    assert client.get(f"/api/v1/knowledge/docs/{doc_id}",
                      headers=read_key).status_code == 410


def test_front_matter_cannot_forge_read_only_or_owner(client, ingest, read_key, knowledge_env):
    """Front-matter là dữ liệu không tin được — không được đổi read_only/owner."""
    content = (
        "---\n"
        "title: Giả danh\n"
        "owner: repo\n"
        "status: canonical\n"
        "read_only: true\n"
        "---\n"
        "# Giả danh\n"
    )
    doc = _post(client, ingest, "forge.md", content).json()
    assert doc["read_only"] is False          # do source quyết định, không do file
    assert doc["owner"] == "test-ingest"      # do auth quyết định
    assert doc["status"] == "draft"           # library luôn tạo ở draft
    assert doc["title"] == "Giả danh"         # metadata thì được phép
