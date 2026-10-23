"""Unit tests for AutomatedFactory Mode 1 and Mode 2 pipelines."""

import json
from pathlib import Path
import pytest

from pipeline.automated_factory import AutomatedFactory
from services.job_queue import JobQueueManager


@pytest.fixture
def test_factory(tmp_path: Path):
    db_file = tmp_path / "factory_test.sqlite3"
    out_dir = tmp_path / "factory_runs"
    return AutomatedFactory(output_dir=out_dir, db_path=db_file)


@pytest.fixture
def mock_xkiro_stubs(monkeypatch):
    """Stub xKiro gateway calls (script factory + character detection) for pipeline tests."""
    monkeypatch.setattr(
        "pipeline.character_manager.detect_characters_in_text_llm",
        lambda text: [],
    )

    def fake_slice(self, transcript_segments, target_shots):
        n = max(1, int(target_shots))
        slices = [
            {
                "index": i + 1,
                "situation": f"tình huống {i + 1}",
                "text": f"Cảnh gốc số {i + 1} diễn biến quan trọng.",
                "prompt": "Whimsical hand-drawn crayon cartoon, base slice",
                "duration": 5.0,
                "is_key_scene": True,
            }
            for i in range(n)
        ]
        import json as _json
        en = _json.loads(_json.dumps(slices))
        for sc in en:
            sc["text"] = f"Base scene number {sc['index']} with dramatic developments."
        return slices, en

    monkeypatch.setattr("services.script_factory.ScriptFactory._slice", fake_slice)

    def fake_chat_json(system_prompt, user_prompt, **kw):
        import json as _json
        import re as _re
        if "Chấm kịch bản" in system_prompt:
            return {
                "score": 9.0,
                "breakdown": {"hook": 2.0, "open_loops": 2.0},
                "feedback": "Ổn, giữ nguyên nhịp.",
            }
        import re as _re
        m = _re.search(r"trên tổng \d+\):\n(\[.*?\])\n\n", user_prompt, _re.DOTALL)
        n = len(_json.loads(m.group(1))) if m else 0
        return {
            "scenes": [
                {
                    "index": i + 1,
                    "situation": f"tình huống cải tiến {i + 1}",
                    "voiceover_vi": (
                        f"Cảnh cải tiến số {i + 1} mở ra diễn biến bất ngờ về dòng tiền "
                        "và điều đó thay đổi tất cả."
                    ),
                    "voiceover_en": f"Improved scene {i + 1} opens with a surprise turn.",
                    "estimated_duration": 5.0,
                    "visual_prompt": "Whimsical hand-drawn crayon editorial cartoon, flat 2D",
                }
                for i in range(n)
            ]
        }

    monkeypatch.setattr("pipeline.llm_client.chat_json", fake_chat_json)


def test_factory_job_creation(test_factory: AutomatedFactory):
    jq = test_factory.jq
    job_id = jq.create_job(mode="reup", url="https://youtube.com/watch?v=demo123", target_duration=120.0)
    assert job_id.startswith("reup_")

    job = jq.get_job(job_id)
    assert job["mode"] == "reup"
    assert job["target_duration"] == 120.0


def test_factory_mode2_dynamic_shots(test_factory: AutomatedFactory, mock_xkiro_stubs, monkeypatch):
    # Mock download_video and try_extract_subtitles
    monkeypatch.setattr("pipeline.automated_factory.download_video", lambda url, p, **kw: p / "download.mp4")
    
    synthetic_subs = [
        {"start": i * 15.0, "end": (i + 1) * 15.0, "text": f"Đoạn diễn biến lịch sử quan trọng {i+1} đột ngột xảy ra"}
        for i in range(12)
    ]
    monkeypatch.setattr("pipeline.automated_factory.try_extract_subtitles", lambda p: synthetic_subs)

    job_id = test_factory.run_ai_genesis("https://youtube.com/watch?v=documentary_test")
    job = test_factory.jq.get_job(job_id)
    assert job["status"] == "WAITING_APPROVAL"

    summary = test_factory.jq.get_job_summary(job_id)
    # Total shots should be dynamically calculated between 6 and 24, not hardcoded to 10
    assert summary["total"] >= 6
    assert summary["pending"] == summary["total"]

    # Verify shots plan was saved
    proj_dir = test_factory.output_dir / job_id
    shots_file = proj_dir / "shots_plan.json"
    assert shots_file.exists()
    shots_data = json.loads(shots_file.read_text(encoding="utf-8"))
    assert len(shots_data) == summary["total"]
    # Check that dramatic keyword correctly triggers is_key_scene
    assert any(s["is_key_scene"] for s in shots_data)

    # Verify human-readable .txt script file was generated for final inspection ("tra phát cuối")
    txt_script = proj_dir / f"kich_ban_VI_{job_id}.txt"
    assert txt_script.exists()
    assert "KỊCH BẢN PHÂN CẢNH VOICE-OVER TIẾNG VIỆT" in txt_script.read_text(encoding="utf-8")


