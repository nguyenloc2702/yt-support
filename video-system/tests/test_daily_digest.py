"""Tests for Daily International EN Digest (@StickSto · semi-auto 06:30 America/New_York)."""
from __future__ import annotations

import pytest

from backend.app import service_digest, service_news
from backend.app.pipeline_short import article_images, overlay_renderer, prompts, story_media, tts_vi
from conftest import make_fake_bank_image


def _make_digest_script(count: int = 12) -> dict:
    hook = "Here are twelve global stories you should know today in under three minutes."
    stories = []
    cats = ["WORLD", "MARKETS", "TECH", "SCIENCE", "SPORTS"]
    for i in range(1, count + 1):
        stories.append({
            "source_index": i,
            "category": cats[(i - 1) % len(cats)],
            "headline": f"Global development number {i} reshapes markets worldwide",
            "hero_stat": f"${i * 10}B" if i % 2 == 0 else f"NOV {10 + i}",
            "hero_sub": f"major impact update across international sector {i}",
            "narration": (
                f"Story {i} brings a major global shift as international officials confirmed "
                f"nearly {i * 10} billion dollars in strategic agreements affecting multiple regions today."
            ),
            "search_keywords_en": f"international business summit {i}",
        })
    closing = "That's your global brief for today — follow @StickSto and let's get to work."
    return {
        "title": "12 Global Stories Today | @StickSto Daily Brief",
        "hook": hook,
        "stories": stories,
        "beats": [s["narration"] for s in stories],
        "closing": closing,
        "hashtags": {"youtube": ["#shorts", "#worldnews"], "tiktok": ["#dailybrief"]},
        "description": "12 global headlines in under 3 minutes by @StickSto.",
        "source_note": "Sources: BBC, CNBC, Al Jazeera | @StickSto",
    }


def _fake_rss_en(count: int = 14) -> str:
    items_xml = []
    topics = [
        "US China summit leaders meet in Beijing for trade talks",
        "Iran oil tanker incident near Strait of Hormuz spikes crude prices",
        "European Central Bank holds interest rates steady amid inflation slowdown",
        "Apple unveils next generation AI chip architecture for data centers",
        "Estonia closes eastern border crossings starting next month",
        "England defeats Spain three two ending historic unbeaten streak",
        "SpaceX prepares next orbital flight test for heavy launch vehicle",
        "Global shipping rates jump across Red Sea maritime corridors",
        "Japanese yen strengthens sharply after central bank policy signal",
        "United Nations climate conference approves new clean energy fund",
        "Semiconductor alliance announces multibillion euro factory expansion",
        "Boeing reaches tentative labor agreement to resume commercial jet output",
        "Amazon launches autonomous drone delivery hubs across European cities",
        "World Health Organization certifies malaria vaccine milestone in Africa",
        "Canada central bank signals cautious pace on monetary easing cycle",
        "Hyundai invests heavily in next generation solid state battery factories",
        "Norwegian sovereign wealth fund reports quarterly infrastructure gains",
        "Brazil expands offshore wind turbine auction zones along Atlantic coast",
        "Airbus delivers record commercial narrowbody jets during quarterly push",
        "India space agency completes cryogenic engine burn for lunar probe",
        "Singapore maritime authority trials green methanol bunkering ships",
        "Swiss watchmakers post strong luxury export rebound across Zurich",
        "Australian lithium miners sign long term battery refining partnerships",
        "German rail operator tests hydrogen passenger locomotives on regional routes",
        "Chile copper output climbs amid modernized desert desalination plants",
        "South Korea nuclear regulator approves reactor life extension plan",
        "UEFA introduces updated financial sustainability audit rules for clubs",
        "NASA telescope detects unexpected atmospheric methane on distant exoplanet",
        "UK renewable wind generation reaches new winter electricity share",
    ]
    for i in range(count):
        t = topics[i] if i < len(topics) else f"International dispatch story number {i + 1} highlights trade growth"
        src = ["BBC World", "CNBC", "Al Jazeera", "Guardian Tech"][i % 4]
        items_xml.append(
            f"<item><title>{t} - {src}</title>"
            f"<link>https://world.example.com/story-{i + 1}</link>"
            f"<pubDate>Tue, 18 Nov 2026 06:00:00 GMT</pubDate>"
            f"<description>Detailed international briefing for story {i + 1}.</description>"
            f"<source url=\"https://example.com\">{src}</source></item>"
        )
    return '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>' + "".join(items_xml) + "</channel></rss>"


def test_prompts_validate_daily_digest_keeps_all_stories():
    raw = _make_digest_script(13)
    clean = prompts.validate_script("daily_digest", raw, lang="en")
    assert len(clean["beats"]) == 13
    assert len(clean["stories"]) == 13
    assert clean["stories"][0]["category"] == "WORLD"
    assert clean["stories"][1]["hero_stat"] == "$20B"
    assert 250 <= clean["word_count"] <= 430


