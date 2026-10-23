"""Unit tests for the Phase 2 best-of-N ScriptFactory (services.script_factory)."""

import json
import re

import pytest

import services.script_factory as sf
from services.script_factory import ScriptFactory, ScriptFactoryError


BASE_SLICES_VI = [
    {"index": i + 1, "situation": f"tình huống {i+1}", "text": f"Cảnh gốc {i+1} diễn biến quan trọng.",
     "prompt": "base prompt", "duration": 5.0, "is_key_scene": True}
    for i in range(3)
]
BASE_SLICES_EN = [
    {"index": sc["index"], "situation": sc["situation"], "text": f"Base scene {sc['index']}.",
     "prompt": "base prompt", "duration": 5.0, "is_key_scene": True}
    for sc in BASE_SLICES_VI
]
TRANSCRIPT = "Công ty Đông Ấn Hà Lan được thành lập năm 1602 với vốn 6.5 triệu guilders."


def _long_vi(index):
    return f"Cảnh cải tiến {index} mở ra bất ngờ về dòng tiền và điều đó thay đổi tất cả."


HOOK_VI = " ".join((
    "Năm 1602 công ty Đông Ấn Hà Lan huy động sáu phẩy năm triệu guilders "
    "và không ai ngờ điều gì xảy ra tiếp theo sau đó?"
).split())  # 27 words

CALLBACK_VI = " ".join((
    "Đây chính là câu trả lời cho nghịch lý năm 1602 khi dòng tiền guilders "
    "quay lại và thay đổi toàn bộ cục diện."
).split())  # 24 words


def _vi_line(index, words):
    base = f"Cảnh cải tiến {index} mở ra bất ngờ về dòng tiền và điều đó thay đổi tất cả".split()
    filler = ["hôm", "nay", "câu", "chuyện", "ấy", "vẫn", "tiếp", "tục", "để", "rồi"]
    return " ".join((base + filler * 4)[:words])


@pytest.fixture
def factory(tmp_path):
    f = ScriptFactory()
    monkey_slice = pytest.MonkeyPatch()
    monkey_slice.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=6: (BASE_SLICES_VI, BASE_SLICES_EN),
    )
    yield f
    monkey_slice.undo()


