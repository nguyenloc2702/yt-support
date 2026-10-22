"""Unit tests for TelegramCommandBot remote control dispatcher."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.account_pool import AccountPoolManager
from services.job_queue import JobQueueManager
from services.telegram_bot import TelegramCommandBot


@pytest.fixture
def temp_jq(tmp_path: Path):
    db_file = tmp_path / "test_tg_jobs.sqlite3"
    return JobQueueManager(db_path=db_file)


@pytest.fixture
def temp_pool(tmp_path: Path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    state_file = sessions / "pool_state.json"
    return AccountPoolManager(session_dir=sessions, state_file=state_file)


@pytest.fixture
def mock_bot(temp_jq: JobQueueManager, temp_pool: AccountPoolManager):
    bot = TelegramCommandBot(
        token="123456:FAKE_TELEGRAM_TOKEN",
        chat_id="999888777",
        job_queue=temp_jq,
        account_pool=temp_pool,
    )
    # Mock notifier so no real HTTP calls happen
    bot.notifier = MagicMock()
    return bot


def test_help_command(mock_bot: TelegramCommandBot):
    mock_bot._handle_command("/help")
    mock_bot.notifier.send_message.assert_called_once()
    assert "HỆ THỐNG ĐIỀU KHIỂN VIDEO FACTORY" in mock_bot.notifier.send_message.call_args[0][0]


def test_status_command(mock_bot: TelegramCommandBot):
    mock_bot._handle_command("/status")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "BÁO CÁO TRẠNG THÁI HỆ THỐNG" in msg
    assert "HOẠT ĐỘNG BÌNH THƯỜNG" in msg


def test_reup_command(mock_bot: TelegramCommandBot):
    reup_handler = MagicMock()
    mock_bot.on_reup_handler = reup_handler

    mock_bot._handle_command("/reup https://youtube.com/watch?v=123 duration=90")
    # Wait briefly as it runs in thread or verify thread started
    import time
    time.sleep(0.1)
    reup_handler.assert_called_once_with("https://youtube.com/watch?v=123", 90.0)


def test_remake_command_initiates_script_extraction(mock_bot: TelegramCommandBot):
    remake_handler = MagicMock()
    mock_bot.on_remake_handler = remake_handler
    mock_bot._async_extract_and_send_script = MagicMock()

    mock_bot._handle_command("/remake https://youtube.com/watch?v=456")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "Đang bóc tách kịch bản gốc và phụ đề từ YouTube" in msg
    assert "xào chẻ" in msg

    # Job is created and placed in AWAITING_CUSTOM_SCRIPT
    active_job = mock_bot.jq.get_active_awaiting_script_job()
    assert active_job is not None
    assert active_job["url"] == "https://youtube.com/watch?v=456"

    # Async extractor dispatched
    mock_bot._async_extract_and_send_script.assert_called_once()
    remake_handler.assert_not_called()


def test_async_extract_and_send_script(mock_bot: TelegramCommandBot, tmp_path: Path):
    srt_file = tmp_path / "sample.srt"
    txt_file = tmp_path / "sample.txt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:05,000\nHello", encoding="utf-8")
    txt_file.write_text("Hello", encoding="utf-8")

    mock_factory = MagicMock()
    mock_factory.extract_and_export_original_script.return_value = (srt_file, txt_file, "Sample Video", 1)
    mock_bot._factory = mock_factory

    mock_bot._async_extract_and_send_script("https://youtube.com/watch?v=456", "remake_123")

    # Both documents must be sent to Telegram
    assert mock_bot.notifier.send_document.call_count == 2
    doc_calls = [call[0][0] for call in mock_bot.notifier.send_document.call_args_list]
    assert str(srt_file) in doc_calls
    assert str(txt_file) in doc_calls

    # Instruction message with inline button must be sent
    mock_bot.notifier.send_message.assert_called_once()
    instr_msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "ĐÃ GỬI FILE KỊCH BẢN & PHỤ ĐỀ GỐC" in instr_msg
    assert "xào chẻ" in instr_msg
    kwargs = mock_bot.notifier.send_message.call_args[1]
    assert "reply_markup" in kwargs
    assert "remake_ai:remake_123" in str(kwargs["reply_markup"])


def test_remake_ai_direct(mock_bot: TelegramCommandBot):
    remake_handler = MagicMock()
    mock_bot.on_remake_handler = remake_handler

    mock_bot._handle_command("/remake https://youtube.com/watch?v=456 ai")
    import time
    time.sleep(0.1)
    remake_handler.assert_called_once()


def test_custom_script_and_voice_flow(mock_bot: TelegramCommandBot, tmp_path: Path):
    remake_handler = MagicMock()
    mock_bot.on_remake_handler = remake_handler

    # 1. Trigger remake with custom option
    mock_bot._handle_command("/remake https://youtube.com/watch?v=789 custom")
    active_job = mock_bot.jq.get_active_awaiting_script_job()
    assert active_job is not None
    job_id = active_job["id"]
    assert active_job["status"] == "AWAITING_CUSTOM_SCRIPT"

    # 2. Test voice command
    mock_bot._handle_command(f"/voice {job_id} vi-VN-NamMinhNeural")
    v_file = Path(f"data/automated_runs/{job_id}/voice.txt")
    if v_file.exists():
        assert v_file.read_text(encoding="utf-8").strip() == "vi-VN-NamMinhNeural"
        v_file.unlink()

    # 3. Test submitting valid custom script text
    valid_script = "\n".join([f"Cảnh {i}: Đây là một cảnh quay tài liệu điện ảnh vô cùng đặc sắc và kịch tính số {i}." for i in range(1, 25)])
    mock_bot._handle_command(f"/script {job_id} {valid_script}")
    import time
    time.sleep(0.1)
    remake_handler.assert_called_once()
    called_kwargs = remake_handler.call_args[1] if remake_handler.call_args[1] else {}
    assert "custom_script" in called_kwargs or len(remake_handler.call_args[0]) >= 2


def test_callback_query_inline_buttons(mock_bot: TelegramCommandBot):
    job_id = mock_bot.jq.create_job("remake", "https://youtube.com/watch?v=111")
    remake_handler = MagicMock()
    mock_bot.on_remake_handler = remake_handler

    # Test callback for remake_ai
    cb = {
        "id": "cb_001",
        "data": f"remake_ai:{job_id}",
        "from": {"id": "999888777"},
    }
    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_bot._handle_callback_query(cb)
        import time
        time.sleep(0.1)
        remake_handler.assert_called_once_with("https://youtube.com/watch?v=111", job_id=job_id)


def test_approve_and_reject_flow(mock_bot: TelegramCommandBot):
    job_id = mock_bot.jq.create_job("reup", "https://youtube.com/watch?v=123")
    mock_bot.jq.update_job_status(job_id, "WAITING_APPROVAL")

    approve_handler = MagicMock()
    mock_bot.on_approve_handler = approve_handler

    # Test approve
    mock_bot._handle_command(f"/approve {job_id}")
    import time
    time.sleep(0.1)
    job = mock_bot.jq.get_job(job_id)
    assert job["status"] == "APPROVED"
    approve_handler.assert_called_once_with(job_id)

    # Test reject another job
    job2_id = mock_bot.jq.create_job("reup", "https://youtube.com/watch?v=999")
    mock_bot.jq.update_job_status(job2_id, "WAITING_APPROVAL")
    mock_bot._handle_command(f"/reject {job2_id}")
    job2 = mock_bot.jq.get_job(job2_id)
    assert job2["status"] == "CANCELLED"


def test_approve_mode2_does_not_trigger_reup_handler(mock_bot: TelegramCommandBot, tmp_path: Path):
    # Mode 2 (remake / ai_genesis)
    p_dir = tmp_path / "mode2_test"
    p_dir.mkdir(parents=True, exist_ok=True)
    job_id = mock_bot.jq.create_job("remake", "https://youtube.com/watch?v=123", project_dir=str(p_dir))
    mock_bot.jq.update_job_status(job_id, "WAITING_APPROVAL")

    # Set up 4-gate approval state so that /approve can proceed
    from services.approval_state_machine import ApprovalStateMachine, BeatIntent
    asm = ApprovalStateMachine(p_dir, job_id)
    asm.set_script("Kịch bản VI", "Script EN", beats=[BeatIntent(id=1, text_vi="A", text_en="B", duration=5.0)])
    asm.approve_script()
    a_vi = p_dir / "a_vi.wav"
    a_en = p_dir / "a_en.wav"
    a_vi.write_bytes(b"RIFF" + b"\x00" * 200)
    a_en.write_bytes(b"RIFF" + b"\x00" * 200)
    asm.set_audio_generated("vi", a_vi, duration=5.0)
    asm.set_audio_generated("en", a_en, duration=5.0)
    asm.approve_audio("vi")
    asm.approve_audio("en")
    asm.set_shots_plan([{"index": 1, "prompt": "shot 1", "duration": 5.0}])

    approve_handler = MagicMock()
    mock_bot.on_approve_handler = approve_handler

    mock_bot._handle_command(f"/approve {job_id}")
    import time
    time.sleep(0.1)

    # Status must be APPROVED for background worker to pick up shots
    job = mock_bot.jq.get_job(job_id)
    assert job["status"] == "APPROVED"
    # Mode 1 reup handler must NOT be called for Mode 2
    approve_handler.assert_not_called()


def test_cancel_and_retry_flow(mock_bot: TelegramCommandBot):
    job_id = mock_bot.jq.create_job("remake", "https://youtube.com/watch?v=123")
    mock_bot.jq.update_job_status(job_id, "PROCESSING")

    # Cancel
    mock_bot._handle_command(f"/cancel {job_id}")
    job = mock_bot.jq.get_job(job_id)
    assert job["status"] == "CANCELLED"

    # Retry
    mock_bot._handle_command(f"/retry {job_id}")
    job = mock_bot.jq.get_job(job_id)
    assert job["status"] == "APPROVED"


def test_pause_and_resume(mock_bot: TelegramCommandBot):
    assert mock_bot.pool.kill_switch_active is False

    mock_bot._handle_command("/pause")
    assert mock_bot.pool.kill_switch_active is True

    mock_bot._handle_command("/resume")
    assert mock_bot.pool.kill_switch_active is False


def test_raw_youtube_url_prompts_user(mock_bot: TelegramCommandBot):
    mock_bot._handle_command("https://youtu.be/9bZkp7q19f0")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "Nhận diện link YouTube" in msg
    assert "/reup" in msg and "/remake" in msg


def test_raw_broken_youtube_url_warns_missing_video_id(mock_bot: TelegramCommandBot):
    mock_bot._handle_command("https://m.youtube.com/watch?si=em77NKhPbEULJaUg")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "thiếu mã video" in msg


def test_reup_missing_video_id_rejected(mock_bot: TelegramCommandBot):
    mock_bot._handle_command("/reup https://m.youtube.com/watch?si=em77NKhPbEULJaUg")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "thiếu mã video" in msg
    # Verify no job was created in DB
    with mock_bot.jq._get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM factory_jobs").fetchone()[0]
        assert cnt == 0


def test_remake_with_mobile_share_title(mock_bot: TelegramCommandBot):
    handler = MagicMock()
    mock_bot.on_remake_handler = handler
    raw_share = "/remake The ENTIRE History of Costco in 19 Minutes https://youtu.be/AwxcZVfJUSA?si=YUia3_msKPGIsuzn ai"
    mock_bot._handle_command(raw_share)
    import time
    time.sleep(0.1)
    handler.assert_called_once()
    called_url = handler.call_args[0][0]
    assert called_url == "https://youtu.be/AwxcZVfJUSA?si=YUia3_msKPGIsuzn"


def test_raw_mobile_share_message_prompts_mode(mock_bot: TelegramCommandBot):
    raw_share = "The ENTIRE History of Costco in 19 Minutes https://youtu.be/AwxcZVfJUSA?si=YUia3_msKPGIsuzn"
    mock_bot._handle_command(raw_share)
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "Nhận diện link YouTube" in msg
    assert "https://youtu.be/AwxcZVfJUSA?si=YUia3_msKPGIsuzn" in msg


def test_accounts_command(mock_bot: TelegramCommandBot):
    import time
    from services.video_provider import SessionValidationResult
    mock_bot.provider.validate_session = MagicMock(return_value=SessionValidationResult(
        valid=True,
        tier="PRO",
        credits_remaining=2000,
        reset_at="2026-10-21T05:42:13+00:00",
        message="OK",
    ))
    # Populate a mock account
    acc_file = mock_bot.pool.session_dir / "acc_01.json"
    acc_file.write_text('{"__Secure-1PSID": "fake"}')
    mock_bot.pool.reload_accounts()

    mock_bot._handle_command("/accounts")
    time.sleep(0.1)
    # Check that send_message was called with prompt and final report
    assert mock_bot.notifier.send_message.call_count >= 2
    report_call = mock_bot.notifier.send_message.call_args_list[-1]
    report_text = report_call[0][0]
    assert "BÁO CÁO CHI TIẾT TÀI KHOẢN" in report_text


def test_button_shortcut_stripping(mock_bot: TelegramCommandBot):
    # Test button with emoji prefix "📊 /status"
    mock_bot._handle_command("📊 /status")
    mock_bot.notifier.send_message.assert_called_once()
    msg = mock_bot.notifier.send_message.call_args[0][0]
    assert "BÁO CÁO TRẠNG THÁI HỆ THỐNG" in msg

    mock_bot.notifier.send_message.reset_mock()
    # Test button with emoji prefix "⏸ /pause"
    mock_bot._handle_command("⏸ /pause")
    assert mock_bot.pool.kill_switch_active is True


def test_accounts_report_formatting(mock_bot: TelegramCommandBot, tmp_path: Path):
    from services.video_provider import SessionValidationResult
    # Setup mock validation return
    mock_bot.provider.validate_session = MagicMock(return_value=SessionValidationResult(
        valid=True,
        tier="PRO",
        credits_remaining=1850,
        reset_at="2026-10-21T05:42:13+00:00",
        message="OK",
    ))

    # Add a mock account to pool
    acc_file = tmp_path / "sessions" / "acc_01.json"
    acc_file.write_text('{"__Secure-1PSID": "fake"}')
    mock_bot.pool.reload_accounts()

    report = mock_bot.get_accounts_report(check_realtime=True)
    assert "acc_01" in report
    assert "PRO ⭐" in report
    assert "1850" in report
    assert "Giới hạn mới" in report
    assert "12:42:13" in report
