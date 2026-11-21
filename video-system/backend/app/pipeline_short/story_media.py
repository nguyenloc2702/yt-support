"""Strict-relevance multi-image collector cho từng tin trong Daily Digest.

Luật tuyệt đối: CHỈ đưa ảnh vào nếu thực sự liên quan tới tin đó:
1. Ảnh gốc từ chính link bài báo (`article_images`).
2. Wikipedia PageImages (đúng thực thể/địa điểm/nhân vật của tin) +
   Wikimedia Commons (lọc khớp từ khóa tiêu đề/mô tả + bỏ logo/flag/svg/banner).
3. Kiểm PIL (>= 420px, tỷ lệ khung hợp lý) + dedupe theo sha1 bytes.
Không bao giờ nhét ảnh ngẫu nhiên không liên quan.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path

from . import article_images

logger = logging.getLogger("video_system.pipeline_short.story_media")

_UA = "video-system/0.2 (+https://vidfac.gacdero.vn; editorial-news-digest)"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_TIMEOUT_S = 15.0
_MIN_SIDE = 420

_NOISE_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "after",
    "amid", "says", "said", "will", "new", "more", "than", "about", "what", "when",
    "how", "why", "are", "was", "were", "has", "have", "had", "not", "but", "who",
    "news", "world", "global", "today", "report", "reports", "update", "breaking",
    "photo", "image", "file", "jpg", "jpeg", "png", "webp", "svg", "wikimedia",
    "commons", "official", "officials", "company", "market", "markets", "billion",
    "million", "percent", "year", "years", "day", "month", "week",
}

_REJECT_ASSET_RE = re.compile(
    r"(flag[_\s-]of|coat[_\s-]of[_\s-]arms|seal[_\s-]of|emblem[_\s-]of|"
    r"locator[_\s-]map|map[_\s-]of|insignia|logo|icon|favicon|avatar|badge|"
    r"signature[_\s-]of|wordmark|diagram|chart|stub)",
    re.IGNORECASE,
)


def _anchor_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {w for w in words if w not in _NOISE_WORDS}


def is_relevant_candidate(candidate_text: str, query: str, headline: str = "") -> bool:
    """Kiểm tra tiêu đề/mô tả ảnh ứng viên có thực sự khớp thực thể/nội dung tin không."""
    if not candidate_text or _REJECT_ASSET_RE.search(candidate_text):
        return False
    cand_toks = _anchor_tokens(candidate_text)
    if not cand_toks:
        return False
    q_ordered = [
        w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())
        if w not in _NOISE_WORDS
    ]
    q_toks = set(q_ordered)
    h_toks = _anchor_tokens(headline)
    target_toks = q_toks or h_toks
    if not target_toks:
        return False

    q_overlap = len(cand_toks & q_toks) if q_toks else 0
    h_overlap = len(cand_toks & h_toks) if h_toks else 0

    # Nếu query bắt đầu bằng tên thực thể/thương hiệu (vd: "Nvidia headquarters Santa Clara"),
    # buộc ảnh phải có từ khóa đầu tiên (tránh khớp nhầm địa danh như "Mission Santa Clara").
    if len(q_ordered) >= 3 and q_ordered[0] not in cand_toks and q_overlap < 3:
        return False

    if len(q_toks) == 1:
        return q_overlap >= 1
    if len(q_toks) >= 2:
        # Khớp >= 2 từ khóa của query, hoặc >= 1 từ khóa query + >= 2 từ khóa headline
        return q_overlap >= 2 or (q_overlap >= 1 and h_overlap >= 2)
    return h_overlap >= 2


def _http_get_json(url: str, params: dict) -> dict | None:
    import httpx
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("API fetch failed (%s): %s", url, exc)
        return None


def _download_valid_image(url: str, out_path: Path, seen_hashes: set[str]) -> Path | None:
    import httpx
    from PIL import Image
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content
    except Exception as exc:  # noqa: BLE001
        logger.debug("image download failed (%s): %s", url[:90], exc)
        return None
    if len(raw) < 8_000:
        return None
    digest = hashlib.sha1(raw).hexdigest()
    if digest in seen_hashes:
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        w, h = im.size
    except Exception:  # noqa: BLE001
        return None
    if min(w, h) < _MIN_SIDE:
        return None
    ratio = w / float(max(1, h))
    if ratio < 0.42 or ratio > 2.6:
        return None
    seen_hashes.add(digest)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return out_path


def _search_wikipedia_image(query: str, headline: str) -> list[dict]:
    """Tìm ảnh đại diện từ bài Wikipedia khớp đúng thực thể/sự kiện trong query."""
    data = _http_get_json(_WIKI_API, {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": 5,
        "prop": "pageimages|info",
        "piprop": "thumbnail|original",
        "pithumbsize": 1280,
        "format": "json",
    })
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out: list[dict] = []
    for p in sorted(pages.values(), key=lambda x: int(x.get("index") or 99)):
        title = str(p.get("title") or "")
        orig = (p.get("original") or {}).get("source") or (p.get("thumbnail") or {}).get("source")
        if not orig or _REJECT_ASSET_RE.search(orig):
            continue
        low_url = orig.lower().split("?")[0]
        if not low_url.endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        if not is_relevant_candidate(title, query, headline):
            continue
        out.append({"url": orig, "attribution": f"Wikipedia: {title}", "source": "wikipedia"})
    return out


def _search_commons_images(query: str, headline: str, limit: int = 4) -> list[dict]:
    """Tìm ảnh chụp thực tế trên Wikimedia Commons với kiểm tra độ khớp từ khóa."""
    data = _http_get_json(_COMMONS_API, {
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": query,
        "gsrlimit": 8,
        "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiurlwidth": 1280,
        "format": "json",
    })
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    out: list[dict] = []
    for p in sorted(pages.values(), key=lambda x: int(x.get("index") or 99)):
        title = str(p.get("title") or "")
        infos = p.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "").lower()
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            continue
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        if min(w, h) < _MIN_SIDE:
            continue
        ext_meta = info.get("extmetadata") or {}
        desc = re.sub(r"<[^>]+>", " ", str((ext_meta.get("ImageDescription") or {}).get("value") or ""))
        obj_name = str((ext_meta.get("ObjectName") or {}).get("value") or "")
        cand_text = f"{title} {obj_name} {desc[:200]}"
        if not is_relevant_candidate(cand_text, query, headline):
            continue
        url = str(info.get("thumburl") or info.get("url") or "")
        if not url:
            continue
        out.append({"url": url, "attribution": f"Wikimedia Commons: {title.removeprefix('File:')[:60]}", "source": "commons"})
        if len(out) >= limit:
            break
    return out


def _headline_fallback_query(headline: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", headline or "") if w.lower() not in _NOISE_WORDS]
    return " ".join(words[:4])


def collect_story_visuals(
    *,
    article_link: str,
    headline: str,
    queries: list[str],
    out_dir: Path,
    target_images: int = 3,
    seen_hashes: set[str] | None = None,
) -> list[dict]:
    """Thu thập tối đa `target_images` ảnh LIÊN QUAN CHẶT CHẼ cho 1 tin.

    Thứ tự: ảnh gốc từ link bài báo → Wikipedia PageImages → Wikimedia Commons.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    hashes = seen_hashes if seen_hashes is not None else set()
    collected: list[dict] = []

    # 1) Ảnh gốc từ bài báo (tối đa 3 ảnh)
    if article_link and article_link.strip():
        try:
            art_paths = article_images.collect_article_images(
                article_link.strip(),
                out_dir / "article",
                limit=target_images,
                title_hint=headline,
            )
            for p in art_paths:
                try:
                    raw = p.read_bytes()
                except OSError:
                    continue
                dig = hashlib.sha1(raw).hexdigest()
                if dig in hashes:
                    continue
                hashes.add(dig)
                collected.append({
                    "path": p,
                    "attribution": "Article original photo",
                    "source": "article",
                })
                if len(collected) >= target_images:
                    return collected
        except Exception as exc:  # noqa: BLE001
            logger.warning("article images collect failed (%s): %s", article_link[:80], exc)

    # 2) Wikipedia + Wikimedia Commons theo từng query thực thể cụ thể
    clean_queries = [str(q).strip() for q in (queries or []) if str(q).strip()]
    fb_q = _headline_fallback_query(headline)
    if fb_q and fb_q.lower() not in {q.lower() for q in clean_queries}:
        clean_queries.append(fb_q)

    seq_i = len(collected) + 1
    for q in clean_queries:
        if len(collected) >= target_images:
            break
        candidates = _search_wikipedia_image(q, headline) + _search_commons_images(q, headline, limit=3)
        for cand in candidates:
            if len(collected) >= target_images:
                break
            ext = ".png" if ".png" in cand["url"].lower() else ".jpg"
            dst = out_dir / f"rel_{seq_i:02d}{ext}"
            saved = _download_valid_image(cand["url"], dst, hashes)
            if saved:
                collected.append({
                    "path": saved,
                    "attribution": cand["attribution"],
                    "source": cand["source"],
                })
                seq_i += 1

    return collected
