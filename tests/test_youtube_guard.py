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
