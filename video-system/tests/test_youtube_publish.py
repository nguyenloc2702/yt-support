"""Tests for YouTube Channel OAuth 2.0, QuotaLedger, Metadata Patch, and Resumable Publishing."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
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
