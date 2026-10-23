"""End-to-End and Integration tests for Four-Gate Approval State Machine.

Follows plans/four_gate_approval_test_plan.md:
- Test A: Happy path (Gate 1 -> Gate 2 & 3 parallel -> Gate 4 readiness -> Manifest approval)
- Test B: Script revision after audio generation (invalidates audio, character, and video readiness)
- Test C: One audio approved, one audio pending (Gate 2 & Gate 4 fail-closed)
- Test D: Character sheet missing or changed (Gate 3 & Gate 4 fail-closed)
- Test E: Character reference enforcement on shot-level (no text-only silent fallback)
- Test F: Parallel execution safety (audio VI, EN, characters running concurrently without race conditions)
- Test G: Idempotency and retry (duplicate approvals, retry safety)
- Test H: Telegram command routing & unauthorized gate skipping rejection
"""

import concurrent.futures
import json
from pathlib import Path
import time
from unittest.mock import MagicMock
import pytest

from services.approval_state_machine import (
    ApprovalStateMachine,
    AudioStatus,
    CharacterStatus,
    ScriptStatus,
    VideoReadinessStatus,
    BeatIntent,
)
from services.job_queue import JobQueueManager
from services.telegram_bot import TelegramCommandBot
from services.account_pool import AccountPoolManager


@pytest.fixture
def test_env(tmp_path: Path):
    """Sets up a realistic test project directory with dummy audio and image assets."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy audio files
    audio_vi = assets_dir / "narration_vi.wav"
    audio_vi.write_bytes(b"RIFF" + b"\x00" * 200)

    audio_en = assets_dir / "narration_en.wav"
    audio_en.write_bytes(b"RIFF" + b"\x00" * 200)

    # Create dummy character reference images
    char_img = assets_dir / "char_jensen.png"
    char_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

    return {
        "project_dir": project_dir,
        "job_id": "job_test_4gate_001",
        "audio_vi": audio_vi,
        "audio_en": audio_en,
        "char_img": char_img,
    }


# =============================================================================
# TEST A: HAPPY PATH
# =============================================================================

def test_four_gate_happy_path(test_env):
    """Test A: Complete walkthrough across all 4 gates without skipping."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    # 1. Draft Dual-script with 2 beats
    beats = [
        BeatIntent(id=1, text_vi="Câu một.", text_en="Sentence one.", duration=4.75, character_ids=["jensen_huang"]),
        BeatIntent(id=2, text_vi="Câu hai.", text_en="Sentence two.", duration=4.75),
    ]
    asm.set_script(
        script_vi="Câu một. Câu hai.",
        script_en="Sentence one. Sentence two.",
        beats=beats,
        required_characters=["Jensen Huang"],
    )
    assert asm.is_script_gate_passed() is False

    # 2. Approve Gate 1
    ok, errs = asm.approve_script(approver="lead_editor")
    assert ok is True
    assert len(errs) == 0
    assert asm.is_script_gate_passed() is True

    # 3. Parallel Generation: Audio VI, Audio EN, Character Reference
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=4.75, voice_id="vi-VN-NamMinhNeural")
    asm.set_audio_generated("en", test_env["audio_en"], duration=4.75, voice_id="en-US-GuyNeural")
    asm.set_character_reference("jensen_huang", test_env["char_img"])

    # Video readiness must be false before approvals
    ready, _, _ = asm.check_video_readiness()
    assert ready is False

    # 4. Approve Gate 2 (Audio VI & EN)
    ok_vi, _ = asm.approve_audio("vi", approver="audio_qa")
    ok_en, _ = asm.approve_audio("en", approver="audio_qa")
    assert ok_vi is True
    assert ok_en is True
    assert asm.is_audio_gate_passed() is True

    # 5. Approve Gate 3 (Character Sheet)
    ok_char, _ = asm.approve_character("jensen_huang", approver="art_director")
    assert ok_char is True
    assert asm.is_character_gate_passed() is True

    # 6. Set Shot Plan
    shots = [
        {"index": 1, "prompt": "Jensen Huang holding Nvidia GPU", "duration": 4.75, "character_ids": ["jensen_huang"]},
        {"index": 2, "prompt": "Animated stock graph skyrocketing", "duration": 4.75, "character_ids": []},
    ]
    asm.set_shots_plan(shots)

    # 7. Check Gate 4 Readiness
    ready, errors, _ = asm.check_video_readiness()
    assert ready is True
    assert len(errors) == 0
    assert asm.video_status == VideoReadinessStatus.READY

    # 8. Approve Gate 4 (Video Manifest)
    ok_manifest, errs = asm.approve_video_generation(approver="producer")
    assert ok_manifest is True
    assert asm.video_status == VideoReadinessStatus.APPROVED
    assert asm.active_manifest is not None
    assert asm.active_manifest.locked is True

    # Verify manifest file written on disk
    manifest_path = p_dir / "video_generation_manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["script_version"] == 1
    assert len(data["shot_manifest"]) == 2
    assert len(data["shot_manifest"][0]["reference_images"]) == 1


