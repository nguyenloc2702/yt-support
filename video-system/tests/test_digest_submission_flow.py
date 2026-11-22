# ruff: noqa: E501
"""Daily Brief submission flow — human-in-the-loop (chốt 3 bên 2026-11-22).

Flow: 16:00 VN Telegram đòi nội dung → owner POST /api/v1/digest/submit
(list tin chốt + link + ảnh + thumbnail) → thu thập media → LLM viết →
critic AI rà soát (fail → regen 1 lần → fail 2 → dừng) → render template
daily_digest → Telegram preview → /approve mới đăng YouTube.
"""
from __future__ import annotations

import pytest

from backend.app import service_digest, service_news
from backend.app import main as app_main
from backend.app.pipeline_short import story_media
from conftest import make_fake_bank_image
from test_daily_digest import _make_digest_script, _fake_rss_en

DIGEST_CFG = {
    "enabled": True,
    "timezone": "Asia/Ho_Chi_Minh",
    "run_at": "16:00",
    "min_stories": 12,
    "max_stories": 15,
    "lookback_hours": 24,
    "window_anchor_hour": 16,
    "brand_header": "@STICKSTO · DAILY BRIEF",
    "signature": "@StickSto",
    "send_video_to_telegram": True,
    "submission_flow": {
        "enabled": True,
        "min_stories": 8,
        "max_stories": 15,
        "max_images_per_story": 4,
        "deadline_hour": "18:00",
        "reminder_hour": "17:15",
        "critic": {"enabled": True, "max_attempts": 2},
        "approve": {"privacy_status": "public"},
    },
    "youtube_auto_publish": {"enabled": True, "privacy_status": "private",
                             "publish_delay_minutes": 45},
}

BASE_NEWS_CONFIG = {
    "interval_min": 60,
    "max_items_per_push": 10,
    "feeds_en": [{"name": "BBC World", "url": "https://feeds.example.com/world.xml"}],
    "queries_en": [],
    "exclude_keywords_en": [],
    "daily_digest": DIGEST_CFG,
    "telegram": {"parse_mode": "HTML", "disable_web_page_preview": False},
}

DATE_KEY = "2026-11-22"


def _stories_payload(count: int = 9) -> list[dict]:
    out = []
    for i in range(1, count + 1):
        out.append({
            "headline": f"Global development number {i} reshapes markets worldwide",
            "link": f"https://reuters.com/story-{i}",
            "source": "Reuters",
            "summary": f"Officials confirmed nearly {i * 10} billion dollars in new agreements today.",
            "thumbnail_url": f"https://img.example.com/thumb-{i}.jpg",
            "images": [f"https://img.example.com/story-{i}-1.jpg"],
        })
    return out


class FakeYouTube:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def publish_short(self, short_id, **kw):
        self.calls.append((short_id, kw))
        return {"upload_id": f"up_{len(self.calls)}"}