class Router:
    """Fake chat_json routing rewrite vs judge calls with configurable judge scores."""

    def __init__(self, score_plan, rewrite_error=None, wrong_count_angles=(),
                 short_rewrites=False, fail_chunks_above=None, repair_words=None):
        # score_plan: {angle_id: [round1_score, round2_score, ...]}
        self.score_plan = score_plan
        self.rewrite_error = rewrite_error
        self.wrong_count_angles = set(wrong_count_angles)
        self.short_rewrites = short_rewrites
        self.fail_chunks_above = fail_chunks_above
        self.repair_words = repair_words
        self.rewrite_prompts = []
        self.repair_calls = 0
        self.hook_calls = 0
        self.callback_calls = 0
        self.judge_calls = 0
        self.rewrite_calls = 0

    def __call__(self, system_prompt, user_prompt, **kw):
        import re

        if "(HOOK 30 GIÂY):" in user_prompt:
            self.hook_calls += 1
            m = re.search(r"\(HOOK 30 GIÂY\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
            items = json.loads(m.group(1)) if m else []
            return {
                "scenes": [
                    {
                        "index": item["index"],
                        "voiceover_vi": HOOK_VI,
                        "voiceover_en": f"Hook line {item['index']} with a shocking number?",
                    }
                    for item in items
                ]
            }
        if "(CALLBACK):" in user_prompt:
            self.callback_calls += 1
            m = re.search(r"\(CALLBACK\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
            items = json.loads(m.group(1)) if m else []
            return {
                "scenes": [
                    {
                        "index": item["index"],
                        "voiceover_vi": CALLBACK_VI,
                        "voiceover_en": "The answer returns to the 1602 paradox.",
                    }
                    for item in items
                ]
            }
        if "DANH SÁCH CẢNH CẦN SỬA NGÂN SÁCH TỪ:" in user_prompt:
            self.repair_calls += 1
            m = re.search(
                r"DANH SÁCH CẢNH CẦN SỬA NGÂN SÁCH TỪ:\n(\[.*?\])\n\n",
                user_prompt, re.DOTALL,
            )
            items = json.loads(m.group(1)) if m else []
            return {
                "scenes": [
                    {
                        "index": item["index"],
                        "voiceover_vi": (
                            _vi_line(item["index"], self.repair_words)
                            if self.repair_words
                            else _long_vi(item["index"])
                        ),
                        "voiceover_en": f"Short English line {item['index']} about money.",
                    }
                    for item in items
                ]
            }
        if "Chấm kịch bản" in system_prompt:
            round_idx = self.judge_calls // len(self.score_plan)
            self.judge_calls += 1
            labels = [
                ("analyst", "Nhà phân tích"),
                ("dramatist", "Dramatist"),
                ("teacher", "Người giảng giải"),
            ]
            angle = next(
                (a for a, label in labels if label in user_prompt), None
            )
            if angle is None:
                angle = next(iter(self.score_plan))
            plan = self.score_plan[angle]
            score = plan[min(round_idx, len(plan) - 1)]
            return {
                "score": score,
                "breakdown": {"hook": 2.0},
                "feedback": f"feedback cho {angle}",
            }
        # Rewrite call: identify angle from its label in the system prompt
        angle = "analyst"
        if "Dramatist" in system_prompt:
            angle = "dramatist"
        elif "Người giảng giải" in system_prompt:
            angle = "teacher"
        self.rewrite_calls += 1
        self.rewrite_prompts.append(user_prompt)
        if self.rewrite_error:
            raise RuntimeError(self.rewrite_error)
        import re

        m = re.search(r"trên tổng \d+\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
        n = len(json.loads(m.group(1))) if m else 0
        if self.fail_chunks_above and n > self.fail_chunks_above:
            raise RuntimeError(f"truncated JSON for {n} scenes")
        count = n - 1 if angle in self.wrong_count_angles else n
        return {
            "scenes": [
                {
                    "index": i + 1,
                    "situation": f"tình huống {angle} {i+1}",
                    "voiceover_vi": (
                        f"Cảnh ngắn gọn số {i+1}."
                        if self.short_rewrites
                        else _long_vi(i + 1)
                    ),
                    "voiceover_en": f"Improved scene {i+1} opens a surprise.",
                    "estimated_duration": 5.0,
                    "visual_prompt": "Whimsical hand-drawn crayon editorial cartoon",
                }
                for i in range(max(0, count))
            ]
        }


def test_best_variant_chosen_and_result_saved(factory, tmp_path, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3, project_dir=tmp_path
    )

    assert result["chosen_angle"] == "analyst"
    assert result["chosen_score"] == 9.2
    assert result["total_rounds"] == 1
    assert len(vi) == 3 and len(en) == 3
    assert vi[0]["situation"].startswith("tình huống analyst")
    assert all(4.0 <= sc["duration"] <= 8.0 for sc in vi)

    saved = json.loads((tmp_path / "algorithm_result.json").read_text(encoding="utf-8"))
    assert saved["chosen_angle"] == "analyst"
    assert saved["rounds"][0]["scores"][0]["angle"] == "analyst"
    assert "mechanical_metrics" in saved


def test_hook_window_and_callback_repairs_satisfy_qa(factory, monkeypatch):
    slices_vi = [
        {"index": i + 1, "situation": f"s{i+1}", "text": f"Cảnh gốc {i+1} diễn biến quan trọng.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(8)
    ]
    slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Base scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=8: (slices_vi, slices_en),
    )
    router = Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 60, "text": TRANSCRIPT}], target_shots=8
    )

    # QA S16: the 30s boundary must be crossed at <= 32s
    acc = 0.0
    for sc in vi:
        acc += sc["duration"]
        if acc >= 30.0:
            break
    assert 30.0 <= acc <= 32.0

    # QA S17: last scene echoes a hook keyword or lands a close phrase
    first_words = set(re.findall(r"\w{5,}", vi[0]["text"].lower()))
    last_words = set(re.findall(r"\w{5,}", vi[-1]["text"].lower()))
    assert first_words & last_words or "câu trả lời" in vi[-1]["text"].lower()

    assert [len(sc["text"].split()) for sc in vi[:4]] == [27, 27, 27, 27]
    assert result["post_repairs"]["hook"]["ok"] is True
    assert result["post_repairs"]["callback"]["ok"] is True
    assert router.hook_calls >= 1 and router.callback_calls >= 1


def test_duration_expansion_tops_up_short_script(factory, monkeypatch):
    slices_vi = [
        {"index": i + 1, "situation": f"s{i+1}", "text": f"Cảnh gốc {i+1} diễn biến quan trọng.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(30)
    ]
    slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Base scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=30: (slices_vi, slices_en),
    )
    router = Router(
        {"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]},
        repair_words=24,
    )
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 200, "text": TRANSCRIPT}],
        target_shots=30,
        target_duration=250.0,
    )
    expansion = result["post_repairs"]["duration_expansion"]
    assert expansion["applicable"] is True
    assert expansion["calls"] >= 1
    assert expansion["final_duration_sec"] > expansion["start_duration_sec"]
    assert expansion["final_drift"] <= 0.28


