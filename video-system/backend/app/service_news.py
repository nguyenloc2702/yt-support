"""Hộp tin nóng (Phụ lục G.10): Google News RSS → dedupe → Telegram push.

- `run_once()`: fetch toàn bộ query → lưu tin mới → push Telegram tin chưa push.
  Một query lỗi không chặn các query còn lại (không fail-cả-kỳ).
- `start_news_scheduler()`: daemon thread chu kỳ `interval_min` (chỉ bật khi
  env `VIDEO_NEWS_SYNC_ENABLED=1`).
- Telegram: chỉ push khi có VIDEO_TELEGRAM_BOT_TOKEN + VIDEO_TELEGRAM_CHAT_ID;
  thiếu → vẫn lưu DB, bỏ push (honest, không lỗi).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .config import NEWS_CONFIG, data_dir
from .store import Store, new_id, now

logger = logging.getLogger("video_system.service_news")

_RSS_TIMEOUT_S = 20.0


def _fetch_rss(url: str) -> str:
    """Tách ra module-level để test monkeypatch được."""
    with httpx.Client(timeout=_RSS_TIMEOUT_S,
                      follow_redirects=True,
                      headers={"User-Agent": "video-system/0.2 (+news-sync)"}) as client:
        resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def parse_rss(xml_text: str, query: str) -> list[dict]:
    """Parse 1 RSS Google News → list item {title, link, source, summary, published_at}."""
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        # Google News: title thường có đuôi " - Tên Báo" và <source> riêng →
        # ưu tiên <source>; nếu có source thì cũng bóc đuôi khớp khỏi title.
        source = ""
        src_el = item.find("source")
        if src_el is not None and src_el.text:
            source = html.unescape(src_el.text.strip())
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        elif not source and " - " in title:
            title, _, src_tail = title.rpartition(" - ")
            title = title.strip()
            source = html.unescape(src_tail.strip())
        desc_html = item.findtext("description") or ""
        summary = re.sub(r"<[^>]+>", " ", html.unescape(desc_html))
        summary = re.sub(r"\s+", " ", summary).strip()[:300]
        pub_raw = (item.findtext("pubDate") or "").strip()
        published_at = _parse_pubdate(pub_raw)
        items.append({"title": title, "link": link, "source": source,
                      "summary": summary, "query": query, "published_at": published_at})
    return items


def _parse_pubdate(raw: str) -> float | None:
    from email.utils import parsedate_to_datetime
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        return None


def _title_allowed(title: str) -> bool:
    low = title.lower()
    for kw in NEWS_CONFIG.get("exclude_keywords") or []:
        if str(kw).lower() in low:
            return False
    return True


def _store_items(store: Store, items: list[dict]) -> list[dict]:
    """Dedupe theo sha1(link) → INSERT. Trả danh sách tin mới thực sự được lưu."""
    new_rows: list[dict] = []
    t = now()
    for it in items:
        if not _title_allowed(it["title"]):
            continue
        link_hash = hashlib.sha1(it["link"].encode("utf-8")).hexdigest()
        exists = store.query_one("SELECT id FROM news_items WHERE link_hash=?",
                                 (link_hash,))
        if exists:
            continue
        row_id = new_id("nws")
        store.execute(
            "INSERT OR IGNORE INTO news_items (id, link_hash, source, title, link, "
            "summary, query, published_at, fetched_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,'new')",
            (row_id, link_hash, it.get("source") or "", it["title"], it["link"],
             it.get("summary") or "", it.get("query") or "",
             it.get("published_at"), t),
        )
        if store.query_one("SELECT id FROM news_items WHERE link_hash=?", (link_hash,)):
            it["id"] = row_id
            new_rows.append(it)
    return new_rows


def _send_telegram(text: str) -> None:
    """Gửi 1 message Telegram. Tách ra để test monkeypatch được."""
    token = os.environ["VIDEO_TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["VIDEO_TELEGRAM_CHAT_ID"].strip()
    parse_mode = (NEWS_CONFIG.get("telegram") or {}).get("parse_mode", "HTML")
    preview = bool((NEWS_CONFIG.get("telegram") or {}).get("disable_web_page_preview", False))
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(url, json={
            "chat_id": chat_id, "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": not preview,
        })
    resp.raise_for_status()


def _telegram_configured() -> bool:
    return bool(os.environ.get("VIDEO_TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("VIDEO_TELEGRAM_CHAT_ID", "").strip())


def _format_news_line(it: dict) -> str:
    esc_title = html.escape(it["title"])
    src = html.escape(it.get("source") or "")
    meta = f" · {src}" if src else ""
    return f"<a href=\"{html.escape(it['link'])}\">{esc_title}</a>{meta}"


def _push_telegram(store: Store, items: list[dict]) -> int:
    """Push tin (chưa pushed) qua Telegram. Trả số tin đã push.

    G.11: chỉ gọi khi `auto_push: true` hoặc qua `push_selected` (user chọn).
    """
    if not items or not _telegram_configured():
        return 0
    limit = int(NEWS_CONFIG.get("max_items_per_push", 10))
    batch = items[:limit]
    lines = [f"<b>TIN NÓNG tiền bạc/kinh doanh — {time.strftime('%H:%M %d/%m')}</b>", ""]
    for i, it in enumerate(batch, start=1):
        lines.append(f"{i}. {_format_news_line(it)}")
    lines += ["", "Mở web → Short Studio → 'Dùng tin này' để làm Short."]
    _send_telegram("\n".join(lines))
    t = now()
    for it in batch:
        store.execute("UPDATE news_items SET pushed_at=? WHERE id=?", (t, it["id"]))
    return len(batch)


def push_selected(store: Store, news_id: str) -> dict:
    """Gửi ĐÚNG 1 tin user chọn qua Telegram (G.11 — không tự push hàng loạt)."""
    row = store.query_one("SELECT * FROM news_items WHERE id=?", (news_id,))
    if not row:
        raise KeyError(news_id)
    if not _telegram_configured():
        raise ValueError(
            "Chưa cấu hình Telegram: cần VIDEO_TELEGRAM_BOT_TOKEN và "
            "VIDEO_TELEGRAM_CHAT_ID trong .env để gửi tin.")
    _send_telegram(
        f"<b>Tin đã chọn — {time.strftime('%H:%M %d/%m')}</b>\n\n"
        f"{_format_news_line(row)}\n\n"
        "Mở web → Short Studio → 'Dùng tin này' để làm Short."
    )
    store.execute("UPDATE news_items SET pushed_at=? WHERE id=?", (now(), news_id))
    return store.query_one("SELECT * FROM news_items WHERE id=?", (news_id,))


def run_once(store: Store) -> dict:
    """1 kỳ sync: fetch feeds trực tiếp + query Google News → dedupe → push."""
    base = "https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"
    all_items: list[dict] = []
    failed: list[str] = []
    # G.11: RSS trực tiếp của báo — link bài là link thật (lấy được ảnh gốc)
    for feed in NEWS_CONFIG.get("feeds") or []:
        name, url = str(feed.get("name") or "rss"), str(feed.get("url") or "")
        if not url:
            continue
        try:
            items = parse_rss(_fetch_rss(url), name)
            for it in items:
                if not it.get("source"):
                    it["source"] = name
            all_items.extend(items)
        except Exception as exc:  # noqa: BLE001
            logger.warning("feed '%s' failed: %s", name, exc)
            failed.append(name)
    for q in NEWS_CONFIG.get("queries") or []:
        try:
            xml_text = _fetch_rss(base.format(q=urllib.parse.quote_plus(q)))
            all_items.extend(parse_rss(xml_text, q))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RSS query '%s' failed: %s", q, exc)
            failed.append(q)
    new_rows = _store_items(store, all_items)
    # G.11: chỉ auto-push khi news_config.yaml:auto_push=true (mặc định false —
    # user chọn tin rồi mới gửi; xem push_selected).
    pushed = _push_telegram(store, new_rows) if NEWS_CONFIG.get("auto_push") else 0
    result = {"fetched": len(all_items), "new": len(new_rows), "pushed": pushed,
              "failed_queries": failed, "at": now()}
    if failed:
        store.execute(
            "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
            "VALUES (?,?,?,?,?,?,?)",
            (new_id("evt"), None, None, "news.sync_failed", "news-scheduler",
             json.dumps(result, ensure_ascii=False), now()),
        )
    return result


def list_news(store: Store, status: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM news_items"
    params: tuple = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params = params + (max(1, min(int(limit), 200)),)
    return store.query(sql, params)


def dismiss_news(store: Store, news_id: str) -> dict | None:
    row = store.query_one("SELECT id FROM news_items WHERE id=?", (news_id,))
    if not row:
        return None
    store.execute("UPDATE news_items SET status='dismissed' WHERE id=?", (news_id,))
    return store.query_one("SELECT * FROM news_items WHERE id=?", (news_id,))


def start_news_scheduler(store: Store) -> threading.Thread | None:
    """Bật daemon thread khi env VIDEO_NEWS_SYNC_ENABLED=1. Trả thread (hoặc None)."""
    if os.environ.get("VIDEO_NEWS_SYNC_ENABLED", "").strip() != "1":
        logger.info("news scheduler disabled (set VIDEO_NEWS_SYNC_ENABLED=1 để bật)")
        return None
    interval_min = int(NEWS_CONFIG.get("interval_min", 60))
    interval_s = max(60, interval_min * 60)

    def _loop() -> None:
        while True:
            try:
                result = run_once(store)
                logger.info("news sync: %s", result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("news scheduler loop error: %s", exc)
            time.sleep(interval_s)

    th = threading.Thread(target=_loop, name="news-sync", daemon=True)
    th.start()
    return th
