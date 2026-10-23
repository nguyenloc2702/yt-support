"""Live E2E mini /remake job: ScriptFactory (xKiro best-of-N) -> xKiro TTS ->
QA scorecard -> 4-gate approval with the Phase 5 hard gate.

Run manually:  python -X utf8 scripts/e2e_mini_remake.py
Artifacts land in <data_dir>/e2e_mini/<timestamp>/
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import settings

TRANSCRIPT_SEGMENTS = [
    {"start": 0.0, "end": 9.0, "text": "Năm 1602, Hà Lan thành lập Công ty Đông Ấn, gọi tắt là VOC."},
    {"start": 9.0, "end": 19.0, "text": "Vốn khởi điểm 6,5 triệu guilders, gấp mười lần cả nước Anh lúc bấy giờ."},
    {"start": 19.0, "end": 30.0, "text": "VOC là công ty cổ phần đầu tiên trên thế giới phát hành trái phiếu và cổ phiếu cho công chúng."},
    {"start": 30.0, "end": 41.0, "text": "Người dân Amsterdam xếp hàng mua cổ phần, từ thương nhân giàu có tới thợ rèn nghèo."},
    {"start": 41.0, "end": 52.0, "text": "Đổi lại, họ được hứa hẹn một phần lợi nhuận từ mọi con tàu vượt đại dương."},
    {"start": 52.0, "end": 63.0, "text": "Nhưng biển cả đầy rủi ro: bão tố, cướp biển, và những chuyến đi kéo dài hàng năm trời."},
    {"start": 63.0, "end": 74.0, "text": "Chính rủi ro ấy sinh ra một phát minh mới: thị trường thứ cấp, nơi cổ phiếu được mua đi bán lại."},
    {"start": 74.0, "end": 85.0, "text": "Sàn giao dịch Amsterdam ra đời năm 1602, tổ tiên của Phố Wall ngày nay."},
    {"start": 85.0, "end": 96.0, "text": "VOC tồn tại gần hai thế kỷ, trả cổ tức liên tục 196 năm liền."},
    {"start": 96.0, "end": 108.0, "text": "Bài học còn nguyên giá trị: cách một quốc gia nhỏ biến rủi ro thành dòng tiền vẫn đang vận hành trong kinh tế hiện đại."},
]


def main() -> int:
    run_dir = Path(settings.data_dir) / "e2e_mini" / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[E2E] project dir: {run_dir}")

    # 1. ScriptFactory live: 3 angles + LLM judge
    from services.script_factory import ScriptFactory

    t0 = time.time()
    factory = ScriptFactory()
    vi, en, result = factory.run(TRANSCRIPT_SEGMENTS, target_shots=6, project_dir=run_dir)
    print(
        f"[E2E] ScriptFactory: angle={result['chosen_angle']} "
        f"score={result['chosen_score']} rounds={result['total_rounds']} "
        f"scenes={len(vi)} in {time.time() - t0:.0f}s"
    )
    print(f"[E2E] metrics: {json.dumps(result['mechanical_metrics'], ensure_ascii=False)}")

    # 2. Persist plan artifacts
    (run_dir / "shots_plan.json").write_text(json.dumps(vi, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "shots_plan_vi.json").write_text(json.dumps(vi, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "shots_plan_en.json").write_text(json.dumps(en, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "voice.txt").write_text("confident-male-vietnamese", encoding="utf-8")
    (run_dir / "kich_ban_VI_e2e.txt").write_text(
        "\n\n".join(f"Cảnh {sc['index']} ({sc['duration']:.1f}s): {sc['text']}" for sc in vi),
        encoding="utf-8",
    )

    # 3. Live xKiro TTS for both languages
    from pipeline.tts import _probe_duration, synthesize_one_xkiro_with_fallback

    t0 = time.time()
    synthesize_one_xkiro_with_fallback(
        " ".join(sc["text"] for sc in vi), "confident-male-vietnamese", run_dir / "audio_vi_full.mp3"
    )
    synthesize_one_xkiro_with_fallback(
        " ".join(sc["text"] for sc in en), "male-voice", run_dir / "audio_en_full.mp3"
    )
    dur_vi = _probe_duration(run_dir / "audio_vi_full.mp3")
    dur_en = _probe_duration(run_dir / "audio_en_full.mp3")
    print(f"[E2E] TTS: vi={dur_vi:.1f}s en={dur_en:.1f}s in {time.time() - t0:.0f}s")

    # 4. QA scorecard (hard gate math on real artifacts)
    from services.algorithm_qa import compute_scorecard

    card = compute_scorecard(run_dir, stage="final")
    card.save(run_dir)
    print(card.format_telegram_report())

    # 5. Four-gate approval with the Phase 5 hard gate
    from services.approval_state_machine import ApprovalStateMachine, BeatIntent

    job_id = f"e2e_{run_dir.name}"
    asm = ApprovalStateMachine(run_dir, job_id)
    beats = [
        BeatIntent(id=sc["index"], text_vi=sc["text"], text_en=en[i]["text"], duration=sc["duration"])
        for i, sc in enumerate(vi)
    ]
    asm.set_script(
        "\n".join(sc["text"] for sc in vi),
        "\n".join(sc["text"] for sc in en),
        beats=beats,
        required_characters=[],
    )
    ok, errs = asm.approve_script(approver="e2e")
    assert ok, errs
    asm.set_audio_generated("vi", run_dir / "audio_vi_full.mp3", duration=dur_vi, voice_id="confident-male-vietnamese")
    asm.set_audio_generated("en", run_dir / "audio_en_full.mp3", duration=dur_en, voice_id="male-voice")
    ok, errs = asm.approve_audio("vi", approver="e2e")
    assert ok, errs
    ok, errs = asm.approve_audio("en", approver="e2e")
    assert ok, errs
    asm.set_shots_plan([
        {"index": sc["index"], "prompt": sc["prompt"], "duration": sc["duration"], "character_ids": []}
        for sc in vi
    ])

    ok, errs = asm.approve_video_generation(approver="e2e")
    if ok:
        print(f"[E2E] GATE 4: APPROVED (hard gate passed) manifest={asm.active_manifest.manifest_id}")
        report = asm.get_qa_report()
        if report:
            print("[E2E] scorecard report attached on Telegram in production")
    else:
        print("[E2E] GATE 4: BLOCKED")
        for e in errs:
            print(f"   - {e}")
        return 1
    print(f"[E2E] DONE — artifacts in {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