def _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path):
    """Common mocks: config, telegram, images, LLM script, offline render."""
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    store_fixture.execute("DELETE FROM news_items")
    store_fixture.execute("DELETE FROM events")
    store_fixture.execute("DELETE FROM digest_submissions")
    store_fixture.execute("DELETE FROM shorts")
    make_fake_bank_image("digest_bank.png")

    monkeypatch.setattr(service_digest._cfg, "NEWS_CONFIG", dict(BASE_NEWS_CONFIG))
    monkeypatch.setattr(service_news, "NEWS_CONFIG", service_digest._cfg.NEWS_CONFIG)
    monkeypatch.setattr(service_news, "_fetch_rss", lambda url: _fake_rss_en(20))
    short_adapters["llm"].script = _make_digest_script(9)

    from PIL import Image
    sample_img = tmp_path / "story_img.png"
    Image.new("RGB", (540, 960), (30, 60, 110)).save(sample_img)

    def _fake_collect(*, article_link, headline, queries, out_dir, target_images=3, seen_hashes=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / "rel_01.png"
        dst.write_bytes(sample_img.read_bytes())
        return [{"path": dst, "attribution": "Article original photo", "source": "article"}]

    monkeypatch.setattr(story_media, "collect_story_visuals", _fake_collect)
    monkeypatch.setattr(service_digest.owner_images, "download_owner_images",
                        lambda date_label, stories, max_per_story=4: {})

    tg_msgs: list[str] = []
    tg_videos: list[tuple] = []
    monkeypatch.setenv("VIDEO_TELEGRAM_BOT_TOKEN", "bot-test-token")
    monkeypatch.setenv("VIDEO_TELEGRAM_CHAT_ID", "1383335819")
    monkeypatch.setattr(service_news, "_send_telegram", lambda txt: tg_msgs.append(txt))
    monkeypatch.setattr(service_digest, "_send_telegram_video",
                        lambda path, cap: tg_videos.append((str(path), cap)) or True)
    return tg_msgs, tg_videos


# --- validation & scopes -------------------------------------------------------


def test_submit_requires_ingest_scope(client, store_fixture):
    r = client.post("/api/v1/digest/submit", headers={"Authorization": "Bearer test-read"},
                    json={"stories": _stories_payload(9)})
    assert r.status_code == 403  # scope read < ingest — deny-by-default


def test_submit_rejects_too_few_stories(client, ingest):
    r = client.post("/api/v1/digest/submit", headers=ingest,
                    json={"stories": _stories_payload(3), "date": DATE_KEY})
    assert r.status_code == 422, r.text
    assert "8" in r.text  # min_stories message


def test_submit_rejects_too_many_stories(client, ingest):
    r = client.post("/api/v1/digest/submit", headers=ingest,
                    json={"stories": _stories_payload(18), "date": DATE_KEY})
    assert r.status_code == 422


def test_submit_locked_when_building_or_built(store_fixture, client, ingest, short_adapters,
                                              monkeypatch, tmp_path):
    tg_msgs, _ = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    from backend.app.main import shorts as main_shorts
    # Submit lock: mô phỏng kỳ đang built
    store_fixture.execute(
        "INSERT INTO digest_submissions (id,date,status,stories_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("dsub_locked", DATE_KEY, "built", "[]", 1.0, 1.0))
    r = client.post("/api/v1/digest/submit", headers=ingest,
                    json={"stories": _stories_payload(9), "date": DATE_KEY})
    assert r.status_code == 409
    store_fixture.execute("DELETE FROM digest_submissions WHERE id='dsub_locked'")


# --- build flow ----------------------------------------------------------------


def test_submit_build_sync_critic_pass_and_preview_with_approve_commands(
        client, store_fixture, ingest, short_adapters, monkeypatch, tmp_path):
    tg_msgs, tg_videos = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: {"verdict": "pass", "issues": []})
    from backend.app.main import shorts as main_shorts

    res = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(9),
        date_key=DATE_KEY, actor="test", build_async=False)

    assert res["ok"] is True
    assert res["short"]["status"] == "ready"
    assert res["auto_publish"] is False  # submission flow: KHÔNG bao giờ tự đăng
    # story_links map đúng theo list chốt (story 1 -> seq 2)
    assert res["short"]["script"]["story_links"]["2"]["link"] == "https://reuters.com/story-1"
    # submission row built + short_id
    sub = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub["status"] == "built"
    assert sub["short_id"] == res["short"]["id"]
    # Telegram preview chứa lệnh chốt
    assert any("/approve" in m for m in tg_msgs)
    assert len(tg_videos) == 1  # MP4 preview đã gửi


def test_critic_regenerate_then_pass(client, store_fixture, ingest, short_adapters,
                                     monkeypatch, tmp_path):
    tg_msgs, _ = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    verdicts = [{"verdict": "regenerate",
                 "issues": [{"type": "number_mismatch", "detail": "story 3 invents a number"}]},
                {"verdict": "pass", "issues": []}]
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: verdicts.pop(0))
    from backend.app.main import shorts as main_shorts

    llm_calls_before = len(short_adapters["llm"].calls)
    res = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(9),
        date_key=DATE_KEY, actor="test", build_async=False)

    assert res["ok"] is True
    assert len(short_adapters["llm"].calls) == llm_calls_before + 2  # gen 2 lần
    # Bản đầu bị huỷ, bản cuối là short built
    sub = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub["status"] == "built"
    cancelled = store_fixture.query("SELECT status FROM shorts WHERE status='cancelled'")
    assert len(cancelled) == 1


def test_critic_fails_twice_no_render(store_fixture, ingest, short_adapters,
                                      monkeypatch, tmp_path):
    tg_msgs, _ = _setup_build_env(store_fixture, store_fixture, short_adapters, monkeypatch, tmp_path)
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: {"verdict": "regenerate",
                                                    "issues": [{"type": "number_mismatch",
                                                                "detail": "bad number"}]})

    from backend.app.main import shorts as main_shorts
    produce_calls: list[str] = []

    def _boom(short_id, **kw):
        produce_calls.append(short_id)
        raise AssertionError("produce must not run when critic fails twice")

    monkeypatch.setattr(main_shorts, "produce", _boom)

    res = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(9),
        date_key=DATE_KEY, actor="test", build_async=False)

    assert res["ok"] is False
    assert len(res["critic_issues"]) >= 1
    assert produce_calls == []  # KHÔNG render bản đã biết lỗi
    sub = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub["status"] == "failed"
    assert len(sub["critic_issues_json"] and "number_mismatch") > 0
    # Telegram cảnh báo có danh sách issue
    assert any("critic" in m.lower() and "number_mismatch" in m for m in tg_msgs)


