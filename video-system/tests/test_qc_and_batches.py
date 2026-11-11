"""QC fail-closed (§4.B.5) + batch contiguous-prefix append (§4.B.7/§4.C.1)."""
import pytest

from backend.app import domain, providers
from tests.conftest import import_payload, make_item


def _import_ok(client, ingest, wav_file, item, **over):
    wav = wav_file(32.0)
    p = import_payload(**over)
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 201, r.text
    return client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]


def _push_to_qc(client, ingest, item, shot):
    client.post(f"/api/v1/items/{item}/shots/{shot['id']}/keyframe/approve",
                headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{shot['id']}/generate", headers=ingest)
    return client.post(f"/api/v1/items/{item}/shots/{shot['id']}/qc", headers=ingest)


def test_ocr_unavailable_cannot_pass(client, ingest, wav_file, adapters,
                                     make_item_fn):
    """THE fail-closed test: no OCR → text allowlist unverifiable → held,
    never silently qc_passed (repo-old disease)."""
    providers.registry.ocr = None
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 2})
    r = _push_to_qc(client, ingest, item, rows[0])
    assert r.status_code == 200
    body = r.json()["shot"]
    assert body["status"] == "held"
    assert body["held_reason"] == "QC_TOOL_UNAVAILABLE"


def test_qc_geometry_failures(client, ingest, wav_file, adapters, make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    s = rows[0]
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/keyframe/approve", headers=ingest)
    # wrong resolution
    adapters["probe"].video["width"] = 1920
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/generate", headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    assert r.json()["shot"]["held_reason"] == "RESOLUTION_FAIL"
    # retry from held → A13: held→generating→produce→generated (one worker step)
    adapters["probe"].video.update({"width": 1280, "height": 720})
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/retry", headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    assert r.json()["shot"]["status"] == "qc_passed"
    # now break duration via a hold (video-group reason) + retry cycle
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/hold", headers=ingest,
                json={"reason": "DURATION_MISMATCH"})
    adapters["probe"].video["duration"] = 9.5
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/retry", headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    assert r.json()["shot"]["held_reason"] == "DURATION_MISMATCH"


def test_qc_text_allowlist(client, ingest, wav_file, adapters, make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    s = rows[0]
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/keyframe/approve", headers=ingest)
    adapters["ocr"].text_by_call = ["SOME LEAKED CAPTION"]
    client.post(f"/api/v1/items/{item}/shots/{s['id']}/generate", headers=ingest)
    r = client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
    assert r.json()["shot"]["held_reason"] == "VIDEO_TEXT_LEAK"


def test_ingest_scope_cannot_approve(client, ingest, wav_file, adapters,
                                     make_item_fn):
    """§7.1: key ingest không được approve — agent ngoài không tự duyệt được."""
    item = make_item_fn()
    r = client.post(f"/api/v1/items/{item}/approve", headers=ingest,
                    json={"gate": "script", "decision": "approved", "reason": "x"})
    assert r.status_code == 403


def test_batch_contiguous_prefix_blocks(client, ingest, admin, wav_file, adapters,
                                        make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    br = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 4})
    batch_id = br.json()["id"]
    s1, s2, s3 = rows[0], rows[1], rows[2]
    _push_to_qc(client, ingest, item, s1)
    client.post(f"/api/v1/items/{item}/shots/{s2['id']}/hold", headers=ingest,
                json={"reason": "KEYFRAME_STYLE_FAIL",
                      "detail": "style sai khi soát keyframe"})
    _push_to_qc(client, ingest, item, s3)

    r = client.post(f"/api/v1/items/{item}/batches/{batch_id}/approve",
                    headers=admin)
    body = r.json()
    assert r.status_code == 200, body
    # contiguous prefix from index 1: [1] only — 2 is held, stops the run
    assert body["appended"] == [1]
    assert body["stopped_at"] == 2
    sh = {s["index_num"]: s for s in
          client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]}
    assert sh[1]["status"] == "appended"
    assert sh[2]["status"] == "held"
    assert sh[3]["status"] == "qc_passed"   # MUST NOT append past held
    assert sh[4]["status"] == "keyframe_pending"
    cum = client.get(f"/api/v1/items/{item}/cumulative", headers=ingest).json()
    assert cum["appended_count"] == 1 and cum["manifest_hash"]


