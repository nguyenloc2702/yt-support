"""Unit tests for the Phase 2 best-of-N ScriptFactory (services.script_factory)."""

import json

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

    def __init__(self, score_plan, rewrite_error=None, wrong_count_angles=()):
        # score_plan: {angle_id: [round1_score, round2_score, ...]}
        self.score_plan = score_plan
        self.rewrite_error = rewrite_error
        self.wrong_count_angles = set(wrong_count_angles)
        self.rewrite_prompts = []
        self.judge_calls = 0
        self.rewrite_calls = 0

    def __call__(self, system_prompt, user_prompt, **kw):
        if "Chấm kịch bản" in system_prompt:
            round_idx = self.judge_calls // len(self.score_plan)
            self.judge_calls += 1
            angle = next(
                (a for a in self.score_plan if f"[{a}]" in user_prompt), None
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
        count = n - 1 if angle in self.wrong_count_angles else n
        return {
            "scenes": [
                {
                    "index": i + 1,
                    "situation": f"tình huống {angle} {i+1}",
                    "voiceover_vi": f"[{angle}] Cảnh cải tiến {i+1} mở ra bất ngờ.",
                    "voiceover_en": f"[{angle}] Improved scene {i+1} opens a surprise.",
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
    assert vi[0]["text"].startswith("[analyst]")
    assert all(4.0 <= sc["duration"] <= 8.0 for sc in vi)

    saved = json.loads((tmp_path / "algorithm_result.json").read_text(encoding="utf-8"))
    assert saved["chosen_angle"] == "analyst"
    assert saved["rounds"][0]["scores"][0]["angle"] == "analyst"
    assert "mechanical_metrics" in saved


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
    # Word-derived durations: 6 words / 3 wps = 2.0s -> clamped to MIN 4.0s
    assert m["total_duration_sec"] == 12.0
    assert 0.0 <= m["short_sentence_pct"] <= 1.0
    assert m["estimated_wpm"] > 0
    assert "wpm_in_range" in m


def test_variants_rewrite_chunked_for_long_scripts(factory, monkeypatch):
    """60 scenes -> 3 rewrite calls per variant (VARIANT_CHUNK=20)."""
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
