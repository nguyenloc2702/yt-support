"""Lấy ảnh gốc từ bài báo cho Short (Phụ lục G.11).

Luồng: link (Google News hoặc trực tiếp) → resolve URL bài thật
(decode base64 link Google News, fallback follow-redirect) → fetch HTML →
extract og:image / twitter:image + ảnh trong body → download → kiểm PIL
(≥320px, decode được) → trả list đường dẫn (og-first).

Mọi fetch có timeout + UA; lỗi từng ảnh không chặn ảnh khác; lỗi toàn cụm
trả list rỗng — caller fallback về bank/AI-gen (honest, không placeholder).
"""
from __future__ import annotations

import base64
import html as html_mod
import json
import logging
import re
import urllib.parse
from pathlib import Path

import httpx

logger = logging.getLogger("video_system.pipeline_short.article_images")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_FETCH_TIMEOUT_S = 20.0
_DOWNLOAD_TIMEOUT_S = 30.0
_MIN_DIM = 320
_MAX_IMAGES = 8
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
_SKIP_PAT = re.compile(
    r"(logo|icon|sprite|avatar|favicon|placeholder|banner-?ad|ads?[/_.-]|1x1|pixel)",
    re.IGNORECASE,
)
_URL_CHAR = r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]"


def _resolve_google_news_api(article_id: str) -> str | None:
    """Định dạng Google News mới (AU_yqL/…) không chứa URL trong base64 —
    phải gọi batchexecute nội bộ với ts+sg lấy từ trang articles/<id>."""
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            page = client.get(f"https://news.google.com/rss/articles/{article_id}")
            ts = re.search(r'data-n-a-ts="([^"]+)"', page.text)
            sg = re.search(r'data-n-a-sg="([^"]+)"', page.text)
            if not ts or not sg:
                return None
            inner = (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                'null,null,null,null,null,0,1],"X","X",1,[1,1],"X",1,1,null,0,0,'
                f'null,0],"{article_id}",{ts.group(1)},"{sg.group(1)}"]'
            )
            freq = "f.req=" + urllib.parse.quote(
                json.dumps([["Fbv4je", inner, "null", "generic"]]))
            resp = client.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                content=freq,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            )
            resp.raise_for_status()
        for line in resp.text.splitlines():
            if "garturlres" not in line:
                continue
            arr = json.loads(line)
            payload = json.loads(arr[0][2])
            url = payload[1]
            if url.startswith("http"):
                return url
    except Exception as exc:  # noqa: BLE001
        logger.debug("batchexecute decode failed: %s", exc)
    return None


_JUNK_HOSTS = ("w3.org", "schema.org", "duckduckgo", "google.", "gstatic",
               "bing.com", "microsoft.com", "facebook.com", "twitter.com",
               "x.com", "youtube.com", "tiktok.com", "instagram.com")


