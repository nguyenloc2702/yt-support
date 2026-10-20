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


def test_remake_command(mock_bot: TelegramCommandBot):
    remake_handler = MagicMock()
    mock_bot.on_remake_handler = remake_handler

    mock_bot._handle_command("/remake https://youtube.com/watch?v=456")
    import time
    time.sleep(0.1)
    remake_handler.assert_called_once_with("https://youtube.com/watch?v=456")


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


def test_approve_mode2_does_not_trigger_reup_handler(mock_bot: TelegramCommandBot):
    # Mode 2 (remake / ai_genesis)
    job_id = mock_bot.jq.create_job("remake", "https://youtube.com/watch?v=123")
    mock_bot.jq.update_job_status(job_id, "WAITING_APPROVAL")

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
