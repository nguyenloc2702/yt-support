"""Phase 5 tests: QA scorecard hard gate wired into Gate 4 approval.

Covers:
- stage="final" excludes video-stage checks until a rendered mp4 exists (n/a rule)
- Scorecard.save/load round-trip
- approve_video_generation blocks remake jobs below QA_GATE_MIN_SCORE (fail-closed)
- approve_video_generation passes a high-quality remake project and locks the manifest
- legacy jobs (no algorithm_result.json) are unaffected by the QA gate
- QA gate crashes are fail-closed (block approval, never silently pass)
"""

import json
import subprocess
from pathlib import Path

import pytest

from config.settings import settings
from services.algorithm_qa import QA_GATE_MIN_SCORE, Scorecard, compute_scorecard
from services.approval_state_machine import (
    ApprovalStateMachine,
    BeatIntent,
    VideoReadinessStatus,
)


def _good_plan(n=8, dur=5.0):
    # 14 words/scene -> wps = 112/40 = 2.8 (inside T07 2.2-3.6)
    text = "Cảnh diễn biến quan trọng số {i} mở ra bất ngờ lớn năm 1602?"
    return [
        {
            "index": i + 1,
            "situation": f"tình huống {i + 1} với biểu cảm ngạc nhiên",
            "text": text.format(i=i + 1),
            "prompt": "Whimsical hand-drawn crayon editorial cartoon, flat 2D, negative space, off-white paper background",
            "duration": dur,
            "is_key_scene": True,
            "character_ids": [],
        }
        for i in range(n)
    ]


def _bad_plan():
    bad = [
        {"index": 1, "situation": "", "text": "", "prompt": "photorealistic 4k photo", "duration": 30.0, "is_key_scene": False},
        {"index": 2, "situation": "", "text": "Ngắn.", "prompt": "", "duration": 1.0, "is_key_scene": False},
    ]
    return bad + _good_plan(6)


def _good_algo():
    return {
        "engine": "script_factory_best_of_n",
        "chosen_angle": "analyst",
        "chosen_score": 8.5,
        "min_score": 8.0,
        "total_rounds": 1,
        "target_duration_sec": 40.0,
        "mechanical_metrics": {"short_sentence_pct": 0.5, "estimated_wpm": 180.0},
    }


def _junk_algo():
    return {
        "engine": "script_factory_best_of_n",
        "chosen_angle": "wild",
        "chosen_score": 5.0,
        "min_score": 8.0,
        "total_rounds": 5,
        "target_duration_sec": 40.0,
    }


