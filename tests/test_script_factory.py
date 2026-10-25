"""Unit tests for the Phase 2 best-of-N ScriptFactory (services.script_factory)."""

import json
import logging
import math
import re

import pytest

import services.script_factory as sf
from services.algorithm_qa import SCENE_DUR_RANGE
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
                 short_rewrites=False, fail_chunks_above=None, repair_words=None,
                 word_plan=None, repair_echo=False, empty_scene_in_chunks_above=None,
                 en_word_plan=None, plain_visual_for=(), outline=None,
                 edit_issues=None, overflow_at_size=None):
        # score_plan: {angle_id: [round1_score, round2_score, ...]}
        self.score_plan = score_plan
        self.outline = outline or {
            "paradox": "Một công ty từ nhà kho dột đứng đầu thế giới.",
            "timeline": [{"year": "1602", "event": "thành lập"}],
            "acts": [{"range": "1-3", "beat": "mở nghịch lý"}],
            "open_loops": ["vì sao"],
            "callback": "quay lại nghịch lý",
            "banned": ["nhà kho dột"],
        }
        self.edit_issues = edit_issues or []
        self.outline_calls = 0
        self.edit_calls = 0
        self.revision_calls = []
        self.revision_prompts = []
        self.rewrite_error = rewrite_error
        self.wrong_count_angles = set(wrong_count_angles)
        self.short_rewrites = short_rewrites
        self.fail_chunks_above = fail_chunks_above
        self.repair_words = repair_words
        # word_plan: {absolute scene index: word count} for rewrite output
        self.word_plan = word_plan or {}
        self.en_word_plan = en_word_plan or {}
        # Scenes whose visual_prompt drops the crayon style (QA S13 regression).
        self.plain_visual_for = set(plain_visual_for)
        # repair_echo: answer a repair request by echoing the same lines back,
        # i.e. a model that cannot fix the scene. Lets tests see the raw verdict.
        self.repair_echo = repair_echo
        self.empty_scene_in_chunks_above = empty_scene_in_chunks_above
        # Inflate every non-strict reply for chunks of this size (the live run
        # answered a 5-scene request with 23 scenes).
        self.overflow_at_size = overflow_at_size
        self.rewrite_prompts = []
        self.repair_prompts = []
        self.repair_calls = 0
        self.hook_calls = 0
        self.hook_prompts = []
        self.callback_calls = 0
        self.judge_calls = 0
        self.judge_prompts = []
        self.rewrite_calls = 0
        self.rewrite_systems = []

    def __call__(self, system_prompt, user_prompt, **kw):
        import re

        if "kiến trúc sư kịch bản" in system_prompt:
            self.outline_calls += 1
            return self.outline
        if "biên tập viên tổng" in system_prompt:
            self.edit_calls += 1
            return {"issues": self.edit_issues}
        if "(HOOK 30 GIÂY):" in user_prompt:
            self.hook_calls += 1
            self.hook_prompts.append(user_prompt)
            m = re.search(r"\(HOOK 30 GIÂY\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
            items = json.loads(m.group(1)) if m else []
            return {
                "cong_thuc": "the-statistic",
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
            self.repair_prompts.append(user_prompt)
            if self.repair_echo:
                return {
                    "scenes": [
                        {
                            "index": item["index"],
                            "voiceover_vi": item["voiceover_vi"],
                            "voiceover_en": item["voiceover_en"],
                        }
                        for item in items
                    ]
                }
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
        if "Nâng MỖI cảnh trên" in user_prompt:
            m = re.search(
                r"DANH SÁCH CẢNH CẦN SỬA:\n(\[.*?\])\n\n", user_prompt, re.DOTALL
            )
            items = json.loads(m.group(1)) if m else []
            return {
                "scenes": [
                    {
                        "index": item["index"],
                        "voiceover_vi": _vi_line(item["index"], self.repair_words or 24),
                        "voiceover_en": f"Expanded English line {item['index']} about money.",
                    }
                    for item in items
                ]
            }
        if "LỖI CẦN SỬA:" in user_prompt:
            m = re.search(
                r"DANH SÁCH CẢNH CẦN SỬA:\n(\[.*?\])\n\n", user_prompt, re.DOTALL
            )
            items = json.loads(m.group(1)) if m else []
            self.revision_calls.append([item["index"] for item in items])
            self.revision_prompts.append(user_prompt)
            return {
                "scenes": [
                    {
                        "index": item["index"],
                        "voiceover_vi": (
                            f"Nhưng rồi câu chuyện cảnh {item['index']} rẽ sang hướng khác "
                            "và dòng tiền vẫn cứ chảy về phía trước."
                        ),
                        "voiceover_en": (
                            f"But then scene {item['index']} turned, and the money "
                            "kept flowing forward anyway."
                        ),
                    }
                    for item in items
                ]
            }
        if "Chấm kịch bản" in system_prompt:
            round_idx = self.judge_calls // len(self.score_plan)
            self.judge_calls += 1
            self.judge_prompts.append(user_prompt)
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
        self.rewrite_systems.append(system_prompt)
        if self.rewrite_error:
            raise RuntimeError(self.rewrite_error)
        import re

        m = re.search(r"trên tổng \d+\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
        items = json.loads(m.group(1)) if m else []
        n = len(items)
        if self.fail_chunks_above and n > self.fail_chunks_above:
            raise RuntimeError(f"truncated JSON for {n} scenes")
        if self.overflow_at_size == n and "LƯU Ý BẮT BUỘC" not in user_prompt:
            count = n + 18
        else:
            count = n - 1 if angle in self.wrong_count_angles else n
        return {
            "scenes": [
                {
                    "index": i + 1,
                    "situation": f"tình huống {angle} {i+1}",
                    "voiceover_vi": self._vi_for(items, i),
                    "voiceover_en": self._en_for(items, i),
                    "estimated_duration": 5.0,
                    "visual_prompt": (
                        "Extreme close-up of a chalkboard with numbers, no photorealistic details"
                        if self._abs_index(items, i) in self.plain_visual_for
                        else "Whimsical hand-drawn crayon editorial cartoon"
                    ),
                }
                for i in range(max(0, count))
            ]
        }

    def _abs_index(self, items, i):
        if i < len(items) and isinstance(items[i], dict):
            return int(items[i].get("index", i + 1))
        return i + 1

    def _vi_for(self, items, i):
        index = self._abs_index(items, i)
        limit = self.empty_scene_in_chunks_above
        if limit is not None and len(items) > limit and i == 0:
            return ""
        if index in self.word_plan:
            return _vi_line(index, self.word_plan[index])
        if self.short_rewrites:
            return f"Cảnh ngắn gọn số {index}."
        return _long_vi(index)

    def _en_for(self, items, i):
        index = self._abs_index(items, i)
        if index in self.en_word_plan:
            return " ".join(["line"] * self.en_word_plan[index])
        return f"Improved scene {index} opens a surprise."


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

    hook_rep = result["post_repairs"]["hook"]
    assert hook_rep["formula"] == "the-statistic"
    assert hook_rep["machine_hook_score"]["verdict"] >= 0
    assert hook_rep["machine_hook_score"]["weakest"] in {
        "specificity", "address", "stakes", "curiosity", "brevity",
    }
    # The repair prompt must carry the 21-formula catalog for the model to pick from
    assert all("CÔNG THỨC HOOK" in p and "the-statistic" in p for p in router.hook_prompts)


def test_judge_prompt_carries_the_machine_hook_verdict(factory, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)

    assert router.judge_prompts
    for p in router.judge_prompts:
        m = re.search(
            r"ĐIỂM HOOK MÁY .*?: (\d+); yếu nhất: "
            r"(specificity|address|stakes|curiosity|brevity)", p,
        )
        assert m, p[-600:]
        assert 0 <= int(m.group(1)) <= 100
        assert "Hook dưới 70 PHẢI bị trừ điểm mục 'hook'" in p


def test_rewrite_system_prompt_carries_the_narration_laws(factory, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)

    assert router.rewrite_systems
    assert all(
        "LUẬT NARRATION" in s and "Mở bằng tình huống cụ thể" in s
        for s in router.rewrite_systems
    )


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


# --- W1: one pacing law, derived from the QA scorecard --------------------


def test_word_window_is_derived_from_the_qa_duration_band():
    """No stage may invent a per-scene gate narrower than the scorecard's."""
    import services.script_auditor as sa

    assert sa.GENERATED_WORD_WINDOW == (
        math.ceil(SCENE_DUR_RANGE[0] * sa.SCENE_WORDS_PER_SECOND),
        math.floor(SCENE_DUR_RANGE[1] * sa.SCENE_WORDS_PER_SECOND),
    )
    assert (sf.VI_MIN_WORDS, sf.VI_MAX_WORDS) == sa.GENERATED_WORD_WINDOW == (14, 28)
    assert sf.WORDS_PER_SECOND == sa.SCENE_WORDS_PER_SECOND
    assert sf.EN_MAX_WORDS == sa.EN_MAX_WORDS_ALGORITHM
    assert sa.MAX_WORDS_PER_SHOT_ALGORITHM == sa.GENERATED_WORD_WINDOW[1]


def test_steering_band_sits_inside_the_enforced_window():
    import services.script_auditor as sa

    assert sf.VI_TARGET_WORDS == sa.RECOMMENDED_WORDS_PER_SHOT_ALGORITHM
    assert sf.VI_MIN_WORDS <= sf.VI_TARGET_WORDS[0]
    assert sf.VI_TARGET_WORDS[1] <= sf.VI_MAX_WORDS


def test_gate_window_maps_into_qa_scene_duration_band():
    edges = [
        sf.VI_MIN_WORDS / sf.WORDS_PER_SECOND,
        sf.VI_MAX_WORDS / sf.WORDS_PER_SECOND,
    ]
    assert all(SCENE_DUR_RANGE[0] <= d <= SCENE_DUR_RANGE[1] for d in edges)


# --- W2/W3: acceptance is a ratio, aggregates are absolute ----------------


def _slices(n):
    vi = [
        {"index": i + 1, "situation": f"s{i+1}", "text": f"Cảnh gốc {i+1} diễn biến quan trọng.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(n)
    ]
    en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Base scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in vi
    ]
    return vi, en


def _variant(factory, monkeypatch, n, scores=None, **router_kw):
    """Build one variant directly, so the acceptance verdict is observable."""
    base_vi, base_en = _slices(n)
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=n: (base_vi, base_en),
    )
    router = Router(scores or {"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]},
                    **router_kw)
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)
    variant = factory._make_variant(sf.VARIANT_ANGLES[0], base_vi, base_en, TRANSCRIPT, "")
    return variant, router


def test_variant_accepted_with_five_percent_outliers(factory, monkeypatch):
    # The old all-or-nothing gate condemned the whole variant for this one scene.
    variant, _ = _variant(factory, monkeypatch, 20, word_plan={7: 9}, repair_echo=True)

    assert variant.valid is True, variant.invalid_reason
    assert variant.budget_report["ratio"] == 0.95
    assert variant.budget_report["offenders"] == 1
    assert variant.budget_report["vi_low"] == 1


def test_variant_survives_outliers_beyond_the_ratio_and_reports_target(factory, monkeypatch):
    """QA has no per-scene word check: the ratio aims pacing, it does not veto."""
    variant, router = _variant(
        factory, monkeypatch, 20, word_plan={3: 9, 8: 9, 13: 9}, repair_echo=True
    )

    assert variant.valid is True, variant.invalid_reason
    assert variant.budget_report["ratio"] == 0.85
    assert variant.budget_report["target_met"] is False
    assert router.repair_calls == sf.MAX_REPAIR_ROUNDS  # echoed output never fixes it


def test_mean_scene_duration_outside_band_is_a_warning_not_a_veto(factory, monkeypatch):
    # Every scene inside the 14-28 window, but 28 words each => 8.0s scenes:
    # S10 grades that mean as a fail, yet the content is legal and S11 tolerates
    # it. A live run lost a whole variant to 6.68s against a 6.5s ceiling, so the
    # band is reported, never enforced here.
    variant, router = _variant(
        factory, monkeypatch, 20,
        word_plan={i: 28 for i in range(1, 21)}, repair_echo=True,
    )

    assert variant.valid is True, variant.invalid_reason
    assert variant.budget_report["ok"] is True
    assert variant.budget_report["offenders"] == 0
    assert any(
        "mean scene duration" in w for w in variant.budget_report["warnings"]
    )
    assert router.repair_calls == 0  # nothing per-scene to repair


def test_min_chunk_overflow_gets_one_strict_retry(factory, monkeypatch, caplog):
    # A 5-scene request came back as a rewrite of the whole story ("23 scenes
    # for 5 inputs") and, being at SPLIT_MIN_CHUNK, had nowhere smaller to go —
    # that alone invalidated two of three live variants.
    with caplog.at_level(logging.WARNING):
        variant, router = _variant(
            factory, monkeypatch, 10, fail_chunks_above=9, overflow_at_size=5,
        )

    assert variant.valid is True, variant.invalid_reason
    assert len(variant.scenes_vi) == 10
    assert router.rewrite_calls == 5  # 10-scene try + 2x(5-scene try + strict retry)
    assert any("LƯU Ý BẮT BUỘC" in p for p in router.rewrite_prompts)
    assert "retrying once with an exact count" in caplog.text


def test_repair_never_runs_on_an_already_compliant_variant(factory, monkeypatch):
    variant, router = _variant(factory, monkeypatch, 20)

    assert variant.valid is True
    assert router.repair_calls == 0


def test_repair_stops_as_soon_as_acceptance_holds(factory, monkeypatch):
    variant, router = _variant(factory, monkeypatch, 20, word_plan={3: 9, 8: 9, 13: 9})

    assert variant.valid is True
    assert router.repair_calls == 1  # one batch of 3, then the verdict already passes


def test_repair_failure_log_shares_the_breakdown(factory, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _variant(
            factory, monkeypatch, 20,
            word_plan={3: 9, 8: 30, 13: 9}, repair_echo=True,
        )

    lines = [r.getMessage() for r in caplog.records if "out of budget" in r.getMessage()]
    assert lines, "repair loop must report what it could not fix"
    assert "vi_low=2" in lines[0] and "vi_high=1" in lines[0]


def test_en_only_violation_counts_in_ratio_not_killer(factory, monkeypatch):
    variant, _ = _variant(factory, monkeypatch, 20, en_word_plan={5: 30}, repair_echo=True)

    assert variant.valid is True, variant.invalid_reason
    assert variant.budget_report["en_high"] == 1

    two_lines, _ = _variant(
        factory, monkeypatch, 20, en_word_plan={5: 30, 9: 30}, repair_echo=True
    )
    assert two_lines.valid is True, two_lines.invalid_reason
    assert two_lines.budget_report["en_high"] == 2
    assert two_lines.budget_report["target_met"] is False


# --- W4: a blank line goes through split-retry, not variant death ---------


def test_empty_voiceover_triggers_halving_retry(factory, monkeypatch):
    variant, router = _variant(factory, monkeypatch, 10, empty_scene_in_chunks_above=5)

    assert variant.valid is True, variant.invalid_reason
    assert len(variant.scenes_vi) == 10
    assert all(sc["text"] for sc in variant.scenes_vi)
    assert router.rewrite_calls == 3  # 1 chunk of 10 failed, retried as 5 + 5


def test_empty_voiceover_persists_only_after_full_split(factory, monkeypatch):
    variant, _ = _variant(factory, monkeypatch, 10, empty_scene_in_chunks_above=0)

    assert variant.valid is False
    assert "returned an empty voiceover_vi" in variant.invalid_reason
    assert "scene 1" in variant.invalid_reason


def test_budget_report_reaches_algorithm_result(factory, monkeypatch, tmp_path):
    base_vi, base_en = _slices(20)
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=20: (base_vi, base_en),
    )
    monkeypatch.setattr(
        "pipeline.llm_client.chat_json",
        Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]},
               word_plan={7: 9}, repair_echo=True),
    )

    _, _, result = factory.run(
        [{"start": 0, "end": 200, "text": TRANSCRIPT}], target_shots=20, project_dir=tmp_path
    )

    report = result["budget_report"]
    assert report["ratio"] >= 0.95
    saved = json.loads((tmp_path / "algorithm_result.json").read_text(encoding="utf-8"))
    assert saved["budget_report"]["ratio"] == report["ratio"]
    assert saved["budget_report"]["offenders"] == report["offenders"]