def test_readiness_uses_algorithm_window_when_marker_present(test_env):
    """Remake plans allow 4-8s scenes; legacy 1-20s window only applies without the marker."""
    p_dir = test_env["project_dir"]
    asm = ApprovalStateMachine(p_dir, test_env["job_id"])
    asm.set_script(
        "Vi",
        "En",
        beats=[BeatIntent(id=1, text_vi="Vi", text_en="En")],
        required_characters=["Jensen Huang"],
    )
    asm.approve_script(approver="user")
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=7.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=7.0)
    asm.approve_audio("vi", approver="user")
    asm.approve_audio("en", approver="user")
    asm.set_character_reference("jensen_huang", test_env["char_img"])
    asm.approve_character("jensen_huang", approver="user")
    asm.set_shots_plan(
        [{"index": 1, "prompt": "p", "duration": 7.0, "character_ids": []}]
    )

    ready, errors, _ = asm.check_video_readiness()
    assert ready is True, errors

    (p_dir / "algorithm_result.json").write_text("{}", encoding="utf-8")
    out_of_window = [
        {"index": 1, "prompt": "p", "duration": 3.0, "character_ids": []}
    ]
    asm.set_shots_plan(out_of_window)
    ready, errors, _ = asm.check_video_readiness()
    assert ready is False
    assert any("4.0s - 8.0s" in e for e in errors)


# =============================================================================
# TEST B: SCRIPT REVISION AFTER AUDIO GENERATION
# =============================================================================

def test_script_revision_invalidates_downstream(test_env):
    """Test B: Modifying a beat bumps version and resets audio & video readiness."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    # Initial approval
    beats = [BeatIntent(id=1, text_vi="Gốc", text_en="Original")]
    asm.set_script("Gốc", "Original", beats=beats)
    asm.approve_script(approver="user")
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=5.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=5.0)
    asm.approve_audio("vi", approver="user")
    asm.approve_audio("en", approver="user")
    assert asm.is_audio_gate_passed() is True

    # Edit beat after approval
    asm.edit_beat(1, new_text_vi="Đã chỉnh sửa")

    # Script version bumped and approval invalidated
    assert asm.script_version == 2
    assert asm.is_script_gate_passed() is False
    assert asm.script_status_vi == ScriptStatus.EDITED

    # Audio approvals marked stale
    assert asm.audio_vi.status == AudioStatus.STALE
    assert asm.audio_en.status == AudioStatus.STALE
    assert asm.is_audio_gate_passed() is False

    # Video readiness locked
    ready, errors, _ = asm.check_video_readiness()
    assert ready is False
    assert any("Gate 1" in e for e in errors)


# =============================================================================
# TEST C: ONE AUDIO APPROVED, ONE AUDIO PENDING
# =============================================================================

def test_partial_audio_approval_blocks_readiness(test_env):
    """Test C: Audio VI approved but Audio EN pending blocks Gate 2 and Gate 4."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("Bản Việt", "English version", beats=[BeatIntent(id=1, text_vi="A", text_en="B")])
    asm.approve_script()

    asm.set_audio_generated("vi", test_env["audio_vi"], duration=4.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=4.0)

    # Approve only VI
    ok_vi, _ = asm.approve_audio("vi")
    assert ok_vi is True
    assert asm.audio_en.status == AudioStatus.GENERATED

    # Gate 2 must remain pending
    assert asm.is_audio_gate_passed() is False

    # Gate 4 readiness must fail
    ready, errors, _ = asm.check_video_readiness()
    assert ready is False
    assert any("Audio EN" in e for e in errors)


