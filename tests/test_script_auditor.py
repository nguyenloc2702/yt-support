from pathlib import Path
from services.script_auditor import (
    ScriptAuditor,
    detect_language,
    build_dual_scripts_from_transcript,
    format_srt_timestamp,
    export_segments_to_srt,
    export_segments_to_txt,
)


def test_format_srt_timestamp():
    assert format_srt_timestamp(0.0) == "00:00:00,000"
    assert format_srt_timestamp(65.123) == "00:01:05,123"
    assert format_srt_timestamp(3661.05) == "01:01:01,050"


def test_parse_srt_script():
    auditor = ScriptAuditor(target_scenes=2)
    raw_srt = """1
00:00:00,800 --> 00:00:04,500
Đây là câu chuyện về một công ty thương mại giàu nhất lịch sử thế giới.

2
00:00:04,500 --> 00:00:08,200
Vào thời kỳ hoàng kim, giá trị của họ vượt xa mọi tập đoàn hiện đại ngày nay.
"""
    scenes = auditor.parse_raw_script(raw_srt)
    assert len(scenes) == 2
    assert "công ty thương mại" in scenes[0]
    assert "00:00" not in scenes[0]
    assert "-->" not in scenes[0]
    assert "1" not in scenes[0]


def test_parse_script_with_metadata_headers():
    auditor = ScriptAuditor(target_scenes=2)
    template_text = """============================================================
📝 KỊCH BẢN GỐC & GỢI Ý PHÂN CẢNH TỪ VIDEO GỐC
============================================================
📺 Tiêu đề: Test Video
🔗 Link gốc: https://youtube.com/watch?v=123
⏱ Thời lượng gốc: 3 phút 20 giây | Tổng số từ: 50 từ
============================================================
📌 HƯỚNG DẪN XÀO CHẺ / TÙY BIẾN KỊCH BẢN:
1. Bạn có thể chỉnh sửa theo ý mình.

[CẢNH 1] (00:00:00 - 00:00:10)
Đây là phân cảnh mở đầu câu chuyện lịch sử thú vị này.

[CẢNH 2] (00:00:10 - 00:00:20)
Diễn biến tiếp theo chứng kiến những biến động kinh tế bất ngờ.
"""
    scenes = auditor.parse_raw_script(template_text)
    assert len(scenes) == 2
    assert "phân cảnh mở đầu" in scenes[0]
    assert "Diễn biến tiếp theo" in scenes[1]
    assert "TIÊU ĐỀ" not in scenes[0].upper()
    assert "HƯỚNG DẪN" not in scenes[0].upper()


def test_export_segments_to_srt_and_txt(tmp_path: Path):
    segments = [
        {"start": 0.0, "end": 4.5, "text": "Câu mở đầu đoạn 1."},
        {"start": 4.5, "end": 9.2, "text": "Câu kết thúc đoạn 2."},
    ]
    srt_file = tmp_path / "test.srt"
    txt_file = tmp_path / "test.txt"

    export_segments_to_srt(segments, srt_file)
    export_segments_to_txt(segments, txt_file, title="Test Video", url="https://youtube.com/watch?v=123", target_scenes=2)

    assert srt_file.exists()
    assert txt_file.exists()

    srt_content = srt_file.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:04,500" in srt_content
    assert "Câu mở đầu đoạn 1." in srt_content

    txt_content = txt_file.read_text(encoding="utf-8")
    assert "Test Video" in txt_content
    assert "[CẢNH 1]" in txt_content
    assert "Câu mở đầu đoạn 1." in txt_content


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
    assert res.recommended_voice == "confident-male-vietnamese"
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


def test_build_dual_scripts_from_transcript(monkeypatch):
    transcript = [
        {"start": 0.0, "end": 10.0, "text": "Kind: captions Language: en This company was worth 7.9 trillion"},
        {"start": 10.0, "end": 20.0, "text": "And Spain hated the Dutch because of trade rivalry"},
    ]

    captured = {}

    def fake_translate_batch(items, source_lang, target_lang, model=None):
        captured["items"] = list(items)
        captured["source_lang"] = source_lang
        captured["target_lang"] = target_lang
        return [f"BẢN_DỊCH_{i}" for i, _ in items]

    monkeypatch.setattr("services.xkiro_translator.translate_batch", fake_translate_batch)

    vi_scenes, en_scenes = build_dual_scripts_from_transcript(transcript, target_shots=2)
    assert len(vi_scenes) == 2
    assert len(en_scenes) == 2
    # Ensure VTT metadata stripped
    assert "Kind:" not in vi_scenes[0]["text"]
    assert "Kind:" not in en_scenes[0]["text"]
    # EN base script translated to VI via one batched xKiro pass
    assert captured["source_lang"] == "en"
    assert captured["target_lang"] == "vi"
    assert len(captured["items"]) == 2
    assert vi_scenes[0]["text"] == "BẢN_DỊCH_0"
    assert detect_language(en_scenes[0]["text"]) == "en"


def test_build_dual_scripts_raises_on_translation_failure(monkeypatch):
    from services.xkiro_translator import TranslationError

    transcript = [
        {"start": 0.0, "end": 10.0, "text": "This company was worth 7.9 trillion"},
        {"start": 10.0, "end": 20.0, "text": "And Spain hated the Dutch because of trade rivalry"},
    ]

    def failing_translate_batch(items, source_lang, target_lang, model=None):
        raise TranslationError("xKiro gateway down")

    monkeypatch.setattr("services.xkiro_translator.translate_batch", failing_translate_batch)

    import pytest
    with pytest.raises(RuntimeError, match="Dịch VI/EN qua xKiro thất bại"):
        build_dual_scripts_from_transcript(transcript, target_shots=2)


def test_audit_algorithm_mode_pacing_and_duration():
    # 40 scenes, each 12 to 24 words (4-8s at ~3 words/s)
    auditor = ScriptAuditor(algorithm_mode=True)
    raw = "\n".join([
        f"{i}. Đây là cảnh số {i} với nội dung đủ dài để đạt mười hai từ trở lên."
        for i in range(1, 41)
    ])
    res = auditor.audit_script(raw)
    assert res.is_valid is True
    assert len(res.scenes) == 40
    assert res.scenes[0]["duration"] == 6.0
    assert 12 <= res.scenes[0]["word_count"] <= 24

