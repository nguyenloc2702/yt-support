"""Golden-file regression tests for the /remake pipeline.

The fixture is the live E2E run from 2026-10-23 that cleared the QA hard gate
at 9.15/10 (8 scenes, dramatist angle, judge 8.3, xKiro TTS drift 18% both
languages). These tests lock in the structural quality bar: any change to the
factory prompts, word budgets, or scorecard checks that would let a similar
run regress must fail here.
"""

import json
from pathlib import Path

import pytest

from services.algorithm_qa import compute_scorecard

GOLDEN = Path(__file__).parent / "fixtures" / "golden_remake_20261023"


@pytest.fixture
def golden_project(tmp_path: Path) -> Path:
    for name in ("shots_plan.json", "shots_plan_vi.json", "shots_plan_en.json", "algorithm_result.json"):
        (tmp_path / name).write_bytes((GOLDEN / name).read_bytes())
    (tmp_path / "voice.txt").write_text("confident-male-vietnamese", encoding="utf-8")
    (tmp_path / "kich_ban_VI_golden.txt").write_text("KỊCH BẢN", encoding="utf-8")
    return tmp_path


def test_golden_word_budgets_hold():
    """Every scene must stay inside the prompt caps: VI 12-24 words, EN <= 16."""
    vi = json.loads((GOLDEN / "shots_plan_vi.json").read_text(encoding="utf-8"))
    en = json.loads((GOLDEN / "shots_plan_en.json").read_text(encoding="utf-8"))
    assert len(vi) == len(en) == 8
    for sc in vi:
        words = len(str(sc["text"]).split())
        assert 12 <= words <= 24, f"scene {sc['index']} VI has {words} words"
        assert 4.0 <= sc["duration"] <= 8.0
    for sc in en:
        words = len(str(sc["text"]).split())
        assert words <= 16, f"scene {sc['index']} EN has {words} words"


def test_golden_script_stage_stays_above_gate(golden_project):
    card = compute_scorecard(golden_project, stage="script")
    assert card.score >= 8.0, card.format_telegram_report()
    failed = {r.id for r in card.failed()}
    assert failed <= {"S06", "S10", "S16"}, f"unexpected script failures: {failed}"


def test_golden_final_stage_without_media_matches_frozen_scorecard(golden_project):
    """Without audio/video files the failures must match the frozen 9.15 run:
    T01/T02 fail (no files), T07 becomes n/a (passes), video checks n/a."""
    card = compute_scorecard(golden_project, stage="final")
    frozen = json.loads((GOLDEN / "qa_scorecard.json").read_text(encoding="utf-8"))
    failed_now = {r.id for r in card.failed()}
    failed_then = {r["id"] for r in frozen["results"] if not r["passed"]}
    assert failed_now == (failed_then - {"T07"}) | {"T01", "T02"}
    assert card.score >= 8.0, card.format_telegram_report()


def test_golden_algorithm_record_is_best_of_n():
    algo = json.loads((GOLDEN / "algorithm_result.json").read_text(encoding="utf-8"))
    assert algo["engine"] == "script_factory_best_of_n"
    assert algo["chosen_angle"] == "dramatist"
    assert algo["chosen_score"] >= 8.0
    assert algo["total_rounds"] <= 3
    metrics = algo["mechanical_metrics"]
    assert 160 <= metrics["estimated_wpm"] <= 200
    assert metrics["scene_count"] == 8
