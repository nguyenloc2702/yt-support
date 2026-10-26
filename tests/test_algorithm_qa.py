"""Unit tests for the Phase 4 QA scorecard (services.algorithm_qa)."""

import json
from pathlib import Path

import pytest

from services.algorithm_qa import ALL_CHECKS, compute_scorecard, count_criteria


# A "healthy script" fixture must actually read as different sentences. All
# eight scenes used to share one template line, which QA S18 (the phrase
# repetition law) correctly flags — the fixture, not the product, was repetitive.
_HEALTHY_LINES = [
    "Năm 1602, công ty Đông Ấn vay vốn tư nhân? Nghịch lý chưa từng có tiền lệ.",
    "Nhà kho cũ ở Delft là nơi một thợ kính mơ về chiếc ống nhìn xa.",
    "Doanh số bát đĩa tăng gấp đôi. Cổ phiếu thì nằm im.",
    "Hải quân tiêu ngân khố cho ba con tàu. Chiếc thứ tư không rời bến.",
    "Vậy tại sao người Amsterdam mua bán giấy nợ trên cầu? Ngay trên cầu.",
    "Nợ công chạm tám phần trăm sau trận thủy chiến. Quốc hội đánh thuế.",
    "Nhưng đó chưa hết. Xưởng dệt thuê trẻ nhỏ làm mười hai giờ mỗi ngày.",
    "Câu trả lời nằm trong sổ sách lãi kép, quay lại đúng nghịch lý ban đầu.",
]


def _base_plan(n=8, dur=5.0, text=None):
    return [
        {
            "index": i + 1,
            "situation": f"tình huống {i + 1} với biểu cảm ngạc nhiên",
            "text": (
                text.format(i=i + 1) if text
                else _HEALTHY_LINES[i % len(_HEALTHY_LINES)]
            ),
            "prompt": "Whimsical hand-drawn crayon editorial cartoon, flat 2D, negative space, off-white paper background",
            "duration": dur,
            "is_key_scene": True,
            "character_ids": [],
        }
        for i in range(n)
    ]


def _base_algo():
    return {
        "engine": "script_factory_best_of_n",
        "chosen_angle": "analyst",
        "chosen_score": 8.5,
        "min_score": 8.0,
        "total_rounds": 1,
        "target_duration_sec": 40.0,
        "mechanical_metrics": {"short_sentence_pct": 0.5, "estimated_wpm": 180.0},
    }


