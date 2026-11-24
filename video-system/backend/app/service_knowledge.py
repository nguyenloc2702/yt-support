"""Knowledge Library service.

Filesystem là source of truth; SQLite chỉ giữ index, version snapshot và audit.
Repo docs (read_me/docs/skills/plans/root *.md) được index read-only. Chỉ
`data/knowledge/` (library) và `read_me/` legacy là writable.

Vì sao không DB-only: repo đang deploy bằng git + rsync, agent còn ghi trực tiếp
bằng PATCH append, và DB nằm trong data/ nên không đi theo release. DB có thể
xoá/rebuild index bất cứ lúc nào mà không mất tài liệu gốc.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import REPO_ROOT, data_dir
from .service_readme import get_read_me_dir
from .store import Store, new_id, now

ALLOWED_DOC_EXTS = {".md"}
MAX_DOC_BYTES = 5 * 1024 * 1024
MAX_VERSIONS = 20
TRASH_KEEP_DAYS = 30

_IGNORE_DIRS = {
    ".git", ".venv", "node_modules", "data", "out", "logs", "temp_tts_cache",
    "__pycache__", ".pytest_cache", ".zcode", ".agents", "frontend", "assets",
    ".trash", "vendor",
}

# Chỉ bắt pattern đủ chắc chắn; không redact mọi chuỗi "key" trong tài liệu.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"(?i)(VIDEO_API_KEYS\s*=\s*)[^\s,]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Authorization:\s*Bearer\s+)[A-Za-z0-9._-]{10,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(VIDEO_WEB_PIN\s*=\s*)\d{4,8}"), r"\1[REDACTED]"),
]


class KnowledgeError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "knowledge_error"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class SourceSpec:
    alias: str
    path: Path
    mode: str
    category: str
    priority: int
    recursive: bool = True


def repo_root() -> Path:
    """Repo root chứa plans/skills. Trên prod app ở /opt/video-system còn repo
    ở /opt/support-video-edit — không phải sibling của app."""
    env = os.environ.get("VIDEO_KNOWLEDGE_REPO_DIR", "").strip()
    if env:
        return Path(env)
    candidates = [REPO_ROOT.parent, Path("/opt/support-video-edit")]
    for candidate in candidates:
        if (candidate / "plans").is_dir() or (candidate / "skills").is_dir():
            return candidate
    return REPO_ROOT.parent


def library_root() -> Path:
    raw = os.environ.get("VIDEO_KNOWLEDGE_DIR", "").strip()
    root = Path(raw) if raw else data_dir() / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _repo_sources_enabled() -> bool:
    raw = os.environ.get("VIDEO_KNOWLEDGE_REPO_SOURCES", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def source_specs() -> list[SourceSpec]:
    specs = [
        SourceSpec("read_me", get_read_me_dir(), "rw", "Hướng dẫn & vận hành", 10),
        SourceSpec("library", library_root(), "rw", "Thư viện tổng hợp", 20),
    ]
    if not _repo_sources_enabled():
        return specs
    rr = repo_root()
    candidates = [
        ("docs", REPO_ROOT / "docs", "Tài liệu kỹ thuật", 30, True),
        ("skills", REPO_ROOT / "skills", "Kỹ năng", 40, True),
        ("plans", rr / "plans", "Kế hoạch", 50, True),
        ("repo_docs", rr / "docs", "Tài liệu kỹ thuật (repo)", 35, True),
        ("repo_skills", rr / "skills", "Kỹ năng (repo)", 45, True),
        ("video_root", REPO_ROOT, "Tổng quan", 60, False),
        ("repo_root", rr, "Tổng quan (repo)", 65, False),
    ]
    for alias, path, category, priority, recursive in candidates:
        if path.is_dir():
            specs.append(SourceSpec(alias, path, "ro", category, priority, recursive))
    return specs


def _source_map() -> dict[str, SourceSpec]:
    return {s.alias: s for s in source_specs()}


def _doc_id(source_alias: str, rel_path: str) -> str:
    raw = f"{source_alias}:{rel_path}".encode("utf-8")
    return "knd_" + hashlib.sha1(raw).hexdigest()[:16]


def _clean_rel(rel_path: str) -> str:
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise KnowledgeError("Đường dẫn không hợp lệ.", 422, "bad_path")
    # Ký tự điều khiển (\r, \n, NUL) làm `_resolve`/`_atomic_write` ném OSError
    # → 500. Chặn ở đây, và để dư chỗ cho hậu tố `.tmp-<ns>` của atomic write.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise KnowledgeError("Đường dẫn chứa ký tự điều khiển.", 422, "bad_path")
    if len(raw) > 180:
        raise KnowledgeError("Đường dẫn quá dài (tối đa 180 ký tự).", 422, "bad_path")
    parts = [p for p in raw.split("/") if p]
    if not parts or any(p in {".", ".."} for p in parts):
        raise KnowledgeError("Đường dẫn không được chứa '..'.", 422, "bad_path")
    path = Path(*parts)
    if path.suffix.lower() not in ALLOWED_DOC_EXTS:
        raise KnowledgeError("Chỉ hỗ trợ file .md.", 415, "unsupported_media_type")
    return path.as_posix()


def _resolve(spec: SourceSpec, rel_path: str) -> Path:
    rel = _clean_rel(rel_path)
    root = spec.path.resolve()
    target = (root / rel).resolve()
    if target == root or root not in target.parents:
        raise KnowledgeError("Đường dẫn thoát khỏi root.", 422, "bad_path")
    if target.is_symlink():
        raise KnowledgeError("Không hỗ trợ symlink.", 422, "bad_path")
    return target


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> str:
    if len(data) > MAX_DOC_BYTES:
        raise KnowledgeError("Tài liệu vượt quá 5MB.", 413, "doc_too_large")
    if b"\x00" in data:
        raise KnowledgeError("Tài liệu chứa NUL/binary.", 415, "unsupported_media_type")
    return data.decode("utf-8", errors="replace")


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    meta: dict[str, Any] = {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            for line in lines[1:end]:
                if ":" not in line or line.lstrip().startswith("#"):
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip().strip('"').strip("'")
                if key == "tags":
                    meta[key] = [x.strip() for x in value.strip("[]").split(",") if x.strip()]
                elif key == "source_refs":
                    meta[key] = [x.strip() for x in value.strip("[]").split(",") if x.strip()]
                else:
                    meta[key] = value
            body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


def _first_heading(body: str, fallback: str) -> str:
    for line in body.splitlines():
        text = line.strip()
        if not text:
            continue
        text = text.lstrip("#").strip()
        if text:
            return text[:200]
    return fallback[:200]


def _default_status(spec: SourceSpec, rel_path: str) -> str:
    low = rel_path.lower()
    if "/archive/" in f"/{low}" or low.startswith("archive/") or "archive_" in low:
        return "archived"
    return "published"


def _initial_status(spec: SourceSpec, rel_path: str) -> str:
    """Status lúc index lần đầu.

    Front-matter KHÔNG được quyết định status: file do agent ghi tay, nếu đọc
    `status: canonical` từ đó thì một file chưa ai duyệt có thể tự thành
    canonical và khoá luôn đường sửa của agent khác. `library` luôn vào draft,
    chỉ `POST /publish` (owner) mới đổi được.
    """
    if spec.alias == "library":
        return "draft"
    return _default_status(spec, rel_path)


def _redact_secrets(text: str) -> tuple[str, bool]:
    redacted = False
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        new = pattern.sub(replacement, out)
        if new != out:
            redacted = True
            out = new
    return out, redacted


def _assert_no_secret(text: str) -> None:
    _clean, redacted = _redact_secrets(text)
    if redacted:
        raise KnowledgeError(
            "Nội dung chứa secret/token. Xoá hoặc thay bằng placeholder trước khi lưu.",
            422, "secret_detected")


def _assert_writable(content: str) -> None:
    """Guard trước khi ghi: nếu không chặn ở đây, file >5MB hoặc chứa NUL sẽ
    ghi được nhưng lần đọc sau đó fail 413/415 — tài liệu thành ngõ cụt."""
    if "\x00" in content:
        raise KnowledgeError("Nội dung chứa NUL/binary.", 415, "unsupported_media_type")
    if len(content.encode("utf-8")) > MAX_DOC_BYTES:
        raise KnowledgeError("Tài liệu vượt quá 5MB.", 413, "doc_too_large")


def _etag(row: dict[str, Any]) -> str:
    sha = str(row.get("current_sha256") or "")
    return f'"{sha[:16]}-v{row.get("version_no") or 1}"'


def _etag_matches(header: str | None, row: dict[str, Any]) -> bool:
    """Chỉ chấp nhận ETag mà API đã phát (`sha16-vN`) hoặc `*`.

    Bản cũ dùng `startswith(prefix)` nên `""`, `"-v0"` hay 1 ký tự hex đều qua
    được → stale writer đè mất bản của người khác. Version phải khớp luôn:
    `restore_version` có thể đưa nội dung về sha cũ ở version mới, nếu chỉ so
    hash thì ETag của bản cũ lại hợp lệ.
    """
    if not header:
        return False
    value = header.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value == "*":
        return True
    match = re.fullmatch(r"([0-9a-fA-F]{16})-v(\d+)", value)
    if not match:
        return False
    sha = str(row.get("current_sha256") or "")
    return (bool(sha) and sha.startswith(match.group(1))
            and int(match.group(2)) == int(row.get("version_no") or 1))


def _snippet(text: str, needle: str) -> str:
    low = text.lower()
    idx = low.find(needle)
    if idx < 0:
        return ""
    start = max(0, idx - 60)
    end = min(len(text), idx + len(needle) + 90)
    raw = " ".join(text[start:end].split())
    return ("…" if start else "") + raw + ("…" if end < len(text) else "")


class KnowledgeService:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._last_sync = 0.0

    # -- source / index -----------------------------------------------------
    def sources(self) -> dict[str, Any]:
        self.ensure_synced()
        counts = {
            row["source_alias"]: int(row["n"])
            for row in self.store.query(
                "SELECT source_alias, COUNT(*) AS n FROM knowledge_docs "
                "WHERE deleted_at IS NULL GROUP BY source_alias")
        }
        items = []
        for spec in source_specs():
            items.append({
                "alias": spec.alias,
                "path": str(spec.path),
                "mode": spec.mode,
                "category": spec.category,
                "priority": spec.priority,
                "recursive": spec.recursive,
                "read_only": spec.mode == "ro",
                "doc_count": counts.get(spec.alias, 0),
            })
        return {"sources": items, "library_root": str(library_root())}

    def _iter_files(self, spec: SourceSpec) -> Iterator[Path]:
        iterator = spec.path.rglob("*.md") if spec.recursive else spec.path.glob("*.md")
        for path in iterator:
            try:
                rel_parts = path.relative_to(spec.path).parts
            except ValueError:
                continue
            if any(part in _IGNORE_DIRS for part in rel_parts):
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_DOC_EXTS:
                continue
            yield path

    def ensure_synced(self) -> None:
        try:
            ttl = float(os.environ.get("VIDEO_KNOWLEDGE_SYNC_TTL", "60") or 60)
        except ValueError:
            ttl = 60.0
        if time.time() - self._last_sync > ttl:
            self.sync()

    def sync(self, *, prune_missing: bool = True, actor: str = "system",
             trigger: str = "manual") -> dict[str, Any]:
        started = now()
        counts: dict[str, Any] = {
            "scanned": 0, "added": 0, "updated": 0, "unchanged": 0,
            "removed": 0, "skipped": 0, "errors": [],
        }
        specs = source_specs()
        with self._lock:
            with self.store.transaction() as conn:
                for spec in specs:
                    conn.execute(
                        "INSERT INTO knowledge_sources (alias,abs_path,mode,category,"
                        "priority,recursive,created_at) VALUES (?,?,?,?,?,?,?) "
                        "ON CONFLICT(alias) DO UPDATE SET abs_path=excluded.abs_path, "
                        "mode=excluded.mode, category=excluded.category, "
                        "priority=excluded.priority, recursive=excluded.recursive",
                        (spec.alias, str(spec.path), spec.mode, spec.category,
                         spec.priority, int(spec.recursive), now()))
                    seen: set[str] = set()
                    for path in self._iter_files(spec):
                        counts["scanned"] += 1
                        try:
                            rel = path.relative_to(spec.path).as_posix()
                            stat = path.stat()
                            if stat.st_size > MAX_DOC_BYTES:
                                counts["skipped"] += 1
                                continue
                            data = path.read_bytes()
                            text = _decode(data)
                            sha = _hash_bytes(data)
                            outcome = self._upsert(conn, spec, rel, text, sha, stat, actor)
                            counts[outcome] += 1
                            seen.add(rel)
                        except KnowledgeError as exc:
                            counts["skipped"] += 1
                            if len(counts["errors"]) < 20:
                                counts["errors"].append(f"{spec.alias}: {exc.message}")
                        except Exception as exc:  # noqa: BLE001
                            counts["skipped"] += 1
                            if len(counts["errors"]) < 20:
                                counts["errors"].append(f"{spec.alias}: {exc!r}")
                    if prune_missing:
                        # FS là source of truth — kể cả với source rw. Nếu không,
                        # `DELETE /read-me/x.md` (legacy) xoá file nhưng row vẫn
                        # nằm trong list rồi trả 404 khi mở. Chỉ ngoại lệ: row
                        # đã xoá mềm qua knowledge API (`trashed_path`) phải giữ
                        # lại để restore được.
                        sql = "SELECT id, rel_path FROM knowledge_docs WHERE source_alias=?"
                        params: tuple = (spec.alias,)
                        if spec.mode == "rw":
                            sql += " AND deleted_at IS NULL AND trashed_path IS NULL"
                        for row in conn.execute(sql, params).fetchall():
                            if row["rel_path"] not in seen:
                                conn.execute("DELETE FROM knowledge_docs WHERE id=?",
                                             (row["id"],))
                                if spec.mode == "ro":
                                    conn.execute(
                                        "DELETE FROM knowledge_versions WHERE doc_id=?",
                                        (row["id"],))
                                counts["removed"] += 1
                finished = now()
                conn.execute(
                    "INSERT INTO knowledge_sync_runs (id,trigger,started_at,finished_at,"
                    "counts_json,errors_json) VALUES (?,?,?,?,?,?)",
                    (new_id("ksr"), trigger, started, finished,
                     json.dumps({k: v for k, v in counts.items() if k != "errors"},
                                ensure_ascii=False),
                     json.dumps(counts["errors"], ensure_ascii=False)))
        self._last_sync = time.time()
        return counts

    def _upsert(self, conn, spec: SourceSpec, rel: str, text: str, sha: str,
                stat: os.stat_result, actor: str) -> str:
        existing = conn.execute(
            "SELECT * FROM knowledge_docs WHERE source_alias=? AND rel_path=?",
            (spec.alias, rel)).fetchone()
        meta, body = _front_matter(text)
        title = str(meta.get("title") or _first_heading(body, Path(rel).stem))
        category = str(meta.get("category") or spec.category)
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else [spec.alias]
        refs = meta.get("source_refs") if isinstance(meta.get("source_refs"), list) else []
        if existing is None:
            doc_id = _doc_id(spec.alias, rel)
            status = _initial_status(spec, rel)
            owner = str(meta.get("owner") or ("repo" if spec.mode == "ro" else "owner"))
            conn.execute(
                "INSERT INTO knowledge_docs (id,source_alias,rel_path,title,category,"
                "tags_json,status,owner,generated_by,source_refs_json,current_sha256,"
                "size_bytes,mtime,version_no,read_only,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, spec.alias, rel, title, category, json.dumps(tags, ensure_ascii=False),
                 status, owner, None, json.dumps(refs, ensure_ascii=False), sha,
                 stat.st_size, stat.st_mtime, 1, int(spec.mode == "ro"), now(), now()))
            if spec.mode == "rw":
                self._insert_version(conn, doc_id, 1, sha, title, actor, "index", text)
            self._event(conn, doc_id, actor, "created", None, sha, {"source": spec.alias})
            return "added"

        changed = existing["current_sha256"] != sha
        if not changed:
            conn.execute(
                "UPDATE knowledge_docs SET mtime=?, size_bytes=? WHERE id=?",
                (stat.st_mtime, stat.st_size, existing["id"]))
            return "unchanged"

        if spec.mode == "rw":
            # Metadata do API đặt (category/tags/source_refs) phải được giữ khi
            # file đổi nội dung; front-matter chỉ override khi thực sự có key.
            category = str(meta.get("category") or existing["category"] or spec.category)
            if not isinstance(meta.get("tags"), list):
                tags = json.loads(existing["tags_json"] or "[]") or [spec.alias]
            if not isinstance(meta.get("source_refs"), list):
                refs = json.loads(existing["source_refs_json"] or "[]") or []
        version_no = int(existing["version_no"] or 1) + (1 if spec.mode == "rw" else 0)
        status = existing["status"] if spec.mode == "rw" else _initial_status(spec, rel)
        deleted_at = existing["deleted_at"]
        trashed_path = existing["trashed_path"]
        if spec.mode == "rw" and deleted_at:
            status = "draft" if spec.alias == "library" else "published"
            deleted_at = None
            trashed_path = None
        conn.execute(
            "UPDATE knowledge_docs SET title=?, category=?, tags_json=?, status=?, "
            "owner=?, source_refs_json=?, current_sha256=?, size_bytes=?, mtime=?, "
            "version_no=?, deleted_at=?, trashed_path=?, updated_at=? WHERE id=?",
            (title, category, json.dumps(tags, ensure_ascii=False), status,
             existing["owner"] or ("repo" if spec.mode == "ro" else "owner"),
             json.dumps(refs, ensure_ascii=False), sha, stat.st_size, stat.st_mtime,
             version_no, deleted_at, trashed_path, now(), existing["id"]))
        if spec.mode == "rw":
            self._insert_version(conn, existing["id"], version_no, sha, title, actor,
                                 "index", text)
        self._event(conn, existing["id"], actor, "updated", existing["current_sha256"],
                    sha, {"source": spec.alias})
        return "updated"

    def _insert_version(self, conn, doc_id: str, version_no: int, sha: str,
                        title: str, author: str, note: str, text: str) -> None:
        # Snapshot cũng phải che: DB được backup lên Drive, và restore_version
        # nếu trả lại secret thì lần ghi sau bị 422 secret_detected.
        safe_text, _ = _redact_secrets(text)
        raw = safe_text.encode("utf-8")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_versions (id,doc_id,version_no,sha256,"
            "title,author,note,content_z,size_bytes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id("kvr"), doc_id, version_no, sha, title, author, note,
             gzip.compress(raw), len(raw), now()))
        conn.execute(
            "DELETE FROM knowledge_versions WHERE doc_id=? AND version_no NOT IN "
            "(SELECT version_no FROM knowledge_versions WHERE doc_id=? "
            "ORDER BY version_no DESC LIMIT ?)",
            (doc_id, doc_id, MAX_VERSIONS))

    def _event(self, conn, doc_id: str | None, actor: str, action: str,
               from_sha: str | None, to_sha: str | None, meta: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO knowledge_events (id,doc_id,actor,action,from_sha,to_sha,"
            "meta_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_id("kev"), doc_id, actor, action, from_sha, to_sha,
             json.dumps(meta or {}, ensure_ascii=False), now()))

    # -- read ----------------------------------------------------------------
    def _row(self, doc_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM knowledge_docs WHERE id=?", (doc_id,))
        if not row:
            raise KnowledgeError("Không tìm thấy tài liệu.", 404, "doc_not_found")
        return row

    def _spec(self, alias: str) -> SourceSpec:
        spec = _source_map().get(alias)
        if not spec:
            raise KnowledgeError("Source không tồn tại.", 404, "source_not_found")
        return spec

    @staticmethod
    def _safe_text(value: Any) -> Any:
        """Front-matter là dữ liệu không tin được: title/tags/... cũng phải che.

        Chỉ redact `content` thì token trong `title: sk-...` vẫn lọt ra qua
        /docs, /raw (Content-Disposition) và snippets.
        """
        return _redact_secrets(value)[0] if isinstance(value, str) else value

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        tags = json.loads(row.get("tags_json") or "[]")
        refs = json.loads(row.get("source_refs_json") or "[]")
        clean = self._safe_text
        return {
            "id": row["id"],
            "source": clean(row["source_alias"]),
            "path": clean(row["rel_path"]),
            "title": clean(row["title"]),
            "category": clean(row["category"]),
            "tags": [clean(t) for t in tags],
            "status": row["status"],
            "owner": clean(row["owner"]),
            "generated_by": clean(row["generated_by"]),
            "source_refs": [clean(r) for r in refs],
            "read_only": bool(row["read_only"]),
            "content_hash": row["current_sha256"],
            "version": int(row["version_no"] or 1),
            "size_bytes": row["size_bytes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "duplicate_of": clean(row["duplicate_of"]),
            "etag": _etag(row),
        }

    def _read_text(self, row: dict[str, Any]) -> tuple[str, bool]:
        spec = self._spec(row["source_alias"])
        target = _resolve(spec, row["rel_path"])
        if not target.is_file():
            raise KnowledgeError("File nguồn không còn tồn tại.", 404, "doc_not_found")
        data = target.read_bytes()
        text = _decode(data)
        return _redact_secrets(text)

    def list_docs(self, *, q: str | None = None, source: str | None = None,
                  category: str | None = None, status: str | None = None,
                  tag: str | None = None, limit: int = 50,
                  offset: int = 0) -> dict[str, Any]:
        self.ensure_synced()
        # limit<=0 coi như "dùng mặc định" (UI gửi 0 khi chưa chọn), còn trần
        # 200 để một client không kéo cả kho trong một request.
        limit = 50 if limit is None or int(limit) <= 0 else min(int(limit), 200)
        offset = max(0, int(offset or 0))
        sql = "SELECT * FROM knowledge_docs WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status=?"
            params.append(status)
        else:
            sql += " AND status!='trashed' AND deleted_at IS NULL"
        if source:
            sql += " AND source_alias=?"
            params.append(source)
        if category:
            sql += " AND category=?"
            params.append(category)
        if tag:
            sql += " AND tags_json LIKE ?"
            params.append(f'%"{tag}"%')
        sql += " ORDER BY updated_at DESC, title COLLATE NOCASE"
        rows = self.store.query(sql, tuple(params))
        if q and q.strip():
            needle = q.strip().lower()
            hits: list[tuple[dict[str, Any], str]] = []
            for row in rows:
                hay = f"{row['title']} {row['rel_path']} {row['tags_json']}".lower()
                snippet = ""
                if needle not in hay:
                    try:
                        text, _redacted = self._read_text(row)
                    except KnowledgeError:
                        continue
                    if needle not in text.lower():
                        continue
                    snippet = _snippet(text, needle)
                hits.append((row, snippet))
            total = len(hits)
            page = hits[offset:offset + limit]
            docs = []
            for row, snippet in page:
                item = self._public(row)
                if snippet:
                    item["snippet"] = snippet
                docs.append(item)
        else:
            total = len(rows)
            docs = [self._public(row) for row in rows[offset:offset + limit]]
        return {"total": total, "limit": limit, "offset": offset, "docs": docs}

    def get_doc(self, doc_id: str) -> dict[str, Any]:
        self.ensure_synced()
        row = self._row(doc_id)
        if row["status"] == "trashed" or row["deleted_at"]:
            raise KnowledgeError("Tài liệu đã bị xoá mềm.", 410, "doc_trashed")
        text, redacted = self._read_text(row)
        out = self._public(row)
        out["content"] = text
        out["secret_redacted"] = redacted
        return out

    def raw_doc(self, doc_id: str) -> tuple[str, str]:
        doc = self.get_doc(doc_id)
        return doc["content"], doc["title"]

    # -- write ---------------------------------------------------------------
    def _atomic_write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp-{time.time_ns()}")
        try:
            tmp.write_bytes(content.encode("utf-8"))
            os.replace(tmp, target)
        except OSError as exc:
            raise KnowledgeError(f"Không ghi được file: {exc}", 422, "bad_path") from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _insert_new(self, spec: SourceSpec, rel: str, content: str, actor: str, *,
                    title: str | None = None, category: str | None = None,
                    tags: list[str] | None = None, source_refs: list[str] | None = None,
                    status: str | None = None, note: str = "create") -> dict[str, Any]:
        target = _resolve(spec, rel)
        if target.exists():
            raise KnowledgeError("Tài liệu đã tồn tại.", 409, "doc_exists")
        _assert_writable(content)
        _assert_no_secret(content)
        self._atomic_write(target, content)
        data = target.read_bytes()
        sha = _hash_bytes(data)
        stat = target.stat()
        meta, body = _front_matter(content)
        doc_title = title or str(meta.get("title") or _first_heading(body, Path(rel).stem))
        doc_category = category or str(meta.get("category") or spec.category)
        doc_tags = tags if tags is not None else (
            meta.get("tags") if isinstance(meta.get("tags"), list) else [spec.alias])
        doc_refs = source_refs if source_refs is not None else (
            meta.get("source_refs") if isinstance(meta.get("source_refs"), list) else [])
        doc_status = status or _initial_status(spec, rel)
        doc_id = _doc_id(spec.alias, rel)
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO knowledge_docs (id,source_alias,rel_path,title,category,"
                "tags_json,status,owner,generated_by,source_refs_json,current_sha256,"
                "size_bytes,mtime,version_no,read_only,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, spec.alias, rel, doc_title, doc_category,
                 json.dumps(doc_tags, ensure_ascii=False), doc_status, actor, actor,
                 json.dumps(doc_refs, ensure_ascii=False), sha, stat.st_size,
                 stat.st_mtime, 1, int(spec.mode == "ro"), now(), now()))
            self._insert_version(conn, doc_id, 1, sha, doc_title, actor, note, content)
            self._event(conn, doc_id, actor, "created", None, sha, {"source": spec.alias})
        return self.get_doc(doc_id)

    def _write_existing(self, row: dict[str, Any], content: str, actor: str, *,
                        title: str | None = None, category: str | None = None,
                        tags: list[str] | None = None, note: str = "update",
                        action: str = "updated") -> dict[str, Any]:
        if row["status"] == "trashed" or row["deleted_at"]:
            # Nếu không chặn: PUT/create overwrite tạo lại file ở path cũ nhưng
            # UPDATE không đổi status → doc vẫn trashed (410), file trong trash
            # bị bỏ rơi và restore_doc trả 409.
            raise KnowledgeError("Tài liệu đang nằm trong trash; hãy restore trước.",
                                 410, "doc_trashed")
        if row["read_only"]:
            raise KnowledgeError("Tài liệu repo là read-only; hãy fork sang library.",
                                 403, "read_only")
        # Chỉ khoá theo status với `library` — đó mới là nơi owner publish có chủ
        # đích. `read_me` legacy lấy status "published" từ index mặc định, khoá
        # theo nó sẽ chặn đúng luồng agent append log hằng ngày.
        if (row["source_alias"] == "library"
                and row["status"] in {"published", "canonical"} and actor != "owner"):
            raise KnowledgeError("Bản published chỉ owner được sửa.", 403, "published_locked")
        spec = self._spec(row["source_alias"])
        target = _resolve(spec, row["rel_path"])
        _assert_writable(content)
        _assert_no_secret(content)
        self._atomic_write(target, content)
        data = target.read_bytes()
        sha = _hash_bytes(data)
        stat = target.stat()
        meta, body = _front_matter(content)
        new_title = title or str(meta.get("title") or _first_heading(body, Path(row["rel_path"]).stem))
        new_category = category or row["category"] or spec.category
        new_tags = tags if tags is not None else json.loads(row["tags_json"] or "[]")
        version_no = int(row["version_no"] or 1) + 1
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE knowledge_docs SET title=?, category=?, tags_json=?, "
                "current_sha256=?, size_bytes=?, mtime=?, version_no=?, "
                "deleted_at=NULL, trashed_path=NULL, updated_at=? WHERE id=?",
                (new_title, new_category, json.dumps(new_tags, ensure_ascii=False),
                 sha, stat.st_size, stat.st_mtime, version_no, now(), row["id"]))
            self._insert_version(conn, row["id"], version_no, sha, new_title, actor, note, content)
            self._event(conn, row["id"], actor, action, row["current_sha256"], sha,
                        {"note": note})
        return self.get_doc(row["id"])

    def create_doc(self, *, source: str, path: str, content: str, actor: str,
                   title: str | None = None, category: str | None = None,
                   tags: list[str] | None = None, source_refs: list[str] | None = None,
                   overwrite: bool = False) -> dict[str, Any]:
        spec = self._spec(source)
        if spec.mode != "rw":
            raise KnowledgeError("Source này là read-only.", 403, "read_only")
        rel = _clean_rel(path)
        # check-then-write: hai agent cùng tạo một path sẽ cùng qua `exists()`
        # rồi cùng ghi. Lock biến nó thành "một thắng, một nhận 409".
        with self._lock:
            existing = self.store.query_one(
                "SELECT * FROM knowledge_docs WHERE source_alias=? AND rel_path=?",
                (spec.alias, rel))
            if existing:
                if not overwrite:
                    raise KnowledgeError("Tài liệu đã tồn tại.", 409, "doc_exists")
                return self._write_existing(existing, content, actor, title=title,
                                            category=category, tags=tags,
                                            note="create overwrite", action="updated")
            return self._insert_new(spec, rel, content, actor, title=title,
                                    category=category, tags=tags,
                                    source_refs=source_refs, note="create")

    def fork_doc(self, doc_id: str, actor: str, *, path: str | None = None,
                 title: str | None = None, category: str | None = None,
                 tags: list[str] | None = None) -> dict[str, Any]:
        source = self.get_doc(doc_id)
        spec = self._spec("library")
        rel = _clean_rel(path or f"forks/{source['source']}/{source['path']}")
        with self._lock:
            return self._insert_new(
                spec, rel, source["content"], actor,
                title=title or f"Tổng hợp: {source['title']}",
                category=category or "Tổng hợp",
                tags=tags or ["library", "fork", source["source"]],
                source_refs=[doc_id], status="draft", note=f"fork {doc_id}")

    def update_doc(self, doc_id: str, content: str, actor: str,
                   if_match: str | None, *, title: str | None = None,
                   category: str | None = None, tags: list[str] | None = None,
                   note: str = "update") -> dict[str, Any]:
        # Giữ lock qua cả check-ETag và ghi: nếu không, hai request cùng If-Match
        # đều qua được bước check rồi lần lượt ghi đè nhau (lost update).
        with self._lock:
            row = self._row(doc_id)
            if row["read_only"]:
                raise KnowledgeError("Tài liệu repo là read-only; hãy fork sang library.",
                                     403, "read_only")
            if not if_match:
                raise KnowledgeError("Thiếu If-Match (content hash).", 428, "missing_if_match")
            if not _etag_matches(if_match, row):
                raise KnowledgeError("Tài liệu đã bị sửa bởi người khác.", 412, "hash_mismatch")
            return self._write_existing(row, content, actor, title=title, category=category,
                                        tags=tags, note=note)

    def patch_doc(self, doc_id: str, *, mode: str, content: str, actor: str,
                  if_match: str | None, target_text: str | None = None,
                  replacement: str | None = None, note: str = "patch") -> dict[str, Any]:
        # Read-modify-write phải nằm trọn trong lock: append của hai agent chạy
        # song song mà không khoá sẽ nuốt mất một dòng.
        with self._lock:
            row = self._row(doc_id)
            if row["read_only"]:
                raise KnowledgeError("Tài liệu repo là read-only; hãy fork sang library.",
                                     403, "read_only")
            mode = (mode or "append").strip().lower()
            if mode in {"overwrite", "replace_text"} and not if_match:
                raise KnowledgeError("Thiếu If-Match cho thao tác ghi đè.", 428,
                                     "missing_if_match")
            if if_match and not _etag_matches(if_match, row):
                raise KnowledgeError("Tài liệu đã bị sửa bởi người khác.", 412, "hash_mismatch")
            current, _redacted = self._read_text(row)
            if mode == "append":
                sep = "" if not current or current.endswith("\n") else "\n"
                new_text = f"{current}{sep}{content}"
            elif mode == "prepend":
                sep = "" if not content or content.endswith("\n") else "\n"
                new_text = f"{content}{sep}{current}"
            elif mode == "replace_text":
                if not target_text:
                    raise KnowledgeError("Thiếu target_text.", 422, "target_text_required")
                if target_text not in current:
                    raise KnowledgeError("Không tìm thấy target_text.", 409, "target_not_found")
                new_text = current.replace(target_text, replacement or "", 1)
            elif mode == "overwrite":
                new_text = content
            else:
                raise KnowledgeError("mode phải là append|prepend|replace_text|overwrite.",
                                     422, "bad_mode")
            return self._write_existing(row, new_text, actor, note=note,
                                        action=f"patch:{mode}")

    def delete_doc(self, doc_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            row = self._row(doc_id)
            if row["read_only"]:
                raise KnowledgeError("Không xoá tài liệu repo qua API.", 403, "read_only")
            if row["status"] == "trashed" or row["deleted_at"]:
                raise KnowledgeError("Tài liệu đã ở trong trash.", 409, "already_trashed")
            spec = self._spec(row["source_alias"])
            target = _resolve(spec, row["rel_path"])
            if not target.is_file():
                raise KnowledgeError("File nguồn không còn tồn tại.", 404, "doc_not_found")
            trash_dir = library_root() / ".trash" / doc_id
            trash_dir.mkdir(parents=True, exist_ok=True)
            trashed = trash_dir / f"{int(now())}_{target.name}"
            shutil.move(str(target), str(trashed))
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE knowledge_docs SET status='trashed', deleted_at=?, trashed_path=?, "
                    "updated_at=? WHERE id=?",
                    (now(), str(trashed), now(), doc_id))
                self._event(conn, doc_id, actor, "deleted", row["current_sha256"], None,
                            {"trashed_to": str(trashed)})
            return {"deleted": True, "doc_id": doc_id, "trashed_to": str(trashed)}

    def restore_doc(self, doc_id: str, actor: str) -> dict[str, Any]:
        with self._lock:
            row = self._row(doc_id)
            if row["status"] != "trashed" or not row["trashed_path"]:
                raise KnowledgeError("Tài liệu không nằm trong trash.", 409, "not_trashed")
            spec = self._spec(row["source_alias"])
            target = _resolve(spec, row["rel_path"])
            if target.exists():
                raise KnowledgeError("Đường dẫn gốc đã có file khác.", 409, "doc_exists")
            src = Path(row["trashed_path"])
            if not src.is_file():
                raise KnowledgeError("File trong trash không còn.", 404, "trash_missing")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            status = "draft" if spec.alias == "library" else "published"
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE knowledge_docs SET status=?, deleted_at=NULL, trashed_path=NULL, "
                    "updated_at=? WHERE id=?", (status, now(), doc_id))
                self._event(conn, doc_id, actor, "restored", None, row["current_sha256"], {})
            return self.get_doc(doc_id)

    def publish_doc(self, doc_id: str, actor: str, status: str = "published") -> dict[str, Any]:
        if status not in {"published", "canonical"}:
            raise KnowledgeError("status phải là published|canonical.", 422, "bad_status")
        with self._lock:
            row = self._row(doc_id)
            if row["status"] == "trashed" or row["deleted_at"]:
                raise KnowledgeError("Không publish tài liệu đang nằm trong trash.",
                                     410, "doc_trashed")
            if row["read_only"]:
                raise KnowledgeError("Không publish tài liệu repo.", 403, "read_only")
            with self.store.transaction() as conn:
                conn.execute("UPDATE knowledge_docs SET status=?, updated_at=? WHERE id=?",
                             (status, now(), doc_id))
                self._event(conn, doc_id, actor, "published", row["current_sha256"],
                            row["current_sha256"], {"status": status})
            return self.get_doc(doc_id)

    # -- versions / stats ----------------------------------------------------
    def versions(self, doc_id: str) -> dict[str, Any]:
        self._row(doc_id)
        rows = self.store.query(
            "SELECT version_no,sha256,title,author,note,size_bytes,created_at "
            "FROM knowledge_versions WHERE doc_id=? ORDER BY version_no DESC", (doc_id,))
        return {"doc_id": doc_id, "versions": rows}

    def restore_version(self, doc_id: str, version_no: int, actor: str) -> dict[str, Any]:
        with self._lock:
            row = self._row(doc_id)
            ver = self.store.query_one(
                "SELECT * FROM knowledge_versions WHERE doc_id=? AND version_no=?",
                (doc_id, version_no))
            if not ver:
                raise KnowledgeError("Không tìm thấy version.", 404, "version_not_found")
            content = gzip.decompress(ver["content_z"]).decode("utf-8")
            return self._write_existing(row, content, actor, title=ver["title"],
                                        note=f"restore v{version_no}", action="version_restore")

    def stats(self) -> dict[str, Any]:
        self.ensure_synced()
        by_status = {r["status"]: int(r["n"]) for r in self.store.query(
            "SELECT status, COUNT(*) AS n FROM knowledge_docs GROUP BY status")}
        by_source = {r["source_alias"]: int(r["n"]) for r in self.store.query(
            "SELECT source_alias, COUNT(*) AS n FROM knowledge_docs "
            "WHERE deleted_at IS NULL GROUP BY source_alias")}
        dup_rows = self.store.query(
            "SELECT current_sha256, COUNT(*) AS n FROM knowledge_docs "
            "WHERE deleted_at IS NULL AND current_sha256 IS NOT NULL "
            "GROUP BY current_sha256 HAVING COUNT(*) > 1")
        total_bytes = self.store.query_one(
            "SELECT COALESCE(SUM(size_bytes),0) AS n FROM knowledge_docs "
            "WHERE deleted_at IS NULL")["n"]
        return {
            "total_docs": sum(by_source.values()),
            "total_bytes": int(total_bytes or 0),
            "by_status": by_status,
            "by_source": by_source,
            "duplicate_groups": len(dup_rows),
            "duplicates": [{"sha256": r["current_sha256"], "count": int(r["n"])}
                           for r in dup_rows[:20]],
            "last_sync_at": self._last_sync,
        }
