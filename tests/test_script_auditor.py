from pathlib import Path
from services.script_auditor import (
    ScriptAuditor,
    detect_language,
    build_dual_scripts_from_transcript,
    format_srt_timestamp,
    export_segments_to_srt,
    export_segments_to_txt,
)


def _segs(n):
    return [
        {"start": float(i), "end": float(i + 1), "text": f"câu số {i} nói về dòng tiền."}
        for i in range(n)
    ]


def _scene_payload(label, n=3):
    return {
        "items": [
            {
                "situation": f"tình huống {label} {i}",
                "voiceover_vi": f"Cảnh {label} số {i} mở ra diễn biến bất ngờ về dòng tiền và thay đổi tất cả.",
                "voiceover_en": f"Scene {label} {i} opens with a money surprise.",
                "estimated_duration": 6.0,
                "visual_prompt": "Whimsical hand-drawn crayon editorial cartoon",
            }
            for i in range(n)
        ]
    }


def test_slice_block_or_split_recovers_from_bad_json(monkeypatch):
    import services.script_auditor as sa

    calls = []

    def fake_chat_json(system_prompt, user_prompt, **kw):
        calls.append(user_prompt)
        # Only the full block (spans both ends) fails, as with gateway truncation
        if "câu số 0" in user_prompt and "câu số 5" in user_prompt:
            raise RuntimeError("LLM trả về JSON không hợp lệ: [truncated")
        tail = user_prompt.split("câu số ")[-1]
        return _scene_payload(tail.strip()[0])

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)

    out = sa._slice_block_or_split(_segs(6), "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)", 6, "test-model")

    assert len(out) == 6  # two halves x 3 scenes each
    assert len(calls) == 4  # 2 failing full-block attempts + 2 half calls
    assert all("câu số 0" not in c or "câu số 5" not in c for c in calls[2:])


def test_slice_block_or_split_keeps_hook_and_callback_roles(monkeypatch):
    import services.script_auditor as sa

    prompts = []

    def fake_chat_json(system_prompt, user_prompt, **kw):
        prompts.append(user_prompt)
        if "KẾT THÚC" in user_prompt and "câu số 0" in user_prompt:
            raise RuntimeError("bad JSON")
        return _scene_payload("x")

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)

    out = sa._slice_block_or_split(_segs(4), "KẾT THÚC (CALLBACK & OUTRO)", 4, "test-model")

    assert len(out) == 6
    assert "KẾT THÚC" in prompts[0]  # full block keeps the callback role
    assert "THÂN BÀI" in prompts[2] and "KẾT THÚC" not in prompts[2]
    assert "KẾT THÚC" in prompts[3]


def test_slice_block_or_split_raises_when_unrecoverable(monkeypatch):
    import pytest

    import services.script_auditor as sa

    def fake_chat_json(system_prompt, user_prompt, **kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)

    with pytest.raises(RuntimeError, match="gateway down"):
        sa._slice_block_or_split([{"start": 0.0, "end": 1.0, "text": "một đoạn duy nhất."}],
                                 "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)", 3, "test-model")


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



def test_slicer_derives_scene_duration_from_word_count(monkeypatch):
    """QA walks these durations (S09/S10), so the model's guess is ignored."""
    import services.script_auditor as sa

    def line(words):
        return " ".join(["từ"] * words)

    payload = {
        "items": [
            {"situation": "s1", "voiceover_vi": line(21), "voiceover_en": "money moves",
             "estimated_duration": 99.0, "visual_prompt": "crayon editorial"},
            {"situation": "s2", "voiceover_vi": line(8), "voiceover_en": "short line",
             "estimated_duration": 1.0, "visual_prompt": "crayon editorial"},
            {"situation": "s3", "voiceover_vi": line(40), "voiceover_en": "long english line",
             "estimated_duration": 3.0, "visual_prompt": "crayon editorial"},
        ]
    }
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        "services.settings_service.get_xkiro_generation_model", lambda: "test-model"
    )
    monkeypatch.setattr(
        "pipeline.llm_client.chat_json", lambda *a, **kw: payload
    )

    segments = [{"start": float(i), "end": float(i + 1), "text": f"nội dung dài dòng {i}."}
                for i in range(3)]
    vi, en = sa.build_semantic_dual_scripts_from_transcript(segments, target_shots=3)

    # 21/3.5 = 6.0s; 8 words clamps up to the floor, 40 words clamps down to the cap
    assert [sc["duration"] for sc in vi] == [
        21 / sa.SCENE_WORDS_PER_SECOND, sa.MIN_SHOT_DURATION_ALGORITHM,
        sa.MAX_SHOT_DURATION_ALGORITHM,
    ]
    assert [sc["duration"] for sc in en] == [sc["duration"] for sc in vi]