def test_factory_mode2_custom_duration_and_telegram_dispatch(test_factory: AutomatedFactory, mock_xkiro_stubs, monkeypatch):
    from unittest.mock import MagicMock
    mock_notifier = MagicMock()
    mock_notifier.is_configured = True
    test_factory.notifier = mock_notifier

    monkeypatch.setattr("pipeline.automated_factory.download_video", lambda url, p, **kw: p / "download.mp4")
    synthetic_subs = [
        {"start": i * 10.0, "end": (i + 1) * 10.0, "text": f"Diễn biến tập {i+1} câu chuyện thế giới"}
        for i in range(30)  # 300 seconds total
    ]
    monkeypatch.setattr("pipeline.automated_factory.try_extract_subtitles", lambda p: synthetic_subs)

    # User specifies custom target_duration = 300.0s (5 minutes)
    job_id = test_factory.run_ai_genesis("https://youtube.com/watch?v=doc_5min", target_duration=300.0)
    summary = test_factory.jq.get_job_summary(job_id)
    # 300s / 6s mean planned scene = 50 shots
    assert summary["total"] == 50

    # Ensure document was sent to Telegram for user final review
    mock_notifier.send_document.assert_called()
    doc_paths = [call[0][0] for call in mock_notifier.send_document.call_args_list]
    assert any(f"kich_ban_VI_{job_id}.txt" in p for p in doc_paths)

    # Ensure approval message with rich preview and Hook is sent
    mock_notifier.send_message.assert_called()
    sent_msgs = [call[0][0] for call in mock_notifier.send_message.call_args_list]
    approval_msg = next(m for m in sent_msgs if "KỊCH BẢN ĐÃ XONG" in m)
    assert "TRÍCH ĐOẠN MỞ ĐẦU (HOOK)" in approval_msg
    assert "50 cảnh" in approval_msg



def test_gate2_sends_audio_previews_to_telegram(tmp_path, monkeypatch):
    from types import SimpleNamespace

    sent = {"audio": [], "messages": []}

    class FakeNotifier:
        is_configured = True

        def send_message(self, text, **kw):
            sent["messages"].append(text)
            return True

        def send_audio(self, path, caption="", title="", performer=""):
            sent["audio"].append(Path(path).name)
            return True

        def send_document(self, *a, **kw):
            return True

    class FakeASM:
        def __init__(self, project_dir, job_id):
            self.script_vi_text = "Xin chào, đây là bản tin thử nghiệm."
            self.script_en_text = "Hello, this is a test bulletin."
            self.characters = {}

        def load(self):
            pass

        def is_script_gate_passed(self):
            return True

        def set_audio_generated(self, **kw):
            pass

    def fake_tts(text, voice, out_path):
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"ID3" + b"\0" * 2000)
        return voice

    monkeypatch.setattr(
        "services.approval_state_machine.ApprovalStateMachine", FakeASM
    )
    monkeypatch.setattr("pipeline.tts.synthesize_one_xkiro_with_fallback", fake_tts)
    monkeypatch.setattr("pipeline.tts._probe_duration", lambda p: 12.5)

    factory = AutomatedFactory(
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "jobs.sqlite3",
        account_pool=SimpleNamespace(get_available_account=lambda reserve=False: None),
    )
    factory.notifier = FakeNotifier()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    factory.generate_gate2_and_gate3_artifacts("job1", project_dir)

    assert set(sent["audio"]) == {"audio_vi_full.mp3", "audio_en_full.mp3"}
    assert (project_dir / "audio_vi_full.mp3").is_file()
    assert (project_dir / "audio_en_full.mp3").is_file()


