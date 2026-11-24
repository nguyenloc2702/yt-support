"""Đồng thời & tính nhất quán của Knowledge Library.

App chạy một process (uvicorn 1 worker) nên `threading.RLock` trong service là
đủ để tuần tự hoá read-modify-write. Bốn bất biến được kiểm:
  1. Append song song không mất dòng.
  2. Hai PUT cùng If-Match → đúng một thắng, còn lại 412.
  3. Tạo song song cùng path → đúng một tài liệu.
  4. Sync chạy chồng nhau không sinh row trùng và không ném lỗi.
"""
from __future__ import annotations

import threading

import pytest

from backend.app.service_knowledge import KnowledgeError, MAX_VERSIONS

THREADS = 8


def _run_parallel(fn, count=THREADS):
    """Chạy `fn(i)` trên `count` thread, trả về (kết quả, lỗi) theo thứ tự."""
    results: list = [None] * count
    errors: list = [None] * count
    barrier = threading.Barrier(count)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)   # ép mọi thread vào đúng một thời điểm
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001 — test cần thấy mọi loại lỗi
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


@pytest.fixture()
def doc_id(knowledge, knowledge_env):
    doc = knowledge.create_doc(source="library", path="conc/log.md",
                               content="# Log\n", actor="test-ingest")
    return doc["id"]


def test_parallel_appends_keep_every_line(knowledge, doc_id):
    def append(i: int) -> int:
        doc = knowledge.patch_doc(doc_id, mode="append", content=f"line-{i}\n",
                                  actor="test-ingest", if_match=None)
        return doc["version"]

    _, errors = _run_parallel(append)
    assert errors == [None] * THREADS, errors

    final = knowledge.get_doc(doc_id)
    body = final["content"]
    missing = [i for i in range(THREADS) if f"line-{i}\n" not in body]
    assert missing == [], f"mất dòng {missing} — read-modify-write không được khoá"
    assert body.startswith("# Log\n")
    # Mỗi append là một version riêng, không gộp.
    assert final["version"] == 1 + THREADS


def test_parallel_put_same_etag_only_one_wins(knowledge, doc_id):
    etag = knowledge.get_doc(doc_id)["etag"]

    def put(i: int) -> str:
        try:
            knowledge.update_doc(doc_id, f"# Log\nwriter-{i}\n", "test-ingest", etag)
            return "ok"
        except KnowledgeError as exc:
            return exc.code

    results, errors = _run_parallel(put)
    assert errors == [None] * THREADS, errors
    assert results.count("ok") == 1, results
    assert results.count("hash_mismatch") == THREADS - 1, results

    # Nội dung cuối phải là của writer thắng, không bị trộn.
    final = knowledge.get_doc(doc_id)["content"]
    assert final.startswith("# Log\nwriter-")
    assert final.count("writer-") == 1


def test_parallel_create_same_path_yields_one_doc(knowledge, knowledge_env):
    def create(i: int) -> str:
        try:
            knowledge.create_doc(source="library", path="conc/race.md",
                                 content=f"# Race {i}\n", actor="test-ingest")
            return "created"
        except KnowledgeError as exc:
            return exc.code

    results, errors = _run_parallel(create)
    assert errors == [None] * THREADS, errors
    assert results.count("created") == 1, results
    assert results.count("doc_exists") == THREADS - 1, results

    docs = knowledge.list_docs(source="library")["docs"]
    assert [d["path"] for d in docs if d["path"] == "conc/race.md"] == ["conc/race.md"]


def test_parallel_reads_triggering_sync_are_stable(knowledge, doc_id):
    """TTL=0 buộc mỗi lần đọc chạy sync — 8 thread cùng sync không được nổ."""
    def read(i: int) -> int:
        return knowledge.list_docs(source="library")["total"]

    results, errors = _run_parallel(read, count=6)
    assert errors == [None] * 6, errors
    assert set(results) == {1}

    rows = knowledge.store.query(
        "SELECT rel_path, COUNT(*) AS n FROM knowledge_docs "
        "WHERE source_alias='library' GROUP BY rel_path HAVING n > 1")
    assert rows == []


def test_version_history_is_capped(knowledge, doc_id):
    for i in range(MAX_VERSIONS + 5):
        knowledge.patch_doc(doc_id, mode="append", content=f"v{i}\n",
                            actor="test-ingest", if_match=None)

    versions = knowledge.versions(doc_id)["versions"]
    assert len(versions) == MAX_VERSIONS
    # Giữ bản mới nhất, bỏ bản cũ — restore vẫn phải chạy được với bản còn lại.
    assert versions[0]["version_no"] == 1 + MAX_VERSIONS + 5
    assert knowledge.restore_version(doc_id, versions[-1]["version_no"],
                                     "test-ingest")["version"] > 0


def test_etag_accepts_wildcard_and_prefix(knowledge, doc_id):
    doc = knowledge.get_doc(doc_id)
    etag = doc["etag"]
    sha_prefix = doc["content_hash"][:16]
    assert etag == f'"{sha_prefix}-v{doc["version"]}"'

    # `*` = "không quan tâm bản nào" (dùng khi agent biết mình là writer duy nhất).
    assert knowledge.update_doc(doc_id, "# Log\nstar\n", "test-ingest", "*")["version"] == 2

    # Chỉ prefix hash cũng đủ (client hay cắt chuỗi) — vẫn phải khớp version hiện tại.
    current = knowledge.get_doc(doc_id)
    prefix = current["content_hash"][:8]
    assert knowledge.update_doc(doc_id, "# Log\nprefix\n", "test-ingest", prefix)["version"] == 3

    # Prefix lạc → 412.
    with pytest.raises(KnowledgeError) as exc:
        knowledge.update_doc(doc_id, "# Log\nx\n", "test-ingest", "ffffffffffffffff")
    assert exc.value.status_code == 412


def test_atomic_write_leaves_no_temp_file(knowledge, doc_id, knowledge_env):
    knowledge.patch_doc(doc_id, mode="append", content="tail\n",
                        actor="test-ingest", if_match=None)
    leftovers = [p.name for p in (knowledge_env["library"] / "conc").iterdir()
                 if p.name.startswith(".") or ".tmp-" in p.name]
    assert leftovers == []
