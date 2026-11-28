"""Owner nạp audio + danh sách shot (không TTS/ASR) cho Short Studio.

ingest_audio → scenes/timing owner; publish_job chỉ chuyển queued (không gọi
`_ensure_voice_timeline`); get_job giữ timing owner.
"""
from __future__ import annotations

import io
import json
import wave

import pytest

from backend.app.pipeline_short import tts_vi
from backend.app.service_shorts import ShortService


def _wav_bytes(seconds: float = 10.0, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _shots(n: int = 5, dur: float = 2.0, start: float = 0.0) -> list[dict]:
    return [
        {"text": f"shot số {i + 1} nói về dòng tiền",
         "start_sec": round(start + i * dur, 3),
         "end_sec": round(start + (i + 1) * dur, 3)}
        for i in range(n)
    ]


def _create_short(client, ingest, short_adapters) -> str:
    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news", "lang": "vi",
        "source_text": "Tin thử nghiệm cho audio owner ingest.",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ingest(client, ingest, sid: str, shots: list[dict], seconds: float = 10.0,
            filename: str = "voice.wav"):
    return client.post(
        f"/api/v1/shorts/{sid}/audio", headers=ingest,
        files={"file": (filename, _wav_bytes(seconds), "audio/wav")},
        data={"shots": json.dumps(shots)},
    )


def test_ingest_audio_sets_owner_timeline(client, ingest, short_adapters, store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    shots = _shots()
    r = _ingest(client, ingest, sid, shots)
    assert r.status_code == 200, r.text
    short = r.json()
    assert short["audio_source"] == "owner"
    assert short["audio_asset_id"]
    assert short["duration_s"] == pytest.approx(10.0, abs=0.01)
    assert len(short["scenes"]) == 5
    for sc, shot in zip(short["scenes"], shots):
        assert sc["text"] == shot["text"]
        assert sc["start_sec"] == pytest.approx(shot["start_sec"])
        assert sc["end_sec"] == pytest.approx(shot["end_sec"])

    svc = ShortService(store_fixture)
    words = json.loads(svc.words_path(sid).read_text(encoding="utf-8"))
    assert words
    assert all(w["end"] > w["start"] for w in words)
    assert all(words[i]["start"] > words[i - 1]["start"] for i in range(1, len(words)))


def test_publish_job_owner_skips_tts(client, ingest, short_adapters, store_fixture,
                                     monkeypatch):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200

    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError("TTS không được gọi khi audio_source='owner'")

    monkeypatch.setattr(tts_vi, "synthesize_short_voice", _boom)
    r = client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


def test_get_job_keeps_owner_timing_and_visual_prompt(client, ingest, short_adapters):
    sid = _create_short(client, ingest, short_adapters)
    shots = _shots()
    assert _ingest(client, ingest, sid, shots).status_code == 200
    assert client.post(f"/api/v1/shorts/{sid}/publish-job",
                       headers=ingest).status_code == 200
    job = client.get(f"/api/v1/shorts/{sid}/job", headers=ingest).json()
    assert len(job["scenes"]) == 5
    for sc, shot in zip(job["scenes"], shots):
        assert sc["audio_start_sec"] == pytest.approx(shot["start_sec"])
        assert sc["audio_end_sec"] == pytest.approx(shot["end_sec"])
        assert sc["visual_prompt"].strip()
        assert sc["duration_sec"] > 0


@pytest.mark.parametrize("bad_shots", [
    # chồng lấn: shot 2 bắt đầu trước khi shot 1 kết thúc > 0.05s
    [{"text": "a", "start_sec": 0.0, "end_sec": 3.0},
     {"text": "b", "start_sec": 2.0, "end_sec": 5.0}],
    # vượt duration audio (audio 10s nhưng shot kết thúc 20s)
    [{"text": "a", "start_sec": 0.0, "end_sec": 10.0},
     {"text": "b", "start_sec": 10.0, "end_sec": 20.0}],
    # quá 18 shot
    [{"text": f"s{i}", "start_sec": round(i * 10 / 19, 3),
      "end_sec": round((i + 1) * 10 / 19, 3)} for i in range(19)],
])
def test_invalid_shots_raise_value_error(client, ingest, short_adapters,
                                         store_fixture, bad_shots):
    sid = _create_short(client, ingest, short_adapters)
    svc = ShortService(store_fixture)
    with pytest.raises(ValueError):
        svc.ingest_audio(sid, raw_bytes=_wav_bytes(10.0), shots=bad_shots)


def test_update_script_rejected_for_owner_audio(client, ingest, short_adapters,
                                                store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    svc = ShortService(store_fixture)
    with pytest.raises(ValueError):
        svc.update_script(sid, {"title": "Đổi tiêu đề"})


def test_ingest_rejected_after_queued(client, ingest, short_adapters, store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    assert client.post(f"/api/v1/shorts/{sid}/publish-job",
                       headers=ingest).status_code == 200
    svc = ShortService(store_fixture)
    with pytest.raises(ValueError):
        svc.ingest_audio(sid, raw_bytes=_wav_bytes(10.0), shots=_shots())


def test_ingest_on_draft_promotes_to_scripted(client, ingest, short_adapters,
                                              store_fixture):
    """draft → queued không phải transition hợp lệ, nên ingest phải đưa lên scripted."""
    sid = _create_short(client, ingest, short_adapters)
    store_fixture.execute("UPDATE shorts SET status='draft' WHERE id=?", (sid,))
    r = _ingest(client, ingest, sid, _shots())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scripted"


def test_clear_audio_restores_tts_flow(client, ingest, short_adapters, store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    assert store_fixture.query_one(
        "SELECT audio_source FROM shorts WHERE id=?", (sid,))["audio_source"] == "owner"

    r = client.delete(f"/api/v1/shorts/{sid}/audio", headers=ingest)
    assert r.status_code == 200, r.text
    row = store_fixture.query_one("SELECT * FROM shorts WHERE id=?", (sid,))
    assert row["audio_source"] == "tts"
    assert row["audio_asset_id"] is None
    assert float(row["duration_s"] or 0) == 0.0

    # Sau khi bỏ audio owner, publish-job quay lại đường TTS bình thường.
    assert client.post(f"/api/v1/shorts/{sid}/publish-job",
                       headers=ingest).status_code == 200


def test_clear_audio_rejected_when_not_owner(client, ingest, short_adapters,
                                             store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    r = client.delete(f"/api/v1/shorts/{sid}/audio", headers=ingest)
    assert r.status_code == 422


def test_audio_endpoint_serves_wav_content_type(client, ingest, short_adapters,
                                                store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    r = client.get(f"/api/v1/shorts/{sid}/audio", headers=ingest)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")


def test_create_from_audio_endpoint_makes_job_ready_short(client, ingest,
                                                          short_adapters):
    """1 call: audio + shots → short scripted (audio_source owner) → publish → queued."""
    r = client.post(
        "/api/v1/shorts/audio", headers=ingest,
        files={"file": ("voice.wav", _wav_bytes(10.0), "audio/wav")},
        data={"shots": json.dumps(_shots()), "title": "Kể chuyện thử",
              "mode": "cinematic", "lang": "vi"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "cinematic"
    assert body["status"] == "scripted"
    assert body["audio_source"] == "owner"
    assert len(body["scenes"]) == 5

    r2 = client.post(f"/api/v1/shorts/{body['id']}/publish-job", headers=ingest)
    assert r2.status_code == 200, r2.text
    job = client.get(f"/api/v1/shorts/{body['id']}/job", headers=ingest).json()
    assert job["status"] == "queued"
    assert len(job["scenes"]) == 5
    assert job["scenes"][0]["duration_sec"] == pytest.approx(2.0, abs=0.01)


def test_create_from_script_keeps_all_beats(client, ingest, short_adapters):
    """Script owner 8 đoạn không bị validate_script cắt còn 4 beat."""
    paras = [f"Paragraph number {i} about the descent." for i in range(1, 7)]
    r = client.post("/api/v1/shorts/from-script", headers=ingest, json={
        "title": "Soyuz Blackout", "mode": "cinematic", "lang": "en",
        "hook": "Four minutes falling through fire.",
        "beats": paras, "closing": "Would you take that seat?",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "scripted"
    assert body["audio_source"] == "tts"
    assert len(body["scenes"]) == 8
    assert body["script"]["beats"] == paras


def test_create_from_script_rejects_too_many_segments(client, ingest, short_adapters):
    r = client.post("/api/v1/shorts/from-script", headers=ingest, json={
        "mode": "cinematic", "lang": "en", "hook": "h",
        "beats": [f"b{i}" for i in range(18)], "closing": "c",
    })
    assert r.status_code == 422


def test_job_urls_actually_resolve(client, ingest, short_adapters, store_fixture):
    """Mọi URL trong get_job phải gọi được — trước đây subtitles trỏ /job/... nên 404."""
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    assert client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest).status_code == 200
    job = client.get(f"/api/v1/shorts/{sid}/job", headers=ingest).json()
    for key in ("audio_url", "subtitles_ass_url", "subtitles_srt_url"):
        r = client.get(job[key], headers=ingest)
        assert r.status_code == 200, f"{key} -> {job[key]} trả {r.status_code}"
        assert r.content, f"{key} rỗng"


def test_scene_prompt_override_wins_over_generated(client, ingest, short_adapters,
                                                    store_fixture):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    assert client.post(f"/api/v1/shorts/{sid}/publish-job", headers=ingest).status_code == 200

    job = client.get(f"/api/v1/shorts/{sid}/job", headers=ingest).json()
    assert job["scenes"][0]["prompt_source"] == "generated"

    custom = "[SHOT #01] custom brief: photoreal Soyuz capsule, 9:16, no text"
    r = client.put(f"/api/v1/shorts/{sid}/scene-prompts", headers=ingest,
                   json={"prompts": [{"seq": 1, "prompt": custom}]})
    assert r.status_code == 200, r.text
    job = client.get(f"/api/v1/shorts/{sid}/job", headers=ingest).json()
    assert job["scenes"][0]["visual_prompt"] == custom
    assert job["scenes"][0]["prompt_source"] == "override"
    # scene khác vẫn dùng prompt tự sinh
    assert job["scenes"][1]["prompt_source"] == "generated"


def test_scene_prompt_override_rejects_unknown_seq(client, ingest, short_adapters):
    sid = _create_short(client, ingest, short_adapters)
    assert _ingest(client, ingest, sid, _shots()).status_code == 200
    r = client.put(f"/api/v1/shorts/{sid}/scene-prompts", headers=ingest,
                   json={"prompts": [{"seq": 99, "prompt": "x"}]})
    assert r.status_code == 422