def test_overlay_renderer_digest_frame_builds_rgba():
    if not overlay_renderer.is_available():
        pytest.skip("Pillow/fonts unavailable")
    meta = {
        "seq": 2,
        "story_idx": 1,
        "category": "WORLD",
        "headline": "Global Leaders Meet for Historic Trade Summit",
        "hero_stat": "NOV 19",
        "hero_sub": "bilateral talks scheduled in Beijing",
    }
    base = overlay_renderer._make_digest_base(meta, 14, "@STICKSTO · DAILY BRIEF", "@StickSto")
    assert base.size == (1080, 1920)
    chunk = [{"text": "Leaders"}, {"text": "will"}, {"text": "meet"}, {"text": "today"}]
    frame = overlay_renderer._draw_digest_subtitles(base, chunk, 1)
    assert frame.size == (1080, 1920)


def test_dedupe_candidates_drops_duplicate_events(store_fixture):
    store_fixture.execute("DELETE FROM news_items")
    rows = [
        {"id": "n1", "source": "BBC", "title": "Trump Xi Putin hold historic summit meeting in Beijing", "link": "https://a/1", "summary": "", "status": "new", "fetched_at": 100.0},
        {"id": "n2", "source": "CNBC", "title": "Trump Xi Putin hold summit meeting in Beijing today", "link": "https://a/2", "summary": "", "status": "new", "fetched_at": 99.0},
        {"id": "n3", "source": "BBC", "title": "SpaceX launches giant rocket booster into orbit", "link": "https://a/3", "summary": "", "status": "new", "fetched_at": 98.0},
    ]
    deduped = service_digest._dedupe_candidates(rows)
    titles = [r["id"] for r in deduped]
    assert titles == ["n1", "n3"]


def test_story_media_relevance_filter_rejects_unrelated(tmp_path, monkeypatch):
    # Liên quan chặt chẽ -> True
    assert story_media.is_relevant_candidate(
        "File:Oil tanker AbQaiq near Strait of Hormuz.jpg",
        "Strait of Hormuz oil tanker",
        "Iran oil tanker hit near Hormuz",
    ) is True
    # Lệch chủ đề hoàn toàn -> False (không đưa vào)
    assert story_media.is_relevant_candidate(
        "File:Cute cat sleeping on sofa.jpg",
        "Strait of Hormuz oil tanker",
        "Iran oil tanker hit near Hormuz",
    ) is False
    # Chặn khớp nhầm địa danh (vd: "Mission Santa Clara" khi tìm "Nvidia headquarters Santa Clara")
    assert story_media.is_relevant_candidate(
        "Mission Santa Clara de Asís",
        "Nvidia headquarters Santa Clara",
        "NVIDIA crushes Q3 revenue",
    ) is False
    # Chặn flag / logo / coat of arms
    assert story_media.is_relevant_candidate(
        "File:Flag of Iran.svg",
        "Iran oil tanker",
        "Iran oil tanker hit near Hormuz",
    ) is False

    # Test collect_story_visuals ghép ảnh bài báo + Wikimedia Commons đúng chủ đề (loại ảnh lệch)
    from PIL import Image
    import hashlib
    art_img = tmp_path / "art.png"
    Image.new("RGB", (600, 600), (10, 40, 80)).save(art_img)
    monkeypatch.setattr(article_images, "collect_article_images", lambda *a, **kw: [art_img])
    monkeypatch.setattr(
        story_media,
        "_search_wikipedia_image",
        lambda q, h: [
            {"url": "https://img.test/relevant_wiki.jpg", "attribution": "Wikipedia: Strait of Hormuz", "source": "wikipedia"},
        ],
    )
    monkeypatch.setattr(
        story_media,
        "_search_commons_images",
        lambda q, h, limit=4: [
            {"url": "https://img.test/relevant_ship.jpg", "attribution": "Wikimedia: Hormuz tanker", "source": "commons"},
        ],
    )

    def _fake_dl(url, out_path, seen_hashes):
        im = Image.new("RGB", (600, 600), (90 if "wiki" in url else 140, 20, 30))
        import io
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        raw = buf.getvalue()
        h = hashlib.sha1(raw).hexdigest()
        if h in seen_hashes:
            return None
        seen_hashes.add(h)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw)
        return out_path

    monkeypatch.setattr(story_media, "_download_valid_image", _fake_dl)
    res = story_media.collect_story_visuals(
        article_link="https://world.example.com/hormuz",
        headline="Iran oil tanker incident near Strait of Hormuz",
        queries=["Strait of Hormuz oil tanker"],
        out_dir=tmp_path / "out",
        target_images=3,
    )
    assert len(res) == 3
    assert [r["source"] for r in res] == ["article", "wikipedia", "commons"]


