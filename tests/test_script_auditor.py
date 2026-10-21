import pytest
from services.script_auditor import (
    ScriptAuditor,
    detect_language,
    build_dual_scripts_from_transcript,
)


def test_detect_language():
    assert detect_language("Đây là một đoạn kịch bản tài liệu tiếng Việt hoàn chỉnh.") == "vi"
    assert detect_language("This company was worth over 7.9 trillion dollars at its peak.") == "en"
    assert detect_language("") == "vi"


def test_parse_numbered_script():
    auditor = ScriptAuditor(target_scenes=3)
    raw = """
    Cảnh 1: Vào thế kỷ 17, Hà Lan đã trở thành trung tâm thương mại thế giới.
    Cảnh 2: Những con tàu buôn vượt đại dương mang về nguồn lợi khổng lồ.
    Cảnh 3: Nhưng cuộc cạnh tranh đã dẫn đến một cuộc khủng hoảng tài chính bất ngờ.
    """
    scenes = auditor.parse_raw_script(raw)
    assert len(scenes) == 3
    assert "thế kỷ 17" in scenes[0]
    assert "tàu buôn" in scenes[1]


def test_audit_valid_script():
    auditor = ScriptAuditor(target_scenes=3)
    raw = """
    1. Vào thế kỷ mười bảy Hà Lan vươn lên thành cường quốc thương mại hàng đầu thế giới.
    2. Các đoàn thuyền buôn mang về vàng bạc và hương liệu quý hiếm từ khắp các châu lục.
    3. Biến động thị trường đã mở ra một kỷ nguyên mới cho nền kinh tế thương mại toàn cầu.
    """
    res = auditor.audit_script(raw)
    assert res.is_valid is True
    assert res.language == "vi"
    assert "NamMinh" in res.recommended_voice
    assert len(res.scenes) == 3


def test_audit_too_long_scene():
    auditor = ScriptAuditor(target_scenes=2)
    # 35 words scene
    raw = """
    1. Đây là một câu chuyện cực kỳ dài và lan man kể về rất nhiều nhân vật khác nhau trong lịch sử của một quốc gia xa xôi nơi mà mọi người đều cố gắng tìm kiếm thành công và vinh quang nhưng cuối cùng thất bại.
    2. Cảnh hai ngắn hơn vừa vặn thời lượng.
    """
    res = auditor.audit_script(raw)
    assert res.is_valid is False
    assert any("quá dài" in issue for issue in res.issues)
    feedback = res.format_telegram_feedback()
    assert "⚠️" in feedback
    assert "Lỗi 1:" in feedback


def test_build_dual_scripts_from_transcript():
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Kind: captions Language: en This company was worth 7.9 trillion"},
        {"start": 10.0, "end": 20.0, "text": "And Spain hated the Dutch because of trade rivalry"},
    ]
    vi_scenes, en_scenes = build_dual_scripts_from_transcript(transcript, target_shots=2)
    assert len(vi_scenes) == 2
    assert len(en_scenes) == 2
    # Ensure VTT metadata stripped
    assert "Kind:" not in vi_scenes[0]["text"]
    assert "Kind:" not in en_scenes[0]["text"]
    # Check language separation
    assert detect_language(vi_scenes[0]["text"]) == "vi"
    assert detect_language(en_scenes[0]["text"]) == "en"
