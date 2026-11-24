"""CRUD + quyền cho Knowledge Library API (/api/v1/knowledge).

Bốn nhóm được kiểm ở đây:
  1. Vòng đời tài liệu: create → read → update (If-Match) → version → restore.
  2. Ghi nguyên tử: file trên đĩa và row trong DB luôn khớp nhau.
  3. Ranh giới quyền: ingest ghi được, chỉ owner xoá/publish.
  4. Legacy: `/api/v1/read-me` không đổi hành vi sau khi thêm library.
"""
from __future__ import annotations

import json


def _create(client, headers, path="guide/setup.md", content="# Setup\nStep 1.\n", **extra):
    body = {"source": "library", "path": path, "content": content, **extra}
    return client.post("/api/v1/knowledge/docs", headers=headers, json=body)


def test_create_read_update_lifecycle(client, ingest, read_key, knowledge_env):
    r = _create(client, ingest, tags=["ops"], category="Vận hành")
    assert r.status_code == 201, r.text
    doc = r.json()
    doc_id = doc["id"]
    assert doc["source"] == "library"
    assert doc["path"] == "guide/setup.md"
    assert doc["title"] == "Setup"          # lấy từ heading đầu tiên
    assert doc["status"] == "draft"         # library mặc định draft
    assert doc["read_only"] is False
    assert doc["owner"] == "test-ingest"    # identity = phần tên của key
    assert doc["tags"] == ["ops"]
    assert doc["category"] == "Vận hành"
    assert doc["version"] == 1
    assert doc["content"].startswith("# Setup")

    # File thật phải tồn tại — FS là source of truth, không phải DB.
    assert (knowledge_env["library"] / "guide" / "setup.md").is_file()

    # GET trả ETag qua header, không chỉ trong body.
    r_get = client.get(f"/api/v1/knowledge/docs/{doc_id}", headers=read_key)
    assert r_get.status_code == 200
    etag = r_get.headers["etag"]
    assert etag == doc["etag"]
    assert etag.startswith('"') and etag.endswith('"')

    # PUT thiếu If-Match → 428 (không cho ghi mù).
    r_no_match = client.put(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                            json={"content": "# Setup\nStep 2.\n"})
    assert r_no_match.status_code == 428
    assert r_no_match.json()["code"] == "missing_if_match"

    # PUT với hash cũ → 412, và file KHÔNG bị đổi.
    r_stale = client.put(f"/api/v1/knowledge/docs/{doc_id}",
                         headers={**ingest, "If-Match": '"deadbeefdeadbeef-v1"'},
                         json={"content": "# Setup\nLost update.\n"})
    assert r_stale.status_code == 412
    assert r_stale.json()["code"] == "hash_mismatch"
    assert (knowledge_env["library"] / "guide" / "setup.md").read_text(
        encoding="utf-8").startswith("# Setup\nStep 1.")

    # PUT đúng hash → 200, version +1, ETag đổi.
    r_put = client.put(f"/api/v1/knowledge/docs/{doc_id}",
                       headers={**ingest, "If-Match": etag},
                       json={"content": "# Setup v2\nStep 2.\n", "note": "fix step"})
    assert r_put.status_code == 200, r_put.text
    updated = r_put.json()
    assert updated["version"] == 2
    assert updated["title"] == "Setup v2"
    assert updated["etag"] != etag
    assert updated["content"] == "# Setup v2\nStep 2.\n"
    assert (knowledge_env["library"] / "guide" / "setup.md").read_text(
        encoding="utf-8") == "# Setup v2\nStep 2.\n"


def test_duplicate_create_and_overwrite(client, ingest, knowledge_env):
    assert _create(client, ingest).status_code == 201

    r_dup = _create(client, ingest, content="# Other\n")
    assert r_dup.status_code == 409
    assert r_dup.json()["code"] == "doc_exists"

    # overwrite=True là đường thoát cho agent bị 409 (idempotent retry).
    r_over = _create(client, ingest, content="# Setup v2\n", overwrite=True)
    assert r_over.status_code == 201, r_over.text
    assert r_over.json()["version"] == 2
    assert r_over.json()["title"] == "Setup v2"


