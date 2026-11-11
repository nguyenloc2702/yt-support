"""Regression cho các finding red-team HTTP thật (docs/REDTEAM_FINDINGS.md)."""
from tests.conftest import import_payload


def _import(client, ingest, wav_file, item, **over):
    wav = wav_file(32.0)
    p = import_payload(**over)
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 201, r.text
    return client.get(f"/api/v1/items/{item}/shots", headers=ingest).json()["shots"]


def test_idor_cross_item_blocked(client, ingest, wav_file, adapters,
                                 import_payload_factory, make_item_fn):
    """P0 red-team: shot item A không được thao tác qua path item B."""
    item_a, item_b = make_item_fn("A"), make_item_fn("B")
    rows_a = _import(client, ingest, wav_file, item_a)
    shot_a = rows_a[0]
    # hold shot của A qua path của B → 404, không được phép
    r = client.post(f"/api/v1/items/{item_b}/shots/{shot_a['id']}/hold",
                    headers=ingest, json={"reason": "KEYFRAME_STYLE_FAIL"})
    assert r.status_code == 404
    # shot A không bị đổi trạng thái
    sh = client.get(f"/api/v1/items/{item_a}/shots", headers=ingest).json()["shots"]
    assert sh[0]["status"] == "pending"
    # tương tự generate + retry + keyframe/approve + qc
    for suffix in ("generate", "retry", "keyframe/approve", "qc"):
        r = client.post(f"/api/v1/items/{item_b}/shots/{shot_a['id']}/{suffix}",
                        headers=ingest)
        assert r.status_code == 404, f"{suffix} bị IDOR: {r.status_code}"


def test_negative_index_rejected(client, ingest, wav_file, adapters,
                                 import_payload_factory, make_item_fn):
    """P1 red-team: index âm phải bị từ chối."""
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory()
    p["shots"][0]["index"] = -5
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    assert any("index_must_be_positive" in str(x.get("reason"))
               for x in r.json()["shots"])


def test_fetch_connection_error_is_422(client, ingest, wav_file,
                                       import_payload_factory, make_item_fn):
    """P2 red-team: keyframe URL không kết nối được → 422, không 500."""
    item = make_item_fn()
    wav = wav_file(32.0)
    p = import_payload_factory()
    p["shots"][0]["keyframe_asset_url"] = "http://localhost:9/kf.png"
    p["audio"]["file_path"] = str(wav)
    r = client.post(f"/api/v1/items/{item}/shots/import", headers=ingest, json=p)
    assert r.status_code == 422
    assert r.json()["error"] == "fetch_failed"


def test_delete_missing_item_404(client, admin):
    r = client.delete("/api/v1/items/itm_khongtontai", headers=admin)
    assert r.status_code == 404