def _seed_remake_project(tmp_path: Path, plan, algo):
    (tmp_path / "shots_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_vi.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_en.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "algorithm_result.json").write_text(json.dumps(algo, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "voice.txt").write_text("confident-male-vietnamese", encoding="utf-8")
    (tmp_path / "kich_ban_VI_job1.txt").write_text("KỊCH BẢN", encoding="utf-8")


def _make_ready_asm(p_dir: Path, job_id: str) -> ApprovalStateMachine:
    """Drive the 4-gate state machine to Gate-4-ready (script/audio approved, no characters)."""
    assets = p_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    audio_vi = assets / "narration_vi.wav"
    audio_vi.write_bytes(b"RIFF" + b"\x00" * 200)
    audio_en = assets / "narration_en.wav"
    audio_en.write_bytes(b"RIFF" + b"\x00" * 200)

    asm = ApprovalStateMachine(p_dir, job_id)
    beats = [BeatIntent(id=1, text_vi="Câu một.", text_en="One.", duration=4.75)]
    asm.set_script("Câu một.", "One.", beats=beats, required_characters=[])
    ok, errs = asm.approve_script(approver="tester")
    assert ok, errs
    asm.set_audio_generated("vi", audio_vi, duration=4.75, voice_id="vi-VN-NamMinhNeural")
    asm.set_audio_generated("en", audio_en, duration=4.75, voice_id="en-US-GuyNeural")
    ok, errs = asm.approve_audio("vi", approver="tester")
    assert ok, errs
    ok, errs = asm.approve_audio("en", approver="tester")
    assert ok, errs
    asm.set_shots_plan([{"index": 1, "prompt": "crayon scene", "duration": 4.75, "character_ids": []}])
    ready, errors, _ = asm.check_video_readiness()
    assert ready, errors
    return asm


# ------------------------------------------------------------------ n/a rule

def test_final_stage_excludes_video_checks_without_render(tmp_path):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    card = compute_scorecard(tmp_path, stage="final")
    assert card.results, "scorecard must not be empty"
    assert all(r.stage != "video" for r in card.results)


def test_final_stage_includes_video_checks_with_render(tmp_path):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    (tmp_path / "final_video.mp4").write_bytes(b"\x00" * 2048)
    card = compute_scorecard(tmp_path, stage="final")
    assert any(r.stage == "video" for r in card.results)


def test_scorecard_load_roundtrip(tmp_path):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    card = compute_scorecard(tmp_path, stage="script")
    card.save(tmp_path)
    loaded = Scorecard.load(tmp_path)
    assert loaded is not None
    assert loaded.stage == card.stage
    assert loaded.score == pytest.approx(card.score, abs=0.01)
    assert [r.id for r in loaded.results] == [r.id for r in card.results]
    assert loaded.passed_count == card.passed_count


def test_scorecard_load_missing_file_returns_none(tmp_path):
    assert Scorecard.load(tmp_path) is None


# ------------------------------------------------------------------ hard gate

def test_hard_gate_blocks_low_quality_remake(tmp_path):
    _seed_remake_project(tmp_path, _bad_plan(), _junk_algo())
    asm = _make_ready_asm(tmp_path, "job_remake_bad")
    ok, errs = asm.approve_video_generation(approver="tester")
    assert ok is False
    assert any("QA scorecard" in e for e in errs), errs
    # Evidence persisted + fail-closed state
    assert (tmp_path / "qa_scorecard.json").is_file()
    assert asm.video_status != VideoReadinessStatus.APPROVED
    assert not (tmp_path / "video_generation_manifest.json").exists()


def test_hard_gate_passes_high_quality_remake(tmp_path):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    subprocess.run(
        [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
         "-b:a", "64k", str(tmp_path / "audio_vi_full.mp3")],
        check=True, capture_output=True, timeout=60,
    )
    subprocess.run(
        [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=40",
         "-b:a", "64k", str(tmp_path / "audio_en_full.mp3")],
        check=True, capture_output=True, timeout=60,
    )
    asm = _make_ready_asm(tmp_path, "job_remake_good")
    ok, errs = asm.approve_video_generation(approver="tester")
    assert ok is True, errs
    assert asm.video_status == VideoReadinessStatus.APPROVED
    assert asm.active_manifest is not None and asm.active_manifest.locked is True

    saved = json.loads((tmp_path / "qa_scorecard.json").read_text(encoding="utf-8"))
    assert saved["score"] >= QA_GATE_MIN_SCORE
    assert saved["stage"] == "final"


def test_hard_gate_skipped_for_legacy_jobs(tmp_path):
    # No algorithm_result.json -> reup/mode1 flow must still approve even with junk on disk
    _seed_remake_project(tmp_path, _bad_plan(), _junk_algo())
    (tmp_path / "algorithm_result.json").unlink()
    asm = _make_ready_asm(tmp_path, "job_reup_legacy")
    ok, errs = asm.approve_video_generation(approver="tester")
    assert ok is True, errs
    assert not (tmp_path / "qa_scorecard.json").exists()


def test_hard_gate_fail_closed_on_crash(tmp_path, monkeypatch):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    asm = _make_ready_asm(tmp_path, "job_remake_crash")

    def _boom(project_dir, stage="final"):
        raise RuntimeError("scorecard exploded")

    monkeypatch.setattr("services.algorithm_qa.compute_scorecard", _boom)
    ok, errs = asm.approve_video_generation(approver="tester")
    assert ok is False
    assert any("fail-closed" in e for e in errs), errs
    assert asm.video_status != VideoReadinessStatus.APPROVED


def test_qa_report_available_after_gate(tmp_path):
    _seed_remake_project(tmp_path, _good_plan(), _good_algo())
    asm = _make_ready_asm(tmp_path, "job_remake_report")
    assert asm.get_qa_report() is None  # nothing computed yet
    ok, errs = asm.approve_video_generation(approver="tester")
    assert ok is True, errs
    report = asm.get_qa_report()
    assert report is not None
    assert "ALGORITHM SCORECARD" in report
