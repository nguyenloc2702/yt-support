"""Short Studio flow (Phụ lục G — luật mới phải có test).

E2E-9: kịch bản → duyệt → produce (offline TTS + fake ffmpeg + bank) → MP4
1080x1920 → publish pack. Kèm luật transition + scope + fail-closed adapter.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.app import domain, providers
from backend.app.config import data_dir
from backend.app.pipeline_short import karaoke
from conftest import default_short_script, make_fake_bank_image


def _create_short(client, ingest, source_text="Tin vắn: 3 tỷ đồng biến mất khỏi tài khoản khách hàng sau một đêm..."):
    return client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news",
        "source_text": source_text,
        "source_note": "Nguồn: VnExpress",
    })


def test_short_create_and_script_flow(client, ingest, short_adapters):
    r = _create_short(client, ingest)
    assert r.status_code == 201, r.text
    short = r.json()
    assert short["status"] == "scripted"
    assert short["mode"] == "news"
    assert short["word_count"] == 107
    assert short["script"]["hook"].startswith("Chỉ sau một đêm")
    assert len(short["script"]["beats"]) == 3
    # Scene = hook + 3 beat + closing
    assert len(short["scenes"]) == 5
    assert short["scenes"][0]["seq_num"] == 1
    # G.11: prompt fallback KHÔNG còn style crayon của bên video dài
    assert "crayon" not in short["scenes"][0]["image_prompt"].lower()
    assert "editorial" in short["scenes"][0]["image_prompt"].lower()

    # LLM đã nhận đúng schema + system prompt theo mode
    assert short_adapters["llm"].calls, "LLM phải được gọi đúng 1 lần tạo kịch bản"
    assert "tin nhanh" in short_adapters["llm"].calls[0]["system"]

    # GET chi tiết + list
    r2 = client.get(f"/api/v1/shorts/{short['id']}", headers=ingest)
    assert r2.status_code == 200
    r3 = client.get("/api/v1/shorts", headers=ingest)
    assert r3.status_code == 200
    assert any(s["id"] == short["id"] for s in r3.json()["shorts"])


def test_short_create_fail_closed_without_adapter(client, ingest, short_adapters_removed):
    r = _create_short(client, ingest)
    assert r.status_code == 503
    assert "adapter" in r.json()["detail"].lower()


def test_short_create_word_budget_violation(client, ingest, short_adapters):
    short_adapters["llm"].script = {
        "title": "Tin ngắn", "hook": "Tin này quá ngắn",
        "beats": ["Một beat duy nhất"], "closing": "Câu chốt.",
        "hashtags": {}, "description": "", "source_note": "",
    }
    r = _create_short(client, ingest)
    assert r.status_code == 422
    assert "ngân sách" in r.json()["detail"]


def test_short_scope_read_cannot_create(client, read_key):
    r = client.post("/api/v1/shorts", headers=read_key, json={
        "mode": "news", "source_text": "tin thử"})
    assert r.status_code == 403


def test_short_idempotency_replay(client, ingest, short_adapters):
    key = "idem-short-001"
    r1 = client.post("/api/v1/shorts", headers={**ingest, "Idempotency-Key": key}, json={
        "mode": "news", "source_text": "tin vắn idempotent"})
    r2 = client.post("/api/v1/shorts", headers={**ingest, "Idempotency-Key": key}, json={
        "mode": "news", "source_text": "tin vắn idempotent"})
    assert r1.status_code == 201
    assert r2.headers.get("Idempotency-Replayed") == "true"
    assert r2.json()["id"] == r1.json()["id"]


def test_short_script_patch_and_transition_laws(client, ingest, short_adapters):
    short = _create_short(client, ingest).json()
    sid = short["id"]

    # Sửa kịch bản: closing gọn hơn → word_count cập nhật, vẫn trong ngân sách
    # (107 từ gốc − 20 từ closing cũ + 13 từ closing mới = 100 từ)
    r = client.patch(f"/api/v1/shorts/{sid}/script", headers=ingest, json={
        "closing": "Theo bạn, ngân hàng hay khách hàng phải chịu trách nhiệm cuối cùng?"})
    assert r.status_code == 200, r.text
    assert r.json()["word_count"] == 100
    assert r.json()["status"] == "scripted"  # self-loop hợp lệ

    # Nhãn VI phải đi kèm (DESIGN.md §3 — cấm enum thô)
    assert r.json()["status_label"] == "Đã có kịch bản"

    # beats rỗng → 422 tiếng người
    r2 = client.patch(f"/api/v1/shorts/{sid}/script", headers=ingest,
                      json={"beats": ["   "]})
    assert r2.status_code == 422


def test_short_produce_offline_full_flow(client, ingest, short_adapters, monkeypatch):
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    make_fake_bank_image("bank_news_01.png")
    make_fake_bank_image("bank_news_02.png")

    short = _create_short(client, ingest).json()
    sid = short["id"]
    r = client.post(f"/api/v1/shorts/{sid}/produce", headers=ingest)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "ready"
    assert done["status_label"] == "Đã có video"
    assert done["duration_s"] > 0
    assert done["video_asset_id"] and done["audio_asset_id"] and done["ass_asset_id"]

    # Fake container đúng header (pattern AssemblerService)
    video_file = data_dir() / "objects" / f"shorts/{sid}/exports/short_{sid}.mp4"
    assert video_file.exists()
    header = video_file.read_bytes().split(b"\n")[0].decode()
    assert header.startswith("SHORT_MP4_CONTAINER|")
    assert "geometry=1080x1920@30" in header

    # Scene windows từ cue offline (audio thật + timeline đều)
    scenes = done["scenes"]
    assert scenes[0]["start_sec"] == pytest.approx(0.0, abs=0.01)
    assert scenes[-1]["end_sec"] == pytest.approx(done["duration_s"], abs=0.35)
    # Fast-mode news: hero gen qua adapter (2), còn lại bank
    gen_calls = short_adapters["img"].calls
    assert len(gen_calls) == 2
    assert gen_calls[0]["size"] == "1080x1920"
    banked = [sc for sc in scenes if sc["bank_used"] == 1]
    assert len(banked) == 3

    # Word timeline JSON asset đã ghi
    words_file = data_dir() / "objects" / f"shorts/{sid}/audio/words_{sid}.json"
    assert words_file.exists()
    words = json.loads(words_file.read_text(encoding="utf-8"))
    assert len(words) == done["word_count"]
    assert all(w["end"] > w["start"] for w in words)

    # FileResponse endpoints (media + karaoke + publish pack)
    rv = client.get(f"/api/v1/shorts/{sid}/video", headers=ingest)
    assert rv.status_code == 200 and "video" in rv.headers["content-type"]
    ra = client.get(f"/api/v1/shorts/{sid}/audio", headers=ingest)
    assert ra.status_code == 200 and "audio" in ra.headers["content-type"]
    rass = client.get(f"/api/v1/shorts/{sid}/subtitles.ass", headers=ingest)
    assert rass.status_code == 200 and "\\k" in rass.text
    rp = client.get(f"/api/v1/shorts/{sid}/publish.md", headers=ingest)
    assert rp.status_code == 200
    pub = rp.text
    assert "3 tỷ đồng biến mất" in pub and "#tinhot" in pub and "Nguồn: VnExpress" in pub
    ri = client.get(f"/api/v1/shorts/{sid}/images/1/file", headers=ingest)
    assert ri.status_code == 200 and ri.headers["content-type"].startswith("image/")
    rb = client.get(f"/api/v1/shorts/{sid}/images/3/file", headers=ingest)
    assert rb.status_code == 200  # bank image resolve được


def _copy_art(src, out_dir):
    """Copy ảnh bài fixture vào đúng thư mục objects (producer pass vào)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy(src, out_dir / src.name))


