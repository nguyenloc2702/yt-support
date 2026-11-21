"""Daily International EN Digest service (@StickSto · chế độ bán tự động).

Luồng mỗi ngày (mặc định 06:30 sáng giờ Mỹ America/New_York):
1. Sync RSS quốc tế EN (`service_news.run_once`).
2. Lọc trùng tiêu đề & lấy top ~30 tin ứng viên đa mảng (World, Markets, Tech, Science, Sports).
3. LLM đánh giá & chọn 12–15 tin nổi bật nhất, viết kịch bản `daily_digest` (EN, < 180s).
4. Map link gốc từng bài (`story_links`) → render MP4 9:16 preset `daily_digest` (có `@StickSto`).
5. Bán tự động: gửi tóm tắt + link duyệt + file MP4 preview qua Telegram (user tự duyệt đăng Shorts).
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import config as _cfg
from . import service_news
from .config import public_base_url
from .store import Store, new_id, now

logger = logging.getLogger("video_system.service_digest")

_TOKEN_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "after",
    "amid", "says", "said", "will", "new", "more", "than", "about", "what", "when",
    "how", "why", "are", "was", "were", "has", "have", "had", "not", "but", "who",
}
_TELEGRAM_MAX_VIDEO_BYTES = 48 * 1024 * 1024


def _digest_cfg() -> dict:
    return dict(_cfg.NEWS_CONFIG.get("daily_digest") or {})


def _parse_hhmm(raw: str, default: tuple[int, int] = (6, 30)) -> tuple[int, int]:
    try:
        hh, _, mm = str(raw or "").strip().partition(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (TypeError, ValueError):
        pass
    return default


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", (title or "").lower())
    return {w for w in words if w not in _TOKEN_STOPWORDS}


_FLUFF_PAT = re.compile(
    r"(\blive updates\b|\bopinion\b|\bpodcast\b|\bcrossword\b|\bgreetings from\b|"
    r"\bi want to know if\b|\bthoughts and\b|\breview\s*[–:\-]|^how to\b|"
    r"\|\s*[A-Z][a-z]+\s+[A-Z][a-z]+$)",
    re.IGNORECASE,
)


def _is_topic_duplicate(cand_toks: set[str], ref_toks: set[str], threshold: float = 0.45) -> bool:
    if not cand_toks or not ref_toks:
        return False
    inter = len(cand_toks & ref_toks)
    union = len(cand_toks | ref_toks)
    return bool((union and (inter / union) >= threshold) or inter >= 4)


def recent_covered_titles(store: Store, *, lookback_hours: int | None = None) -> list[str]:
    """Lấy tiêu đề/headline các tin đã lên sóng trong 72h qua để chống lặp tin giữa các ngày."""
    cfg = _digest_cfg()
    hours = int(lookback_hours or cfg.get("dedupe_lookback_hours") or 72)
    cutoff = now() - hours * 3600
    titles: list[str] = []

    used_rows = store.query(
        "SELECT title FROM news_items WHERE lang='en' AND status='used' "
        "AND fetched_at >= ? ORDER BY fetched_at DESC LIMIT 200",
        (cutoff,),
    )
    for r in used_rows:
        if r.get("title"):
            titles.append(str(r["title"]).strip())

    short_rows = store.query(
        "SELECT script_json FROM shorts WHERE mode='daily_digest' AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 10",
        (cutoff,),
    )
    for sr in short_rows:
        try:
            sc = json.loads(sr.get("script_json") or "{}")
            for st in sc.get("stories") or []:
                hl = str((st or {}).get("headline") or "").strip()
                if hl:
                    titles.append(hl)
        except json.JSONDecodeError:
            continue
    return list(dict.fromkeys(titles))


def _dedupe_candidates(rows: list[dict], max_items: int = 30,
                       excluded_titles: list[str] | None = None) -> list[dict]:
    """Loại tin trùng nội dung giữa nhiều báo + trùng tin đã phát 72h qua + tránh dồn 1 nguồn."""
    picked: list[dict] = []
    picked_tokens: list[set[str]] = []
    excluded_tokens: list[set[str]] = [
        _title_tokens(t) for t in (excluded_titles or []) if _title_tokens(t)
    ]
    source_counts: dict[str, int] = {}

    for row in rows:
        raw_title = str(row.get("title") or "")
        if _FLUFF_PAT.search(raw_title):
            continue
        toks = _title_tokens(raw_title)
        # Lớp 2: loại trùng xuyên ngày với các tin đã phát 72h qua
        if any(_is_topic_duplicate(toks, ex_toks, threshold=0.42) for ex_toks in excluded_tokens):
            continue
        # Loại trùng trong cùng kỳ chọn giữa các báo khác nhau
        if any(_is_topic_duplicate(toks, prev, threshold=0.48) for prev in picked_tokens):
            continue
        src = (row.get("source") or "unknown").strip().lower()
        if source_counts.get(src, 0) >= 4 and len(rows) > max_items:
            continue
        picked.append(row)
        picked_tokens.append(toks)
        source_counts[src] = source_counts.get(src, 0) + 1
        if len(picked) >= max_items:
            break
    return picked


def _ny_daily_window(cfg: dict, now_ny: datetime | None = None) -> tuple[float, float, datetime, datetime]:
    """Khung 24h chuẩn: 07:00 sáng hôm trước -> 07:00 sáng hôm nay theo America/New_York."""
    tz = ZoneInfo(str(cfg.get("timezone") or "America/New_York"))
    ref = (now_ny or datetime.now(tz)).astimezone(tz)
    anchor_h = int(cfg.get("window_anchor_hour") or 7)
    hours = int(cfg.get("lookback_hours") or 24)
    end_dt = ref.replace(hour=anchor_h, minute=0, second=0, microsecond=0)
    if ref < end_dt:
        end_dt -= timedelta(days=1)
    start_dt = end_dt - timedelta(hours=hours)
    return start_dt.timestamp(), end_dt.timestamp(), start_dt, end_dt


def select_candidates(store: Store, *, lookback_hours: int | None = None,
                      max_candidates: int = 38,
                      recent_titles: list[str] | None = None,
                      use_ny_window: bool = True) -> list[dict]:
    """Lấy tin EN quốc tế MỚI (`status='new'`) trong đúng khung 07:00->07:00 giờ Mỹ."""
    cfg = _digest_cfg()
    hours = int(lookback_hours or cfg.get("lookback_hours") or 24)
    excluded = recent_titles if recent_titles is not None else recent_covered_titles(store)
    if use_ny_window:
        t0, t1, _, _ = _ny_daily_window(cfg)
    else:
        t1 = now()
        t0 = t1 - hours * 3600
    rows = store.query(
        "SELECT * FROM news_items WHERE lang='en' AND status='new' "
        "AND COALESCE(published_at, fetched_at) BETWEEN ? AND ? "
        "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 200",
        (t0, t1),
    )
    deduped = _dedupe_candidates(rows, max_items=max_candidates, excluded_titles=excluded)
    if len(deduped) < int(cfg.get("min_stories") or 12):
        # Mở rộng thêm tối đa 30h gần nhất (vẫn ưu tiên 'new', loại tin trùng 72h qua)
        cutoff_wide = now() - max(30, hours) * 3600
        wider_new = store.query(
            "SELECT * FROM news_items WHERE lang='en' AND status='new' "
            "AND COALESCE(published_at, fetched_at) >= ? "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 200",
            (cutoff_wide,),
        )
        deduped = _dedupe_candidates(wider_new, max_items=max_candidates, excluded_titles=excluded)
    if len(deduped) < int(cfg.get("min_stories") or 12):
        # Chỉ khi test / DB mới rỗng mới fallback thêm các tin non-dismissed (VẪN loại tin trùng 72h qua)
        fallback_rows = store.query(
            "SELECT * FROM news_items WHERE lang='en' AND status != 'dismissed' "
            "ORDER BY CASE WHEN status='new' THEN 0 ELSE 1 END, "
            "COALESCE(published_at, fetched_at) DESC LIMIT 160"
        )
        seen_ids = {r["id"] for r in deduped}
        extra = [
            r for r in _dedupe_candidates(fallback_rows, max_items=max_candidates, excluded_titles=excluded)
            if r["id"] not in seen_ids
        ]
        deduped.extend(extra[: max(0, max_candidates - len(deduped))])
    return deduped


def build_digest_prompt_input(candidates: list[dict], max_chars: int = 12500,
                              recent_headlines: list[str] | None = None) -> str:
    """Định dạng tin ứng viên [1]..[N] + danh sách tin đã phát gần đây để LLM tránh lặp lại."""
    lines: list[str] = []
    if recent_headlines:
        recent_block = " | ".join(recent_headlines[:18])[:1200]
        lines.append(f"RECENTLY AIRED (last 48-72h — DO NOT REPEAT): {recent_block}")
        lines.append("")
    lines.append(
        "SELECT 12 to 15 TOP INTERNATIONAL STORIES from these numbered candidates "
        "(prefer diversity across WORLD, MARKETS, TECH, SCIENCE, SPORTS; merge duplicates):"
    )
    total_len = sum(len(x) + 1 for x in lines)
    for idx, c in enumerate(candidates, start=1):
        src = (c.get("source") or "Global News").strip()
        title = (c.get("title") or "").strip()
        summary = re.sub(r"\s+", " ", (c.get("summary") or "").strip())[:180]
        entry = f"[{idx}] ({src}) {title}"
        if summary and summary.lower() not in title.lower():
            entry += f" — {summary}"
        if total_len + len(entry) + 2 > max_chars:
            break
        lines.append(entry)
        total_len += len(entry) + 1
    return "\n".join(lines)


def _send_telegram_video(video_path: Path, caption: str) -> bool:
    """Gửi MP4 preview trực tiếp qua Telegram sendVideo (best-effort, không làm fail job)."""
    if not service_news._telegram_configured() or not video_path.exists():
        return False
    size = video_path.stat().st_size
    if size > _TELEGRAM_MAX_VIDEO_BYTES:
        logger.info("skip Telegram video upload: %d bytes > 48MB limit", size)
        return False
    token = os.environ["VIDEO_TELEGRAM_BOT_TOKEN"].strip()
    chat_id = os.environ["VIDEO_TELEGRAM_CHAT_ID"].strip()
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    with video_path.open("rb") as fh:
        files = {"video": (video_path.name, fh, "video/mp4")}
        data = {
            "chat_id": chat_id,
            "caption": caption[:1000],
            "parse_mode": "HTML",
            "supports_streaming": "true",
            "width": "1080",
            "height": "1920",
        }
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, data=data, files=files)
    resp.raise_for_status()
    return True


def _notify_telegram_digest(short: dict, video_path: Path | None) -> dict:
    """Gửi báo cáo + MP4 preview về Telegram cho bước duyệt bán tự động."""
    if not service_news._telegram_configured():
        return {"message_sent": False, "video_sent": False}
    script = short.get("script") or {}
    stories = list(script.get("stories") or [])
    dur = float(short.get("duration_s") or 0.0)
    base_url = public_base_url().rstrip("/")
    studio_url = f"{base_url}/#/shorts"
    ny_str = datetime.now(ZoneInfo(_digest_cfg().get("timezone") or "America/New_York")).strftime("%Y-%m-%d %H:%M %Z")

    lines = [
        f"<b>🌍 @StickSto Daily World Brief (EN) — {html.escape(ny_str)}</b>",
        f"ID: <code>{html.escape(short['id'])}</code> · <b>{len(stories)} tin</b> · ⏱ <b>{dur:.1f}s</b> ({'✅ Chuẩn YouTube Shorts ≤180s' if dur <= 180 else '⚠️ Quá 180s'})",
        f"<b>{html.escape(short.get('title') or '')}</b>",
        "",
    ]
    for st in stories[:15]:
        cat = html.escape(str(st.get("category") or "WORLD"))
        hl = html.escape(str(st.get("headline") or ""))
        lines.append(f"{int(st.get('index') or 0):02d}. [{cat}] {hl}")
    lines += [
        "",
        f"👉 Duyệt &amp; đăng YouTube Shorts tại: <a href=\"{html.escape(studio_url)}\">Mở Short Studio</a>",
    ]
    msg_sent = False
    vid_sent = False
    try:
        service_news._send_telegram("\n".join(lines))
        msg_sent = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram digest summary failed: %s", exc)

    if _digest_cfg().get("send_video_to_telegram", True) and video_path:
        try:
            cap = f"🎬 <b>{html.escape(short.get('title') or 'Daily World Brief')}</b>\n@StickSto · {dur:.1f}s · {len(stories)} stories"
            vid_sent = _send_telegram_video(video_path, cap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram digest sendVideo failed: %s", exc)
    return {"message_sent": msg_sent, "video_sent": vid_sent}


def _already_ran_for_ny_date(store: Store, ny_date: str) -> bool:
    rows = store.query(
        "SELECT type, actor, payload_json, at FROM events "
        "WHERE type IN ('digest.completed', 'digest.running', 'digest.failed') "
        "ORDER BY at DESC LIMIT 15"
    )
    t_now = now()
    for r in rows:
        if r.get("actor") != "digest-scheduler":
            continue
        try:
            p = json.loads(r.get("payload_json") or "{}")
        except json.JSONDecodeError:
            continue
        if p.get("ny_date") != ny_date:
            continue
        if r["type"] == "digest.completed":
            return True
        if r["type"] == "digest.failed":
            return False
        if r["type"] == "digest.running" and (t_now - float(r.get("at") or 0)) < 1200:
            return True
    return False


def _record_event(store: Store, event_type: str, actor: str, payload: dict) -> None:
    store.execute(
        "INSERT INTO events (id,item_id,shot_id,type,actor,payload_json,at) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_id("evt"), payload.get("short_id"), None, event_type, actor,
         json.dumps(payload, ensure_ascii=False), now()),
    )


def run_daily_digest(
    store: Store,
    shorts_svc,
    *,
    sync_first: bool = True,
    send_telegram: bool = True,
    force: bool = False,
    actor: str = "digest-scheduler",
    youtube_svc=None,
) -> dict:
    """Chạy trọn 1 chu kỳ Daily Digest EN (bán tự động): Sync → Chọn 12–15 tin → Render → Telegram → (tuỳ chọn) YouTube."""
    cfg = _digest_cfg()
    tz_name = str(cfg.get("timezone") or "America/New_York")
    ny_now = datetime.now(ZoneInfo(tz_name))
    ny_date = ny_now.strftime("%Y-%m-%d")

    if not force and _already_ran_for_ny_date(store, ny_date):
        return {"skipped": True, "reason": f"already ran for {ny_date}", "ny_date": ny_date}

    _record_event(store, "digest.running", actor, {"ny_date": ny_date})

    try:
        return _run_daily_digest_inner(
            store, shorts_svc,
            cfg=cfg, ny_now=ny_now, ny_date=ny_date,
            sync_first=sync_first, send_telegram=send_telegram, actor=actor,
            youtube_svc=youtube_svc,
        )
    except Exception as exc:
        _record_event(store, "digest.failed", actor, {"ny_date": ny_date, "error": str(exc)})
        raise


def _run_daily_digest_inner(
    store: Store,
    shorts_svc,
    *,
    cfg: dict,
    ny_now: datetime,
    ny_date: str,
    sync_first: bool,
    send_telegram: bool,
    actor: str,
    youtube_svc=None,
) -> dict:
    sync_res = service_news.run_once(store) if sync_first else {"skipped": True}
    recent_titles = recent_covered_titles(store)
    candidates = select_candidates(store, recent_titles=recent_titles)
    if len(candidates) < 8:
        err_msg = f"Chỉ có {len(candidates)} tin EN quốc tế — cần tối thiểu 8 tin để tạo Daily Digest."
        raise ValueError(err_msg)

    prompt_text = build_digest_prompt_input(candidates, recent_headlines=recent_titles)
    brand_header = str(cfg.get("brand_header") or "@STICKSTO · DAILY BRIEF")
    signature = str(cfg.get("signature") or "@StickSto")
    date_label = ny_now.strftime("%b %d")

    short = shorts_svc.create(
        mode="daily_digest",
        lang="en",
        source_text=prompt_text,
        source_note=f"{signature} · Global News Desk ({date_label})",
        target_seconds=155.0,
        actor=actor,
        extra_script_meta={
            "brand_header": brand_header,
            "signature": signature,
            "ny_date": ny_date,
        },
    )

    # Map source_index -> link gốc của từng tin & đánh dấu news_items='used'
    script = dict(short.get("script") or {})
    stories = list(script.get("stories") or [])
    story_links: dict[str, dict] = {}
    used_news_ids: list[str] = []
    sources_seen: list[str] = []
    kw_list = [f"world news studio globe {signature}"]

    for pos, st in enumerate(stories):
        seq = pos + 2  # seq 1 = Hook
        src_idx = st.get("source_index")
        cand = None
        if isinstance(src_idx, int) and 1 <= src_idx <= len(candidates):
            cand = candidates[src_idx - 1]
        elif pos < len(candidates):
            cand = candidates[pos]
        if cand:
            story_links[str(seq)] = {
                "link": cand.get("link") or "",
                "title": cand.get("title") or st.get("headline") or "",
                "source": cand.get("source") or "",
            }
            if cand.get("id"):
                used_news_ids.append(cand["id"])
            if cand.get("source") and cand["source"] not in sources_seen:
                sources_seen.append(cand["source"])
        kw_list.append(st.get("search_keywords_en") or st.get("headline") or "global news")

    if "2" in story_links:
        story_links["1"] = story_links["2"]
    last_seq = str(len(stories) + 2)
    if str(len(stories) + 1) in story_links:
        story_links[last_seq] = story_links[str(len(stories) + 1)]
    kw_list.append("global financial skyline sunset")

    script["story_links"] = story_links
    script["search_keywords_en"] = kw_list
    if sources_seen:
        script["source_note"] = f"Sources: {', '.join(sources_seen[:5])} | {signature}"

    store.execute(
        "UPDATE shorts SET script_json=?, source_note=?, updated_at=? WHERE id=?",
        (json.dumps(script, ensure_ascii=False), script["source_note"], now(), short["id"]),
    )
    for nid in used_news_ids:
        store.execute("UPDATE news_items SET status='used' WHERE id=?", (nid,))
    # Đánh dấu thêm các tin 'new' cùng chủ đề trong DB là 'used' để ngày mai không lặp từ báo khác
    chosen_toks = [_title_tokens(st.get("headline") or "") for st in stories if _title_tokens(st.get("headline") or "")]
    if chosen_toks:
        fresh_rows = store.query(
            "SELECT id, title FROM news_items WHERE lang='en' AND status='new' LIMIT 300"
        )
        for fr in fresh_rows:
            ftoks = _title_tokens(fr.get("title") or "")
            if any(_is_topic_duplicate(ftoks, ct, threshold=0.45) for ct in chosen_toks):
                store.execute("UPDATE news_items SET status='used' WHERE id=?", (fr["id"],))

    produced = shorts_svc.produce(short["id"], actor=actor)
    final_short = shorts_svc.get(short["id"])
    vid_path = None
    try:
        vid_path = shorts_svc.video_path(short["id"])
    except Exception:  # noqa: BLE001
        vid_path = None

    tg_res = {"message_sent": False, "video_sent": False}
    if send_telegram:
        tg_res = _notify_telegram_digest(final_short, vid_path)

    # Auto-publish lên YouTube (config gate `daily_digest.youtube_auto_publish`).
    # Upload ngay với privacy_status cấu hình; có publish_at = now + delay để có
    # khung thời gian hủy/sửa trước khi video public. Lỗi publish KHÔNG làm hỏng digest.
    yt_res: dict[str, Any] = {"auto_publish": False}
    yt_cfg = cfg.get("youtube_auto_publish") or {}
    if yt_cfg.get("enabled") and youtube_svc is not None:
        try:
            delay_min = int(yt_cfg.get("publish_delay_minutes", 45))
            publish_at = (datetime.now(timezone.utc) + timedelta(minutes=delay_min)).isoformat()
            up = youtube_svc.publish_short(
                short["id"],
                title=str(final_short["title"])[:100],
                description=str((final_short.get("script") or {}).get("description") or ""),
                privacy_status=str(yt_cfg.get("privacy_status") or "private"),
                publish_at=publish_at,
            )
            yt_res = {
                "auto_publish": True,
                "upload_id": up.get("upload_id"),
                "publish_at": publish_at,
            }
        except Exception as exc:  # noqa: BLE001
            yt_res = {"auto_publish": False, "error": str(exc)[:220]}

    payload = {
        "ny_date": ny_date,
        "short_id": short["id"],
        "title": final_short["title"],
        "stories_count": len(stories),
        "duration_s": produced.get("duration") or final_short.get("duration_s"),
        "sync": sync_res,
        "telegram": tg_res,
        "youtube": yt_res,
    }
    _record_event(store, "digest.completed", actor, payload)
    return {"short": final_short, **payload}


def get_digest_status(store: Store) -> dict:
    """Thông tin lịch chạy Daily Digest & lần chạy gần nhất."""
    cfg = _digest_cfg()
    tz_name = str(cfg.get("timezone") or "America/New_York")
    ny_tz = ZoneInfo(tz_name)
    vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
    ny_now = datetime.now(ny_tz)
    h, m = _parse_hhmm(str(cfg.get("run_at") or "07:00"), default=(7, 0))
    next_ny = ny_now.replace(hour=h, minute=m, second=0, microsecond=0)
    if next_ny <= ny_now:
        next_ny += timedelta(days=1)

    last_row = store.query_one(
        "SELECT type, payload_json, at FROM events "
        "WHERE type IN ('digest.completed', 'digest.failed', 'digest.running') "
        "ORDER BY at DESC LIMIT 1"
    )
    last_run = None
    if last_row:
        try:
            p = json.loads(last_row.get("payload_json") or "{}")
        except json.JSONDecodeError:
            p = {}
        last_run = {"type": last_row["type"], "at": last_row["at"], **p}

    return {
        "enabled": os.environ.get("VIDEO_DAILY_DIGEST_ENABLED", "").strip() == "1",
        "mode": "semi_auto",
        "timezone": tz_name,
        "run_at": f"{h:02d}:{m:02d}",
        "brand_header": cfg.get("brand_header") or "@STICKSTO · DAILY BRIEF",
        "signature": cfg.get("signature") or "@StickSto",
        "story_range": [int(cfg.get("min_stories") or 12), int(cfg.get("max_stories") or 15)],
        "next_run_us_eastern": next_ny.isoformat(),
        "next_run_vn": next_ny.astimezone(vn_tz).isoformat(),
        "last_run": last_run,
    }


def start_digest_scheduler(store: Store, shorts_svc, *, youtube_svc=None) -> threading.Thread | None:
    """Khởi chạy scheduler nền 06:30 AM giờ Mỹ khi VIDEO_DAILY_DIGEST_ENABLED=1."""
    if os.environ.get("VIDEO_DAILY_DIGEST_ENABLED", "").strip() != "1":
        logger.info("daily digest scheduler disabled (set VIDEO_DAILY_DIGEST_ENABLED=1 để bật)")
        return None
    cfg = _digest_cfg()
    tz_name = str(cfg.get("timezone") or "America/New_York")
    target_h, target_m = _parse_hhmm(str(cfg.get("run_at") or "07:00"), default=(7, 0))

    def _loop() -> None:
        tz = ZoneInfo(tz_name)
        while True:
            try:
                ny_now = datetime.now(tz)
                ny_date = ny_now.strftime("%Y-%m-%d")
                if (ny_now.hour, ny_now.minute) >= (target_h, target_m):
                    if not _already_ran_for_ny_date(store, ny_date):
                        logger.info("Starting scheduled daily digest for %s (%s)", ny_date, tz_name)
                        run_daily_digest(store, shorts_svc, force=False, actor="digest-scheduler",
                                         youtube_svc=youtube_svc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("daily digest scheduler error: %s", exc)
            time.sleep(30)

    th = threading.Thread(target=_loop, name="daily-digest", daemon=True)
    th.start()
    return th