def _seed_project(tmp_path: Path, plan=None, algo=None, characters=None):
    plan = plan if plan is not None else _base_plan()
    algo = algo if algo is not None else _base_algo()
    (tmp_path / "shots_plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_vi.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_en.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "algorithm_result.json").write_text(json.dumps(algo, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "voice.txt").write_text("confident-male-vietnamese", encoding="utf-8")
    (tmp_path / "kich_ban_VI_job1.txt").write_text("KỊCH BẢN", encoding="utf-8")
    for cid in characters or []:
        cdir = tmp_path / "characters" / cid
        cdir.mkdir(parents=True, exist_ok=True)
    return plan


def test_exactly_63_criteria():
    assert count_criteria() == 63
    ids = [fn._qa_id for fn in ALL_CHECKS]
    assert len(ids) == len(set(ids)), "duplicate check ids"


def test_stage_ordering_and_unknown_stage(tmp_path):
    _seed_project(tmp_path)
    with pytest.raises(ValueError):
        compute_scorecard(tmp_path, stage="bogus")


def test_script_stage_scorecard_full_pass(tmp_path):
    plan = _seed_project(tmp_path)
    card = compute_scorecard(tmp_path, stage="script")
    assert card.stage == "script"
    # Only script-stage checks are counted
    assert all(r.stage == "script" for r in card.results)
    assert len(card.results) == 18
    assert card.score >= 9.0, card.format_telegram_report()


def test_bad_script_fails_checks(tmp_path):
    bad_plan = [
        {"index": 1, "situation": "", "text": "", "prompt": "photorealistic 4k photo", "duration": 30.0, "is_key_scene": False},
        {"index": 2, "situation": "", "text": "Ngắn.", "prompt": "", "duration": 1.0, "is_key_scene": False},
    ] + _base_plan(6)
    _seed_project(tmp_path, plan=bad_plan)
    card = compute_scorecard(tmp_path, stage="script")
    failed_ids = {r.id for r in card.failed()}
    assert {"S03", "S05", "S07", "S08", "S09", "S10", "S12", "S13", "S15"} & failed_ids
    assert "S01" not in failed_ids
    assert card.score < 9.0


def test_assets_stage_with_characters(tmp_path):
    _seed_project(tmp_path, characters=["char_a"])
    # Missing style report + reference + assignments -> asset checks fail, not crash
    card = compute_scorecard(tmp_path, stage="assets")
    failed_ids = {r.id for r in card.failed()}
    assert {"A02", "A03", "A04", "A05", "A12"} & failed_ids


def test_assets_stage_style_scores_below_bar(tmp_path):
    _seed_project(tmp_path, characters=["char_a"])
    cdir = tmp_path / "characters" / "char_a"
    (cdir / "reference.png").write_bytes(b"x" * 20_000)
    (cdir / "style_check.json").write_text(
        json.dumps({"ok": False, "score": 5.0, "issues": ["3D"], "summary": ""}), encoding="utf-8"
    )
    card = compute_scorecard(tmp_path, stage="assets")
    failed_ids = {r.id for r in card.failed()}
    assert "A03" in failed_ids  # style score 5.0 < 7.0
    assert "A02" not in failed_ids and "A04" not in failed_ids


def test_audio_stage_with_real_files(tmp_path):
    """Real tiny audio file via ffmpeg lavfi so ffprobe/lufs paths are exercised."""
    import subprocess

    from config.settings import settings

    plan = _seed_project(tmp_path)
    audio = tmp_path / "audio_vi_full.mp3"
    subprocess.run(
        [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=40",
         "-b:a", "64k", str(audio)],
        check=True, capture_output=True, timeout=60,
    )
    card = compute_scorecard(tmp_path, stage="audio")
    failed_ids = {r.id for r in card.failed()}
    assert "T01" not in failed_ids
    assert "T03" not in failed_ids
    # 40s audio vs 40s plan (8 x 5.0) -> T05 pass; sine has no speech -> T07 likely fails on wps
    t05 = next(r for r in card.results if r.id == "T05")
    assert t05.passed, t05.value


def test_audio_stage_skips_gracefully_without_files(tmp_path):
    _seed_project(tmp_path)
    card = compute_scorecard(tmp_path, stage="audio")
    t01 = next(r for r in card.results if r.id == "T01")
    assert not t01.passed  # audio file missing -> check fails at audio stage
    t07 = next(r for r in card.results if r.id == "T07")
    assert t07.passed and t07.value is None  # n/a when no audio file
    t10 = next(r for r in card.results if r.id == "T10")
    t11 = next(r for r in card.results if r.id == "T11")
    assert t10.passed and t10.value is None and "n/a" in t10.target
    assert t11.passed and t11.value is None and "n/a" in t11.target


def _audio_sync_payload(wer=0.04, missing_scenes=0, atempo=0.92, capped=False,
                        wpm=205.0, wpm_in_band=True, global_wpm=None):
    scenes = [
        {"index": i + 1, "missing": i < missing_scenes}
        for i in range(10)
    ]
    return {
        "language": "vi",
        "wer": wer,
        "asr_words": 180,
        "local_wpm_mean": wpm,
        "global_wpm": wpm if global_wpm is None else global_wpm,
        "atempo": atempo,
        "naturalness": {
            "atempo_ok": atempo is None or atempo >= 0.75,
            "wpm_in_band": wpm_in_band,
            "slowdown_capped": capped,
        },
        "scenes": scenes,
    }


def test_audio_sync_checks_pass_on_clean_audit(tmp_path):
    _seed_project(tmp_path)
    (tmp_path / "audio_sync_vi.json").write_text(
        json.dumps(_audio_sync_payload()), encoding="utf-8"
    )
    card = compute_scorecard(tmp_path, stage="audio")
    t10 = next(r for r in card.results if r.id == "T10")
    t11 = next(r for r in card.results if r.id == "T11")
    assert t10.passed, t10.value
    assert t11.passed, t11.value


def test_audio_natural_grades_measured_delivery_rate_not_the_stale_flag(tmp_path):
    """Live: fitted 206.1 WPM delivery, stale wpm_in_band=False from articulation 222."""
    _seed_project(tmp_path)
    (tmp_path / "audio_sync_vi.json").write_text(
        json.dumps(_audio_sync_payload(wpm=221.7, wpm_in_band=False, global_wpm=206.1)),
        encoding="utf-8",
    )
    card = compute_scorecard(tmp_path, stage="audio")
    t11 = next(r for r in card.results if r.id == "T11")

    assert t11.passed, t11.value
    assert t11.value["wpm"] == 206.1


def test_audio_sync_checks_flag_bad_audio(tmp_path):
    _seed_project(tmp_path)
    (tmp_path / "audio_sync_vi.json").write_text(
        json.dumps(_audio_sync_payload(
            wer=0.42, missing_scenes=2, atempo=0.70, capped=True, wpm=300.0,
            wpm_in_band=False,
        )),
        encoding="utf-8",
    )
    card = compute_scorecard(tmp_path, stage="audio")
    t10 = next(r for r in card.results if r.id == "T10")
    t11 = next(r for r in card.results if r.id == "T11")
    assert not t10.passed
    assert t10.value["wer"] == 0.42
    assert t10.value["coverage"] == 0.8  # 8/10 scenes matched, below the 90% floor
    assert not t11.passed
    assert t11.value["capped"] is True


def test_video_stage_with_real_render(tmp_path):
    import subprocess

    from config.settings import settings

    plan = _seed_project(tmp_path)
    shots = tmp_path / "shots"
    shots.mkdir()
    for i in range(len(plan)):
        subprocess.run(
            [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=5",
             str(shots / f"shot_{i:03d}.mp4")],
            check=True, capture_output=True, timeout=60,
        )
    (tmp_path / "bgm_main.mp3").write_bytes(b"fake")
    final = tmp_path / "final_video.mp4"
    subprocess.run(
        [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=40",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=40",
         "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "1200k",
         "-c:a", "aac", "-b:a", "128k", "-shortest", str(final)],
        check=True, capture_output=True, timeout=120,
    )
    card = compute_scorecard(tmp_path, stage="video")
    failed_ids = {r.id for r in card.failed()}
    assert "V01" not in failed_ids
    assert "V02" not in failed_ids
    assert "V03" not in failed_ids
    assert "V04" not in failed_ids
    assert "V05" not in failed_ids
    assert "V07" not in failed_ids
    assert "V10" not in failed_ids
    assert "V11" not in failed_ids
    assert "V12" not in failed_ids
    assert "V13" not in failed_ids


def test_process_stage_zero_fake_and_rounds(tmp_path):
    canned = _base_plan(8, text="Giai đoạn lịch sử thứ {i}, mở ra những bước chuyển mình quan trọng.")
    _seed_project(tmp_path, plan=canned)
    card = compute_scorecard(tmp_path, stage="final")
    failed_ids = {r.id for r in card.failed()}
    assert "P01" in failed_ids  # canned translation marker detected
    assert "P06" not in failed_ids  # _base_algo has a valid angle

    algo = _base_algo()
    algo["chosen_angle"] = "wild"
    algo["total_rounds"] = 5
    _seed_project(tmp_path, algo=algo)
    card2 = compute_scorecard(tmp_path, stage="final")
    failed2 = {r.id for r in card2.failed()}
    assert "P02" in failed2
    assert "P06" in failed2


def test_scorecard_save_and_telegram_report(tmp_path):
    _seed_project(tmp_path)
    card = compute_scorecard(tmp_path, stage="script")
    out = card.save(tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["stage"] == "script"
    assert len(data["results"]) == 18
    report = card.format_telegram_report()
    assert "ALGORITHM SCORECARD" in report
    assert "/10" in report


def test_audio_first_real_durations_do_not_break_script_checks(tmp_path):
    """After Gate 2 the plan carries the real narration audio in `duration`;
    S09/S10 grade the planned durations the script was built on (flexibility
    law: audio wins the timeline, the script law stays measurable)."""
    plan = _seed_project(tmp_path)
    audio_corrected = []
    for sc in plan:
        corrected = dict(sc)
        corrected["planned_duration"] = sc["duration"]
        corrected["duration"] = round(sc["duration"] + 3.4, 2)  # real audio past 8s
        audio_corrected.append(corrected)
    (tmp_path / "shots_plan.json").write_text(json.dumps(audio_corrected, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_vi.json").write_text(json.dumps(audio_corrected, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "shots_plan_en.json").write_text(json.dumps(audio_corrected, ensure_ascii=False), encoding="utf-8")

    card = compute_scorecard(tmp_path, stage="script")
    failed = {r.id for r in card.failed()}
    assert "S09" not in failed
    assert "S10" not in failed


def _plan_repeating_phrase(replaced: int) -> list:
    """First `replaced` scenes all lean on the same metaphor."""
    plan = _base_plan(8)
    for i in range(replaced):
        plan[i]["text"] = (
            f"Cảnh {i + 1}: nhà kho dột bên kênh vẫn nuôi tham vọng chiếm trọn đơn hàng."
        )
    return plan


def test_s18_red_line_is_six_scenes_per_phrase(tmp_path):
    """One phrase carried by five scenes is a style choice; six is the law."""
    def verdict(replaced):
        _seed_project(tmp_path, plan=_plan_repeating_phrase(replaced))
        result = next(
            r for r in compute_scorecard(tmp_path, stage="script").results if r.id == "S18"
        )
        return result.passed, result.value

    assert verdict(5) == (True, 5)
    assert verdict(6) == (False, 6)
    assert verdict(0) == (True, 0)
