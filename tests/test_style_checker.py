"""Unit tests for the Phase 3 VLM style checker (services.style_checker)."""

from pathlib import Path

import pytest

from services.style_checker import (
    StyleCheckError,
    check_image_style,
    generate_with_style_check,
    save_style_report,
)


@pytest.fixture
def sample_image(tmp_path: Path):
    from PIL import Image

    p = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), color=(240, 234, 220)).save(p, "PNG")
    return p


def _mock_vlm(monkeypatch, verdict=None, error=None):
    captured = {}

    def fake_multimodal(system_prompt, user_text, images=None, **kw):
        captured["system"] = system_prompt
        captured["user"] = user_text
        captured["images"] = images
        if error:
            raise error
        return verdict or {}

    import pipeline.llm_client as llm
    monkeypatch.setattr(llm, "chat_json_multimodal", fake_multimodal)
    return captured


def test_check_image_style_pass(sample_image, monkeypatch):
    _mock_vlm(monkeypatch, verdict={
        "ok": True, "score": 8.5,
        "issues": [], "summary": "crayon cartoon, flat 2D",
    })
    res = check_image_style(sample_image, subject="Nhà vua Hà Lan")
    assert res["ok"] is True
    assert res["score"] == 8.5
    assert res["issues"] == []


def test_check_image_style_score_floor_policy(sample_image, monkeypatch):
    """ok=true with score < 7.0 must NOT pass — the score floor is enforced in code."""
    _mock_vlm(monkeypatch, verdict={"ok": True, "score": 6.0, "issues": ["thiếu texture giấy"]})
    res = check_image_style(sample_image)
    assert res["ok"] is False
    assert res["issues"] == ["thiếu texture giấy"]


def test_check_image_style_vlm_failure_raises(sample_image, monkeypatch):
    from pipeline.llm_client import LLMResponseError

    _mock_vlm(monkeypatch, error=LLMResponseError("gateway down"))
    with pytest.raises(StyleCheckError, match="VLM style check failed"):
        check_image_style(sample_image)


def test_check_image_style_falls_back_when_primary_judge_refuses(sample_image, monkeypatch):
    from pipeline.llm_client import LLMResponseError

    monkeypatch.setattr(
        "services.settings_service.get_xkiro_vlm_model", lambda: "primary/vision"
    )
    calls = []

    def fake_multimodal(system_prompt, user_text, images=None, **kw):
        calls.append(kw.get("model"))
        if len(calls) == 1:
            raise LLMResponseError(
                "Error code: 400 - An attached image was rejected by the provider's safety policy."
            )
        return {"ok": True, "score": 8.5, "issues": [], "summary": "ok"}

    import pipeline.llm_client as llm
    monkeypatch.setattr(llm, "chat_json_multimodal", fake_multimodal)

    res = check_image_style(sample_image)
    assert res["ok"] is True and res["score"] == 8.5
    assert calls == ["primary/vision", "cohere/command-a-vision"]


def test_check_image_style_non_safety_error_does_not_fall_back(sample_image, monkeypatch):
    from pipeline.llm_client import LLMResponseError

    monkeypatch.setattr(
        "services.settings_service.get_xkiro_vlm_model", lambda: "primary/vision"
    )
    calls = []

    def fake_multimodal(system_prompt, user_text, images=None, **kw):
        calls.append(kw.get("model"))
        raise LLMResponseError("gateway down")

    import pipeline.llm_client as llm
    monkeypatch.setattr(llm, "chat_json_multimodal", fake_multimodal)

    with pytest.raises(StyleCheckError, match="VLM style check failed"):
        check_image_style(sample_image)
    assert calls == ["primary/vision"]


def test_check_image_style_missing_image_raises(tmp_path, monkeypatch):
    with pytest.raises(StyleCheckError, match="image not found"):
        check_image_style(tmp_path / "nope.png")


def test_check_image_style_non_numeric_score_raises(sample_image, monkeypatch):
    _mock_vlm(monkeypatch, verdict={"ok": True, "score": "high"})
    with pytest.raises(StyleCheckError, match="non-numeric score"):
        check_image_style(sample_image)


def test_generate_with_style_check_passes_first_try(tmp_path, sample_image, monkeypatch):
    _mock_vlm(monkeypatch, verdict={"ok": True, "score": 9.0, "issues": []})
    calls = []

    def gen(prompt, out_path):
        calls.append(prompt)
        out_path.write_bytes(sample_image.read_bytes())

    verdict = generate_with_style_check(gen, "draw a king", tmp_path / "ref.png")
    assert verdict["ok"] is True
    assert len(calls) == 1


def test_generate_with_style_check_regenerates_with_issues(tmp_path, sample_image, monkeypatch):
    verdicts = iter([
        {"ok": False, "score": 4.0, "issues": ["quả thật photorealistic", "bóng 3D"]},
        {"ok": True, "score": 8.0, "issues": []},
    ])
    captured_prompts = []

    def fake_multimodal(system_prompt, user_text, images=None, **kw):
        return next(verdicts)

    import pipeline.llm_client as llm
    monkeypatch.setattr(llm, "chat_json_multimodal", fake_multimodal)

    def gen(prompt, out_path):
        captured_prompts.append(prompt)
        out_path.write_bytes(sample_image.read_bytes())

    out = tmp_path / "ref.png"
    verdict = generate_with_style_check(gen, "draw a king", out)

    assert verdict["ok"] is True
    assert len(captured_prompts) == 2
    assert "STRICT STYLE REMINDERS" in captured_prompts[1]
    assert "quả thật photorealistic" in captured_prompts[1]


def test_generate_with_style_check_returns_last_verdict_after_exhaustion(
    tmp_path, sample_image, monkeypatch
):
    def fake_multimodal(system_prompt, user_text, images=None, **kw):
        return {"ok": False, "score": 5.0, "issues": ["vẫn 3D"]}

    import pipeline.llm_client as llm
    monkeypatch.setattr(llm, "chat_json_multimodal", fake_multimodal)

    calls = {"n": 0}

    def gen(prompt, out_path):
        calls["n"] += 1
        out_path.write_bytes(sample_image.read_bytes())

    verdict = generate_with_style_check(gen, "draw", tmp_path / "ref.png", max_attempts=2)
    assert calls["n"] == 2
    assert verdict["ok"] is False
    assert verdict["score"] == 5.0


def test_generate_fn_failure_propagates(tmp_path, monkeypatch):
    _mock_vlm(monkeypatch, verdict={"ok": True, "score": 9.0})

    def gen(prompt, out_path):
        raise RuntimeError("no image provider")

    with pytest.raises(RuntimeError, match="no image provider"):
        generate_with_style_check(gen, "draw", tmp_path / "ref.png")


def test_save_style_report(tmp_path):
    out = save_style_report(tmp_path, "Nhà vua", {"ok": True, "score": 8.0, "issues": [], "summary": "s"})
    import json
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["subject"] == "Nhà vua"
    assert data["score"] == 8.0
