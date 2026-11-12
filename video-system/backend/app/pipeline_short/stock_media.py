"""Stock media connector — ảnh + video b-roll cho Short quốc tế.

Primary: **Pixabay API** (ảnh + video, free thương mại, key cấp ngay sau đăng
ký; Pexels đã tạm dừng cấp key mới — paused 2026-11). Fallback: **Pexels API**
cho tài khoản đã có key từ trước.

- API key: env `VIDEO_PIXABAY_API_KEY` / `VIDEO_PEXELS_API_KEY`. Thiếu cả hai →
  mọi hàm trả None/[] (honest, pipeline fallback sang nguồn khác, không chết).
- License cả hai: free thương mại, credit photographer nên ghi — trả về
  `attribution` để scene metadata lưu lại.
- Pixabay: ảnh lọc `orientation=vertical`; video chọn variant dọc (height>width)
  vừa đủ HD. Rate limit 100 req/phút — cache download theo sha1(url).
- Cache tại data_dir()/shorts_bank/pexels/{photos,videos}/ (giữ nguyên thư mục
  cũ — tên mang tính lịch sử, dùng chung cho cả 2 provider).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from ..config import data_dir

logger = logging.getLogger("video_system.stock_media")

_PIXABAY_IMAGES = "https://pixabay.com/api/"
_PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
_PEXELS_PHOTOS = "https://api.pexels.com/v1/search"
_PEXELS_VIDEOS = "https://api.pexels.com/videos/search"
_TIMEOUT_S = 30.0
_MAX_VIDEO_WIDTH = 1080          # portrait 1080x1920 — đủ render, đỡ nặng
_VIDEO_EXT = {".mp4"}


def pixabay_key() -> str:
    return os.environ.get("VIDEO_PIXABAY_API_KEY", "").strip()


def pexels_key() -> str:
    return os.environ.get("VIDEO_PEXELS_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(pixabay_key() or pexels_key())


def _http_get(url: str, params: dict | None = None,
              headers: dict | None = None) -> bytes:
    """Tách module-level để test monkeypatch được."""
    import httpx
    with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers=headers or {})
    resp.raise_for_status()
    return resp.content


# Stopword EN + VI phổ biến — từ chức năng không phải ý chính, làm bẩn search stock
_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can",
    "her", "was", "one", "our", "out", "day", "get", "has", "had", "how",
    "man", "new", "now", "old", "see", "two", "way", "who", "did", "its",
    "let", "she", "too", "use", "that", "with", "have", "this", "will",
    "your", "from", "they", "them", "their", "there", "here", "than",
    "then", "when", "what", "where", "why", "which", "into", "over",
    "after", "before", "between", "during", "about", "been", "were",
    "more", "most", "some", "such", "only", "same", "very", "just",
    "also", "each", "other", "later", "early", "still", "being", "make",
    "made", "said", "says", "would", "could", "should", "may", "might",
    "must", "shall", "does", "doing", "done", "upon", "among", "along",
    "các", "những", "và", "của", "cho", "là", "đã", "được", "theo", "một",
    "nhưng", "nếu", "khi", "tại", "trong", "ra", "về", "này", "kia", "bị",
    "sẽ", "mà", "thì", "có", "không", "phải", "hơn", "từ", "cả", "hai",
}


def keywords_for_query(text: str, max_words: int = 6) -> str:
    """Trích keyword tìm kiếm từ câu thoại scene: bỏ dấu câu + stopword
    (từ chức năng EN/VI), giữ tối đa ~max_words từ mang ý chính.
    "The bank later confirmed a gang posing" → "bank gang posing"."""
    clean = re.sub(r"[^0-9A-Za-zÀ-ỹ\s]", " ", str(text or ""), flags=re.UNICODE)
    words = []
    for w in clean.split():
        lw = w.lower()
        if len(w) <= 2 or lw in _STOPWORDS:
            continue
        words.append(w)
        if len(words) >= max_words:
            break
    return " ".join(words)


def _cache_dir(kind: str) -> Path:
    d = data_dir() / "shorts_bank" / "pexels" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(kind: str, url: str, ext: str) -> Path:
    name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext
    return _cache_dir(kind) / name


def _download(url: str, kind: str, ext: str) -> Path | None:
    out = _cache_path(kind, url, ext)
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        data = _http_get(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock download %s failed: %s", url[:100], exc)
        return None
    if not data:
        return None
    out.write_bytes(data)
    return out


# --------------------------------------------------------------------- Pixabay
def _pixabay_search_json(base_url: str, params: dict) -> dict | None:
    key = pixabay_key()
    if not key:
        return None
    try:
        data = _http_get(base_url, params={**params, "key": key,
                                           "safesearch": "true"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("pixabay search failed: %s", exc)
        return None
    import json
    try:
        return json.loads(data)
    except ValueError:
        return None


def _pixabay_photo(query: str) -> dict | None:
    """Ảnh dọc Pixabay — hit đầu tiên có largeImageURL. None nếu hết kết quả."""
    payload = _pixabay_search_json(_PIXABAY_IMAGES, {
        "q": query.strip(), "image_type": "photo", "orientation": "vertical",
        "per_page": 5,
    })
    if not payload:
        return None
    for hit in payload.get("hits") or []:
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not url:
            continue
        user = str((hit.get("user") or "Pixabay"))
        path = _download(url, "photos", ".jpg")
        if path:
            return {"path": path, "kind": "image",
                    "attribution": f"Image: {user} / Pixabay"}
    return None


def _pick_pixabay_video_variant(variants: dict) -> dict | None:
    """Chọn variant mp4: ưu tiên DỌC (height>width, ≤_MAX_VIDEO_WIDTH, lớn nhất);
    không có variant dọc → fallback variant NGANG ≤_MAX_VIDEO_WIDTH (assembler
    crop-fill 9:16 sẽ cắt viền); cùng hướng vẫn không có → smallest bất kỳ.
    (Thực tế nhiều video Pixabay chỉ có variant ngang — fallback bắt buộc.)"""
    mp4s = [v for v in (variants or {}).values()
            if isinstance(v, dict) and str(v.get("url") or "").endswith(".mp4")
            and v.get("width") and v.get("height")]
    if not mp4s:
        return None
    vertical = [v for v in mp4s if int(v["height"]) > int(v["width"])
                and int(v["width"]) <= _MAX_VIDEO_WIDTH]
    if vertical:
        return max(vertical, key=lambda v: int(v["width"]))
    horiz_fit = [v for v in mp4s if int(v["width"]) <= _MAX_VIDEO_WIDTH]
    if horiz_fit:
        return max(horiz_fit, key=lambda v: int(v["width"]))
    return min(mp4s, key=lambda v: int(v["width"]))


def _pixabay_video(query: str) -> dict | None:
    payload = _pixabay_search_json(_PIXABAY_VIDEOS, {"q": query.strip(),
                                                     "per_page": 5})
    if not payload:
        return None
    for hit in payload.get("hits") or []:
        best = _pick_pixabay_video_variant(hit.get("videos") or {})
        if not best:
            continue
        user = hit.get("user")
        if isinstance(user, dict):  # API ảnh trả dict, video trả string
            user = user.get("name")
        path = _download(best["url"], "videos", ".mp4")
        if path:
            return {"path": path, "kind": "video_clip",
                    "attribution": f"Video: {user or 'Pixabay'} / Pixabay"}
    return None


# --------------------------------------------------------------------- Pexels
def _pexels_search_json(base_url: str, params: dict) -> dict | None:
    key = pexels_key()
    if not key:
        return None
    try:
        data = _http_get(base_url, params=params,
                         headers={"Authorization": key})
    except Exception as exc:  # noqa: BLE001
        logger.warning("pexels search failed: %s", exc)
        return None
    import json
    try:
        return json.loads(data)
    except ValueError:
        return None


def _pexels_photo(query: str) -> dict | None:
    payload = _pexels_search_json(_PEXELS_PHOTOS, {
        "query": query.strip(), "per_page": 3, "orientation": "portrait"})
    if not payload:
        return None
    for ph in payload.get("photos") or []:
        src = (ph.get("src") or {}).get("large2x") or (ph.get("src") or {}).get("original")
        if not src:
            continue
        path = _download(src, "photos", ".jpg")
        if path:
            return {"path": path, "kind": "image",
                    "attribution": f"Photo: {ph.get('photographer')} / Pexels"}
    return None


def _pick_pexels_video_file(files: list[dict]) -> dict | None:
    """Chọn 1 file mp4: chất lượng vừa đủ (≤_MAX_VIDEO_WIDTH, ưu tiên cao nhất)."""
    mp4s = [f for f in files
            if str(f.get("file_type") or "").endswith("mp4") and f.get("link")]
    if not mp4s:
        return None
    fits = [f for f in mp4s if int(f.get("width") or 0) <= _MAX_VIDEO_WIDTH]
    pool = fits or mp4s
    return max(pool, key=lambda f: int(f.get("width") or 0))


def _pexels_video(query: str) -> dict | None:
    payload = _pexels_search_json(_PEXELS_VIDEOS, {
        "query": query.strip(), "per_page": 3, "orientation": "portrait"})
    if not payload:
        return None
    for vid in payload.get("videos") or []:
        best = _pick_pexels_video_file(vid.get("video_files") or [])
        if not best:
            continue
        user = str((vid.get("user") or {}).get("name") or "Pexels")
        ext = Path(best["link"]).suffix.lower() or ".mp4"
        if ext not in _VIDEO_EXT:
            ext = ".mp4"
        path = _download(best["link"], "videos", ext)
        if path:
            return {"path": path, "kind": "video_clip",
                    "attribution": f"Video: {user} / Pexels"}
    return None


# ---------------------------------------------------------------- public API
def fetch_photo(query: str) -> dict | None:
    """Tải 1 ảnh dọc theo query: Pixabay trước, Pexels fallback. None nếu hết."""
    if not query.strip():
        return None
    return _pixabay_photo(query) or _pexels_photo(query)


def fetch_video(query: str) -> dict | None:
    """Tải 1 video b-roll dọc theo query: Pixabay trước, Pexels fallback."""
    if not query.strip():
        return None
    return _pixabay_video(query) or _pexels_video(query)
