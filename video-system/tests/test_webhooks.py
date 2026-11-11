"""Webhook HMAC signature + event subscription (Appendix E)."""
import hashlib
import hmac
import json

from backend.app.webhooks import sign, build_payload


def test_signature_is_hmac_sha256_of_raw_body():
    body = b'{"event":"shot.held"}'
    assert sign("whsec-test", body) == hmac.new(b"whsec-test", body,
                                                hashlib.sha256).hexdigest()
    # different secret → different signature
    assert sign("other", body) != sign("whsec-test", body)


def test_payload_shape_has_event_id_and_timestamp():
    row = {"id": "evt_1", "type": "shot.held", "item_id": "itm_1",
           "shot_id": "shot_1", "payload_json": json.dumps({"held_reason": "X"}),
           "at": 1730000000.0}
    payload = json.loads(build_payload(row))
    assert payload["event"] == "shot.held"
    assert payload["event_id"] == "evt_1"
    assert payload["item_id"] == "itm_1"
    assert payload["data"] == {"held_reason": "X"}
    assert payload["timestamp"] == 1730000000.0