def test_gate2_audio_audit_applies_audio_first_timing(tmp_path, monkeypatch):
    import pipeline.audio_audit as audit_mod
    from pipeline.audio_audit import AudioAuditResult

    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    def _plan(prefix):
        return [
            {"index": 1, "text": f"{prefix} cảnh một", "duration": 5.0},
            {"index": 2, "text": f"{prefix} cảnh hai", "duration": 5.0},
        ]

    for name in ("shots_plan_vi.json", "shots_plan_en.json", "shots_plan.json"):
        (project_dir / name).write_text(json.dumps(_plan("x")), encoding="utf-8")
    (project_dir / "voice.txt").write_text("vi-VN-NamMinhTri", encoding="utf-8")
    (project_dir / "audio_vi_full.mp3").write_bytes(b"ID3")
    (project_dir / "audio_en_full.mp3").write_bytes(b"ID3")

    timing_map = [
        {"index": 1, "start_sec": 0.0, "duration_sec": 6.2, "hold_sec": 6.2},
        {"index": 2, "start_sec": 6.2, "duration_sec": 4.4, "hold_sec": 4.4},
    ]

    def fake_audit(audio_path, plan, language, **kw):
        return AudioAuditResult(
            language=language,
            audio_path=str(audio_path),
            audio_duration_sec=10.6,
            plan_duration_sec=10.0,
            duration_drift=0.06,
            wer=0.0,
            word_accuracy=1.0,
            matched_words=8,
            expected_words=8,
            asr_words=8,
            local_wpm_mean=206.0,
            local_wpm_std=4.0,
            global_wpm=206.0,
            pause_count=2,
            pause_total_sec=0.6,
            pause_ratio=0.06,
            pause_histogram={},
            timing_map=[dict(entry) for entry in timing_map],
        )

    monkeypatch.setattr(audit_mod, "audit_audio", fake_audit)

    class FakeASM:
        def __init__(self):
            self.shots = None

        def set_shots_plan(self, shots):
            self.shots = shots

    factory = AutomatedFactory(
        output_dir=tmp_path / "runs", db_path=tmp_path / "jobs.sqlite3"
    )
    asm = FakeASM()

    reports = factory._audit_gate2_audio("job1", project_dir, asm)

    assert set(reports) == {"vi", "en"}
    for lang in ("vi", "en"):
        assert (project_dir / f"audio_sync_{lang}.json").is_file()
        saved = json.loads(
            (project_dir / f"shots_plan_{lang}.json").read_text(encoding="utf-8")
        )
        assert [sc["duration"] for sc in saved] == [6.2, 4.4]
        assert "AUDIO AUDIT" in reports[lang].summary_vi()

    active = json.loads((project_dir / "shots_plan.json").read_text(encoding="utf-8"))
    assert [sc["duration"] for sc in active] == [6.2, 4.4]
    assert asm.shots is not None
    assert [sc["duration"] for sc in asm.shots] == [6.2, 4.4]


def test_gate2_audio_audit_skips_missing_audio(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "shots_plan_vi.json").write_text("[]", encoding="utf-8")

    class FakeASM:
        def set_shots_plan(self, shots):  # pragma: no cover - must not be called
            raise AssertionError("no audio means no timing to apply")

    factory = AutomatedFactory(
        output_dir=tmp_path / "runs", db_path=tmp_path / "jobs.sqlite3"
    )
    assert factory._audit_gate2_audio("job1", project_dir, FakeASM()) == {}


def test_deduplicate_vtt_segments():
    from pipeline.automated_factory import deduplicate_vtt_segments

    rolling_cues = [
        {"start": 0.0, "end": 2.0, "text": "Xin chào các bạn"},
        {"start": 1.0, "end": 3.0, "text": "Xin chào các bạn đã quay trở lại"},
        {"start": 3.0, "end": 5.0, "text": "với kênh video hôm nay"},
        {"start": 4.0, "end": 6.0, "text": "với kênh video hôm nay chúng ta sẽ tìm hiểu"},
    ]
    deduped = deduplicate_vtt_segments(rolling_cues)
    assert len(deduped) == 2
    assert deduped[0]["text"] == "Xin chào các bạn đã quay trở lại"
    assert deduped[1]["text"] == "với kênh video hôm nay chúng ta sẽ tìm hiểu"


def test_factory_uses_injected_account_pool(tmp_path: Path):
    sentinel = object()
    factory = AutomatedFactory(
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "db.sqlite3",
        account_pool=sentinel,
    )
    assert factory.account_pool is sentinel


def test_factory_creates_account_pool_by_default(test_factory: AutomatedFactory):
    assert hasattr(test_factory.account_pool, "get_available_account")


def test_execute_approved_reup_rejects_non_reup(test_factory: AutomatedFactory):
    # If execute_approved_reup is called on a remake job, it must return False safely without corrupting DB
    job_id = test_factory.jq.create_job(mode="remake", url="https://youtube.com/watch?v=mode2_test")
    test_factory.jq.update_job_status(job_id, "APPROVED")

    success = test_factory.execute_approved_reup(job_id)
    assert success is False
    job = test_factory.jq.get_job(job_id)
    # Status should remain APPROVED for background worker
    assert job["status"] == "APPROVED"