def _is_junk_host(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    return any(host.endswith(j) or j in host for j in _JUNK_HOSTS)


def _resolve_via_search(title: str) -> str | None:
    """Tìm URL bài thật theo tiêu đề qua DuckDuckGo HTML (G.11 — ổn định hơn
    hack batchexecute của Google News, không cần API key)."""
    q = urllib.parse.quote_plus((title or "").strip()[:160])
    if not q:
        return None
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(f"https://html.duckduckgo.com/html/?q={q}")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("DDG search failed: %s", exc)
        return None
    # 1) anchor kết quả thật của DDG (<a class="result__a" href=...>)
    for m in re.finditer(
            r'<a[^>]+class=["\'][^"\']*result__a[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
            resp.text, re.IGNORECASE):
        href = html_mod.unescape(m.group(1))
        if "uddg=" in href:
            u = re.search(r"uddg=([^&]+)", href)
            if u:
                href = urllib.parse.unquote(u.group(1))
        if href.startswith("http") and not _is_junk_host(href):
            return href
    # 2) fallback: link gián tiếp uddg= đầu tiên
    for m in re.finditer(r"uddg=([A-Za-z0-9%._~:/?#\[\]@!$&'()*+,;=%-]+)", resp.text):
        url = urllib.parse.unquote(m.group(1))
        if url.startswith("http") and not _is_junk_host(url):
            return url
    return None


def resolve_article_url(url: str, title_hint: str = "") -> str:
    """Link Google News → URL bài thật; link thường trả nguyên.

    Tầng: base64 (format cũ) → batchexecute → follow-redirect → search theo
    tiêu đề (DDG) nếu còn kẹt ở news.google.com."""
    url = (url or "").strip()
    if "news.google.com" not in urllib.parse.urlsplit(url).netloc:
        return url
    seg = url.split("/articles/")[-1].split("?")[0].strip("/")
    if seg:
        try:
            pad = "=" * (-len(seg) % 4)
            raw = base64.urlsafe_b64decode(seg + pad)
            text = raw.decode("latin-1", errors="ignore")
            cands = [m for m in re.findall(r"https?://" + _URL_CHAR + "+", text)
                     if "google." not in m and "gstatic." not in m]
            if cands:
                return max(cands, key=len).rstrip(".,;)")
        except Exception as exc:  # noqa: BLE001
            logger.debug("base64 decode Google News link failed: %s", exc)
        # Định dạng mới: thử batchexecute nội bộ (Google hay đổi giao thức)
        resolved = _resolve_google_news_api(seg)
        if resolved:
            return resolved
    # fallback: follow redirect thật
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
        final = str(resp.url)
        if "news.google.com" not in urllib.parse.urlsplit(final).netloc:
            return final
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve redirect failed for %s: %s", url[:80], exc)
    # cuối: tìm theo tiêu đề
    if title_hint:
        via_search = _resolve_via_search(title_hint)
        if via_search:
            return via_search
    return url


def _absolutize(src: str, base_url: str) -> str | None:
    src = html_mod.unescape((src or "").strip())
    if not src or src.startswith("data:"):
        return None
    if src.endswith(".svg") or ".svg" in src.split("?")[0]:
        return None
    return urllib.parse.urljoin(base_url, src)


def extract_article_images(html_text: str, page_url: str) -> list[str]:
    """Trích URL ảnh ứng viên từ HTML: og/twitter image trước, ảnh body sau."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if not u:
            return
        u = u.split()[0]  # srcset "url 2x, url 1x" → lấy phần đầu
        if u in seen or _SKIP_PAT.search(u):
            return
        if not u.lower().split("?")[0].endswith(_IMG_EXT):
            return
        seen.add(u)
        urls.append(u)

    for pattern in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r"<meta[^>]+name=['\"]twitter:image(?::src)?['\"][^>]+content=['\"]([^'\"]+)['\"]",
    ):
        m = re.search(pattern, html_text, re.IGNORECASE)
        if m:
            add(m.group(1))

    for m in re.finditer(r"<img[^>]+>", html_text, re.IGNORECASE):
        tag = m.group(0)
        attr = re.search(
            r'(?:data-src|data-original|data-lazy-src|src)=["\']([^"\']+)["\']',
            tag, re.IGNORECASE)
        if attr:
            add(_absolutize(attr.group(1), page_url))

    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out[: _MAX_IMAGES * 2]


def _looks_valid_image(data: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.width, img.height
    except Exception:  # noqa: BLE001
        return None


def collect_article_images(source_link: str, out_dir: Path,
                           limit: int = 6, title_hint: str = "") -> list[Path]:
    """Cụm đầy đủ: resolve → fetch HTML → extract → download → kiểm PIL.

    Trả danh sách file ảnh hợp lệ (og-first). Lỗi toàn cụm → list rỗng.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    article_url = resolve_article_url(source_link, title_hint=title_hint)
    try:
        with httpx.Client(timeout=_FETCH_TIMEOUT_S, follow_redirects=True,
                          headers={"User-Agent": _UA}) as client:
            resp = client.get(article_url)
        resp.raise_for_status()
        page_html = resp.text
        page_url = str(resp.url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch article failed (%s): %s", article_url[:80], exc)
        return []

    candidates = extract_article_images(page_html, page_url)
    got: list[Path] = []
    for u in candidates:
        if len(got) >= max(1, limit):
            break
        try:
            with httpx.Client(timeout=_DOWNLOAD_TIMEOUT_S, follow_redirects=True,
                              headers={"User-Agent": _UA}) as client:
                dl = client.get(u)
            dl.raise_for_status()
            if len(dl.content) < 5_000:
                continue
            dims = _looks_valid_image(dl.content)
            if not dims or dims[0] < _MIN_DIM or dims[1] < _MIN_DIM:
                continue
            ext = ".jpg" if dims else ".png"
            if u.lower().endswith(".png"):
                ext = ".png"
            elif u.lower().endswith(".webp"):
                ext = ".webp"
            else:
                ext = ".jpg"
            path = out_dir / f"article_{len(got) + 1:02d}{ext}"
            path.write_bytes(dl.content)
            got.append(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("download article image failed (%s): %s", u[:80], exc)
    logger.info("article images collected: %d/%d candidates from %s",
                len(got), len(candidates), article_url[:80])
    return got