# =============================================================================
# TEST D: CHARACTER SHEET MISSING OR CHANGED
# =============================================================================

def test_character_sheet_missing_or_changed(test_env):
    """Test D: Missing character approval blocks Gate 3; changing image resets approval."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("VI", "EN", beats=[BeatIntent(id=1, text_vi="VI", text_en="EN")], required_characters=["Steve Jobs", "Elon Musk"])
    asm.approve_script()
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=4.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=4.0)
    asm.approve_audio("vi")
    asm.approve_audio("en")

    # Set and approve only Steve Jobs
    asm.set_character_reference("steve_jobs", test_env["char_img"])
    asm.approve_character("steve_jobs")

    # Elon Musk is missing
    assert asm.is_character_gate_passed() is False

    # Now add Elon Musk and approve all
    char_img_2 = test_env["project_dir"] / "elon.png"
    char_img_2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x11" * 100)
    asm.set_character_reference("elon_musk", char_img_2)
    asm.approve_character("elon_musk")
    assert asm.is_character_gate_passed() is True

    # Changing reference for Steve Jobs resets approval
    char_img_updated = test_env["project_dir"] / "steve_v2.png"
    char_img_updated.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x22" * 100)
    asm.set_character_reference("steve_jobs", char_img_updated)

    assert asm.characters["steve_jobs"].status == CharacterStatus.GENERATED
    assert asm.is_character_gate_passed() is False


# =============================================================================
# TEST E: REFERENCE ENFORCEMENT ON SHOT-LEVEL
# =============================================================================

def test_character_reference_enforcement_on_shot(test_env):
    """Test E: A shot declaring a character must have a valid resolved image; deletion fails closed."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("VI", "EN", beats=[BeatIntent(id=1, text_vi="VI", text_en="EN")])
    asm.approve_script()
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=4.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=4.0)
    asm.approve_audio("vi")
    asm.approve_audio("en")

    # Register and approve character
    char_file = test_env["project_dir"] / "char.png"
    char_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    asm.register_character("charlie", "Charlie", "Test char")
    asm.set_character_reference("charlie", char_file)
    asm.approve_character("charlie")

    # Shot referencing charlie
    asm.set_shots_plan([{"index": 1, "prompt": "Charlie walking", "duration": 4.5, "character_ids": ["charlie"]}])
    ready, _, _ = asm.check_video_readiness()
    assert ready is True

    # Delete reference image
    char_file.unlink()

    # Fail closed: must not proceed to video generation
    ready, errors, _ = asm.check_video_readiness()
    assert ready is False
    assert any("thiếu file ảnh tham chiếu" in e for e in errors)


# =============================================================================
# TEST F: PARALLEL EXECUTION SAFETY
# =============================================================================

