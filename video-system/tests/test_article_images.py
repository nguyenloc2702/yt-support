"""article_images (G.11) + push_selected (G.10 sửa đổi) + blur-pad compose."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.app import service_news
from backend.app.config import data_dir
from backend.app.pipeline_short import article_images, assembler

_ARTICLE_HTML = """
<html><head>
<meta property="og:image" content="https://img.example/photos/hero-main.jpg">
<meta name="twitter:image" content="https://img.example/photos/hero-main.jpg?w=1200">
</head><body>
<img src="/static/logo.png"/>
<img src="https://img.example/photos/scene-1.jpg"/>
<img src="data:image/png;base64,AAAA"/>
<img data-lazy-src="https://cdn.example.vn/pic/xuong-may.webp"/>
<img src="https://img.example/ad/banner-ad.jpg"/>
</body></html>"""


def test_extract_article_images_order_and_filters():
    urls = article_images.extract_article_images(_ARTICLE_HTML, "https://tuoitre.example/bai-viet")
    # og:image đầu tiên, logo + data-uri + banner-ad bị loại
    assert urls[0].startswith("https://img.example/photos/hero-main.jpg")
    assert "https://img.example/photos/scene-1.jpg" in urls
    assert "https://cdn.example.vn/pic/xuong-may.webp" in urls
    assert not any("logo" in u or "banner-ad" in u or u.startswith("data:") for u in urls)


def test_resolve_article_url_direct_passthrough():
    assert article_images.resolve_article_url(
        "https://tuoitre.example/bai-viet-123") == "https://tuoitre.example/bai-viet-123"


def test_resolve_article_url_decodes_google_news_base64():
    import base64
    inner = "https://tuoitre.example/tin-that-2026.html"
    seg = base64.urlsafe_b64encode(
        b"\x12\x20" + inner.encode("utf-8")).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/{seg}?oc=5"
    resolved = article_images.resolve_article_url(url)
    assert resolved == inner


def test_collect_article_images_downloads_and_validates(tmp_path, monkeypatch):
    # JPEG "nhiễu" đủ lớn (>5KB floor) cho PIL decode thật
    from PIL import Image
    import io
    import random
    rnd = random.Random(42)
    buf = io.BytesIO()
    img = Image.new("RGB", (640, 360))
    img.putdata([(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
                 for _ in range(640 * 360)])
    img.save(buf, format="JPEG", quality=90)
    real_jpg = buf.getvalue()

    class FakeClient:
        def __init__(self, *a, **k): pass

        def __enter__(self): return self

        def __exit__(self, *a): return False

        def get(self, url, **k):
            class R:
                def __init__(self, text=None, content=None, url=None, status=200):
                    self._text, self._content, self._url, self.status = text, content, url, status

                def raise_for_status(self):
                    assert self.status == 200

                @property
                def text(self): return self._text

                @property
                def content(self): return self._content

                @property
                def url(self): return self._url
            if "tuoitre" in url or "articles/" in url:
                return R(text=_ARTICLE_HTML, url=url)
            return R(content=real_jpg)

    monkeypatch.setattr(article_images.httpx, "Client", FakeClient)
    got = article_images.collect_article_images(
        "https://tuoitre.example/bai", tmp_path / "article_images", limit=4)
    assert len(got) >= 1
    assert all(p.exists() and p.stat().st_size > 5000 for p in got)
    # og-image (hero-main) được tải trước ảnh body
    assert "article_01" in got[0].name


def test_push_selected_news_single(store_fixture, monkeypatch):
    # seed 1 tin
    store_fixture.execute(
        "INSERT OR IGNORE INTO news_items (id, link_hash, source, title, link, "
        "summary, query, published_at, fetched_at, status) VALUES (?,?,?,?,?,?,?,?,?,'new')",
        ("nws_push_test_1", "hash-push-1", "VnExpress", "Tin chọn push",
         "https://vnexpress.example/tin-1", "tóm tắt", "q", None, 1.0))
    monkeypatch.setenv("VIDEO_TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("VIDEO_TELEGRAM_CHAT_ID", "123")
    sent = []
    monkeypatch.setattr(service_news, "_send_telegram", lambda text: sent.append(text))
    row = service_news.push_selected(store_fixture, "nws_push_test_1")
    assert row["pushed_at"] is not None
    assert len(sent) == 1 and "Tin chọn push" in sent[0]
    # thiếu cấu hình → lỗi tiếng người
    monkeypatch.delenv("VIDEO_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Telegram"):
        service_news.push_selected(store_fixture, "nws_push_test_1")
    # id lạ → KeyError
    with pytest.raises(KeyError):
        service_news.push_selected(store_fixture, "nws_khong_ton_tai")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg thật")
def test_prepare_scene_image_blur_pads_landscape(tmp_path):
    from PIL import Image
    src = tmp_path / "landscape.jpg"
    Image.new("RGB", (1200, 675), (30, 40, 60)).save(src, format="JPEG")
    out = assembler._prepare_scene_image(src, tmp_path, 1)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == (1080, 1920)
    # ảnh dọc giữ nguyên path
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (800, 1400), (10, 10, 10)).save(portrait, format="PNG")
    assert assembler._prepare_scene_image(portrait, tmp_path, 2) == portrait