def test_slicer_prompt_states_the_derived_budget_not_a_hardcoded_one(monkeypatch):
    """Steering copy and the enforced gate must come from the same constants."""
    import services.script_auditor as sa

    seen = {}

    def fake_chat_json(system_prompt, user_prompt, **kw):
        seen["prompt"] = user_prompt
        return {"items": []}

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)
    lo, hi = sa.RECOMMENDED_WORDS_PER_SHOT_ALGORITHM
    sa._slice_one_block(
        [{"start": 0.0, "end": 60.0, "text": "một đoạn transcript đủ dài để slice."}],
        "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)", 3, "test-model",
    )

    assert f"MỖI cảnh {lo}-{hi} từ" in seen["prompt"]
    assert f"{lo / sa.SCENE_WORDS_PER_SECOND:.1f}-{hi / sa.SCENE_WORDS_PER_SECOND:.1f} giây" in seen["prompt"]
    assert f"{sa.EN_MIN_WORDS}-{sa.EN_MAX_WORDS_ALGORITHM} từ mỗi cảnh" in seen["prompt"]


def _narration(lines):
    return "\n".join(lines)


def _sentence(words, seed="câu chuyện dòng tiền"):
    filler = (seed.split() * 40)[:words]
    return " ".join(filler) + "."


def test_parser_does_not_treat_a_leading_number_as_a_scene_marker():
    """A 173-line narration became 6 'scenes' of 675 words because '84% …' matched."""
    import services.script_auditor as sa

    lines = [_sentence(16) for _ in range(20)]
    lines[0] = "84% tin ngân hàng trung ương tạo tiền. Sai. " + _sentence(10)
    lines[4] = "10.000 đô la không tồn tại nay xuất hiện. " + _sentence(8)

    parsed = sa.ScriptAuditor(algorithm_mode=True).parse_raw_script(_narration(lines))

    assert len(parsed) == len(lines)


def test_parser_still_reads_decorated_scene_prefixes():
    import services.script_auditor as sa

    text = "[CẢNH 1] " + _sentence(16) + "\n[CẢNH 2] " + _sentence(16) + "\n3. " + _sentence(16)

    parsed = sa.ScriptAuditor(algorithm_mode=True).parse_raw_script(text)

    assert len(parsed) == 3
    assert parsed[2].endswith(".")


def test_algorithm_audit_tolerates_the_same_tail_the_factory_accepts():
    import services.script_auditor as sa

    lines = [_sentence(18) for _ in range(20)]
    lines[5] = _sentence(40)

    result = sa.ScriptAuditor(target_scenes=20, algorithm_mode=True).audit_script(_narration(lines))

    assert result.total_scenes == 20
    assert result.is_valid is True, result.issues


def test_algorithm_audit_rejects_once_outliers_pass_the_ratio():
    import services.script_auditor as sa

    lines = [_sentence(18) for _ in range(20)]
    for idx in (2, 7, 13):
        lines[idx] = _sentence(40)

    result = sa.ScriptAuditor(target_scenes=20, algorithm_mode=True).audit_script(_narration(lines))

    assert result.is_valid is False
    assert any("quá dài" in issue for issue in result.issues)


def test_audit_length_cap_follows_the_scene_count_it_was_given():
    """A 1000s video plans ~168 scenes; the old default cap of 120 rejected it."""
    import services.script_auditor as sa

    lines = [_sentence(18) for _ in range(173)]

    strict = sa.ScriptAuditor(algorithm_mode=True).audit_script(_narration(lines))
    sized = sa.ScriptAuditor(target_scenes=168, algorithm_mode=True).audit_script(_narration(lines))

    assert strict.is_valid is False
    assert any("quá dài (173 cảnh)" in issue for issue in strict.issues)
    assert sized.is_valid is True, sized.issues