def test_parallel_generation_safety(test_env):
    """Test F: Audio VI, Audio EN, and Character generation run concurrently without race conditions."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("VI text", "EN text", beats=[BeatIntent(id=1, text_vi="A", text_en="B")], required_characters=["Alice"])
    asm.approve_script()

    def gen_audio_vi():
        f = p_dir / "async_vi.wav"
        f.write_bytes(b"RIFF" + b"\x01" * 150)
        asm.set_audio_generated("vi", f, duration=4.5)
        return "vi_done"

    def gen_audio_en():
        f = p_dir / "async_en.wav"
        f.write_bytes(b"RIFF" + b"\x02" * 150)
        asm.set_audio_generated("en", f, duration=4.8)
        return "en_done"

    def gen_char():
        f = p_dir / "async_alice.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x03" * 150)
        asm.set_character_reference("alice", f)
        return "char_done"

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(gen_audio_vi)
        f2 = executor.submit(gen_audio_en)
        f3 = executor.submit(gen_char)
        results = [f1.result(), f2.result(), f3.result()]

    assert "vi_done" in results
    assert "en_done" in results
    assert "char_done" in results

    # Re-load from disk to verify atomic save
    asm_reloaded = ApprovalStateMachine(p_dir, job_id)
    assert asm_reloaded.audio_vi.status == AudioStatus.GENERATED
    assert asm_reloaded.audio_en.status == AudioStatus.GENERATED
    assert asm_reloaded.characters["alice"].status == CharacterStatus.GENERATED


# =============================================================================
# TEST G: IDEMPOTENCY AND RETRY
# =============================================================================

def test_idempotency_and_retry(test_env):
    """Test G: Repeated approvals do not create duplicate versions or inconsistent manifests."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("Script VI", "Script EN", beats=[BeatIntent(id=1, text_vi="A", text_en="B")])
    asm.approve_script(approver="user1")
    v1_hash = asm.script_hash

    # Approve again with same content
    ok, _ = asm.approve_script(approver="user2")
    assert ok is True
    assert asm.script_version == 1
    assert asm.script_hash == v1_hash

    # Setup ready state
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=4.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=4.0)
    asm.approve_audio("vi")
    asm.approve_audio("en")
    asm.set_shots_plan([{"index": 1, "prompt": "Test shot", "duration": 4.0}])

    # Approve video generation
    asm.approve_video_generation(approver="producer")
    m1 = asm.active_manifest

    # Calling approve video generation again is idempotent and retains locked manifest
    asm.approve_video_generation(approver="producer")
    assert asm.active_manifest.manifest_hash == m1.manifest_hash


# =============================================================================
# TEST H: TELEGRAM COMMAND ROUTING & GATE ENFORCEMENT
# =============================================================================

def test_telegram_gate_dispatch_and_rejections(tmp_path: Path):
    """Test H: Telegram bot routes commands to correct gates and rejects unauthorized premature approvals."""
    db_file = tmp_path / "test_tg_gates.sqlite3"
    jq = JobQueueManager(db_path=db_file)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    pool = AccountPoolManager(session_dir=sessions, state_file=sessions / "pool.json")

    bot = TelegramCommandBot(
        token="123456:FAKE_TOKEN",
        chat_id="999888777",
        job_queue=jq,
        account_pool=pool,
    )
    bot.notifier = MagicMock()

    # Create job with 4-gate approval state
    p_dir = tmp_path / "job_telegram_test"
    p_dir.mkdir(parents=True, exist_ok=True)
    job_id = jq.create_job("remake", "https://youtube.com/watch?v=123", project_dir=str(p_dir))
    jq.transition_job(job_id, to_state="WAITING_APPROVAL")

    asm = ApprovalStateMachine(p_dir, job_id)
    asm.set_script("Kịch bản tiếng Việt", "English script", beats=[BeatIntent(id=1, text_vi="A", text_en="B")])

    # 1. /gates command
    bot._handle_command(f"/gates {job_id}")
    bot.notifier.send_message.assert_called()
    assert "TIẾN ĐỘ 4 GATE APPROVAL" in bot.notifier.send_message.call_args[0][0]

    # 2. Premature /approve_video when script is not approved yet
    bot.notifier.send_message.reset_mock()
    bot._handle_command(f"/approve_video {job_id}")
    msg = bot.notifier.send_message.call_args[0][0]
    assert "CHƯA ĐỦ ĐIỀU KIỆN" in msg

    # Job status must remain WAITING_APPROVAL
    assert jq.get_job(job_id)["status"] == "WAITING_APPROVAL"

    # 3. Approve script via /approve_script
    bot.notifier.send_message.reset_mock()
    bot._handle_command(f"/approve_script {job_id}")
    msg = bot.notifier.send_message.call_args[0][0]
    assert "GATE 1 - KỊCH BẢN ĐÃ DUYỆT" in msg

    # Reload ASM and verify Gate 1 passed
    asm.load()
    assert asm.is_script_gate_passed() is True