def test_short_produce_article_images_priority(client, ingest, short_adapters, monkeypatch, tmp_path):
    """G.11: có source_link → ảnh gốc bài báo phủ scene trước, AI-gen/bank chỉ lấp chỗ trống."""
    import shutil
    from PIL import Image
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    from backend.app.pipeline_short import article_images as ai

    art_dir = tmp_path / "article"
    art_dir.mkdir()
    arts = []
    for i in range(1, 3):  # 2 ảnh bài (đủ cho scene 1-2)
        p = art_dir / f"article_{i:02d}.jpg"
        Image.new("RGB", (900, 500), (200 - i * 30, 60, 60)).save(p, format="JPEG")
        arts.append(p)
    monkeypatch.setattr(ai, "collect_article_images",
                        lambda link, out, limit=6, title_hint="": [_copy_art(p, out)
                                                                   for p in arts])
    make_fake_bank_image("bank_article_01.png")  # bank cho scene 3-5

    r = client.post("/api/v1/shorts", headers=ingest, json={
        "mode": "news",
        "source_text": "Tin vắn về bài báo có ảnh gốc minh họa.",
        "source_note": "Nguồn: Tuổi Trẻ",
        "source_link": "https://tuoitre.example/bai-co-anh",
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    rp = client.post(f"/api/v1/shorts/{sid}/produce", headers=ingest)
    assert rp.status_code == 200, rp.text
    done = rp.json()
    assert done["status"] == "ready"

    scenes = done["scenes"]
    # scene 1-2: ảnh gốc bài (asset engine article-original), scene 3-5: bank
    assert scenes[0]["image_asset_id"] and scenes[1]["image_asset_id"]
    ast0 = client.get(f"/api/v1/shorts/{sid}/images/1/file", headers=ingest)
    assert ast0.status_code == 200
    banked = [sc for sc in scenes if sc["bank_used"] == 1]
    assert len(banked) == 3
    # AI-gen KHÔNG được gọi vì ảnh bài + bank đã phủ đủ
    assert short_adapters["img"].calls == []
    # source_link lưu đúng vào short
    assert client.get(f"/api/v1/shorts/{sid}", headers=ingest).json()["source_link"] \
        == "https://tuoitre.example/bai-co-anh"


def test_short_produce_no_image_source_honest_error(client, ingest, short_adapters, monkeypatch):
    """Không adapter ảnh + bank trống → lỗi tiếng người (không placeholder)."""
    import shutil
    monkeypatch.setenv("VIDEO_OFFLINE_TTS", "1")
    monkeypatch.setenv("VIDEO_FAKE_FFMPEG", "1")
    # Test này phải chắc chắn bank RỖNG (test khác có thể đã ghi bank chung)
    shutil.rmtree(data_dir() / "shorts_bank", ignore_errors=True)
    saved_img = providers.registry.image_gen
    providers.registry.image_gen = None  # chỉ có LLM, không có image-gen
    try:
        short = _create_short(client, ingest).json()
        r = client.post(f"/api/v1/shorts/{short['id']}/produce", headers=ingest)
        assert r.status_code == 422
        assert "shorts_bank" in r.json()["detail"]
        # Trạng thái failed + lý do tiếng người
        r2 = client.get(f"/api/v1/shorts/{short['id']}", headers=ingest)
        assert r2.json()["status"] == "failed"
        assert "Produce thất bại" in r2.json()["error_detail"]
    finally:
        providers.registry.image_gen = saved_img


def test_short_transition_laws_domain():
    # Luật: ready có thể produce lại, cancelled là terminal, draft không nhảy ready
    domain.assert_short_transition("draft", "scripted")
    domain.assert_short_transition("scripted", "scripted")  # self-loop sửa kịch bản
    domain.assert_short_transition("ready", "producing")
    domain.assert_short_transition("failed", "scripted")
    with pytest.raises(domain.TransitionError):
        domain.assert_short_transition("draft", "ready")
    with pytest.raises(domain.TransitionError):
        domain.assert_short_transition("cancelled", "scripted")


def test_karaoke_ass_geometry_and_k_tags(tmp_path):
    words = [
        {"text": "Chỉ", "start": 0.0, "end": 0.2},
        {"text": "sau", "start": 0.2, "end": 0.4},
        {"text": "1", "start": 0.4, "end": 0.5},     # nhấn: số
        {"text": "đêm", "start": 0.5, "end": 0.8},
    ]
    out = tmp_path / "test.ass"
    n = karaoke.build_short_ass(words, out, total_duration=1.0)
    assert n >= 1
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
    assert "{\\k" in text
    assert "MarginV" in text and ",288,1" in text  # safe margin 15% × 1920
    assert "{\\b1}" in text  # từ "1" được nhấn đậm


def test_short_delete_requires_admin(client, ingest, admin, short_adapters):
    short = _create_short(client, ingest).json()
    r = client.delete(f"/api/v1/shorts/{short['id']}", headers=ingest)
    assert r.status_code == 403
    r2 = client.delete(f"/api/v1/shorts/{short['id']}", headers=admin)
    assert r2.status_code == 200
    assert r2.json()["status"] == "cancelled"
    assert r2.json()["status_label"] == "Đã hủy"
