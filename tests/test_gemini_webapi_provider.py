"""Unit tests for GeminiWebApiProvider and session utilities."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.gemini_webapi_provider import (
    GeminiWebApiProvider,
    atomic_save_session,
    extract_cookies_from_file,
)
from services.video_provider import ErrorKind


def test_extract_cookies_dict_format(tmp_path: Path):
    file = tmp_path / "session_dict.json"
    file.write_text(json.dumps({
        "__Secure-1PSID": "test_psid",
        "__Secure-1PSIDTS": "test_psidts",
    }))

    psid, psidts = extract_cookies_from_file(file)
    assert psid == "test_psid"
    assert psidts == "test_psidts"


def test_extract_cookies_playwright_format(tmp_path: Path):
    file = tmp_path / "session_pw.json"
    file.write_text(json.dumps({
        "cookies": [
            {"name": "OTHER_COOKIE", "value": "xyz"},
            {"name": "__Secure-1PSID", "value": "pw_psid"},
            {"name": "__Secure-1PSIDTS", "value": "pw_psidts"},
        ]
    }))

    psid, psidts = extract_cookies_from_file(file)
    assert psid == "pw_psid"
    assert psidts == "pw_psidts"


def test_extract_cookies_nonexistent(tmp_path: Path):
    psid, psidts = extract_cookies_from_file(tmp_path / "nonexistent.json")
    assert psid is None
    assert psidts is None


def test_atomic_save_session(tmp_path: Path):
    target = tmp_path / "saved_session.json"
    atomic_save_session(target, "my_psid", "my_psidts")

    assert target.exists()
    data = json.loads(target.read_text())
    assert data["__Secure-1PSID"] == "my_psid"
    assert data["__Secure-1PSIDTS"] == "my_psidts"
    assert "updated_at" in data

    # Test overwriting existing
    atomic_save_session(target, "new_psid", "new_psidts")
    data2 = json.loads(target.read_text())
    assert data2["__Secure-1PSID"] == "new_psid"


@patch("services.gemini_webapi_provider.GeminiClient")
def test_validate_session_pro(mock_client_cls, tmp_path: Path):
    session_file = tmp_path / "acc_test.json"
    atomic_save_session(session_file, "psid_val", "psidts_val")

    mock_client = MagicMock()
    mock_client.init = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.usage_info = {
        "tier": {"id": 2, "label": "PRO"},
        "current_5h": {"remaining_credits": 2000, "reset_at": "2026-10-20T05:00:00"},
    }
    mock_client.cookies = {"__Secure-1PSIDTS": "psidts_val"}
    mock_client_cls.return_value = mock_client

    provider = GeminiWebApiProvider()
    res = provider.validate_session(session_file)

    assert res.valid is True
    assert res.tier == "PRO"
    assert res.credits_remaining == 2000


@patch("services.gemini_webapi_provider.GeminiClient")
def test_validate_session_free_warns(mock_client_cls, tmp_path: Path):
    session_file = tmp_path / "acc_free.json"
    atomic_save_session(session_file, "psid_free", "psidts_free")

    mock_client = MagicMock()
    mock_client.init = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.usage_info = {
        "tier": {"id": 1, "label": "FREE"},
        "current_5h": {"remaining_credits": 600, "reset_at": "2026-10-20T05:00:00"},
    }
    mock_client.cookies = {}
    mock_client_cls.return_value = mock_client

    provider = GeminiWebApiProvider()
    res = provider.validate_session(session_file)

    assert res.valid is True
    assert res.tier == "FREE"
    assert "FREE" in res.message


@patch("services.gemini_webapi_provider.GeminiClient")
def test_generate_success(mock_client_cls, tmp_path: Path):
    session_file = tmp_path / "acc.json"
    atomic_save_session(session_file, "psid", "psidts")
    out_video = tmp_path / "output.mp4"

    mock_video = MagicMock()
    async def fake_save(path, filename, verbose=False):
        p = Path(path) / filename
        p.write_bytes(b"FAKE_MP4_CONTENT_LONG_ENOUGH_TO_PASS_MIN_SIZE" * 500)
        return {"video": str(p), "video_thumbnail": str(p.with_suffix(".jpg"))}

    mock_video.save = AsyncMock(side_effect=fake_save)

    mock_resp = MagicMock()
    mock_resp.videos = [mock_video]

    mock_client = MagicMock()
    mock_client.init = AsyncMock()
    mock_client.close = AsyncMock()
    mock_client.generate_content = AsyncMock(return_value=mock_resp)
    mock_client.usage_info = {"tier": {"label": "PRO"}}
    mock_client.cookies = {}
    mock_client_cls.return_value = mock_client

    provider = GeminiWebApiProvider()
    result = provider.generate("A cinematic shot", out_video, session_file)

    assert result.ok is True
    assert result.video_path.exists()
    assert result.duration_sec >= 0.0
