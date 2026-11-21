"""Tests for YouTube Channel OAuth 2.0, QuotaLedger, Metadata Patch, and Resumable Publishing."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.app import main as app_main
from backend.app.service_youtube import YouTubeService


@pytest.fixture()
def yt_env(monkeypatch):
    fkey = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "yt-client-id-123")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "yt-client-sec-456")
    monkeypatch.setenv("YOUTUBE_TOKEN_ENCRYPTION_KEY", fkey)
    monkeypatch.setenv("YOUTUBE_UPLOAD_CHUNK_MB", "1")
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    return fkey


def _setup_mock_youtube(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    yt = YouTubeService(
        app_main.store,
        app_main.webhooks,
        http_factory=lambda: httpx.Client(transport=transport, timeout=30.0),
        poll_interval_sec=0.0,
        poll_max_attempts=2,
        retry_sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(app_main, "youtube", yt)
    return yt


def _connect_sample_channel(yt: YouTubeService, store, *, expires_in: float = 3600.0):
    t = time.time()
    store.execute("DELETE FROM youtube_channels")
    store.execute("DELETE FROM youtube_uploads")
    store.execute(
        "INSERT INTO youtube_channels "
        "(id,channel_id,title,custom_url,thumbnail_url,uploads_playlist_id,"
        "subscriber_count,video_count,access_token_enc,refresh_token_enc,"
        "token_expires_at,scopes,status,is_default,connected_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ytc_test1",
            "UC_TEST_123",
            "StickSto Test Channel",
            "@StickStoTest",
            "https://img.example/avatar.jpg",
            "UU_TEST_123",
            1200,
            15,
            yt.tokens.encrypt("access-tok-1"),
            yt.tokens.encrypt("refresh-tok-1"),
            t + expires_in,
            "https://www.googleapis.com/auth/youtube.upload",
            "connected",
            1,
            t,
            t,
        ),
    )


def test_oauth_start_callback_and_replay_protection(client, read_key, yt_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com" and req.url.path == "/token":
            return httpx.Response(200, json={
                "access_token": "acc_live_1",
                "refresh_token": "ref_live_1",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/youtube.upload",
            })
        if req.url.host == "www.googleapis.com" and req.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={
                "items": [{
                    "id": "UC_CHAN_99",
                    "snippet": {"title": "My Finance Channel", "customUrl": "@myfinance", "thumbnails": {"default": {"url": "https://yt/a.jpg"}}},
                    "statistics": {"subscriberCount": "4500", "videoCount": "42"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU_CHAN_99"}},
                }]
            })
        raise AssertionError(f"Unexpected request: {req.method} {req.url}")

    _setup_mock_youtube(monkeypatch, handler)

    r_start = client.get("/api/v1/youtube/auth/start", headers=read_key)
    assert r_start.status_code == 200, r_start.text
    auth_url = r_start.json()["auth_url"]
    assert "yt-client-id-123" in auth_url
    parsed = httpx.URL(auth_url)
    state = parsed.params["state"]

    # Callback succeeds and redirects to /#/youtube?yt=connected
    r_cb = client.get(f"/api/v1/youtube/auth/callback?code=auth_code_xyz&state={state}", follow_redirects=False)
    assert r_cb.status_code == 302
    assert r_cb.headers["location"] == "/#/youtube?yt=connected"

    # Replaying same state must fail with error redirect
    r_replay = client.get(f"/api/v1/youtube/auth/callback?code=auth_code_xyz&state={state}", follow_redirects=False)
    assert r_replay.status_code == 302
    assert "yt=error" in r_replay.headers["location"]

    # Tokens stored encrypted, status returns channel
    r_st = client.get("/api/v1/youtube/status", headers=read_key)
    assert r_st.status_code == 200
    st = r_st.json()
    assert st["configured"] is True
    assert st["connected"] is True
    assert st["channel"]["channel_id"] == "UC_CHAN_99"
    row = app_main.store.query_one("SELECT access_token_enc, refresh_token_enc FROM youtube_channels WHERE channel_id='UC_CHAN_99'")
    assert row["access_token_enc"] != "acc_live_1"
    assert row["refresh_token_enc"] != "ref_live_1"


def test_token_auto_refresh_and_invalid_grant(client, read_key, yt_env, monkeypatch):
    calls = {"mode": "refresh_ok"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com" and req.url.path == "/token":
            if calls["mode"] == "refresh_ok":
                return httpx.Response(200, json={"access_token": "acc_refreshed", "expires_in": 3600})
            return httpx.Response(400, json={"error": "invalid_grant"})
        if req.url.path == "/youtube/v3/playlistItems":
            assert req.headers["Authorization"] == "Bearer acc_refreshed"
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"Unexpected: {req.url}")

    yt = _setup_mock_youtube(monkeypatch, handler)
    _connect_sample_channel(yt, app_main.store, expires_in=30.0)  # < 120s triggers refresh

    r1 = client.get("/api/v1/youtube/videos", headers=read_key)
    assert r1.status_code == 200, r1.text

    # Force expiry again and return invalid_grant
    app_main.store.execute("UPDATE youtube_channels SET token_expires_at=? WHERE channel_id='UC_TEST_123'", (time.time() - 10,))
    calls["mode"] = "invalid_grant"
    r2 = client.get("/api/v1/youtube/videos", headers=read_key)
    assert r2.status_code == 401
    row = app_main.store.query_one("SELECT status FROM youtube_channels WHERE channel_id='UC_TEST_123'")
    assert row["status"] == "expired"


def test_list_videos_never_calls_search_and_patch_merges_category(client, ingest, read_key, yt_env, monkeypatch):
    seen_paths: list[str] = []
    updated_payloads: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_paths.append(req.url.path)
        if req.url.path == "/youtube/v3/playlistItems":
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": "vid_abc"}}]})
        if req.url.path == "/youtube/v3/videos" and req.method == "GET":
            return httpx.Response(200, json={
                "items": [{
                    "id": "vid_abc",
                    "snippet": {
                        "title": "Old Title",
                        "description": "Old Desc",
                        "tags": ["old"],
                        "categoryId": "27",
                        "publishedAt": "2026-11-10T00:00:00Z",
                        "thumbnails": {"default": {"url": "https://img/v.jpg"}},
                    },
                    "statistics": {"viewCount": "500", "likeCount": "30", "commentCount": "5"},
                    "status": {"privacyStatus": "private"},
                }]
            })
        if req.url.path == "/youtube/v3/videos" and req.method == "PUT":
            body = json.loads(req.content.decode("utf-8"))
            updated_payloads.append(body)
            return httpx.Response(200, json=body)
        raise AssertionError(f"Unexpected: {req.method} {req.url}")

    yt = _setup_mock_youtube(monkeypatch, handler)
    _connect_sample_channel(yt, app_main.store)
    app_main.store.execute("DELETE FROM youtube_quota_ledger")

    r_list = client.get("/api/v1/youtube/videos", headers=read_key)
    assert r_list.status_code == 200, r_list.text
    assert "/youtube/v3/search" not in seen_paths
    assert r_list.json()["videos"][0]["video_id"] == "vid_abc"

    r_patch = client.patch(
        "/api/v1/youtube/videos/vid_abc",
        headers={"Authorization": "Bearer test-admin"},
        json={"title": "New Title", "privacy_status": "unlisted"},
    )
    assert r_patch.status_code == 200, r_patch.text
    assert updated_payloads[0]["snippet"]["categoryId"] == "27"
    assert updated_payloads[0]["snippet"]["description"] == "Old Desc"
    assert updated_payloads[0]["snippet"]["title"] == "New Title"
    assert updated_payloads[0]["status"]["privacyStatus"] == "unlisted"


def test_publish_todo_resumable_chunks_thumbnail_captions_and_guards(client, ingest, read_key, yt_env, monkeypatch):
    # Create todo item + fake final_en.mp4 (>1MB to test 2 chunks!) + thumbnail + srt
    r_cr = client.post("/api/v1/todos/from-script", headers=ingest, json={
        "title": "YouTube Publish Flow",
        "script_text": "Global investors watch central bank liquidity closely. One clear chart explains why markets move fast.",
        "bgm_enabled": False,
        "collect_references": False,
    })
    assert r_cr.status_code == 201
    item_id = r_cr.json()["item_id"]

    # Missing final_en.mp4 -> 422
    admin_hdr = {"Authorization": "Bearer test-admin"}
    yt = _setup_mock_youtube(monkeypatch, lambda req: httpx.Response(200, json={}))
    _connect_sample_channel(yt, app_main.store)
    r_nofinal = client.post(f"/api/v1/todos/{item_id}/youtube/publish", headers=admin_hdr, json={"title": "T"})
    assert r_nofinal.status_code == 422

    # Create final_en.mp4 (~1.2MB) without thumbnail -> 422
    exports_dir = Path(yt._final_video_path(item_id)).parent
    exports_dir.mkdir(parents=True, exist_ok=True)
    yt._final_video_path(item_id).write_bytes(b"\x00" * (1200 * 1024))
    r_nothumb = client.post(f"/api/v1/todos/{item_id}/youtube/publish", headers=admin_hdr, json={"title": "T"})
    assert r_nothumb.status_code == 422

    # Upload thumbnail
    r_th = client.post(
        f"/api/v1/todos/{item_id}/images",
        headers=ingest,
        files={"file": ("thumb.png", b"\x89PNG\r\n\x1a\n" + b"0" * 600, "image/png")},
        data={"kind": "thumbnail"},
    )
    assert r_th.status_code == 201

    # Past publish_at -> 422
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    r_past = client.post(
        f"/api/v1/todos/{item_id}/youtube/publish",
        headers=admin_hdr,
        json={"title": "T", "publish_at": past_iso, "privacy_status": "public"},
    )
    assert r_past.status_code == 422

    # Mock resumable 2-chunk upload + processing poll + thumbnail + captions
    put_ranges: list[str] = []
    called_ops: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/upload/youtube/v3/videos" and req.method == "POST":
            called_ops.append("init")
            return httpx.Response(200, headers={"Location": "https://upload.yt.example/session/abc"})
        if req.url.host == "upload.yt.example" and req.method == "PUT":
            cr = req.headers.get("Content-Range", "")
            put_ranges.append(cr)
            if cr.startswith("bytes 0-"):
                return httpx.Response(308, headers={"Range": f"bytes=0-{1024*1024 - 1}"})
            return httpx.Response(200, json={"id": "yt_vid_999"})
        if req.url.path == "/youtube/v3/videos" and req.method == "GET":
            called_ops.append("poll")
            return httpx.Response(200, json={"items": [{"id": "yt_vid_999", "processingDetails": {"processingStatus": "succeeded"}}]})
        if req.url.path == "/upload/youtube/v3/thumbnails/set":
            called_ops.append("thumb")
            return httpx.Response(200, json={"items": []})
        if req.url.path == "/upload/youtube/v3/captions":
            called_ops.append("captions")
            return httpx.Response(200, json={"id": "cap_1"})
        raise AssertionError(f"Unexpected: {req.method} {req.url}")

    yt = _setup_mock_youtube(monkeypatch, handler)
    future_iso = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    res = yt.publish_todo(
        item_id,
        title="Published Video Title",
        description="Desc",
        tags=["ai", "video"],
        privacy_status="public",  # must be forced to private due to publish_at
        publish_at=future_iso,
        upload_captions=True,
        run_async=False,
    )
    assert res["upload_status"] == "uploaded"
    assert res["youtube_video_id"] == "yt_vid_999"
    assert res["youtube_url"] == "https://www.youtube.com/watch?v=yt_vid_999"
    assert res["privacy_status"] == "private"
    assert res["thumbnail_synced"] is True
    assert res["captions_synced"] is True
    assert len(put_ranges) == 2
    assert called_ops == ["init", "poll", "thumb", "captions"]

    # Calling publish again returns already_published=True (200)
    r_dup = client.post(
        f"/api/v1/todos/{item_id}/youtube/publish",
        headers=admin_hdr,
        json={"title": "Duplicate Attempt"},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["already_published"] is True

    # Quota exceeded check
    app_main.store.execute(
        "INSERT OR REPLACE INTO youtube_quota_ledger (day, units_used) VALUES (?, ?)",
        (yt.quota.current_day(), 8000),
    )
    with pytest.raises(Exception) as exc_info:
        yt.quota.ensure_upload_budget()
    assert exc_info.value.status_code == 429


def test_startup_sweep_and_disconnect(client, ingest, yt_env, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "oauth2.googleapis.com" and req.url.path == "/revoke":
            return httpx.Response(200)
        return httpx.Response(200, json={})

    yt = _setup_mock_youtube(monkeypatch, handler)
    _connect_sample_channel(yt, app_main.store)
    app_main.store.execute("DELETE FROM youtube_quota_ledger")
    t = time.time()
    # Record with no session URI -> startup sweep marks failed
    app_main.store.execute(
        "INSERT INTO youtube_uploads (id,item_id,channel_id,title,upload_status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ytu_broken", "itm_x", "UC_TEST_123", "Broken", "uploading", t, t),
    )
    yt.startup_sweep(run_async=False)
    r_broken = yt.get_upload("ytu_broken")
    assert r_broken["upload_status"] == "failed"

    # Active queued upload gets canceled when channel disconnected
    app_main.store.execute(
        "INSERT INTO youtube_uploads (id,item_id,channel_id,title,upload_status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("ytu_q", "itm_y", "UC_TEST_123", "Queued", "queued", t, t),
    )
    r_disc = client.delete("/api/v1/youtube/channel", headers={"Authorization": "Bearer test-admin"})
    assert r_disc.status_code == 200
    assert yt.get_upload("ytu_q")["upload_status"] == "canceled"


def test_publish_short_immediate_scheduled_and_ui_hydration(client, ingest, read_key, yt_env, monkeypatch):
    admin_hdr = {"Authorization": "Bearer test-admin"}
    init_bodies: list[dict] = []
    cap_langs: list[str] = []
    called_ops: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/upload/youtube/v3/videos" and req.method == "POST":
            called_ops.append("init")
            init_bodies.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(200, headers={"Location": "https://upload.yt.example/session/short1"})
        if req.url.host == "upload.yt.example" and req.method == "PUT":
            cr = req.headers.get("Content-Range", "")
            if cr.startswith("bytes 0-1048575/") and not cr.endswith("/1048576"):
                return httpx.Response(308, headers={"Range": f"bytes=0-{1024*1024 - 1}"})
            return httpx.Response(200, json={"id": "yt_short_1"})
        if req.url.path == "/youtube/v3/videos" and req.method == "GET":
            called_ops.append("poll")
            return httpx.Response(
                200,
                json={"items": [{"id": "yt_short_1", "processingDetails": {"processingStatus": "succeeded"}}]},
            )
        if req.url.path == "/youtube/v3/channels":
            return httpx.Response(
                200,
                json={"items": [{"id": "UC_TEST_123", "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST_123"}}}]},
            )
        if req.url.path == "/youtube/v3/playlistItems":
            return httpx.Response(200, json={"items": []})
        if req.url.path == "/upload/youtube/v3/thumbnails/set":
            called_ops.append("thumb")
            return httpx.Response(200, json={})
        if req.url.path == "/upload/youtube/v3/captions":
            called_ops.append("captions")
            cap_langs.append(req.content.decode("utf-8", errors="ignore"))
            return httpx.Response(200, json={"id": "cap_short_1"})
        raise AssertionError(f"Unexpected request: {req.method} {req.url}")

    yt = _setup_mock_youtube(monkeypatch, handler)
    _connect_sample_channel(yt, app_main.store)
    app_main.store.execute("DELETE FROM youtube_quota_ledger")

    # 1. Missing short -> 404
    r_404 = client.post("/api/v1/shorts/sho_missing/youtube/publish", headers=admin_hdr, json={"title": "Nope"})
    assert r_404.status_code == 404

    # 2. Short without MP4 -> 422
    t = time.time()
    app_main.store.execute(
        "INSERT INTO shorts (id, mode, lang, title, status, script_json, source_text, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("sho_nomp4", "news", "vi", "No MP4 Short", "scripted", "{}", "raw", t, t),
    )
    r_nomp4 = client.post("/api/v1/shorts/sho_nomp4/youtube/publish", headers=admin_hdr, json={"title": "No MP4"})
    assert r_nomp4.status_code == 422
    assert r_nomp4.json()["error"] == "missing_final_video"

    # 3. Upload valid MP4 Short (no thumbnail required) & immediate publish via HTTP endpoint
    mp4_small = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 512
    r_up1 = client.post(
        "/api/v1/shorts/upload",
        headers=admin_hdr,
        data={"title": "Immediate Short Product", "mode": "news", "lang": "en"},
        files={"file": ("imm_short.mp4", BytesIO(mp4_small), "video/mp4")},
    )
    assert r_up1.status_code == 201
    sid1 = r_up1.json()["id"]

    r_pub1 = client.post(
        f"/api/v1/shorts/{sid1}/youtube/publish",
        headers=admin_hdr,
        json={
            "title": "Immediate Short #Shorts",
            "description": "Fast update\n\n#Shorts",
            "tags": ["Shorts", "news"],
            "privacy_status": "public",
            "upload_captions": True,
        },
    )
    assert r_pub1.status_code == 202
    uid1 = r_pub1.json()["upload_id"]
    assert r_pub1.json()["target_type"] == "short"

    # Wait briefly for worker thread to finish
    status_data = None
    for _ in range(50):
        ru = client.get(f"/api/v1/youtube/uploads/{uid1}", headers=read_key)
        status_data = ru.json()
        if status_data["upload_status"] in ("uploaded", "failed"):
            break
        time.sleep(0.02)
    assert status_data is not None
    assert status_data["upload_status"] == "uploaded"
    assert status_data["target_type"] == "short"
    assert status_data["thumbnail_synced"] is False
    assert status_data["captions_synced"] is False
    assert "thumb" not in called_ops
    assert init_bodies[-1]["status"]["privacyStatus"] == "public"
    assert "publishAt" not in init_bodies[-1]["status"]

    # Hydration on GET /api/v1/shorts & GET /api/v1/shorts/{id} & /api/v1/youtube/videos
    r_list = client.get("/api/v1/shorts", headers=read_key)
    assert r_list.status_code == 200
    matched = [s for s in r_list.json()["shorts"] if s["id"] == sid1]
    assert matched and matched[0]["youtube_upload"]["upload_status"] == "uploaded"
    assert matched[0]["youtube_upload"]["youtube_video_id"] == "yt_short_1"

    r_get = client.get(f"/api/v1/shorts/{sid1}", headers=read_key)
    assert r_get.status_code == 200
    assert r_get.json()["youtube_upload"]["youtube_url"] == "https://www.youtube.com/watch?v=yt_short_1"

    r_ytv = client.get("/api/v1/youtube/videos", headers=read_key)
    assert r_ytv.status_code == 200
    yt_up = [u for u in r_ytv.json()["uploads"] if u["upload_id"] == uid1]
    assert yt_up and yt_up[0]["target_type"] == "short"

    # Duplicate publish guard -> 200 already_published
    r_dup = client.post(
        f"/api/v1/shorts/{sid1}/youtube/publish",
        headers=admin_hdr,
        json={"title": "Dup"},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["already_published"] is True

    # 4. Scheduled publish (>1MB 2 chunks) + VI SRT captions on second Short
    mp4_big = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * (1200 * 1024)
    r_up2 = client.post(
        "/api/v1/shorts/upload",
        headers=admin_hdr,
        data={"title": "Scheduled VI Short", "mode": "decode", "lang": "vi"},
        files={"file": ("sched_short.mp4", BytesIO(mp4_big), "video/mp4")},
    )
    assert r_up2.status_code == 201
    sid2 = r_up2.json()["id"]

    # Attach an SRT asset to sid2
    srt_rel = f"shorts/{sid2}/subtitles.srt"
    srt_file = yt._resolve_upload_artifacts("short", sid2)["video_path"].parent / "subtitles.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:02,000\nXin chào!\n", encoding="utf-8")
    app_main.store.execute(
        "INSERT INTO assets (id, item_id, kind, storage_key, sha256, size, provenance_engine, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("ast_srt_2", None, "subtitle", srt_rel, "0" * 64, srt_file.stat().st_size, "test", t),
    )
    app_main.store.execute("UPDATE shorts SET srt_asset_id=? WHERE id=?", ("ast_srt_2", sid2))

    # Past publish_at rejected -> 422
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    r_past = client.post(
        f"/api/v1/shorts/{sid2}/youtube/publish",
        headers=admin_hdr,
        json={"title": "Past", "publish_at": past_iso, "privacy_status": "public"},
    )
    assert r_past.status_code == 422

    future_iso = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    res2 = yt.publish_short(
        sid2,
        title="Scheduled Short #Shorts",
        description="Scheduled #Shorts",
        tags=["Shorts"],
        privacy_status="public",
        publish_at=future_iso,
        upload_captions=True,
        run_async=False,
    )
    assert res2["upload_status"] == "uploaded"
    assert res2["target_type"] == "short"
    assert res2["privacy_status"] == "private"
    assert res2["publish_at"] is not None
    assert res2["thumbnail_synced"] is False
    assert res2["captions_synced"] is True
    assert '"language": "vi"' in cap_langs[-1]
    assert "publishAt" in init_bodies[-1]["status"]


def test_set_video_thumbnail_endpoint(client, ingest, read_key, yt_env, monkeypatch):
    seen_thumbs: list[tuple[str, str, bytes]] = []
    mode = {"status": 200}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/upload/youtube/v3/thumbnails/set":
            if mode["status"] != 200:
                return httpx.Response(mode["status"], json={"error": {"message": "forbidden"}})
            seen_thumbs.append((
                req.url.params["videoId"],
                req.headers["Content-Type"],
                req.content,
            ))
            return httpx.Response(200, json={"items": [{"default": {"url": "https://yt/thumb.jpg"}}]})
        raise AssertionError(f"Unexpected request: {req.method} {req.url}")

    yt = _setup_mock_youtube(monkeypatch, handler)
    _connect_sample_channel(yt, app_main.store)
    t = time.time()
    app_main.store.execute(
        "INSERT INTO youtube_uploads (id,item_id,target_type,channel_id,title,privacy_status,"
        "upload_status,youtube_video_id,youtube_url,thumbnail_synced,captions_synced,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ytu_thumb_1", "sho_t1", "short", "UC_TEST_123", "Test Short", "public",
         "uploaded", "yt_vid_99", "https://www.youtube.com/watch?v=yt_vid_99", 0, 0, t, t),
    )

    admin_hdr = {"Authorization": "Bearer test-admin"}
    jpg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 64

    # Ingest key lacks admin scope -> 403
    r_unauth = client.post(
        "/api/v1/youtube/videos/yt_vid_99/thumbnail",
        headers=ingest,
        files={"file": ("thumb.jpg", BytesIO(jpg_bytes), "image/jpeg")},
    )
    assert r_unauth.status_code == 403

    # Non-image payload -> 422
    r_bad = client.post(
        "/api/v1/youtube/videos/yt_vid_99/thumbnail",
        headers=admin_hdr,
        files={"file": ("bad.txt", BytesIO(b"not an image"), "text/plain")},
    )
    assert r_bad.status_code == 422

    # Valid JPEG upload -> 200 and updates youtube_uploads.thumbnail_synced
    r_ok = client.post(
        "/api/v1/youtube/videos/yt_vid_99/thumbnail",
        headers=admin_hdr,
        files={"file": ("thumb.jpg", BytesIO(jpg_bytes), "image/jpeg")},
    )
    assert r_ok.status_code == 200, r_ok.text
    body = r_ok.json()
    assert body["video_id"] == "yt_vid_99"
    assert body["thumbnail_synced"] is True
    assert body["thumbnails"]["default"]["url"] == "https://yt/thumb.jpg"
    assert seen_thumbs == [("yt_vid_99", "image/jpeg", jpg_bytes)]

    row = app_main.store.query_one("SELECT thumbnail_synced FROM youtube_uploads WHERE id='ytu_thumb_1'")
    assert row["thumbnail_synced"] == 1

    # Upstream 403 maps cleanly to 403
    mode["status"] = 403
    r_403 = client.post(
        "/api/v1/youtube/videos/yt_vid_99/thumbnail",
        headers=admin_hdr,
        files={"file": ("thumb.jpg", BytesIO(jpg_bytes), "image/jpeg")},
    )
    assert r_403.status_code == 403
