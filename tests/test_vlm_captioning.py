"""Unit tests for VLM keyframe captioning pipeline (Phase 2)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.llm_client import LLMResponseError
from pipeline.vlm_captioning import (
    caption_all_shots_parallel,
    caption_shot,
    caption_single_keyframe,
    generate_deterministic_description,
)


@pytest.fixture
def dummy_image(tmp_path: Path) -> Path:
    img = tmp_path / "test_frame.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64)
    return img


def test_vlm_caption_single_frame_success(dummy_image: Path):
    """VLM returns structured JSON and populates subject, action, context, and summary."""
    mock_payload = {
        "subject": "Người thợ gốm",
        "action": "đang xoay bàn gốm nắn hình bình hoa",
        "context": "xưởng thủ công truyền thống nhiều nắng",
        "summary": "Người thợ gốm đang tỉ mỉ nắn bình hoa đất sét trên bàn xoay trong xưởng mộc mạc.",
    }

    with patch("pipeline.vlm_captioning.chat_json_multimodal", return_value=mock_payload):
        res = caption_single_keyframe(dummy_image, model="gemini-2.0-flash")

    assert res["success"] is True
    assert res["is_vlm"] is True
    assert "Người thợ gốm" in res["summary"]
    assert res["subject"] == "Người thợ gốm"
    assert res["action"] == "đang xoay bàn gốm nắn hình bình hoa"
    assert res["model_used"] == "gemini-2.0-flash"


def test_vlm_caption_markdown_fenced_json(dummy_image: Path):
    """When chat_json_multimodal receives markdown fenced json, parser handles it."""
    # Test llm_client's _parse_llm_json with markdown fences
    from pipeline.llm_client import _parse_llm_json

    fenced_raw = (
        "```json\n"
        '{\n  "subject": "Xe cứu thương",\n  "action": "bật còi ưu tiên chạy nhanh",\n'
        '  "context": "đại lộ đông đúc lúc chập tối",\n  "summary": "Xe cứu thương vượt qua dòng xe cộ."\n}\n'
        "```"
    )
    parsed = _parse_llm_json(fenced_raw)
    assert parsed["subject"] == "Xe cứu thương"
    assert parsed["action"] == "bật còi ưu tiên chạy nhanh"
    assert parsed["summary"] == "Xe cứu thương vượt qua dòng xe cộ."


def test_vlm_caption_graceful_fallback_on_network_error(dummy_image: Path):
    """Provider failure or network error triggers graceful fallback without crashing."""
    shot = {
        "shot_id": 12,
        "start": 10.0,
        "end": 14.5,
        "keyframes": [{"path": str(dummy_image)}],
    }

    with patch("pipeline.vlm_captioning.chat_json_multimodal", side_effect=LLMResponseError("Connection timeout 504")):
        result_shot = caption_shot(shot)

    assert result_shot["vlm_success"] is False
    assert "Shot 12 (4.5s" in result_shot["vlm_description"]
    assert "Connection timeout 504" in result_shot["vlm_description"]


def test_vlm_caption_empty_keyframes():
    """Shot with no keyframes generates deterministic fallback gracefully."""
    shot = {
        "shot_id": 3,
        "start": 0.0,
        "end": 3.0,
        "keyframes": [],
    }

    result_shot = caption_shot(shot)
    assert result_shot["vlm_success"] is False
    assert "Shot 3" in result_shot["vlm_description"]
    assert "không có keyframe" in result_shot["vlm_description"]


def test_vlm_batch_parallel_ordering_and_progress(dummy_image: Path):
    """Parallel batch maintains original shot order and invokes progress callback."""
    shots = [
        {"shot_id": i, "start": float(i * 3), "end": float((i + 1) * 3), "keyframes": [{"path": str(dummy_image)}]}
        for i in range(5)
    ]

    progress_calls = []

    def _progress(pct: int, msg: str):
        progress_calls.append((pct, msg))

    mock_resp = {
        "subject": "Vận động viên",
        "action": "chạy nước rút",
        "context": "sân điền kinh",
        "summary": "Vận động viên đang bứt tốc về đích.",
    }

    with patch("pipeline.vlm_captioning.chat_json_multimodal", return_value=mock_resp):
        out_shots = caption_all_shots_parallel(
            shots,
            model="gpt-4o-mini",
            max_workers=2,
            progress_callback=_progress,
        )

    assert len(out_shots) == 5
    for i, shot in enumerate(out_shots):
        assert shot["shot_id"] == i
        assert shot["vlm_success"] is True
        assert shot["vlm_description"] == "Vận động viên đang bứt tốc về đích."

    assert len(progress_calls) >= 5
    assert progress_calls[-1][0] == 100


def test_generate_deterministic_description():
    """Deterministic generator classifies pace based on shot duration."""
    shot_fast = {"shot_id": 1, "start": 0.0, "end": 1.2}
    desc_fast = generate_deterministic_description(shot_fast)
    assert "cắt cảnh nhanh" in desc_fast

    shot_slow = {"shot_id": 2, "start": 0.0, "end": 8.0}
    desc_slow = generate_deterministic_description(shot_slow)
    assert "cảnh dài / tĩnh" in desc_slow