def test_patch_modes(client, ingest, knowledge_env):
    doc_id = _create(client, ingest, content="alpha\nbeta\n").json()["id"]

    r_app = client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                         json={"mode": "append", "content": "gamma\n"})
    assert r_app.status_code == 200, r_app.text
    assert r_app.json()["content"] == "alpha\nbeta\ngamma\n"

    r_pre = client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                         json={"mode": "prepend", "content": "zero\n"})
    assert r_pre.status_code == 200
    assert r_pre.json()["content"].startswith("zero\nalpha")

    r_rep = client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                         json={"mode": "replace_text", "target_text": "beta",
                               "replacement": "BETA"})
    # replace_text cũng là thao tác phá huỷ → cần If-Match như overwrite.
    assert r_rep.status_code == 428
    etag = client.get(f"/api/v1/knowledge/docs/{doc_id}",
                      headers=ingest).headers["etag"]
    r_rep = client.patch(f"/api/v1/knowledge/docs/{doc_id}",
                         headers={**ingest, "If-Match": etag},
                         json={"mode": "replace_text", "target_text": "beta",
                               "replacement": "BETA"})
    assert r_rep.status_code == 200, r_rep.text
    assert "BETA" in r_rep.json()["content"]
    assert "beta" not in r_rep.json()["content"]

    # replace_text không tìm thấy target → 409, không ghi gì.
    etag = client.get(f"/api/v1/knowledge/docs/{doc_id}",
                      headers=ingest).headers["etag"]
    r_miss = client.patch(f"/api/v1/knowledge/docs/{doc_id}",
                          headers={**ingest, "If-Match": etag},
                          json={"mode": "replace_text", "target_text": "khong-co",
                                "replacement": "x"})
    assert r_miss.status_code == 409
    assert r_miss.json()["code"] == "target_not_found"

    # replace_text/overwrite là thao tác phá huỷ → bắt buộc If-Match.
    r_no_match = client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                              json={"mode": "overwrite", "content": "x"})
    assert r_no_match.status_code == 428

    # mode lạ bị Pydantic chặn trước khi vào service.
    r_bad = client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                         json={"mode": "insert", "content": "x"})
    assert r_bad.status_code == 422

    # append KHÔNG cần If-Match: agent ghi log nối tiếp, retry vẫn an toàn.
    assert client.patch(f"/api/v1/knowledge/docs/{doc_id}", headers=ingest,
                        json={"mode": "append", "content": "delta\n"}).status_code == 200


def test_versions_and_restore(client, ingest, read_key, owner, knowledge_env):
    doc_id = _create(client, ingest, content="v1 body\n").json()["id"]
    for i in range(2, 4):
        etag = client.get(f"/api/v1/knowledge/docs/{doc_id}",
                          headers=read_key).headers["etag"]
        r = client.put(f"/api/v1/knowledge/docs/{doc_id}",
                       headers={**ingest, "If-Match": etag},
                       json={"content": f"v{i} body\n"})
        assert r.status_code == 200

    r_ver = client.get(f"/api/v1/knowledge/docs/{doc_id}/versions", headers=read_key)
    assert r_ver.status_code == 200
    versions = r_ver.json()["versions"]
    assert [v["version_no"] for v in versions] == [3, 2, 1]
    assert versions[0]["sha256"] != versions[-1]["sha256"]

    # Khôi phục v1 → tạo version MỚI (4) chứ không ghi đè lịch sử.
    r_restore = owner.post(
        f"/api/v1/knowledge/docs/{doc_id}/versions/1/restore",
        headers={"Authorization": "Bearer test-admin"})
    assert r_restore.status_code == 200, r_restore.text
    restored = r_restore.json()
    assert restored["content"] == "v1 body\n"
    assert restored["version"] == 4

    r_missing = owner.post(
        f"/api/v1/knowledge/docs/{doc_id}/versions/99/restore",
        headers={"Authorization": "Bearer test-admin"})
    assert r_missing.status_code == 404
    assert r_missing.json()["code"] == "version_not_found"


def test_delete_restore_is_owner_only(client, ingest, read_key, admin, owner, knowledge_env):
    doc_id = _create(client, ingest).json()["id"]
    path = f"/api/v1/knowledge/docs/{doc_id}"

    # ingest key là admin scope nhưng KHÔNG phải owner → 403.
    r_forbidden = client.delete(path, headers=admin)
    assert r_forbidden.status_code == 403
    assert r_forbidden.json()["code"] == "owner_only"

    r_del = owner.delete(path, headers=admin)
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["deleted"] is True
    # Xoá mềm: file rời khỏi library nhưng vẫn nằm trong .trash.
    assert not (knowledge_env["library"] / "guide" / "setup.md").exists()
    trashed = knowledge_env["library"] / ".trash" / doc_id
    assert trashed.is_dir() and any(trashed.iterdir())

    # Đọc lại báo 410 chứ không 404 — client phân biệt "đã xoá" với "chưa từng có".
    r_gone = client.get(path, headers=read_key)
    assert r_gone.status_code == 410
    assert r_gone.json()["code"] == "doc_trashed"

    # Không xuất hiện trong list mặc định.
    r_list = client.get("/api/v1/knowledge/docs", headers=read_key)
    assert doc_id not in [d["id"] for d in r_list.json()["docs"]]

    r_restore = owner.post(f"{path}/restore", headers=admin)
    assert r_restore.status_code == 200, r_restore.text
    assert r_restore.json()["status"] == "draft"
    assert (knowledge_env["library"] / "guide" / "setup.md").is_file()

    # Restore lần 2 → 409 vì không còn trong trash.
    r_again = owner.post(f"{path}/restore", headers=admin)
    assert r_again.status_code == 409
    assert r_again.json()["code"] == "not_trashed"