def test_regen_round_feeds_back_and_stops(factory, tmp_path, monkeypatch):
    # Round 1: all below 8.0 -> feedback loop; round 2: analyst reaches 8.5
    router = Router({
        "analyst": [6.0, 8.5],
        "dramatist": [6.5, 7.0],
        "teacher": [5.0, 5.5],
    })
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3, project_dir=tmp_path
    )
    assert result["total_rounds"] == 2
    assert result["chosen_score"] == 8.5
    assert result["chosen_angle"] == "analyst"
    # Round-1 best feedback (dramatist, 6.5) must appear in round-2 rewrite prompts
    assert any("feedback cho dramatist" in p for p in router.rewrite_prompts)


def test_returns_best_even_below_min_after_all_rounds(factory, monkeypatch):
    router = Router({"analyst": [5.0], "dramatist": [5.5], "teacher": [4.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3
    )
    assert result["total_rounds"] == 3  # initial + 2 regen rounds
    assert result["chosen_score"] == 5.5
    assert result["chosen_angle"] == "dramatist"


def test_invalid_scene_count_scores_zero(factory, monkeypatch):
    router = Router({"analyst": [9.0], "dramatist": [7.0], "teacher": [6.0]},
                    wrong_count_angles=("dramatist",))
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3
    )
    assert result["chosen_angle"] == "analyst"
    dramatist = next(
        s for r in result["rounds"] for s in r["scores"] if s["angle"] == "dramatist"
    )
    assert dramatist["valid"] is False
    assert dramatist["score"] == 0.0
    assert "returned 2 scenes for 3 inputs" in dramatist["invalid_reason"]


def test_all_rewrites_fail_raises(factory, monkeypatch):
    router = Router({}, rewrite_error="gateway 503")
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    with pytest.raises(ScriptFactoryError, match="invalid across all rounds"):
        factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)


def test_slice_failure_raises(factory, monkeypatch):
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=6: None,
    )
    with pytest.raises(ScriptFactoryError, match="Semantic slicing unavailable"):
        factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)


def test_mechanical_metrics_recorded(factory, monkeypatch):
    router = Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3
    )
    m = result["mechanical_metrics"]
    assert m["scene_count"] == 3
    # 18-word scenes at 3.5 w/s plus the 24-word callback close on the last scene:
    # 2 * 5.14s + 6.86s = 17.14s
    assert m["total_duration_sec"] == 17.1
    assert 0.0 <= m["short_sentence_pct"] <= 1.0
    assert m["estimated_wpm"] > 0
    assert "wpm_in_range" in m


def test_word_budget_repair_enforces_18_24_and_passes_audit(factory, monkeypatch):
    slices_vi = [
        {"index": i + 1, "situation": f"s{i+1}", "text": f"Cảnh gốc {i+1} diễn biến quan trọng.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(6)
    ]
    slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Base scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=6: (slices_vi, slices_en),
    )
    router = Router(
        {"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]},
        short_rewrites=True,
    )
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=6
    )
    assert router.repair_calls > 0
    assert all(18 <= len(sc["text"].split()) <= 28 for sc in vi)
    assert all(len(sc["text"].split()) <= 16 for sc in en)
    assert [len(sc["text"].split()) for sc in vi[:4]] == [27, 27, 27, 27]

    from services.script_auditor import ScriptAuditor
    audit = ScriptAuditor(algorithm_mode=True).audit_script(
        "\n".join(sc["text"] for sc in vi)
    )
    assert audit.is_valid is True


def test_rewrite_chunk_split_recovers_from_truncated_json(factory, monkeypatch):
    slices_vi = [
        {"index": i + 1, "situation": f"s{i+1}", "text": f"Cảnh {i+1}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(60)
    ]
    slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=60: (slices_vi, slices_en),
    )
    router = Router(
        {"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]},
        fail_chunks_above=sf.SPLIT_MIN_CHUNK,
    )
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run([{"start": 0, "end": 300, "text": "x"}], target_shots=60)
    assert len(vi) == 60
    assert [sc["index"] for sc in vi] == list(range(1, 61))
    assert router.rewrite_calls > 18  # 10-scene chunks had to be split into 5+5


def test_variants_rewrite_chunked_for_long_scripts(factory, monkeypatch):
    """60 scenes -> 6 rewrite calls per variant (VARIANT_CHUNK=10)."""
    long_slices_vi = [
        {"index": i + 1, "situation": f"s{i}", "text": f"Cảnh {i+1}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(60)
    ]
    long_slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in long_slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=60: (long_slices_vi, long_slices_en),
    )
    router = Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run([{"start": 0, "end": 300, "text": "x"}], target_shots=60)
    assert len(vi) == 60
    assert vi[59]["index"] == 60
    assert result["scene_count"] == 60
    expected_chunks = -(-60 // sf.VARIANT_CHUNK)  # ceil: 6 calls per variant
    assert router.rewrite_calls == 3 * expected_chunks
