"""CRUD service for the `read_me` knowledge/docs/logs folder."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

ALLOWED_DOC_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".log"}
MAX_DOC_BYTES = 2 * 1024 * 1024  # 2MB per doc


def get_read_me_dir() -> Path:
    env_dir = os.environ.get("VIDEO_READ_ME_DIR", "").strip()
    if env_dir:
        d = Path(env_dir)
    else:
        d = REPO_ROOT / "read_me"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ReadMeService:
    """Safe file-backed CRUD for `read_me/` docs, clip guides, API contracts, and logs."""

    @staticmethod
    def _resolve_safe_path(rel_path: str) -> Path:
        raw = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not raw or ".." in raw.split("/"):
            raise ValueError("Invalid doc path")
        p = Path(raw)
        if p.suffix.lower() not in ALLOWED_DOC_EXTS:
            raise ValueError(f"Unsupported extension '{p.suffix}'. Allowed: {', '.join(sorted(ALLOWED_DOC_EXTS))}")
        root = get_read_me_dir().resolve()
        target = (root / p).resolve()
        if root not in target.parents:
            raise ValueError("Path escapes read_me directory")
        return target

    @staticmethod
    def _meta(root: Path, file_path: Path, include_content: bool = False) -> dict[str, Any]:
        stat = file_path.stat()
        rel = file_path.relative_to(root).as_posix()
        text = file_path.read_text(encoding="utf-8", errors="replace")
        first_line = ""
        for ln in text.splitlines():
            s = ln.strip().lstrip("#").strip()
            if s:
                first_line = s[:120]
                break
        out: dict[str, Any] = {
            "path": rel,
            "name": file_path.name,
            "title": first_line or file_path.stem,
            "size_bytes": stat.st_size,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "updated_at": stat.st_mtime,
        }
        if include_content:
            out["content"] = text
        return out

    def list_docs(self, *, prefix: str | None = None, q: str | None = None) -> dict[str, Any]:
        root = get_read_me_dir().resolve()
        docs: list[dict[str, Any]] = []
        norm_prefix = (prefix or "").strip().replace("\\", "/").lstrip("/").lower()
        norm_q = (q or "").strip().lower()
        for f in sorted(root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in ALLOWED_DOC_EXTS:
                continue
            rel = f.relative_to(root).as_posix()
            if norm_prefix and not rel.lower().startswith(norm_prefix):
                continue
            if norm_q:
                content_low = f.read_text(encoding="utf-8", errors="replace").lower()
                if norm_q not in rel.lower() and norm_q not in content_low:
                    continue
            docs.append(self._meta(root, f, include_content=False))
        return {"read_me_dir": str(root), "total": len(docs), "docs": docs}

    def get_doc(self, rel_path: str) -> dict[str, Any]:
        root = get_read_me_dir().resolve()
        target = self._resolve_safe_path(rel_path)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        return self._meta(root, target, include_content=True)

    def create_doc(self, rel_path: str, content: str, *, overwrite: bool = False) -> dict[str, Any]:
        root = get_read_me_dir().resolve()
        target = self._resolve_safe_path(rel_path)
        if len(content.encode("utf-8")) > MAX_DOC_BYTES:
            raise ValueError("Content exceeds 2MB limit")
        if target.exists() and not overwrite:
            raise FileExistsError(f"Doc '{rel_path}' already exists (pass overwrite=true to replace)")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self._meta(root, target, include_content=True)

    def put_doc(self, rel_path: str, content: str) -> dict[str, Any]:
        return self.create_doc(rel_path, content, overwrite=True)

    def patch_doc(
        self,
        rel_path: str,
        *,
        mode: str = "append",
        content: str = "",
        target_text: str | None = None,
        replacement: str | None = None,
    ) -> dict[str, Any]:
        root = get_read_me_dir().resolve()
        target = self._resolve_safe_path(rel_path)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        cur = target.read_text(encoding="utf-8", errors="replace")
        m = (mode or "append").strip().lower()
        if m == "append":
            sep = "" if (not cur or cur.endswith("\n")) else "\n"
            new_text = f"{cur}{sep}{content}"
        elif m == "prepend":
            sep = "" if (not content or content.endswith("\n")) else "\n"
            new_text = f"{content}{sep}{cur}"
        elif m == "replace_text":
            if not target_text:
                raise ValueError("target_text is required for mode='replace_text'")
            if target_text not in cur:
                raise ValueError("target_text not found in document")
            new_text = cur.replace(target_text, replacement or "", 1)
        elif m == "overwrite":
            new_text = content
        else:
            raise ValueError("mode must be one of: append | prepend | replace_text | overwrite")

        if len(new_text.encode("utf-8")) > MAX_DOC_BYTES:
            raise ValueError("Updated content exceeds 2MB limit")
        target.write_text(new_text, encoding="utf-8")
        return self._meta(root, target, include_content=True)

    def delete_doc(self, rel_path: str) -> dict[str, Any]:
        target = self._resolve_safe_path(rel_path)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        target.unlink()
        return {"deleted": True, "path": rel_path.strip().replace("\\", "/").lstrip("/")}