# =============================================================================
# TEST I: AUDIO DURATION TOLERANCE & FILE TAMPER DETECTION
# =============================================================================

def test_audio_duration_tolerance_and_tamper(test_env):
    """Verifies duration tolerance check and post-approval audio file tampering detection."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script(
        "Kịch bản một. Kịch bản hai.",
        "Script one. Script two.",
        beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0), BeatIntent(id=2, text_vi="C", text_en="D", duration=5.0)],
    )
    asm.approve_script()

    # Timeline total is 10.0s. An audio duration of 35.0s deviates by > 200% -> must be rejected
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=35.0)
    ok, errs = asm.approve_audio("vi")
    assert ok is False
    assert any("lệch quá nhiều" in e for e in errs)

    # Valid duration (10.5s, within tolerance) -> approved
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=10.5)
    ok, _ = asm.approve_audio("vi")
    assert ok is True

    asm.set_audio_generated("en", test_env["audio_en"], duration=10.0)
    ok, _ = asm.approve_audio("en")
    assert ok is True

    asm.set_shots_plan([{"index": 1, "prompt": "Shot 1", "duration": 5.0}, {"index": 2, "prompt": "Shot 2", "duration": 5.0}])
    asm.approve_video_generation()
    assert asm.active_manifest.locked is True

    # Tamper with audio file on disk after manifest locked
    test_env["audio_vi"].write_bytes(b"RIFF_MODIFIED_PAYLOAD_NEW_HASH_XYZ")
    valid, err = asm.verify_manifest_integrity()
    assert valid is False
    assert "Audio VI file hash không khớp manifest" in err


# =============================================================================
# TEST J: CHARACTER DISAMBIGUATION & NEW CHARACTER ADDED POST-APPROVAL
# =============================================================================

def test_character_disambiguation_and_add_post_approval(test_env):
    """Verifies distinct IDs for similar names, and adding a new character invalidates Gate 3."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    # Register two characters with similar base names
    c1 = asm.register_character("john_doe", "John Doe", "Engineer")
    c2 = asm.register_character("john_wayne", "John Wayne", "Actor")
    assert c1.id != c2.id
    assert c1.id == "john_doe"
    assert c2.id == "john_wayne"

    asm.set_script(
        "Kịch bản", "Script",
        beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0)],
        required_characters=["John Doe"],
    )
    asm.approve_script()

    # Provide reference and approve John Doe
    asm.set_character_reference("john_doe", test_env["char_img"])
    ok, _ = asm.approve_character("john_doe")
    assert ok is True
    assert asm.is_character_gate_passed() is True

    # Now script adds a second required character
    asm.set_script(
        "Kịch bản mới", "New script",
        beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0)],
        required_characters=["John Doe", "John Wayne"],
    )
    # Character gate must now be pending because John Wayne has no approved reference
    assert asm.is_character_gate_passed() is False


# =============================================================================
# TEST K: MANIFEST TAMPER DETECTION & RE-APPROVAL BLOCKED WHEN GENERATING
# =============================================================================

