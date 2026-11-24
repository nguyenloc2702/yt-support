"""Tìm kiếm / lọc / thống kê của Knowledge Library.

Corpus nhỏ (~130 file) nên MVP dùng LIKE + quét nội dung trong bộ nhớ thay vì
FTS5. Test ở đây khoá đúng hành vi đó — khi Phase 2 chuyển sang FTS5, các
assert về *kết quả* vẫn phải giữ nguyên.
"""
from __future__ import annotations

import pytest


def _mk(client, headers, path, content, **extra):
    r = client.post("/api/v1/knowledge/docs", headers=headers,
                    json={"source": "library", "path": path, "content": content, **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def seeded(client, ingest, knowledge_env):
    return [
        _mk(client, ingest, "ops/deploy.md", "# Deploy runbook\nRestart service bằng systemd.\n",
            tags=["ops", "deploy"], category="Vận hành"),
        _mk(client, ingest, "ops/rollback.md", "# Rollback runbook\nTrả image về tag trước.\n",
            tags=["ops"], category="Vận hành"),
        _mk(client, ingest, "arch/etag.md", "# ETag design\nIf-Match chống lost update.\n",
            tags=["arch"], category="Kiến trúc"),
        _mk(client, ingest, "arch/storage.md", "# Storage layout\nFilesystem là source of truth.\n",
            tags=["arch", "storage"], category="Kiến trúc"),
        _mk(client, ingest, "misc/notes.md", "# Ghi chú\nKhông có gì đặc biệt.\n",
            tags=["misc"], category="Khác"),
    ]


def test_search_by_title_and_body(client, read_key, seeded):
    # Khớp title/path/tags → không cần đọc file.
    r = client.get("/api/v1/knowledge/docs?q=rollback", headers=read_key)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["docs"][0]["path"] == "ops/rollback.md"

    # Khớp nội dung → phải kèm snippet để UI hiển thị ngữ cảnh.
    r_body = client.get("/api/v1/knowledge/docs?q=systemd", headers=read_key)
    hit = r_body.json()["docs"][0]
    assert hit["path"] == "ops/deploy.md"
    assert "snippet" in hit
    assert "systemd" in hit["snippet"]

    # Không phân biệt hoa thường.
    assert client.get("/api/v1/knowledge/docs?q=ETAG", headers=read_key).json()["total"] == 1

    # Không có kết quả → total 0, không lỗi.
    r_none = client.get("/api/v1/knowledge/docs?q=khongtontai", headers=read_key)
    assert r_none.status_code == 200
    assert r_none.json() == {"total": 0, "limit": 50, "offset": 0, "docs": []}


def test_filters_by_source_category_tag_status(client, read_key, ingest, seeded):
    def paths(**params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        r = client.get(f"/api/v1/knowledge/docs?{query}", headers=read_key)
        assert r.status_code == 200, r.text
        return {d["path"] for d in r.json()["docs"]}

    assert paths(source="library") == {d["path"] for d in seeded}
    assert paths(category="Kiến trúc") == {"arch/etag.md", "arch/storage.md"}
    assert paths(tag="deploy") == {"ops/deploy.md"}
    assert paths(tag="ops") == {"ops/deploy.md", "ops/rollback.md"}
    assert paths(status="draft") == {d["path"] for d in seeded}
    assert paths(status="published") == set()

    # Kết hợp filter: AND, không phải OR.
    assert paths(category="Vận hành", tag="deploy") == {"ops/deploy.md"}

    # q kết hợp filter cũng phải AND.
    r = client.get("/api/v1/knowledge/docs?q=runbook&category=Kiến trúc", headers=read_key)
    assert r.json()["total"] == 0


def test_pagination_and_limits(client, read_key, seeded):
    r = client.get("/api/v1/knowledge/docs?limit=2&offset=0", headers=read_key)
    page1 = r.json()
    assert page1["total"] == 5
    assert page1["limit"] == 2 and page1["offset"] == 0
    assert len(page1["docs"]) == 2

    r2 = client.get("/api/v1/knowledge/docs?limit=2&offset=4", headers=read_key)
    assert len(r2.json()["docs"]) == 1

    # offset vượt tổng → rỗng nhưng total vẫn đúng (UI cần total để vẽ phân trang).
    r3 = client.get("/api/v1/knowledge/docs?limit=2&offset=99", headers=read_key)
    assert r3.json()["docs"] == [] and r3.json()["total"] == 5

    # limit bị chặn trên (200); limit<=0 hiểu là "mặc định" (50) chứ không phải 1.
    assert client.get("/api/v1/knowledge/docs?limit=9999",
                      headers=read_key).json()["limit"] == 200
    assert client.get("/api/v1/knowledge/docs?limit=0",
                      headers=read_key).json()["limit"] == 50
    assert client.get("/api/v1/knowledge/docs?limit=-5",
                      headers=read_key).json()["limit"] == 50

    # Pagination của nhánh search cũng phải cắt đúng.
    r_q = client.get("/api/v1/knowledge/docs?q=runbook&limit=1&offset=1", headers=read_key)
    assert r_q.json()["total"] == 2
    assert len(r_q.json()["docs"]) == 1


def test_stats_and_duplicates(client, read_key, ingest, seeded):
    # Hai tài liệu nội dung y hệt → cùng sha256 → 1 nhóm trùng.
    dup = "# Deploy runbook\nRestart service bằng systemd.\n"
    _mk(client, ingest, "copy/deploy-copy.md", dup)

    r = client.get("/api/v1/knowledge/stats", headers=read_key)
    assert r.status_code == 200
    stats = r.json()
    assert stats["total_docs"] == 6
    assert stats["by_source"]["library"] == 6
    assert stats["by_status"]["draft"] == 6
    assert stats["duplicate_groups"] == 1
    assert stats["duplicates"][0]["count"] == 2
    assert stats["total_bytes"] > 0
    assert stats["last_sync_at"] > 0


def test_sources_endpoint(client, read_key, knowledge_env):
    r = client.get("/api/v1/knowledge/sources", headers=read_key)
    assert r.status_code == 200
    body = r.json()
    aliases = {s["alias"]: s for s in body["sources"]}
    assert set(aliases) == {"library", "read_me"}     # repo sources OFF trong test
    assert aliases["library"]["mode"] == "rw"
    assert aliases["library"]["read_only"] is False
    assert aliases["read_me"]["read_only"] is False
    assert aliases["library"]["path"] == str(knowledge_env["library"])
    assert body["library_root"] == str(knowledge_env["library"])
    assert aliases["library"]["doc_count"] == 0


def test_sources_reports_repo_roots_as_read_only(client, read_key, knowledge_repo_env):
    r = client.get("/api/v1/knowledge/sources", headers=read_key)
    sources = {s["alias"]: s for s in r.json()["sources"]}
    for alias in ("repo_docs", "plans"):
        assert sources[alias]["mode"] == "ro"
        assert sources[alias]["read_only"] is True
        assert sources[alias]["doc_count"] == 1
    assert sources["repo_docs"]["path"].startswith(str(knowledge_repo_env["repo"]))


def test_search_survives_missing_file(client, read_key, ingest, knowledge_env):
    """Row còn trong DB nhưng file bị xoá tay → search bỏ qua, không 500."""
    _mk(client, ingest, "gone.md", "# Gone\nsẽ bị xoá tay\n")
    (knowledge_env["library"] / "gone.md").unlink()

    r = client.get("/api/v1/knowledge/docs?q=sẽ", headers=read_key)
    assert r.status_code == 200
    assert r.json()["total"] == 0
