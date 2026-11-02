"""Tests for the reference-standard grader (P0).

Covers: grading logic, the black-card detector on a synthetic clip, caption
coverage from a real cue file, drift against plans/visual_standard.md, and the
worker hook never breaking a job.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.smoothness.metrics import detect_black_cards
from core.smoothness.standard import CAP_KEYS, CHECKS, grade, spec_targets, summary
from pipeline.subtitles import caption_coverage, caption_intervals
from tools.standard_report import (
    find_subtitle_file,
    format_table,
    format_telegram_block,
    load_standard_report,
    report_from_measurement,
    write_standard_report,
)


def _measurement(**over) -> dict:
    """A measurement shaped like core.smoothness.metrics.measure_video output."""
    base = {
        "video": {
            "fps": 24.0,
            "duration_s": 272.0,
            "cuts_per_min": 8.37,
            "motion": {
                "shot_dur_s": {"median": 6.88},
                "camera": {"trans_px_s_median": 17.19, "zoom_pct_s_median": 0.01,
                           "share_zoom_gt0_5pct_s": 0.44},
                "local_motion": {"share_static_rawdiff_lt0_5": 0.0,
                                 "share_rigid_residual_lt2": 0.69,
                                 "share_strong_local_residual_ge6": 0.0},
            },
            "black_cards": {"available": True, "count": 0, "total_s": 0.0},
            "captions": {"available": False, "caption_share": 0.0},
        },
        "audio": {"integrated_lufs": -20.2, "lra_lu": 2.3,
                  "side_minus_mid_when_quiet_db": -180.9},
        "source": {"type": "file", "path": "final.mp4"},
    }
    for key, value in over.items():
        section, field = key.split("__", 1)
        base[section][field] = value
    return base


def test_grading_our_current_render_finds_the_known_gaps():
    rows = {r["key"]: r for r in grade(_measurement())}
    assert rows["caption_share"]["status"] == "miss"
    assert rows["black_per_min"]["status"] == "miss"
    assert rows["static_share"]["status"] == "miss"
    assert rows["bed_side_quiet_db"]["status"] == "miss"
    assert rows["cuts_per_min"]["status"] == "warn"
    assert rows["fps"]["status"] == "warn"          # 24 is below, but not far below


def test_grading_a_reference_like_measurement_passes():
    measured = _measurement(
        video__fps=30.0,
        video__duration_s=1000.0,
        video__cuts_per_min=13.0,
        video__black_cards={"available": True, "count": 26, "total_s": 64.5},
        video__captions={"available": True, "caption_share": 0.9},
        video__motion={
            "shot_dur_s": {"median": 4.2},
            "camera": {"trans_px_s_median": 10.1, "zoom_pct_s_median": 1.4,
                       "share_zoom_gt0_5pct_s": 0.5},
            "local_motion": {"share_static_rawdiff_lt0_5": 0.17,
                             "share_rigid_residual_lt2": 0.39,
                             "share_strong_local_residual_ge6": 0.16},
        },
        audio__integrated_lufs=-18.0,
        audio__lra_lu=3.9,
        audio__side_minus_mid_when_quiet_db=-4.8,
    )
    rows = {r["key"]: r for r in grade(measured)}
    assert all(rows[c.key]["status"] == "pass" for c in CHECKS), \
        [ (k, r["status"], r["value"], r["target"]) for k, r in rows.items() if r["status"] != "pass"]


def test_unmeasurable_checks_are_miss_not_crash():
    rows = {r["key"]: r for r in grade({"video": {}, "audio": {}})}
    assert all(r["status"] == "miss" for r in rows.values())
    assert summary(list(rows.values()))["miss"] == len(CHECKS)


def _ffmpeg(*args) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def test_black_card_scan_finds_a_synthetic_card(tmp_path: Path):
    clip = tmp_path / "with_card.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2",
        "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=30:duration=1.5",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2.5",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    )
    found = detect_black_cards(clip)
    assert found["count"] == 1
    assert 1.0 <= found["segments"][0]["duration_s"] <= 1.9
    assert found["segments"][0]["start_s"] == pytest.approx(2.0, abs=0.6)


def test_black_card_scan_is_zero_on_pure_colour(tmp_path: Path):
    clip = tmp_path / "no_card.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
    assert detect_black_cards(clip)["count"] == 0


ASS_TWO_CUES = """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Narr,,0,0,0,,first line
Dialogue: 0,0:00:04.00,0:00:06.00,Narr,,0,0,0,,second line
"""


def test_caption_coverage_is_measured_from_the_cue_file(tmp_path: Path):
    ass = tmp_path / "subtitles_en.ass"
    ass.write_text(ASS_TWO_CUES, encoding="utf-8")
    assert caption_intervals(ass) == [(0.0, 3.0), (4.0, 6.0)]
    cov = caption_coverage(ass, 8.0)
    assert cov["caption_share"] == pytest.approx(0.625)
    assert cov["cues"] == 2


def test_caption_coverage_without_a_cue_file_is_zero(tmp_path: Path):
    assert caption_coverage(tmp_path / "missing.ass", 8.0)["caption_share"] == 0.0
    assert caption_coverage(None, 8.0)["available"] is False


def test_overlapping_cues_are_merged(tmp_path: Path):
    ass = tmp_path / "overlap.ass"
    ass.write_text(ASS_TWO_CUES.replace("0:00:03.00", "0:00:05.00"), encoding="utf-8")
    assert caption_coverage(ass, 8.0)["caption_share"] == pytest.approx(0.75)


def test_captions_are_only_credited_when_the_render_says_it_burned_them(tmp_path: Path):
    """A cue file in the job dir proves nothing: the sidecar is the evidence."""
    video = tmp_path / "final_en.mp4"
    video.write_bytes(b"x")
    (tmp_path / "subtitles_en.ass").write_text(ASS_TWO_CUES, encoding="utf-8")

    assert find_subtitle_file(video) is None            # no sidecar: older render

    marker = tmp_path / "final_en.captions.json"
    marker.write_text(json.dumps({"burned": False, "cue_file": None}), encoding="utf-8")
    assert find_subtitle_file(video) is None            # burn failed, no credit

    marker.write_text(json.dumps({"burned": True, "cue_file": "subtitles_en.ass"}),
                      encoding="utf-8")
    assert find_subtitle_file(video) == tmp_path / "subtitles_en.ass"


def test_machine_targets_match_the_written_spec():
    """plans/visual_standard.md section 6 and CHECKS are one contract, not two."""
    targets = spec_targets()
    assert set(targets) == {c.key for c in CHECKS}
    for c in CHECKS:
        t = targets[c.key]
        if c.better == "min":
            assert t["low"] == c.low and t["high"] is None, (c.key, t, c.low)
        elif c.better == "max":
            assert t["high"] == c.high and t["low"] is None, (c.key, t, c.high)
        else:
            assert (t["low"], t["high"]) == (c.low, c.high), (c.key, t)
        expected = "cap" if c.key in CAP_KEYS else "measured"
        assert t["kind"] == expected, c.key


def test_report_roundtrip_and_blocks(tmp_path: Path):
    result = report_from_measurement(_measurement(), video="final.mp4")
    path = write_standard_report(result, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["summary"]["miss"] >= 4
    assert load_standard_report(tmp_path)["video"] == "final.mp4"
    assert load_standard_report(tmp_path / "nowhere") is None

    table = format_table(result)
    assert "CHUẨN THAM CHIẾU" in table and "Phủ caption" in table
    block = format_telegram_block(result)
    assert "CHUẨN THAM CHIẾU" in block
    assert "Phủ caption" in block            # a miss must be listed
    assert "Độ lớn âm" not in block          # a pass must not be listed


def test_worker_hook_never_breaks_a_job(monkeypatch, tmp_path: Path):
    import core.worker as worker_mod

    def boom(*a, **k):
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr("tools.standard_report.report_from_video", boom)
    worker = worker_mod.VideoAutomationWorker.__new__(worker_mod.VideoAutomationWorker)
    worker._grade_against_standard(tmp_path, tmp_path / "final.mp4")   # must not raise
    assert not (tmp_path / "standard_report.json").exists()


def test_worker_hook_writes_the_report_for_the_job_report(monkeypatch, tmp_path: Path):
    import core.worker as worker_mod

    fake = {"video": "final.mp4", "checks": [{"key": "fps", "status": "miss", "label": "fps",
                                              "value": 24.0, "unit": "fps", "target": ">=30"}],
            "summary": {"pass": 0, "warn": 0, "miss": 1}}
    monkeypatch.setattr("tools.standard_report.report_from_video", lambda *a, **k: fake)
    worker = worker_mod.VideoAutomationWorker.__new__(worker_mod.VideoAutomationWorker)
    worker._grade_against_standard(tmp_path, tmp_path / "final.mp4")
    assert load_standard_report(tmp_path)["video"] == "final.mp4"


def test_job_report_shows_the_standard_block(tmp_path: Path):
    """The owner sees the standard gaps in the same Telegram report as the scorecard."""
    from services.approval_state_machine import ApprovalStateMachine

    write_standard_report(report_from_measurement(_measurement()), tmp_path)
    report = ApprovalStateMachine(tmp_path, "job_1").get_qa_report()
    assert report and "CHUẨN THAM CHIẾU" in report and "Phủ caption" in report


def test_job_report_unchanged_without_a_standard_report(tmp_path: Path):
    from services.approval_state_machine import ApprovalStateMachine

    assert ApprovalStateMachine(tmp_path, "job_1").get_qa_report() is None
