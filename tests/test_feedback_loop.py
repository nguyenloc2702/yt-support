"""Phase 5 tests: retention feedback loop (services.feedback_store) and its
fold into ScriptFactory prompts."""

import json

import pytest

from services import feedback_store as fs
from services.script_factory import ScriptFactory


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
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_store_path", lambda: tmp_path / "retention_feedback.json")
    return tmp_path / "retention_feedback.json"


class CaptureRouter:
    """Fake chat_json: captures rewrite system prompts and judge user prompts."""

    def __init__(self, score=9.0):
        self.score = score
        self.rewrite_system = []
        self.judge_user = []

    def __call__(self, system_prompt, user_prompt, **kw):
        if "Chấm kịch bản" in system_prompt:
            self.judge_user.append(user_prompt)
            return {"score": self.score, "breakdown": {"hook": 2.0}, "feedback": "ok"}
        self.rewrite_system.append(system_prompt)
        import re
        m = re.search(r"trên tổng \d+\):\n(\[.*?\])\n\n", user_prompt, re.DOTALL)
        n = len(json.loads(m.group(1))) if m else 0
        return {
            "scenes": [
                {
                    "index": i + 1,
                    "situation": f"tình huống {i+1}",
                    "voiceover_vi": f"Cảnh cải tiến {i+1} mở ra bất ngờ.",
                    "voiceover_en": f"Improved scene {i+1} opens a surprise.",
                    "estimated_duration": 5.0,
                    "visual_prompt": "Whimsical hand-drawn crayon editorial cartoon",
                }
                for i in range(max(0, n))
            ]
        }


# ---------------------------------------------------------------- store

def test_add_and_load_roundtrip(store):
    fs.add_feedback("job1", 8.5, note="tốt", angle="analyst", project_dir="p1")
    fs.add_feedback("job2", 3.0, note="hook yếu", angle="dramatist")
    records = fs.load_feedback()
    assert [r["job_id"] for r in records] == ["job1", "job2"]
    assert records[1]["rating"] == 3.0
    assert records[1]["note"] == "hook yếu"
    assert records[0]["angle"] == "analyst"


def test_rating_must_be_within_0_10(store):
    with pytest.raises(ValueError):
        fs.add_feedback("job1", 10.5)
    with pytest.raises(ValueError):
        fs.add_feedback("job1", -0.1)
    assert not store.exists()


def test_corrupt_store_returns_empty(store):
    store.write_text("not json{", encoding="utf-8")
    assert fs.load_feedback() == []
    assert fs.build_feedback_brief() == ""


# ---------------------------------------------------------------- brief

def test_brief_empty_when_no_feedback(store):
    assert fs.build_feedback_brief() == ""


def test_brief_contains_lessons(store):
    fs.add_feedback("job_best", 9.0, note="số liệu mở đầu rất hút", angle="analyst")
    fs.add_feedback("job_worst", 3.0, note="giữa video loãng, mất nhịp", angle="teacher")
    brief = fs.build_feedback_brief()
    assert "RETENTION FEEDBACK" in brief
    assert "2 rated video" in brief
    assert "6.0/10" in brief  # average
    assert "analyst" in brief and "9.0/10" in brief
    assert "teacher" in brief and "3.0/10" in brief
    assert "số liệu mở đầu rất hút" in brief
    assert "giữa video loãng, mất nhịp" in brief


# ---------------------------------------------------------------- fold into factory

def _run_factory(monkeypatch, brief):
    monkeypatch.setattr(
        "services.script_auditor.build_semantic_dual_scripts_from_transcript",
        lambda segs, target_shots=6: (BASE_SLICES_VI, BASE_SLICES_EN),
    )
    router = CaptureRouter(score=9.0)
    monkeypatch.setattr("pipeline.llm_client.chat_json", router)
    monkeypatch.setattr("services.feedback_store.build_feedback_brief", lambda limit=50: brief)
    factory = ScriptFactory()
    _, _, result = factory.run(
        [{"start": 0, "end": 20, "text": TRANSCRIPT}], target_shots=3, project_dir=None
    )
    assert result["chosen_score"] == 9.0
    return router


def test_factory_folds_feedback_into_prompts(monkeypatch):
    router = _run_factory(monkeypatch, "RETENTION FEEDBACK TESTMARKER: avoid dry openings")
    assert router.rewrite_system, "must capture rewrite calls"
    assert any("TESTMARKER" in s for s in router.rewrite_system)
    assert router.judge_user, "must capture judge calls"
    assert any("TESTMARKER" in u for u in router.judge_user)


def test_factory_without_feedback_keeps_prompts_clean(monkeypatch):
    router = _run_factory(monkeypatch, "")
    assert router.rewrite_system
    assert all("RETENTION FEEDBACK" not in s for s in router.rewrite_system)
    assert all("RETENTION FEEDBACK" not in u for u in router.judge_user)
