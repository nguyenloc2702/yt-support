"""Import gate — §3.2 verbatim: hash, probe, tolerances, re-import, allowlist."""
import json

import pytest

from tests.conftest import import_payload, make_item


def test_import_ok_and_hash_stable(client, admin, ingest, wav_file, adapters,
                                   import_payload_factory, make_item_fn):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory()
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 4
    assert body["audio"]["duration_trusted"] is False
    assert body["audio"]["duration_sec_probed"] == 32.0
    # idempotent replay: same payload → replayed
    r2 = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r2.status_code == 201 and r2.json().get("replayed") is True
    # payload duration LIE (says 99s) does not change gate outcome: probe wins
    p["audio"]["duration_sec"] = 99.0
    r3 = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r3.status_code == 201 and r3.json().get("replayed") is True


def test_global_drift_rejected_with_report(client, ingest, wav_file, adapters,
                                           import_payload_factory, make_item_fn):
    item = make_item_fn()
    wav = wav_file(30.0)  # shots total 32s → drift +2s > ±1.0
    p = import_payload_factory(duration=30.0)
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    rep = r.json()
    assert rep["error"] == "import_gate_failed"
    assert rep["audio"]["duration_sec_probed"] == 30.0
    assert rep["audio"]["duration_trusted"] is False
    assert abs(rep["global_drift_sec"]) > 1.0
    assert "Không tự kéo giãn" in rep["hint"]


def test_segment_gap_overlaps_rejected(client, ingest, wav_file, adapters,
                                       import_payload_factory, make_item_fn):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory(seg=10.0)  # 4×10=40 vs 32 → but adjacency: 10→10 ok
    # overlap: shot 2 starts before shot 1 ends
    p["shots"][1]["audio_start_sec"] = 7.0
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    assert r.json()["shots"][0]["reason"] == "SEGMENT_GAP_OVERLAP"


def test_on_screen_text_allowlist(client, ingest, wav_file, adapters,
                                  import_payload_factory, make_item_fn):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory(on_screen_text="NOT ALLOWED")
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    assert r.json()["error"] == "schema_violations"
    # quote WITH on_screen_text is fine
    item2 = make_item_fn()
    p2 = import_payload_factory()
    p2["shots"][2]["shot_type"] = "quote"
    p2["shots"][2]["on_screen_text"] = "It exploded."
    p2["shots"][2]["on_screen_type"] = "thought_bubble"
    p2["audio"]["file_path"] = str(wav)
    r2 = client.post(f"/api/v1/items/{item2}/shots/import", headers=ingest, json=p2)
    assert r2.status_code == 201


def test_same_version_diff_content_conflict(client, ingest, wav_file, adapters,
                                            import_payload_factory, make_item_fn):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory()
    p["audio"]["file_path"] = str(wav)
    assert client.post(f"/api/v1/items/{item}/shots/import", headers=ingest,
                       json=p).status_code == 201
    p["shots"][0]["narration"] = "changed"
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 409
    assert r.json()["error"] == "script_version_conflict"


def test_new_version_supersedes_old_never_overwrites(client, ingest, wav_file,
                                                     adapters, import_payload_factory,
                                                     make_item_fn):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory(version=1)
    p["audio"]["file_path"] = str(wav)
    assert client.post(f"/api/v1/items/{item}/shots/import", headers=ingest,
                       json=p).status_code == 201
    p2 = import_payload_factory(version=2, count=4)
    p2["shots"][0]["narration"] = "v2 line"
    p2["audio"]["file_path"] = str(wav)
    assert client.post(f"/api/v1/items/{item}/shots/import", headers=ingest,
                       json=p2).status_code == 201
    rows = client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]
    v1 = [s for s in rows if s["script_version"] == 1]
    v2 = [s for s in rows if s["script_version"] == 2]
    assert v1 and all(s["status"] == "superseded" for s in v1)
    assert v2 and all(s["status"] == "pending" for s in v2)
    # history retained — old narration still queryable
    assert any("line 1" in s["narration"] for s in v1)


def test_limits_enforced(client, ingest, wav_file, adapters, make_item_fn,
                         import_payload_factory):
    from backend.app.config import RENDER_SPEC
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory(count=RENDER_SPEC["limits"]["max_shots_per_import"] + 1)
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 413


def test_url_host_allowlist(client, ingest, wav_file, make_item_fn):
    item = make_item_fn()
    r = client.post(f"/api/v1/items", headers=ingest, json={
        "title": "x", "source": {"type": "youtube_url",
                                 "url": "https://evil.example.com/v"},
        "target": {"language": "en", "style_lock_version": "v3"}})
    assert r.status_code == 422
    assert "not allowed" in r.json()["detail"]