def test_on_progress_heartbeats_while_the_script_is_being_written(factory, monkeypatch, tmp_path):
    """A genesis that never touches its row looks identical to a dead one."""
    base_vi, base_en = _slices(20)
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=20: (base_vi, base_en),
    )
    monkeypatch.setattr(
        "pipeline.llm_client.chat_json",
        Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]}),
    )

    beats = []
    factory.run(
        [{"start": 0, "end": 200, "text": TRANSCRIPT}], target_shots=20,
        project_dir=tmp_path, on_progress=lambda: beats.append(len(beats)),
    )

    assert len(beats) >= 6  # at least one beat per rewrite chunk of every variant
    assert sf._beat(None) is None  # heartbeat must tolerate no callback


def test_judge_sees_the_whole_script_not_just_the_first_9000_chars(factory, monkeypatch):
    """Rubric items 2 (open loops) and 7 (callback) live in the ending.

    The old prompt cut the script at 9000 chars; a 173-scene script is ~17k, so
    the judge scored the back half blind and its breakdown could not sum to its
    score (observed: 8.8 vs 7.8).
    """
    router = Router({"analyst": [9.0], "dramatist": [5.0], "teacher": [5.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    scenes = [
        {
            "index": i + 1,
            "text": f"Cảnh số {i + 1} kể về dòng tiền guilders và hậu quả khó tin của nó.",
            "duration": 5.0,
        }
        for i in range(150)
    ]
    variant = sf.Variant(angle=sf.VARIANT_ANGLES[0], scenes_vi=scenes, valid=True)

    factory._judge(variant, TRANSCRIPT)

    prompt = router.judge_prompts[0]
    assert len(prompt) > 9000
    assert "Cảnh 150" in prompt  # last scene survives intact
    assert "Cảnh 150 (5.0s): Cảnh số 150" in prompt


def test_trim_to_word_cap_keeps_the_opening_number_and_closing_question():
    """A 34-word hook line cannot be read in an 8s scene (QA S08/S09)."""
    line = (
        "84% tin ngân hàng trung ương tạo tiền. Sai. "
        "2014, giáo sư Werner bước vào ngân hàng Đức. "
        "Yêu cầu: Ngồi đây. Theo dõi khoản vay. "
        "Tiền sinh ra từ hư vô? Thật không?"
    )
    capped = sf._trim_to_word_cap(line, 28)

    assert len(capped.split()) <= 28
    assert capped.startswith("84%")
    assert capped.endswith("?")
    assert sf._trim_to_word_cap("Ngắn gọn thôi.", 28) == "Ngắn gọn thôi."


def test_hook_window_fit_never_compresses_a_scene_below_its_reading_time():
    """Fitting the 30s boundary must not invent a 357 WPM read."""
    scenes_vi = [
        {"index": i + 1, "text": " ".join(["từ"] * 24), "duration": 8.0}
        for i in range(4)
    ]
    scenes_en = [{"index": i + 1, "text": "short", "duration": 8.0} for i in range(4)]

    acc = sf.ScriptFactory._fit_hook_window(scenes_vi, scenes_en)

    natural = 24 / sf.WORDS_PER_SECOND
    assert all(sc["duration"] >= natural - 1e-3 for sc in scenes_vi)  # durations round to ms
    assert acc <= 32.0


def test_hook_window_pads_a_short_hook_so_the_s16_window_closes_inside_it():
    """Live S16 fail: 28.86s of hook let scene 5 into the window (33.15s)."""
    scenes_vi = [
        {"index": i + 1, "text": " ".join(["từ"] * 24), "duration": 6.858}
        for i in range(4)
    ]
    scenes_en = [{"index": i + 1, "text": "short", "duration": 6.858} for i in range(4)]

    acc = sf.ScriptFactory._fit_hook_window(scenes_vi, scenes_en)

    assert 30.0 <= acc <= 32.0
    assert all(4.0 <= sc["duration"] <= 8.0 for sc in scenes_vi)
    assert all(sc["duration"] >= 24 / sf.WORDS_PER_SECOND - 1e-3 for sc in scenes_vi)
    assert [sc["duration"] for sc in scenes_en] == [sc["duration"] for sc in scenes_vi]


def test_shot_prompts_leave_the_factory_carrying_the_crayon_look(factory, monkeypatch):
    """QA S13 rejects a prompt with no crayon keyword or a photoreal phrase."""
    from services.algorithm_qa import PHOTO_KEYWORDS, STYLE_KEYWORDS

    router = Router(
        {"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]}, plain_visual_for={2}
    )
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, _ = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3, project_dir=None
    )

    for scene in (*vi, *en):
        prompt = scene["prompt"].lower()
        assert any(k in prompt for k in STYLE_KEYWORDS), scene["prompt"]
        assert not any(k in prompt for k in PHOTO_KEYWORDS), scene["prompt"]


def test_reading_rate_fit_pads_room_left_by_a_capped_scene():
    """A 30-word scene capped at 8s alone breaks the 210 WPM ceiling."""
    vi = [
        {"index": 1, "text": " ".join(["từ"] * 30), "duration": 8.0},
        {"index": 2, "text": " ".join(["từ"] * 20), "duration": 5.714},
    ]
    en = [{"index": 1, "text": "short", "duration": 8.0},
          {"index": 2, "text": "short", "duration": 5.714}]

    added = sf.ScriptFactory._fit_reading_rate(vi, en)

    dur = sum(sc["duration"] for sc in vi)
    assert added > 0
    assert (50 / dur * 60) <= 210.0
    assert all(4.0 <= sc["duration"] <= 8.0 for sc in vi)
    assert en[1]["duration"] == vi[1]["duration"]


def test_rewrite_prompt_budgets_each_language_for_its_own_voice(factory, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)

    assert router.rewrite_prompts
    for p in router.rewrite_prompts:
        # EN reads ~160 wpm: full sentences 12-22 words, not the old 16 cap
        assert f"{sf.EN_MIN_WORDS}-{sf.EN_MAX_WORDS} từ mỗi cảnh" in p
        assert "~160 wpm" in p
        assert "NGẮN GỌN" not in p
        # VI: at most one <=6-word punch per scene, story-teller connectors
        assert "TỐI ĐA 1 câu mỗi cảnh" in p
        assert "người kể chuyện" in p


def test_judge_sees_the_machine_rhythm_report(factory, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    factory.run([{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3)

    assert router.judge_prompts
    for p in router.judge_prompts:
        m = re.search(r"NHỊP ĐỌC MÁY: CV độ dài câu ([\d.]+)", p)
        assert m, p[-600:]
        assert 0 <= float(m.group(1)) <= 2.0
        assert "narration_rhythm" in p


def test_naturalness_report_flags_staccato_prose():
    stiff = " ".join(
        "Tiền sinh ra không gốc. Hệ thống ghi sổ. Werner nhìn màn hình. "
        "Số dư nhảy lên. Không ai giải thích. Câu hỏi đặt ra."
        for _ in range(3)
    )
    natural = (
        "Năm 2014, một ngân hàng Đức phê duyệt khoản vay mà không ai từng thấy trước đó, "
        "và rồi 10.000 euro xuất hiện trên màn hình. Nhưng tiền đó đến từ đâu? "
        "Thực ra hệ thống chỉ gõ vài phím, vậy là số dư nhảy lên. "
        "Điều đáng nói là không một tờ tiền vật lý nào được in ra, vì khoản vay này chỉ tồn tại trong sổ sách."
    )
    stiff_rep = ScriptFactory._naturalness_report(stiff)
    nat_rep = ScriptFactory._naturalness_report(natural)

    assert stiff_rep["punch_pct"] > ScriptFactory.NATURAL_MAX_PUNCH_PCT
    assert stiff_rep["cv"] < ScriptFactory.NATURAL_MIN_CV
    assert nat_rep["cv"] >= ScriptFactory.NATURAL_MIN_CV
    assert nat_rep["connectors_per_100"] >= ScriptFactory.NATURAL_MIN_CONNECTORS


# --- outline spine, repetition registry, global revision -------------------


def test_outline_pass_feeds_every_chunk_and_reaches_the_result(factory, monkeypatch):
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    _, _, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3
    )

    assert router.outline_calls == 1
    assert result["outline"]["paradox"]
    assert router.rewrite_prompts
    for prompt in router.rewrite_prompts:
        assert "OUTLINE TOÀN CỤC" in prompt
        assert '"timeline"' in prompt


def test_second_chunk_sees_the_phrases_already_used(factory, monkeypatch):
    slices_vi = [
        {"index": i + 1, "situation": f"s{i}", "text": f"Cảnh {i+1}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for i in range(12)
    ]
    slices_en = [
        {"index": sc["index"], "situation": sc["situation"], "text": f"Scene {sc['index']}.",
         "prompt": "p", "duration": 5.0, "is_key_scene": True}
        for sc in slices_vi
    ]
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=12: (slices_vi, slices_en),
    )
    router = Router({"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]})
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    factory.run([{"start": 0, "end": 100, "text": "x"}], target_shots=12)

    first_chunks = router.rewrite_prompts[0::2]
    second_chunks = router.rewrite_prompts[1::2]
    assert len(first_chunks) == len(second_chunks) == 3
    assert all("CỤM TỪ/ẨN DỤ ĐÃ DÙNG" not in p for p in first_chunks)
    assert all("CỤM TỪ/ẨN DỤ ĐÃ DÙNG" in p for p in second_chunks)


def test_global_revision_repairs_only_flagged_scenes(factory, monkeypatch):
    router = Router(
        {"analyst": [9.2], "dramatist": [7.0], "teacher": [6.0]},
        edit_issues=[{"type": "choppy", "scenes": [2], "fix": "gộp câu cụt"}],
    )
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)

    vi, en, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3
    )

    assert router.edit_calls == 1
    assert router.revision_calls == [[2]]
    assert result["revision"]["accepted"] is True
    assert result["revision"]["revised_scenes"] == 1
    assert [sc["index"] for sc in vi] == [1, 2, 3]
    assert "câu chuyện cảnh 2" in vi[1]["text"]
    assert "scene 2 turned" in en[1]["text"]


def test_overused_phrase_registry_ignores_function_words_and_years():
    texts = [
        "Công ty trở về nhà kho dột và dòng tiền vẫn chảy.",
        "Họ vẫn ở nhà kho dột khi dòng tiền cạn.",
        "Nhà kho dột ấy là nơi dòng tiền bắt đầu.",
        "Năm 1984 nhà kho dột đón dòng tiền mới.",
    ]

    phrases = ScriptFactory._overused_phrases(texts, min_count=3)

    assert "nhà kho dột" in phrases
    assert "dòng tiền" in phrases
    assert all(not phrase.split()[0].isdigit() for phrase in phrases)
