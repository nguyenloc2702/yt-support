"""Short mảng quốc tế EN (lang='en') + hộp tin EN quốc tế.

Luật: lang='en' → prompt EN + voice tts_en + publish pack EN; tin EN vào
news_items.lang='en' và GET /api/v1/news?lang=en lọc được. Không key Pexels →
produce vẫn chạy đúng chuỗi nguồn cũ (regression an toàn).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: F401 (đảm bảo parse RSS dùng stdlib)

import pytest

from backend.app.config import data_dir
from backend.app.pipeline_short import prompts
from backend.app.service_news import parse_rss, run_once
from backend.app import service_news
from conftest import default_short_script


def default_short_script_en() -> dict:
    """Script EN mode news đủ ngân sách 65–90 từ (tổng ~72 từ)."""
    hook = "Overnight, three billion dong vanished from dozens of bank accounts"
    beats = [
        "By morning, customers kept calling the hotline about strange transactions they never made",
        "The bank later confirmed a gang posing as support staff had been stealing OTP codes",
        "Average losses hit fifty million dong each, with some victims losing two hundred million dong",
    ]
    closing = "So who bears the final responsibility — the bank or the customer?"
    texts = [hook] + beats + [closing]
    wc = sum(len(t.split()) for t in texts)
    return {
        "title": "Three billion dong gone overnight",
        "hook": hook, "beats": beats, "closing": closing,
        "hashtags": {"tiktok": ["#breakingnews", "#money"],
                     "facebook": ["#news"], "youtube": ["#shorts"]},
        "description": "Bank OTP scam drained customer accounts overnight.",
        "source_note": "Source: BBC Business",
        "word_count": wc,
    }


def _create_en_short(client, ingest, llm_script=None):
    if llm_script is not None:
        from backend.app import providers
        providers.registry.llm.script = llm_script
    return client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news",
        "lang": "en",
        "source_text": "Brief: bank OTP scam drained customer accounts overnight...",
        "source_note": "Source: BBC Business",
    })


def test_create_en_short_uses_en_prompt_voice_and_scene(client, ingest, short_adapters):
    short_adapters["llm"].script = default_short_script_en()
    r = _create_en_short(client, ingest)
    assert r.status_code == 201, r.text
    short = r.json()
    assert short["lang"] == "en"
    assert short["script"]["lang"] == "en"
    # LLM nhận đúng system prompt EN (không phải bản VI)
    sys_txt = short_adapters["llm"].calls[0]["system"]
    assert "vertical short-form news" in sys_txt
    assert "tin nhanh dạng dọc" not in sys_txt
    # Image prompt scene đầu: toàn EN, không còn "Ý cảnh:"
    p0 = short["scenes"][0]["image_prompt"]
    assert "Scene:" in p0
    assert "Ý cảnh" not in p0


def test_en_word_budget_uses_en_limits(client, ingest, short_adapters):
    # Script VI (107 từ) đẩy vào lang='en' → vượt trần 90 từ → 422
    r = _create_en_short(client, ingest, llm_script=default_short_script())
    assert r.status_code == 422
    assert "ngân sách" in r.json()["detail"]


def test_lang_validation_rejects_unknown(client, ingest, short_adapters):
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news", "lang": "jp", "source_text": "tin thử"})
    assert r.status_code == 422


def test_prompts_lang_helpers():
    assert prompts._budget_en("news") == (65, 90)
    assert prompts._budget_en("decode") == (100, 135)
    with pytest.raises(ValueError):
        prompts.system_prompt("news", "jp")


def test_en_publish_pack_labels(client, ingest, short_adapters, monkeypatch):
    from backend.app.service_shorts import _publish_markdown
    script = default_short_script_en()
    md = _publish_markdown(script, 32.0, "en")
    assert "Duration: 32.0s" in md
    assert md.count("Source: BBC Business") == 1  # không lặp "Source: Source:"
    md_vi = _publish_markdown(default_short_script(), 32.0, "vi")
    assert "Thời lượng: 32.0s" in md_vi


_EN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>BBC Business</title>
<item><title>Global markets rally as inflation cools</title>
<link>https://www.bbc.com/news/business-1</link>
<description source="BBC Business">Markets rose sharply.</description>
<pubDate>Thu, 12 Nov 2026 09:00:00 GMT</pubDate><source url="">BBC Business</source></item>
</channel></rss>"""


def _en_news_config() -> dict:
    return {
        "interval_min": 60, "auto_push": False, "max_items_per_push": 10,
        "feeds": [], "queries": [],
        "feeds_en": [{"name": "BBC Business", "url": "https://feeds.bbci.co.uk/rss.xml"}],
        "queries_en": ["global economy news"],
        "exclude_keywords": [], "exclude_keywords_en": ["politics"],
        "telegram": {"parse_mode": "HTML"},
    }


def test_news_sync_en_items_stored_with_lang_en(store_fixture, monkeypatch):
    store_fixture.execute("DELETE FROM news_items")  # store dùng chung giữa các test
    monkeypatch.setattr(service_news, "NEWS_CONFIG", _en_news_config())
    urls_seen = []

    def fake_fetch_rss(url: str) -> str:
        urls_seen.append(url)
        return _EN_RSS

    monkeypatch.setattr(service_news, "_fetch_rss", fake_fetch_rss)
    result = run_once(store_fixture)
    # feed + query cùng trả 1 link → dedupe về 1 tin
    assert result["new"] == 1
    rows = store_fixture.query("SELECT * FROM news_items ORDER BY id")
    assert len(rows) == 1
    assert rows[0]["lang"] == "en"
    assert any("news.google.com" in u and "hl=en" in u for u in urls_seen)
    assert any("feeds.bbci.co.uk" in u for u in urls_seen)


def test_news_list_lang_filter_endpoint(client, ingest, store_fixture, monkeypatch):
    t = service_news.now()
    store_fixture.execute(
        "INSERT OR IGNORE INTO news_items (id, link_hash, source, title, link, "
        "summary, query, lang, published_at, fetched_at, status) "
        "VALUES ('nws_en_1','h1','BBC','EN story','https://x.com/1','','q','en',?,?,'new')",
        (t, t))
    store_fixture.execute(
        "INSERT OR IGNORE INTO news_items (id, link_hash, source, title, link, "
        "summary, query, lang, published_at, fetched_at, status) "
        "VALUES ('nws_vi_1','h2','VnExpress','Tin VI','https://x.com/2','','q','vi',?,?,'new')",
        (t, t))
    r = client.get("/api/v1/news?status=new&lang=en", headers=ingest)
    assert r.status_code == 200
    items = r.json()["news"]
    assert any(n["id"] == "nws_en_1" for n in items)
    assert all(n["lang"] == "en" for n in items)
    r_bad = client.get("/api/v1/news?lang=jp", headers=ingest)
    assert r_bad.status_code == 422