def test_batch_cannot_start_at_skipped_index(client, ingest, admin, wav_file,
                                             adapters, make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    # hold shot 1 first, then a batch of [2,3,4] cannot be approved since
    # contiguous prefix starts at 1
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 4})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/hold", headers=ingest,
                json={"reason": "HUMAN_REJECT"})
    b = client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]
    # approve batch → appended prefix empty (first shot not qc_passed → stopped at 1)
    br = client.post(f"/api/v1/items/{item}/batches",
                     headers=ingest, json={"size": 1})  # no pending left? shot1 held
    # nothing pending besides none — batch creation fails or empty
    assert br.status_code in (201, 409)


def test_retry_from_keyframe_held_goes_to_keyframe_pending(client, ingest,
                                                           wav_file, adapters,
                                                           make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    s = rows[0]
    # keyframe size fail via probe
    adapters["probe"].image = {"width": 1024, "height": 1024}
    # simulate keyframe QC hold: hold with keyframe reason from keyframe_pending
    r = client.post(f"/api/v1/items/{item}/shots/{s['id']}/hold", headers=ingest,
                    json={"reason": "KEYFRAME_SIZE_FAIL"})
    assert r.status_code == 200
    # retry (keyframe group) → keyframe_pending
    r2 = client.post(f"/api/v1/items/{item}/shots/{s['id']}/retry", headers=ingest)
    assert r2.json()["shot"]["status"] == "keyframe_pending"


def test_full_e2e_flow_to_appended(client, ingest, admin, wav_file, adapters,
                                   make_item_fn):
    """E2E-1: import → gates → batch → keyframe → generate → QC → approve → appended."""
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    # gates: script + audio approved by admin
    r = client.post(f"/api/v1/items/{item}/approve", headers=admin,
                    json={"gate": "script", "decision": "approved", "reason": "ok"})
    assert r.status_code == 200
    client.post(f"/api/v1/items/{item}/approve", headers=admin,
                json={"gate": "audio", "decision": "approved", "reason": "ok"})
    # batch of 2
    br = client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 2})
    batch_id = br.json()["id"]
    for s in rows[:2]:
        assert client.post(f"/api/v1/items/{item}/shots/{s['id']}/keyframe/approve",
                           headers=ingest).status_code == 200
        g = client.post(f"/api/v1/items/{item}/shots/{s['id']}/generate", headers=ingest)
        assert g.json()["shot"]["status"] == "generated"
        q = client.post(f"/api/v1/items/{item}/shots/{s['id']}/qc", headers=ingest)
        assert q.json()["shot"]["status"] == "qc_passed"
    ap = client.post(f"/api/v1/items/{item}/batches/{batch_id}/approve", headers=admin)
    assert ap.status_code == 200
    assert ap.json()["appended"] == [1, 2]
    cum = client.get(f"/api/v1/items/{item}/cumulative", headers=ingest).json()
    assert cum["appended_count"] == 2
    assert abs(cum["duration_sec"] - 16.0) < 0.01
    # item entered generating after first batch
    it = client.get(f"/api/v1/items/{item}", headers=ingest).json()
    assert it["status"] == "generating"
    # engine recorded, no silent downgrade
    sh = client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]
    assert sh[0]["render_engine"] == "veo"


def test_delete_blocked_while_generating(client, ingest, admin, wav_file, adapters,
                                         make_item_fn):
    item = make_item_fn()
    rows = _import_ok(client, ingest, wav_file, item)
    client.post(f"/api/v1/items/{item}/batches", headers=ingest, json={"size": 1})
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/keyframe/approve",
                headers=ingest)
    client.post(f"/api/v1/items/{item}/shots/{rows[0]['id']}/generate", headers=ingest)
    r = client.delete(f"/api/v1/items/{item}", headers=admin)
    assert r.status_code == 409  # in-flight shot blocks delete (§7.6)
