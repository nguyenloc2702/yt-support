"""Unit tests for pipeline/script_matcher.py.

Tests script-to-report matching, partial matching, missing evidence,
audio missing detection (silence/music when speech expected), and empty inputs.
"""

from __future__ import annotations

from pipeline.script_matcher import match_script_to_report


def test_empty_script_and_empty_report():
    """Empty script and empty report returns clean structure without crash."""
    report = {"rows": []}
    result = match_script_to_report("", report)

    assert result["summary"]["total"] == 0
    assert result["summary"]["matched"] == 0
    assert result["summary"]["miss"] == 0
    assert result["issues"] == []


def test_empty_report_with_script():
    """Non-empty script with empty report reports miss gracefully."""
    report = {"rows": []}
    script = "Chào mừng bạn đến với kênh hôm nay."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["miss"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "miss"
    assert issue["severity"] == "high"
    assert "rỗng" in issue["message"]


def test_script_matching_exact():
    """Script segment with strong evidence match returns matched and low severity."""
    report = {
        "rows": [
            {
                "output_start": 0.0,
                "output_end": 5.0,
                "transcript": "Hôm nay chúng ta sẽ tìm hiểu về trí tuệ nhân tạo",
                "visual_description": "Toàn cảnh phòng máy",
                "has_speech": True,
                "audio_state": "speech_detected",
            }
        ]
    }
    script = "Hôm nay chúng ta sẽ tìm hiểu về trí tuệ nhân tạo."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["matched"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "matched"
    assert issue["severity"] == "low"
    assert issue["start"] == 0.0
    assert issue["end"] == 5.0


def test_script_partial_matching():
    """Script segment with partial evidence match returns partial and medium severity."""
    report = {
        "rows": [
            {
                "output_start": 10.0,
                "output_end": 15.0,
                "transcript": "trí tuệ nhân tạo phát triển nhanh",
                "visual_description": "đồ thị tăng trưởng công nghệ",
                "has_speech": True,
                "audio_state": "speech_detected",
            }
        ]
    }
    script = "Trí tuệ nhân tạo đang thay đổi toàn bộ hệ thống giáo dục hiện đại."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["partial"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "partial"
    assert issue["severity"] == "medium"
    assert issue["start"] == 10.0
    assert issue["end"] == 15.0


def test_script_miss_no_evidence():
    """Script segment with completely unrelated text returns miss and high severity."""
    report = {
        "rows": [
            {
                "output_start": 0.0,
                "output_end": 5.0,
                "transcript": "con mèo đang ngủ",
                "visual_description": "chú mèo tam thể nằm trên ghế",
                "has_speech": True,
                "audio_state": "speech_detected",
            }
        ]
    }
    script = "Tàu vũ trụ phóng lên sao hỏa thành công rực rỡ."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["miss"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "miss"
    assert issue["severity"] == "high"


def test_script_speech_required_but_silence_detected():
    """Script explicitly requiring voice/speech in a silence region triggers HIGH miss with actionable timestamp."""
    report = {
        "rows": [
            {
                "output_start": 12.3,
                "output_end": 18.7,
                "transcript": "",
                "visual_description": "phong cảnh rừng núi tĩnh lặng",
                "has_speech": False,
                "audio_state": "silence",
            }
        ]
    }
    script = "MC nói lời mở đầu giới thiệu về khu bảo tồn thiên nhiên hoang dã."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["miss"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "miss"
    assert issue["severity"] == "high"
    assert issue["start"] == 12.3
    assert issue["end"] == 18.7
    assert "từ 12.3s đến 18.7s đang miss audio" in issue["message"]
    assert "silence" in issue["message"]


def test_script_speech_required_but_music_or_noise_only():
    """Script requiring speech in a region with only background noise/music triggers HIGH miss."""
    report = {
        "rows": [
            {
                "output_start": 25.0,
                "output_end": 35.0,
                "transcript": "",
                "visual_description": "cảnh xe chạy trên đường phố",
                "has_speech": False,
                "audio_state": "noise_or_music_or_unknown",
            }
        ]
    }
    script = "Chuyên gia chia sẻ kinh nghiệm lái xe an toàn trong mùa mưa."
    result = match_script_to_report(script, report)

    assert result["summary"]["total"] == 1
    assert result["summary"]["miss"] == 1
    issue = result["issues"][0]
    assert issue["status"] == "miss"
    assert issue["severity"] == "high"
    assert "thiếu thoại" in issue["message"]