def test_publish_transitions(client, ingest, read_key, admin, owner, knowledge_env):
    doc_id = _create(client, ingest).json()["id"]
    path = f"/api/v1/knowledge/docs/{doc_id}"

    assert client.post(f"{path}/publish", headers=admin,
                       json={"status": "published"}).status_code == 403

    r_pub = owner.post(f"{path}/publish", headers=admin, json={"status": "canonical"})
    assert r_pub.status_code == 200
    assert r_pub.json()["status"] == "canonical"

    # Sau khi canonical, agent ingest không còn sửa được (published_locked).
    etag = client.get(path, headers=read_key).headers["etag"]
    r_locked = client.put(path, headers={**ingest, "If-Match": etag},
                          json={"content": "# Hacked\n"})
    assert r_locked.status_code == 403
    assert r_locked.json()["code"] == "published_locked"

    # Owner vẫn sửa được.
    r_owner = owner.put(path, headers={**admin, "If-Match": etag},
                        json={"content": "# Owner edit\n"})
    assert r_owner.status_code == 200

    r_bad = owner.post(f"{path}/publish", headers=admin, json={"status": "secret"})
    assert r_bad.status_code == 422


def test_raw_download_and_not_found(client, ingest, read_key, knowledge_env):
    doc_id = _create(client, ingest, content="# Setup\nStep 1.\n").json()["id"]

    r_raw = client.get(f"/api/v1/knowledge/docs/{doc_id}/raw", headers=read_key)
    assert r_raw.status_code == 200
    assert r_raw.text == "# Setup\nStep 1.\n"
    assert r_raw.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r_raw.headers["content-disposition"]
    assert ".md" in r_raw.headers["content-disposition"]

    r_404 = client.get("/api/v1/knowledge/docs/knd_khongtontai", headers=read_key)
    assert r_404.status_code == 404
    assert r_404.json()["code"] == "doc_not_found"


def test_fork_repo_doc_into_library(client, ingest, read_key, knowledge_repo_env):
    r_repo = client.get("/api/v1/knowledge/docs?source=repo_docs", headers=read_key)
    assert r_repo.status_code == 200
    repo_docs = r_repo.json()["docs"]
    assert len(repo_docs) == 1, repo_docs
    repo_doc = repo_docs[0]
    assert repo_doc["read_only"] is True
    assert repo_doc["owner"] == "repo"

    # Sửa thẳng tài liệu repo → 403 read_only (deploy rsync sẽ ghi đè).
    r_write = client.put(f"/api/v1/knowledge/docs/{repo_doc['id']}",
                         headers={**ingest, "If-Match": repo_doc["etag"]},
                         json={"content": "# hacked\n"})
    assert r_write.status_code == 403
    assert r_write.json()["code"] == "read_only"

    r_fork = client.post(f"/api/v1/knowledge/docs/{repo_doc['id']}/fork",
                         headers=ingest, json={})
    assert r_fork.status_code == 201, r_fork.text
    fork = r_fork.json()
    assert fork["source"] == "library"
    assert fork["read_only"] is False
    assert fork["status"] == "draft"
    assert fork["path"].startswith("forks/repo_docs/")
    assert fork["source_refs"] == [repo_doc["id"]]
    assert "Repo Protocol" in fork["content"]

    # Fork lại cùng path → 409 (không âm thầm nhân bản).
    r_dup = client.post(f"/api/v1/knowledge/docs/{repo_doc['id']}/fork",
                        headers=ingest, json={})
    assert r_dup.status_code == 409


def test_scope_enforcement(client, ingest, read_key, knowledge_env):
    doc_id = _create(client, ingest).json()["id"]

    # read scope: đọc OK, ghi 403.
    assert client.get(f"/api/v1/knowledge/docs/{doc_id}", headers=read_key).status_code == 200
    r_write = client.put(f"/api/v1/knowledge/docs/{doc_id}", headers=read_key,
                         json={"content": "x"})
    assert r_write.status_code == 403

    # ingest không được sync (admin) hay xoá (owner).
    assert client.post("/api/v1/knowledge/sync", headers=ingest,
                       json={}).status_code == 403

    # Không có key → 401.
    assert client.get("/api/v1/knowledge/docs").status_code == 401


