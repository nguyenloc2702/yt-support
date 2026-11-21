"""Kiểm tra PIN gate (utils.auth): chặn trang, sai PIN báo lỗi, đúng PIN mở khoá."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from utils.auth import _pin_matches

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PAGE = ROOT / "pages" / "00_settings.py"


def test_pin_matches_accepts_only_correct_pin():
    assert _pin_matches("997220")
    assert not _pin_matches("997221")
    assert not _pin_matches("99722")
    assert not _pin_matches("")
    assert not _pin_matches("abcdef")


@pytest.fixture()
def gated_page():
    at = AppTest.from_file(str(SETTINGS_PAGE), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def test_page_blocked_without_pin(gated_page):
    assert [ti for ti in gated_page.text_input if ti.key == "studio_access_pin"]
    assert not list(gated_page.title), "Chưa đăng nhập mà đã thấy nội dung trang!"


def test_wrong_pin_keeps_gate_and_shows_error(gated_page):
    gated_page.text_input(key="studio_access_pin").set_value("111111").run()
    assert not gated_page.exception
    assert any("không đúng" in e.value for e in gated_page.error)
    assert not list(gated_page.title), "Vẫn phải chặn sau khi PIN sai"


def test_correct_pin_unlocks_page(gated_page):
    gated_page.text_input(key="studio_access_pin").set_value("997220").run()
    assert not gated_page.exception
    assert list(gated_page.title), "Nhập PIN đúng phải vào được trang!"
    assert not list(gated_page.error)


def test_lockout_blocks_even_correct_pin(gated_page):
    """After 5 wrong PIN attempts, session is locked and even the correct PIN must not unlock."""
    for i in range(5):
        gated_page.text_input(key="studio_access_pin").set_value(f"00000{i}").run()
        assert not gated_page.exception

    # Attempt with the valid PIN while still locked out -> must remain blocked
    gated_page.text_input(key="studio_access_pin").set_value("997220").run()
    assert not gated_page.exception
    assert any("Nhập sai quá nhiều lần" in e.value for e in gated_page.error)
    assert not list(gated_page.title), "Đang bị khóa thì nhập đúng PIN cũng không được mở!"


