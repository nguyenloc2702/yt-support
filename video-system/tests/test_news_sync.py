"""Hộp tin nóng (Phụ lục G.10): RSS parse + dedupe + Telegram skip + endpoints."""
from __future__ import annotations

import pytest

from backend.app import service_news

_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Google News</title>
  <item>
    <title>Ngân hàng nâng lãi suất tiết kiệm lên 6%/năm - VietFin</title>
    <link>https://news.example.com/vd1</link>
    <pubDate>Tue, 10 Nov 2026 07:30:00 GMT</pubDate>
    <description>&lt;a href="x"&gt;Chi tiết:&lt;/a&gt; Lãi suất kỳ hạn 12 tháng tăng mạnh trong tuần.</description>
    <source url="https://vietfin.example">VietFin</source>
  </item>
  <item>
    <title>Bẫy giá shopee: nhiều người mất tiền oan - VnExpress</title>
    <link>https://news.example.com/vd2</link>
    <pubDate>Tue, 10 Nov 2026 06:10:00 GMT</pubDate>
    <description>Đơn hàng ảo tràn lan trên sàn thương mại điện tử.</description>
  </item>
  <item>
    <title>Tin không hợp lệ tử vong thương tâm - Báo X</title>
    <link>https://news.example.com/vd3</link>
    <pubDate>Tue, 10 Nov 2026 05:00:00 GMT</pubDate>
    <description>Nội dung thuộc danh sách loại trừ.</description>
  </item>
</channel></rss>"""


def test_parse_rss_extracts_and_splits_source():
    items = service_news.parse_rss(_RSS_XML, "ngân hàng lãi suất")
    assert len(items) == 3
    first = items[0]
    assert first["title"] == "Ngân hàng nâng lãi suất tiết kiệm lên 6%/năm"
    assert first["source"] == "VietFin"
    assert "Lãi suất" in first["summary"]
    assert first["published_at"] is not None
    # item 2 không có <source> → tách đuôi " - VnExpress"
    assert items[1]["source"] == "VnExpress"
    assert items[1]["title"] == "Bẫy giá shopee: nhiều người mất tiền oan"


def _minimal_news_config():
    """Gói NEWS_CONFIG về 1 query cho test deterministic."""
    return {
        "interval_min": 60,
        "max_items_per_push": 10,
        "queries": ["ngân hàng lãi suất"],
        "exclude_keywords": ["tử vong", "máu me"],
        "telegram": {"parse_mode": "HTML", "disable_web_page_preview": False},
    }


def test_run_once_dedupe_and_no_telegram_without_token(client, store_fixture, monkeypatch):
    monkeypatch.setattr(service_news, "NEWS_CONFIG", _minimal_news_config())
    monkeypatch.setattr(service_news, "_fetch_rss", lambda url: _RSS_XML)
    monkeypatch.delenv("VIDEO_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("VIDEO_TELEGRAM_CHAT_ID", raising=False)

    r1 = service_news.run_once(store_fixture)
    assert r1["fetched"] == 3
    assert r1["new"] == 2          # tin "tử vong" bị loại theo exclude_keywords
    assert r1["pushed"] == 0       # không cấu hình Telegram → bỏ push, không lỗi

    # Chạy lại toàn bộ → 0 tin mới (dedupe theo sha1 link)
    r2 = service_news.run_once(store_fixture)
    assert r2["new"] == 0
    assert r2["fetched"] == 3


def test_news_endpoints_scopes(client, store_fixture, admin, read_key, ingest, monkeypatch):
    # Store dùng chung giữa các test → reset bảng news trước khi chạy kịch bản này
    store_fixture.execute("DELETE FROM news_items")
    monkeypatch.setattr(service_news, "NEWS_CONFIG", _minimal_news_config())
    monkeypatch.setattr(service_news, "_fetch_rss", lambda url: _RSS_XML)
    # read không được sync
    assert client.post("/api/v1/news/sync", headers=read_key).status_code == 403
    # ingest sync được
    r = client.post("/api/v1/news/sync", headers=ingest)
    assert r.status_code == 200
    body = r.json()
    assert body["new"] == 2
    # read list được
    rl = client.get("/api/v1/news?status=new", headers=read_key)
    assert rl.status_code == 200
    rows = rl.json()["news"]
    assert len(rows) == 2
    # dismiss (ingest) → không còn trong new
    rid = rows[0]["id"]
    rd = client.post(f"/api/v1/news/{rid}/dismiss", headers=ingest)
    assert rd.status_code == 200
    assert rd.json()["status"] == "dismissed"
    rl2 = client.get("/api/v1/news?status=new", headers=read_key)
    assert len(rl2.json()["news"]) == 1
    # 422 cho status lạ
    assert client.get("/api/v1/news?status=weird", headers=read_key).status_code == 422


def test_news_scheduler_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIDEO_NEWS_SYNC_ENABLED", raising=False)
    assert service_news.start_news_scheduler(None) is None