def test_sync_endpoint_and_idempotent_create(client, ingest, admin, knowledge_env):
    # Idempotency-Key: retry cùng key trả lại đúng response cũ, không tạo bản 2.
    headers = {**ingest, "Idempotency-Key": "knd-test-1"}
    r1 = _create(client, headers)
    r2 = _create(client, headers)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    r_sync = client.post("/api/v1/knowledge/sync", headers=admin)
    assert r_sync.status_code == 200, r_sync.text
    counts = r_sync.json()
    assert counts["scanned"] >= 1
    assert "errors" in counts

    # Body tuỳ chọn — gọi không body vẫn chạy (dùng cho bước deploy).
    assert client.post("/api/v1/knowledge/sync", headers=admin).status_code == 200


def test_index_reflects_file_added_out_of_band(client, read_key, knowledge_env):
    """Agent ghi thẳng file bằng PATCH/append hoặc shell → sync phải thấy."""
    target = knowledge_env["library"] / "manual" / "note.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Note tay\nDo agent khác ghi.\n", encoding="utf-8")

    r = client.get("/api/v1/knowledge/docs?source=library", headers=read_key)
    assert r.status_code == 200
    docs = r.json()["docs"]
    assert [d["path"] for d in docs] == ["manual/note.md"]
    assert docs[0]["title"] == "Note tay"


def test_legacy_read_me_endpoint_unchanged(client, ingest, read_key, knowledge_env):
    """`/api/v1/read-me` giữ nguyên contract cũ sau khi thêm library."""
    r = client.post("/api/v1/read-me", headers=ingest,
                    json={"path": "legacy/log.md", "content": "# Log\n- ok\n"})
    assert r.status_code == 201
    assert r.json()["path"] == "legacy/log.md"

    # Cùng file đó cũng xuất hiện trong library dưới source `read_me`.
    r_lib = client.get("/api/v1/knowledge/docs?source=read_me", headers=read_key)
    assert r_lib.status_code == 200
    assert "legacy/log.md" in [d["path"] for d in r_lib.json()["docs"]]

    # Và sửa qua API mới thì API cũ thấy nội dung mới (chung một file).
    doc = r_lib.json()["docs"][0]
    r_put = client.put(f"/api/v1/knowledge/docs/{doc['id']}",
                       headers={**ingest, "If-Match": doc["etag"]},
                       json={"content": "# Log\n- updated via knowledge\n"})
    assert r_put.status_code == 200, r_put.text
    r_old = client.get("/api/v1/read-me/legacy/log.md", headers=read_key)
    assert "updated via knowledge" in r_old.json()["content"]


def test_read_me_source_stays_agent_writable(client, ingest, read_key, knowledge_env):
    """`read_me` index ra status published nhưng KHÔNG bị published_locked.

    Đó là status mặc định của index, không phải quyết định publish của owner;
    khoá theo nó sẽ chặn đúng luồng agent append log hằng ngày. Khoá chỉ áp
    cho source `library` (xem test_publish_transitions).
    """
    client.post("/api/v1/read-me", headers=ingest,
                json={"path": "ops.md", "content": "# Ops\nrunbook\n"})
    docs = client.get("/api/v1/knowledge/docs?source=read_me",
                      headers=read_key).json()["docs"]
    doc = docs[0]
    assert doc["status"] == "published"

    r_ok = client.put(f"/api/v1/knowledge/docs/{doc['id']}",
                      headers={**ingest, "If-Match": doc["etag"]},
                      json={"content": "# Ops\nrunbook v2\n"})
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["status"] == "published"   # giữ nguyên status khi sửa


def test_events_and_sync_runs_are_recorded(client, ingest, read_key, admin, knowledge_env):
    doc_id = _create(client, ingest).json()["id"]
    etag = client.get(f"/api/v1/knowledge/docs/{doc_id}", headers=read_key).headers["etag"]
    client.put(f"/api/v1/knowledge/docs/{doc_id}", headers={**ingest, "If-Match": etag},
               json={"content": "# Setup\nv2\n"})
    client.post("/api/v1/knowledge/sync", headers=admin)

    from backend.app.main import store
    events = store.query(
        "SELECT action FROM knowledge_events WHERE doc_id=? ORDER BY created_at", (doc_id,))
    actions = [e["action"] for e in events]
    assert actions[0] == "created"
    assert "updated" in actions

    runs = store.query("SELECT counts_json FROM knowledge_sync_runs")
    assert runs, "sync phải ghi lại một run"
    counts = json.loads(runs[-1]["counts_json"])
    assert counts["scanned"] >= 1
