"""Regression tests for adversarial-review findings (round 2 fixes)."""
import json

import pytest

from backend.app.main import store, webhooks
from tests.conftest import import_payload


def _import(client, ingest, wav_file, item, **over):
    wav = wav_file(32.0)
    p = import_payload(**over)
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 201, r.text
    return client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]


def test_ref_image_missing_when_character_declared(client, ingest, wav_file,
                                                   adapters, make_item_fn):
    """§4.B.2 P0: shot declaring character_ids without an approved sheet →
    held REF_IMAGE_MISSING, never generated."""
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload()
    p["shots"][0]["character_ids"] = ["Wang"]
    p["audio"]["file_path"] = str(wav)
    assert client.post(f"/api/v1/items/{item}/shots/import", headers=ingest,
                       json=p).status_code == 201
    rows = client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate",
                    headers=ingest)
    body = r.json()["shot"]
    assert body["status"] == "held"
    assert body["held_reason"] == "REF_IMAGE_MISSING"


def test_ref_image_missing_without_keyframe(client, ingest, wav_file, adapters,
                                            make_item_fn):
    """Keyframe-first mandatory: no keyframe asset at all → cannot animate."""
    item = make_item_fn()
    rows = _import(client, ingest, wav_file, item, keyframe_sha256=None)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate",
                    headers=ingest)
    assert r.json()["shot"]["held_reason"] == "REF_IMAGE_MISSING"


def test_character_sheet_flow_unblocks_generation(client, ingest, admin, wav_file,
                                                  adapters, make_item_fn, tmp_path):
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload()
    p["shots"][0]["character_ids"] = ["Wang"]
    p["audio"]["file_path"] = str(wav)
    assert client.post(f"/api/v1/items/{item}/shots/import", headers=ingest,
                       json=p).status_code == 201
    # upload ref image under data dir
    ref = tmp_path / "wang_ref.png"
    ref.write_bytes(b"PNGDATA" * 1000)
    from backend.app.config import data_dir
    inside = data_dir() / "uploads" / "wang_ref.png"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(ref.read_bytes())
    r = client.post(f"/api/v1/items/{item}/characters", headers=ingest,
                    json={"name": "Wang", "role": "protagonist",
                          "reference_path": str(inside)})
    assert r.status_code == 200, r.text
    # approve character gate (admin)
    client.post(f"/api/v1/items/{item}/approve", headers=admin,
                json={"gate": "character", "decision": "approved", "reason": "ok"})
    rows = client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate",
                    headers=ingest)
    assert r.json()["shot"]["status"] == "generated"


def test_human_held_cannot_be_retried(client, ingest, wav_file, adapters,
                                      make_item_fn):
    """Human-group held is resolved by review, not by /retry (audit P1)."""
    item = make_item_fn()
    rows = _import(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/qc", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/hold", headers=ingest,
                json={"reason": "FACE_MISMATCH"})
    r = client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/retry",
                    headers=ingest)
    assert r.status_code == 409


def test_wrong_group_hold_rejected(client, ingest, wav_file, adapters,
                                   make_item_fn):
    item = make_item_fn()
    rows = _import(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    # human reason while still keyframe_pending → rejected (wrong group)
    r = client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/hold",
                    headers=ingest, json={"reason": "FACE_MISMATCH"})
    assert r.status_code == 409


def test_batch_recovery_after_missed_window(client, ingest, admin, wav_file,
                                            adapters, make_item_fn):
    """Audit P0 deadlock: shot 2 held at approval → [1] appended; shot 2 later
    qc_passed must be batchable again and appended at index 2."""
    item = make_item_fn()
    rows = _import(client, ingest, wav_file, item)
    br = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 4})
    batch_id = br.json()["id"]
    for s in rows[:3]:
        client.post(f"/api/v1/items/{item}/shots/{s['id']}/keyframe/approve",
                    headers=ingest)
        client.post(f"/api/v1/items/{item}/shots/{s['id']}/generate", headers=ingest)
        client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[1]['id']}/hold", headers=ingest,
                json={"reason": "DURATION_MISMATCH"})
    ap = client.post(f"/api/v1/items/{item}/batches/{batch_id}/approve", headers=admin)
    assert ap.json()["appended"] == [1]
    assert ap.json()["stopped_at"] == 2
    # resolve shot 2: retry (video group) → produce → qc
    adapters["probe"].video["duration"] = 8.0  # fix the fake probe
    r = client.post(f"/api/v1/items/{item}/shots/{rows[1]['id']}/retry", headers=ingest)
    assert r.status_code == 200
    # hmm: retry produced with duration 8.0 already — qc passes
    client.post(f"/api/v1/items/{item}/shots/{rows[1]['id']}/qc", headers=ingest)
    # new batch picks up the qc_passed shot at index 2 (recovery path)
    br2 = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 4})
    assert br2.status_code == 201, br2.text
    b2 = br2.json()
    ap2 = client.post(f"/api/v1/items/{item}/batches/{b2['id']}/approve", headers=admin)
    body = ap2.json()
    # contiguous prefix from index 2: [2] — shot 3 (qc_passed but index 3) is a
    # later contiguous run; recover it via one more batch
    assert body["appended"] == [2]
    br3 = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 4})
    ap3 = client.post(f"/api/v1/items/{item}/batches/{br3.json()['id']}/approve",
                      headers=admin)
    assert ap3.json()["appended"] == [3]
    cum = client.get(f"/api/v1/items/{item}/cumulative", headers=ingest).json()
    assert cum["appended_count"] == 3


def test_webhook_fires_on_shot_held(client, ingest, admin, wav_file, adapters,
                                    make_item_fn):
    """Audit P0: emitter/dispatcher name mismatch meant webhooks never fired."""
    item = make_item_fn()
    client.post("/api/v1/webhooks", headers=admin, json={
        "url": "http://127.0.0.1:9/receive",  # closed port — delivery fails, row exists
        "events": ["shot.held"], "secret": "s3cret"})
    rows = _import(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/qc", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/hold", headers=ingest,
                json={"reason": "FACE_MISMATCH"})
    import time
    time.sleep(0.5)  # async dispatch thread
    rows_d = store.query("SELECT * FROM webhook_deliveries")
    assert rows_d, "webhook delivery row must exist — dispatcher is alive"
    ev = store.query("SELECT type FROM events WHERE item_id=? ORDER BY at", (item,))
    assert any(e["type"] == "shot.status" for e in ev)


def test_manifest_refuses_unbound_video(client, ingest, admin, wav_file, adapters,
                                        make_item_fn):
    """Audit P1: append must refuse a video asset without sha256."""
    item = make_item_fn()
    rows = _import(client, ingest, wav_file, item)
    br = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    s = rows[0]
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/keyframe/approve", headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/generate", headers=ingest)
    # strip the sha256 to simulate unbound asset
    store.execute("UPDATE assets SET sha256=NULL WHERE item_id=? AND kind='video'",
                  (item,))
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    r = client.post(f"/api/v1/items/{item}/batches/{br.json()['id']}/approve",
                    headers=admin)
    assert r.status_code == 409
    assert "sha256" in r.json()["detail"]


def test_file_path_outside_data_dir_rejected(client, ingest, wav_file,
                                             import_payload_factory, make_item_fn):
    """Audit P1: audio.file_path is a sandboxed path, not a hash oracle."""
    item = make_item_fn()
    p = import_payload_factory()
    p["audio"]["file_path"] = "C:/Windows/win.ini"
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    assert r.json()["error"] == "file_path_outside_data_dir"
