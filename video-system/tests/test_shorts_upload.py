"""Tests for direct Short MP4 product upload endpoints."""
from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient


def test_create_short_from_upload_and_serve_video(client: TestClient, admin: dict):
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 256
    r = client.post(
        "/api/v1/shorts/upload",
        headers=admin,
        data={
            "title": "SpaceX Catches Giant Rocket Mid-Air",
            "mode": "news",
            "lang": "en",
            "source_note": "SpaceX / Wikimedia",
            "description": "Starship Flight 5 catch #spacex",
        },
        files={"file": ("spacex_catch.mp4", BytesIO(fake_mp4), "video/mp4")},
    )
    assert r.status_code == 201, r.text
    short = r.json()
    assert short["status"] == "ready"
    assert short["title"] == "SpaceX Catches Giant Rocket Mid-Air"
    assert short["mode"] == "news"
    assert short["lang"] == "en"
    assert short["video_asset_id"]

    vr = client.get(f"/api/v1/shorts/{short['id']}/video", headers=admin)
    assert vr.status_code == 200
    assert vr.content == fake_mp4


def test_upload_video_to_existing_short(client: TestClient, admin: dict):
    fake_mp4_1 = b"\x00\x00\x00\x18ftypmp42" + b"A" * 128
    fake_mp4_2 = b"\x00\x00\x00\x18ftypmp42" + b"B" * 128
    r = client.post(
        "/api/v1/shorts/upload",
        headers=admin,
        data={"title": "Initial Short", "mode": "news", "lang": "vi"},
        files={"file": ("v1.mp4", BytesIO(fake_mp4_1), "video/mp4")},
    )
    assert r.status_code == 201
    sid = r.json()["id"]

    r2 = client.post(
        f"/api/v1/shorts/{sid}/video",
        headers=admin,
        files={"file": ("v2.mp4", BytesIO(fake_mp4_2), "video/mp4")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "ready"

    vr = client.get(f"/api/v1/shorts/{sid}/video", headers=admin)
    assert vr.status_code == 200
    assert vr.content == fake_mp4_2


def test_upload_rejects_unsupported_extension(client: TestClient, admin: dict):
    r = client.post(
        "/api/v1/shorts/upload",
        headers=admin,
        data={"title": "Bad File"},
        files={"file": ("notes.txt", BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 422