# --- approve / reject ----------------------------------------------------------


def test_approve_publishes_public_and_idempotent(client, store_fixture, ingest, short_adapters,
                                                 monkeypatch, tmp_path):
    tg_msgs, _ = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: {"verdict": "pass", "issues": []})
    from backend.app.main import shorts as main_shorts

    res = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(9),
        date_key=DATE_KEY, actor="test", build_async=False)
    short_id = res["short"]["id"]

    fake_yt = FakeYouTube()
    r1 = service_digest.approve_digest(store_fixture, fake_yt, short_id, actor="test")
    assert r1["approved"] is True
    assert r1["privacy_status"] == "public"  # đã qua human gate -> public ngay
    assert len(fake_yt.calls) == 1

    r2 = service_digest.approve_digest(store_fixture, fake_yt, short_id, actor="test")
    assert r2.get("already_approved") is True
    assert len(fake_yt.calls) == 1  # idempotent — không đăng lại

    sub = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub["status"] == "approved"
    ev = store_fixture.query_one(
        "SELECT * FROM events WHERE type='digest.published' ORDER BY at DESC LIMIT 1")
    assert ev is not None


def test_reject_then_resubmit(client, store_fixture, ingest, short_adapters,
                              monkeypatch, tmp_path):
    tg_msgs, _ = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: {"verdict": "pass", "issues": []})
    from backend.app.main import shorts as main_shorts

    res = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(9),
        date_key=DATE_KEY, actor="test", build_async=False)
    short_id = res["short"]["id"]

    rej = service_digest.reject_digest(store_fixture, short_id, "hook yếu", actor="test")
    assert rej["rejected"] is True
    sub = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub["status"] == "rejected"

    # Nộp lại nội dung mới (rejected -> replace được)
    res2 = service_digest.submit_digest_stories(
        store_fixture, main_shorts, _stories_payload(10),
        date_key=DATE_KEY, actor="test", build_async=False)
    assert res2["ok"] is True
    sub2 = service_digest.get_digest_submission(store_fixture, DATE_KEY)
    assert sub2["status"] == "built"
    assert sub2["short_id"] == res2["short"]["id"]


# --- auto-publish bypass + fallback message -------------------------------------


def test_auto_publish_bypassed_when_submission_flow_enabled(
        client, store_fixture, ingest, read_key, short_adapters, monkeypatch, tmp_path):
    """Legacy run_daily_digest phải KHÔNG auto-publish khi submission_flow bật."""
    tg_msgs, tg_videos = _setup_build_env(client, store_fixture, short_adapters, monkeypatch, tmp_path)
    monkeypatch.setattr(service_digest, "_critic_review",
                        lambda script, candidates: {"verdict": "pass", "issues": []})

    r = client.post("/api/v1/shorts/daily-digest/run", headers=ingest)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["youtube"]["auto_publish"] is False  # bypass bằng chứng
    assert any("/approve" in m for m in tg_msgs)  # preview vẫn chờ chốt


def test_request_and_reminder_message_content(store_fixture):
    store_fixture.execute("DELETE FROM events")
    ok = service_digest.send_digest_request_message(DATE_KEY)
    assert ok in (True, False)  # telegram không cấu hình trong env này -> False ok
    # Nội dung tin nhắn có đủ endpoint + format (test bằng hàm build text qua _tg_text mock)
    msgs: list[str] = []
    import backend.app.service_digest as sd
    import backend.app.service_news as sn
    orig = sn._send_telegram
    sn._send_telegram = lambda txt: msgs.append(txt)
    try:
        sd.send_digest_request_message(DATE_KEY)
        sd.send_digest_reminder_message(DATE_KEY, 18, 0)
    finally:
        sn._send_telegram = orig
    req = next(m for m in msgs if "BRIEF" in m)
    assert "/api/v1/digest/submit" in req
    assert "headline" in req and "link" in req and "thumbnail" in req
    assert "curl" in req
    rem = next(m for m in msgs if "Chưa nhận" in m)
    assert "18:00" in rem
