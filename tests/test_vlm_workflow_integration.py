"""Integration tests for VLM into timeline report and script matching (Phase 3)."""

from pipeline.script_matcher import match_script_to_report
from pipeline.timeline_report import build_timeline_report


def test_timeline_report_incorporates_vlm_descriptions():
    """Report rows incorporate VLM descriptions from shots and elevate confidence."""
    timeline = {
        "version": 1,
        "actual_duration": 10.0,
        "clips": [
            {
                "id": "clip_001",
                "source_start": 0.0,
                "source_end": 5.0,
                "enabled": True,
            },
            {
                "id": "clip_002",
                "source_start": 5.0,
                "source_end": 10.0,
                "enabled": True,
            },
        ],
    }

    shots = [
        {
            "shot_id": 1,
            "start": 0.0,
            "end": 5.0,
            "vlm_description": "Người nông dân đang thu hoạch cà phê chín đỏ trên đồi dốc.",
            "vlm_success": True,
        },
        {
            "shot_id": 2,
            "start": 5.0,
            "end": 10.0,
            "vlm_description": "Cận cảnh máy rang hạt cà phê đang bốc khói thơm trong xưởng.",
            "vlm_success": True,
        },
    ]

    transcript = [
        {"start": 1.0, "end": 4.0, "text": "Mùa thu hoạch cà phê bắt đầu từ sáng sớm"},
    ]

    report = build_timeline_report(timeline=timeline, transcript=transcript, shots=shots)

    rows = report["rows"]
    assert len(rows) >= 2

    # Row with speech should have high confidence (both speech and VLM visual)
    r_speech = next(r for r in rows if r["has_speech"])
    assert "Người nông dân" in r_speech["visual_description"]
    assert r_speech["has_vlm"] is True
    assert r_speech["confidence"] == "high"

    # Row for clip 2 has VLM but no speech -> medium/high confidence
    r_clip2 = next(r for r in rows if r["clip_index"] == 1)
    assert "máy rang hạt cà phê" in r_clip2["visual_description"]
    assert r_clip2["has_vlm"] is True
    assert r_clip2["confidence"] in ("high", "medium")


def test_script_matcher_with_vlm_visual_evidence():
    """Script describing a visual action matches successfully using VLM visual keywords."""
    report = {
        "rows": [
            {
                "row_id": "row_001",
                "output_start": 0.0,
                "output_end": 4.0,
                "source_start": 10.0,
                "source_end": 14.0,
                "visual_description": "Người thợ hàn đang cắt kim loại tóe lửa trong xưởng cơ khí.",
                "transcript": "",
                "has_speech": False,
                "has_vlm": True,
                "audio_state": "noise_or_music_or_unknown",
            }
        ]
    }

    script = "Người thợ hàn thực hiện cắt gọt kim loại chuyên nghiệp."
    result = match_script_to_report(script, report)

    issue = result["issues"][0]
    assert issue["status"] == "matched"
    assert issue["severity"] == "low"
    assert "Khớp tốt với bối cảnh hình ảnh VLM" in issue["message"]
    assert "thợ" in issue["message"] or "kim" in issue["message"]


def test_script_matcher_detects_visual_mismatch():
    """Script expecting a beach scene flags a mismatch when VLM reports an office setting."""
    report = {
        "rows": [
            {
                "row_id": "row_001",
                "output_start": 0.0,
                "output_end": 5.0,
                "source_start": 0.0,
                "source_end": 5.0,
                "visual_description": "Nhân viên văn phòng đang gõ bàn phím trước màn hình máy tính.",
                "transcript": "",
                "has_speech": False,
                "has_vlm": True,
                "audio_state": "silence",
            }
        ]
    }

    script = "Khung cảnh bãi biển ngập tràn ánh nắng mùa hè và sóng biển vỗ rì rào."
    result = match_script_to_report(script, report)

    issue = result["issues"][0]
    assert issue["status"] == "miss"
    assert issue["severity"] == "high"
    assert "Lệch hình ảnh" in issue["message"]
    assert "Nhân viên văn phòng" in issue["message"]
