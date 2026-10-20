"""Tests for SSRF guard in pipeline.youtube.validate_public_url."""

import pytest

from core.exceptions import AppError
from pipeline.youtube import validate_public_url


def test_rejects_loopback():
    with pytest.raises(AppError):
        validate_public_url("http://127.0.0.1:8501/x")
    with pytest.raises(AppError):
        validate_public_url("http://localhost/x")


def test_rejects_private_range():
    with pytest.raises(AppError):
        validate_public_url("http://192.168.1.1/x")
    with pytest.raises(AppError):
        validate_public_url("http://10.0.0.1/x")
    with pytest.raises(AppError):
        validate_public_url("http://172.16.0.1/x")


def test_rejects_link_local_and_metadata():
    with pytest.raises(AppError):
        validate_public_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(AppError):
        validate_public_url("http://[::1]/x")


def test_rejects_unresolvable_host():
    with pytest.raises(AppError):
        validate_public_url("http://this-domain-definitely-does-not-exist-xyz.invalid/x")


def test_rejects_invalid_url():
    with pytest.raises(AppError):
        validate_public_url("not a url at all")


def test_validate_youtube_url_missing_video_id():
    from core.exceptions import InvalidMediaError
    from pipeline.youtube import validate_youtube_url

    # Missing v= parameter (watch with tracking param only)
    with pytest.raises(InvalidMediaError, match="thiếu mã video"):
        validate_youtube_url("https://m.youtube.com/watch?si=em77NKhPbEULJaUg")


def test_validate_youtube_url_valid_formats():
    from pipeline.youtube import validate_youtube_url

    # Should not raise for valid formats
    validate_youtube_url("https://www.youtube.com/watch?v=9bZkp7q19f0")
    validate_youtube_url("https://m.youtube.com/watch?v=9bZkp7q19f0&si=abc")
    validate_youtube_url("https://youtu.be/9bZkp7q19f0?si=em77NKhPbEULJaUg")
    validate_youtube_url("https://www.youtube.com/shorts/9bZkp7q19f0")