def test_run_daily_digest_e2e_and_api_endpoints(client, store_fixture, ingest, read_key, short_adapters, monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    store_fixture.execute("DELETE FROM news_items")
    store_fixture.execute("DELETE FROM events")
    make_fake_bank_image("digest_bank.png")

    monkeypatch.setattr(
        service_digest._cfg,
        "NEWS_CONFIG",
        {
            "interval_min": 60,
            "max_items_per_push": 10,
            "feeds_en": [{"name": "BBC World", "url": "https://feeds.example.com/world.xml"}],
            "queries_en": [],
            "exclude_keywords_en": ["gore"],
            "daily_digest": {
                "enabled": True,
                "timezone": "America/New_York",
                "run_at": "06:30",
                "min_stories": 12,
                "max_stories": 15,
                "lookback_hours": 30,
                "brand_header": "@STICKSTO · DAILY BRIEF",
                "signature": "@StickSto",
                "send_video_to_telegram": True,
            },
            "telegram": {"parse_mode": "HTML", "disable_web_page_preview": False},
        },
    )
    monkeypatch.setattr(service_news, "NEWS_CONFIG", service_digest._cfg.NEWS_CONFIG)
    rss_xml = _fake_rss_en(28)
    monkeypatch.setattr(service_news, "_fetch_rss", lambda url: rss_xml)
    short_adapters["llm"].script = _make_digest_script(12)

    # Mock strict-relevance story_media collection so no network calls happen
    from PIL import Image
    sample_img = tmp_path / "story_img.png"
    Image.new("RGB", (540, 960), (20, 50, 90)).save(sample_img)

    def _fake_collect(*, article_link, headline, queries, out_dir, target_images=3, seen_hashes=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        dst1 = out_dir / "rel_01.png"
        dst2 = out_dir / "rel_02.png"
        dst1.write_bytes(sample_img.read_bytes())
        dst2.write_bytes(sample_img.read_bytes())
        return [
            {"path": dst1, "attribution": "Article original photo", "source": "article"},
            {"path": dst2, "attribution": "Wikipedia: Global Summit", "source": "wikipedia"},
        ]

    monkeypatch.setattr(story_media, "collect_story_visuals", _fake_collect)

    tg_msgs = []
    tg_videos = []
    monkeypatch.setenv("VIDEO_TELEGRAM_BOT_TOKEN", "bot-test-token")
    monkeypatch.setenv("VIDEO_TELEGRAM_CHAT_ID", "1383335819")
    monkeypatch.setattr(service_news, "_send_telegram", lambda txt: tg_msgs.append(txt))
    monkeypatch.setattr(service_digest, "_send_telegram_video", lambda path, cap: tg_videos.append((str(path), cap)) or True)

    # Scope checks on new endpoints
    assert client.get("/api/v1/shorts/daily-digest/status", headers=read_key).status_code == 200
    assert client.post("/api/v1/shorts/daily-digest/run", headers=read_key).status_code == 403

    r = client.post("/api/v1/shorts/daily-digest/run", headers=ingest)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["stories_count"] == 12
    assert res["short"]["mode"] == "daily_digest"
    assert res["short"]["lang"] == "en"
    assert res["short"]["status"] == "ready"
    assert res["short"]["script"]["brand_header"] == "@STICKSTO · DAILY BRIEF"
    assert res["short"]["script"]["signature"] == "@StickSto"
    assert "2" in res["short"]["script"]["story_links"]
    assert res["telegram"]["message_sent"] is True
    assert res["telegram"]["video_sent"] is True
    assert len(tg_msgs) == 1 and "@StickSto" in tg_msgs[0]
    assert len(tg_videos) == 1

    # Manual API trigger (actor='api') does NOT cancel scheduled run;
    # second scheduled run (actor='digest-scheduler') for same NY date skips cleanly
    from backend.app.main import shorts as main_shorts
    sched1 = service_digest.run_daily_digest(store_fixture, main_shorts, force=False, actor="digest-scheduler")
    assert sched1.get("skipped") is None
    dup = service_digest.run_daily_digest(store_fixture, main_shorts, force=False, actor="digest-scheduler")
    assert dup.get("skipped") is True

    # Status reports last_run & 06:30 schedule
    st = client.get("/api/v1/shorts/daily-digest/status", headers=read_key).json()
    assert st["run_at"] == "06:30"
    assert st["timezone"] == "America/New_York"
    assert st["last_run"]["short_id"] == sched1["short_id"]


def test_tts_en_pronunciation_normalization_and_moving_watermark(tmp_path):
    s = tts_vi.normalize_en_tts_text("U.S. and E.U. Q3 deal hits $1.24T and €2.5bn — follow @StickSto today 15%!")
    assert "US" in s and "U.S." not in s
    assert "EU" in s and "E.U." not in s
    assert "third quarter" in s
    assert "1.24 trillion dollars" in s
    assert "2.5 billion euros" in s
    assert "Stick Sto" in s and "@" not in s
    assert "15 percent" in s

    if overlay_renderer.is_available():
        wm = tmp_path / "wm.png"
        assert overlay_renderer._render_moving_watermark_png("@StickSto", wm, opacity=0.22, font_size=36) is True
        assert wm.exists() and wm.stat().st_size > 100


def test_cross_day_dedupe_blocks_repeat_topics_across_days(store_fixture):
    store_fixture.execute("DELETE FROM news_items")
    # Yesterday's used news item + fresh today item from another outlet on SAME story + genuinely new item
    from backend.app.store import now as _now
    t = _now()
    store_fixture.execute(
        "INSERT INTO news_items (id, link_hash, source, title, link, summary, query, lang, published_at, fetched_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("n_old", "h1", "BBC", "Iran tanker incident near Strait of Hormuz spikes crude prices", "https://bbc/1", "", "", "en", t - 36000, t - 36000, "used"),
    )
    store_fixture.execute(
        "INSERT INTO news_items (id, link_hash, source, title, link, summary, query, lang, published_at, fetched_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("n_repeat", "h2", "CNBC", "Crude oil prices surge after Iran tanker strike near Strait of Hormuz", "https://cnbc/2", "", "", "en", t - 600, t - 600, "new"),
    )
    store_fixture.execute(
        "INSERT INTO news_items (id, link_hash, source, title, link, summary, query, lang, published_at, fetched_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("n_fresh", "h3", "Guardian", "Japan launches deep ocean mineral exploration robot vessel", "https://guard/3", "", "", "en", t - 300, t - 300, "new"),
    )
    recent = service_digest.recent_covered_titles(store_fixture, lookback_hours=72)
    assert any("Hormuz" in x for x in recent)
    picked = service_digest.select_candidates(store_fixture, recent_titles=recent)
    picked_ids = [p["id"] for p in picked]
    assert "n_fresh" in picked_ids
    assert "n_repeat" not in picked_ids


def test_overlay_renderer_concat_duration_matches_audio_zero_drift(tmp_path, monkeypatch):
    if not overlay_renderer.is_available():
        pytest.skip("Pillow/fonts unavailable")
    # Tạo 100 từ giả có khe hở 0.012s giữa mỗi từ (từng làm trôi ~1.2s)
    words = []
    t = 0.0
    for i in range(100):
        words.append({"text": f"w{i}", "start": round(t, 3), "end": round(t + 0.25, 3), "cue_idx": i // 20})
        t += 0.262  # khe 0.012s < 0.015s
    dur = round(t + 0.5, 3)

    monkeypatch.setattr(overlay_renderer.subprocess, "run", lambda *a, **kw: None)
    base_mp4 = tmp_path / "base.mp4"
    wav = tmp_path / "voice.wav"
    out_mp4 = tmp_path / "out.mp4"
    base_mp4.write_bytes(b"MP4")
    wav.write_bytes(b"WAV")

    ok = overlay_renderer.render_overlay_and_mux(
        base_mp4, wav, dur, words, out_mp4, tmp_path,
        preset="daily_digest",
        digest_spec={"brand_header": "@STICKSTO", "signature": "@StickSto", "total_stories": 3,
                     "scenes": [{"seq": i + 1, "story_idx": i, "start_sec": i * 5.0, "end_sec": (i + 1) * 5.0,
                                 "category": "WORLD", "headline": "H", "hero_stat": "1", "hero_sub": "S"} for i in range(5)]},
    )
    assert ok is True
    ov_txt = (tmp_path / "ov_concat.txt").read_text(encoding="utf-8")
    durs = [float(line.split()[1]) for line in ov_txt.splitlines() if line.startswith("duration ")]
    assert abs(sum(durs) - dur) <= 0.04


def test_fluff_filter_removes_reviews_opinions_and_essays():
    rows = [
        {"id": "1", "source": "Guardian", "title": "Grand Theft Auto VI review – love story", "link": "https://a/1"},
        {"id": "2", "source": "Guardian", "title": "David Beckham perfume | Arwa Mahdawi", "link": "https://a/2"},
        {"id": "3", "source": "NPR", "title": "Greetings from a Berlin square statue", "link": "https://a/3"},
        {"id": "4", "source": "BBC", "title": "I want to know if toxic lagoon fire caused illness", "link": "https://a/4"},
        {"id": "5", "source": "CNBC", "title": "US Congress passes $90B semiconductor defense bill", "link": "https://a/5"},
    ]
    picked = service_digest._dedupe_candidates(rows)
    assert [r["id"] for r in picked] == ["5"]


