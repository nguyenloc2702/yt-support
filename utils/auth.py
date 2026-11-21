"""Shared-PIN gate cho Studio web UI.

Khoá cửa nhẹ bằng mã PIN 6 số: nhập đúng mới cho vào app. PIN mặc định chỉ
lưu dưới dạng SHA-256 (không giữ bản plain trong code), và có thể thay bằng
biến môi trường ``APP_ACCESS_PIN`` (plain) hoặc ``APP_ACCESS_PIN_SHA256``.

Lưu ý: đây là khoá cửa đơn giản cho một người dùng, không phải xác thực
người dùng thật (đa người dùng/identity hãy dùng OIDC ``st.login``).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import streamlit as st

from core.logging_config import get_logger

logger = get_logger(__name__)

# SHA-256 của PIN mặc định (đổi PIN: đặt APP_ACCESS_PIN hoặc APP_ACCESS_PIN_SHA256).
_DEFAULT_PIN_SHA256 = "247f64e3e29b0fb599f27709dd3ded36911d3405bf435d877b37084536c87073"

_THROTTLE_AFTER_FAILURES = 5
_THROTTLE_SECONDS_PER_EXTRA_FAILURE = 1.5
_THROTTLE_CAP_SECONDS = 10.0

_SESSION_KEY_AUTHENTICATED = "studio_authenticated"
_SESSION_KEY_PIN_INPUT = "studio_access_pin"
_SESSION_KEY_FAILURES = "studio_pin_failures"
_SESSION_KEY_LOCKED_UNTIL = "studio_pin_locked_until"


def _expected_pin() -> str:
    """Trả về PIN plain (từ env) hoặc digest SHA-256 để so khớp."""
    plain = os.environ.get("APP_ACCESS_PIN", "").strip()
    if plain:
        return plain
    digest = os.environ.get("APP_ACCESS_PIN_SHA256", "").strip().lower()
    return digest if digest else _DEFAULT_PIN_SHA256


def _pin_matches(candidate: str) -> bool:
    expected = _expected_pin()
    if len(expected) == 64 and all(c in "0123456789abcdef" for c in expected):
        return hmac.compare_digest(hashlib.sha256(candidate.encode()).hexdigest(), expected)
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _register_failure(pin: str) -> None:
    failures = int(st.session_state.get(_SESSION_KEY_FAILURES, 0)) + 1
    st.session_state[_SESSION_KEY_FAILURES] = failures
    if failures >= _THROTTLE_AFTER_FAILURES:
        extra = failures - _THROTTLE_AFTER_FAILURES + 1
        delay = min(_THROTTLE_CAP_SECONDS, extra * _THROTTLE_SECONDS_PER_EXTRA_FAILURE)
        st.session_state[_SESSION_KEY_LOCKED_UNTIL] = time.monotonic() + delay
    logger.warning("Wrong studio PIN attempt (%d chars entered).", len(pin))


def _throttle_if_locked() -> bool:
    locked_until = st.session_state.get(_SESSION_KEY_LOCKED_UNTIL)
    if locked_until is None:
        return False
    remaining = float(locked_until) - time.monotonic()
    if remaining <= 0:
        st.session_state.pop(_SESSION_KEY_LOCKED_UNTIL, None)
        return False
    st.error(f"Nhập sai quá nhiều lần. Vui lòng đợi {int(remaining) + 1} giây rồi thử lại.")
    return True


def _render_login_screen() -> None:
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown("<div style='height:14vh'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='text-align:center;'>"
            "<h2 style='margin-bottom:0.25rem;'>🎬 Video Studio</h2>"
            "<p style='opacity:0.7; margin-bottom:1.5rem;'>Nhập mã PIN 6 số để tiếp tục.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        pin = st.text_input(
            "Mã PIN",
            value="",
            max_chars=6,
            type="password",
            key=_SESSION_KEY_PIN_INPUT,
            autocomplete="current-password",
            placeholder="••••••",
        )
        if not pin:
            return
        if _throttle_if_locked():
            return
        if _pin_matches(pin.strip()):
            st.session_state[_SESSION_KEY_AUTHENTICATED] = True
            st.session_state.pop(_SESSION_KEY_FAILURES, None)
            st.session_state.pop(_SESSION_KEY_LOCKED_UNTIL, None)
            logger.info("Studio unlocked via PIN gate.")
            st.rerun()
        _register_failure(pin)
        _throttle_if_locked()
        st.error("Mã PIN không đúng. Vui lòng thử lại.")


def require_pin_login() -> None:
    """Chặn trang lại màn hình nhập PIN cho đến khi nhập đúng.

    Gọi ngay sau ``st.set_page_config()`` trong ``app.py`` và mọi trang
    trong ``pages/`` (session_state dùng chung nên chỉ cần mở khoá một lần).
    """
    if st.session_state.get(_SESSION_KEY_AUTHENTICATED):
        return
    _render_login_screen()
    st.stop()


def render_lock_button() -> None:
    """Nút khoá màn hình (sidebar của app.py): xoá trạng thái mở khoá."""
    if st.sidebar.button("🔒 Khoá màn hình"):
        st.session_state[_SESSION_KEY_AUTHENTICATED] = False
        st.session_state.pop(_SESSION_KEY_PIN_INPUT, None)
        st.rerun()
