"""Tests for Reference Image Harvesting & TODO Integration (10-20 reference images throughout script)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from backend.app.pipeline_en.reference_image_collector import (
    download_or_generate_reference,
    extract_reference_anchors,
)


@pytest.fixture()
def fast_offline_tts(monkeypatch):
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_OFFLINE_SEARCH", "1")


def test_extract_reference_anchors_distribution():
    """Verify reference anchors are distributed evenly across beginning, middle, and end."""
    shots = [
        {"index": i, "narration": f"Shot {i}: Wang Chuanfu expanded BYD battery production in Shenzhen.", "visual_prompt": "Clean vector"}
        for i in range(1, 101)
    ]
    anchors = extract_reference_anchors(shots, target_count=15, item_title="BYD — Global EV Expansion")
    assert len(anchors) == 15
    # Check that beginning, middle, and end are covered
    indexes = [a["shot_index"] for a in anchors]
    assert indexes[0] == 1
    assert indexes[-1] == 100
    assert any(45 <= idx <= 55 for idx in indexes)  # Middle is represented
    for a in anchors:
        assert a["query"]
        assert "BYD" in a["query"] or "Wang" in a["query"] or "Battery" in a["query"]


def test_offline_synthetic_reference_generation(tmp_path: Path, monkeypatch):
    """When offline, generates a valid 1280x720 reference concept PNG card without failing."""
    monkeypatch.setenv("VIDEO_OFFLINE_SEARCH", "1")
    anchor = {
        "seq_num": 1,
        "shot_index": 5,
        "query": "BYD Blade Battery Cell",
        "narration_snippet": "The Blade Battery passed the rigorous nail penetration test.",
        "shot_type": "diagram",
    }
    dest = tmp_path / "ref_test.png"
    res = download_or_generate_reference(anchor, dest)
    assert res["is_synthetic"] is True
    assert res["width"] == 1280
    assert res["height"] == 720
    assert dest.exists()
    assert dest.stat().st_size > 1000


def test_todo_collect_references_flow(client, ingest, read_key, monkeypatch, fast_offline_tts):
    """Verify harvesting 10-20 reference images and retrieving them through the TODO API."""
    monkeypatch.setenv("VIDEO_OFFLINE_SEARCH", "1")

    script_lines = [
        f"Shot {i}: Exploring Wall Street investment strategies and Aladdin risk algorithms at BlackRock."
        for i in range(1, 21)
    ]
    r_create = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "BlackRock Aladdin Documentary",
            "script_text": "\n".join(script_lines),
            "bgm_enabled": False,
            "collect_references": False,
        },
    )
    assert r_create.status_code == 201, r_create.text
    item_id = r_create.json()["item_id"]

    # 1. Harvest reference images via POST /api/v1/todos/{item_id}/collect-references
    r_coll = client.post(
        f"/api/v1/todos/{item_id}/collect-references",
        headers=ingest,
        json={"count": 12, "force": True},
    )
    assert r_coll.status_code == 200, r_coll.text
    todo_pkg = r_coll.json()
    assert len(todo_pkg["reference_images"]) == 12
    assert todo_pkg["image_progress"]["references_count"] == 12

    # Verify that shots have reference_image_url populated
    shots_with_refs = [s for s in todo_pkg["shots"] if s.get("reference_image_url")]
    assert len(shots_with_refs) == 12

    # 2. List references via GET /api/v1/todos/{item_id}/references
    r_refs = client.get(f"/api/v1/todos/{item_id}/references", headers=read_key)
    assert r_refs.status_code == 200
    refs_data = r_refs.json()
    assert refs_data["count"] == 12
    assert len(refs_data["reference_images"]) == 12

    # 3. Download the first reference image file
    first_seq = refs_data["reference_images"][0]["seq_num"]
    r_file = client.get(
        f"/api/v1/todos/{item_id}/images/{first_seq}/file?kind=reference",
        headers=read_key,
    )
    assert r_file.status_code == 200
    assert r_file.headers.get("content-type") in {"image/png", "image/jpeg"}
    assert len(r_file.content) > 500


def test_auto_collect_references_on_from_script(client, ingest, read_key, monkeypatch, fast_offline_tts):
    """When collect_references=True in from-script, TODO item immediately contains reference images."""
    monkeypatch.setenv("VIDEO_OFFLINE_SEARCH", "1")

    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Automated Reference Collection Test",
            "shots": [
                {"index": 1, "narration": "Wang Chuanfu founded BYD.", "shot_type": "scene"},
                {"index": 2, "narration": "Blade Battery set a new standard.", "shot_type": "diagram"},
                {"index": 3, "narration": "Yangwang U9 demonstrated dancing suspension.", "shot_type": "scene"},
            ],
            "bgm_enabled": False,
            "collect_references": True,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    assert "reference_images" in pkg
    assert len(pkg["reference_images"]) == 3
    assert pkg["image_progress"]["references_count"] == 3
    assert pkg["shots"][0]["reference_image_url"] is not None


def test_default_auto_collect_references_without_flag(client, ingest, read_key):
    """When collect_references is omitted, it defaults to True and populates reference_images."""
    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Default Reference Test",
            "shots": [
                {"index": 1, "narration": "Jensen Huang introduced the Nvidia Blackwell architecture.", "shot_type": "scene"},
                {"index": 2, "narration": "Liquid cooling was essential for the GPU cluster.", "shot_type": "scene"},
            ],
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    pkg = r.json()
    assert "reference_images" in pkg
    assert len(pkg["reference_images"]) == 2
    assert pkg["image_progress"]["references_count"] == 2
    # uploaded_images should ONLY contain keyframes/characters uploaded by external agents
    assert len(pkg["uploaded_images"]) == 0
    assert pkg["image_progress"]["uploaded_images_count"] == 0


def test_script_hash_integrity_after_reference_sync(client, ingest):
    """Verify script hash in database matches script_en.json on disk after references are synced."""
    from backend.app.config import data_dir
    from backend.app.main import store
    from backend.app.service_todos import sha256_file

    r = client.post(
        "/api/v1/todos/from-script",
        headers=ingest,
        json={
            "title": "Script Hash Integrity Test",
            "shots": [
                {"index": 1, "narration": "First shot of hash test.", "shot_type": "scene"},
                {"index": 2, "narration": "Second shot of hash test.", "shot_type": "scene"},
            ],
            "bgm_enabled": False,
        },
    )
    assert r.status_code == 201
    item_id = r.json()["item_id"]

    script_path = data_dir() / "objects" / item_id / "todo" / "script_en.json"
    assert script_path.exists()
    disk_sha = sha256_file(script_path)

    asset_row = store.query_one(
        "SELECT sha256, size FROM assets WHERE item_id=? AND kind='script'",
        (item_id,),
    )
    assert asset_row is not None
    assert asset_row["sha256"] == disk_sha
    assert asset_row["size"] == script_path.stat().st_size

    script_row = store.query_one(
        "SELECT hash FROM scripts WHERE item_id=? AND status='active'",
        (item_id,),
    )
    assert script_row is not None
    assert script_row["hash"] == disk_sha


def test_pure_python_png_fallback_when_pil_missing(tmp_path: Path, monkeypatch):
    """Verify fallback to pure-Python PNG synthesis when PIL is not available."""
    import backend.app.pipeline_en.reference_image_collector as ref_mod

    monkeypatch.setattr(ref_mod, "HAS_PIL", False)
    anchor = {
        "seq_num": 1,
        "shot_index": 1,
        "query": "Falcon Heavy Rocket",
        "narration_snippet": "Side boosters landed simultaneously.",
        "shot_type": "scene",
    }
    dest = tmp_path / "fallback_card.png"
    w, h = ref_mod._synthesize_reference_card(dest, 1, anchor["query"], anchor["narration_snippet"])
    assert w == 1280
    assert h == 720
    assert dest.exists()
    # Check PNG magic bytes
    data = dest.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in data
    assert b"IDAT" in data
    assert b"IEND" in data