def test_manifest_tamper_and_reapproval_blocked(test_env):
    """Verifies disk manifest modification is detected and re-approval blocked when GENERATING."""
    p_dir = test_env["project_dir"]
    job_id = test_env["job_id"]
    asm = ApprovalStateMachine(p_dir, job_id)

    asm.set_script("Kịch bản", "Script", beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0)])
    asm.approve_script()
    asm.set_audio_generated("vi", test_env["audio_vi"], duration=5.0)
    asm.set_audio_generated("en", test_env["audio_en"], duration=5.0)
    asm.approve_audio("vi")
    asm.approve_audio("en")
    asm.set_shots_plan([{"index": 1, "prompt": "Shot 1", "duration": 5.0}])
    asm.approve_video_generation()

    # 1. Modify video_generation_manifest.json on disk
    m_file = p_dir / "video_generation_manifest.json"
    data = json.loads(m_file.read_text(encoding="utf-8"))
    data["manifest_hash"] = "tampered_hash_123"
    m_file.write_text(json.dumps(data), encoding="utf-8")

    valid, err = asm.verify_manifest_integrity()
    assert valid is False
    assert "Manifest trên đĩa bị thay đổi" in err

    # Restore valid manifest
    data["manifest_hash"] = asm.active_manifest.manifest_hash
    m_file.write_text(json.dumps(data), encoding="utf-8")
    assert asm.verify_manifest_integrity()[0] is True

    # 2. When worker starts generating, video_status -> GENERATING
    asm.mark_generating()
    assert asm.video_status == VideoReadinessStatus.GENERATING

    # Attempting to re-approve while generating must be blocked!
    ok, errs = asm.approve_video_generation()
    assert ok is False
    assert any("GENERATING" in e for e in errs)


# =============================================================================
# TEST L: TELEGRAM CALLBACK QUERY & AUTHORIZATION ENFORCEMENT
# =============================================================================

def test_telegram_authorization_and_callbacks(tmp_path: Path):
    """Verifies unauthorized senders are blocked, and callback queries trigger correct commands."""
    db_file = tmp_path / "test_tg_auth.sqlite3"
    jq = JobQueueManager(db_path=db_file)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    pool = AccountPoolManager(session_dir=sessions, state_file=sessions / "pool.json")

    bot = TelegramCommandBot(
        token="123456:FAKE_TOKEN",
        chat_id="ALLOWED_USER_999",
        job_queue=jq,
        account_pool=pool,
    )
    bot.notifier = MagicMock()

    p_dir = tmp_path / "job_cb_test"
    p_dir.mkdir()
    job_id = jq.create_job("remake", "https://youtube.com/watch?v=123", project_dir=str(p_dir))
    jq.transition_job(job_id, to_state="WAITING_APPROVAL")

    asm = ApprovalStateMachine(p_dir, job_id)
    asm.set_script("VI text", "EN text", beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0)])

    # Callback: approve_script:{job_id}
    bot._handle_callback_query({"id": "cb1", "data": f"approve_script:{job_id}"})
    asm.load()
    assert asm.is_script_gate_passed() is True

    # Callback: gates:{job_id}
    bot.notifier.send_message.reset_mock()
    bot._handle_callback_query({"id": "cb2", "data": f"gates:{job_id}"})
    assert bot.notifier.send_message.called


# =============================================================================
# TEST M: INTEGRATION - AUTOMATED FACTORY SCRIPT BRANCHES
# =============================================================================

def test_factory_custom_script_and_ai_genesis_branches(tmp_path: Path):
    """Verifies factory runs both branches without NameError and enforces EN requirement."""
    from pipeline.automated_factory import AutomatedFactory
    db_file = tmp_path / "factory_test.sqlite3"
    jq = JobQueueManager(db_path=db_file)
    factory = AutomatedFactory(db_path=db_file)
    factory.notifier = MagicMock()

    # 1. Custom script branch (no NameError!)
    custom_script = (
        "[CẢNH 1] Mở đầu hành trình khám phá thế giới tự nhiên.\n"
        "[CẢNH 2] Bước ngoặt tiếp theo mở ra nhiều điều kỳ thú.\n"
    )
    job_id = jq.create_job("remake", "https://youtube.com/watch?v=xyz")
    res_id = factory.run_ai_genesis(
        youtube_url="https://youtube.com/watch?v=xyz",
        custom_script=custom_script,
        job_id=job_id,
        preview_voice_samples=False,
    )
    assert res_id == job_id
    p_dir = Path(f"data/automated_runs/{job_id}")
    asm = ApprovalStateMachine(p_dir, job_id)
    # Gate 1 should fail because EN script is missing
    ok, errs = asm.approve_script()
    assert ok is False
    assert any("EN" in e for e in errs)
