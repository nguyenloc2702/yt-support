"""Unit tests for the xKiro AI gateway circuit breaker (services.ai_breaker)."""

import threading
import time

import pytest

from services.ai_breaker import AICircuitBreaker, AIGatewayOpenError


def test_closed_passes_through_and_resets():
    breaker = AICircuitBreaker("t", failure_threshold=3, cooldown_seconds=60.0)

    assert breaker.call(lambda: "ok", "desc") == "ok"

    def flaky():
        raise ValueError("boom")

    for _ in range(2):
        with pytest.raises(ValueError):
            breaker.call(flaky, "desc")

    # A success resets the consecutive failure counter (still CLOSED)
    assert breaker.call(lambda: 42, "desc") == 42
    assert breaker._state == "CLOSED"
    assert breaker._consecutive_failures == 0


def test_opens_after_threshold_and_fails_fast():
    breaker = AICircuitBreaker("t", failure_threshold=3, cooldown_seconds=300.0)
    calls = {"n": 0}

    def failing():
        calls["n"] += 1
        raise RuntimeError("gateway down")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            breaker.call(failing, "desc")

    assert breaker._state == "OPEN"

    # While OPEN the function is NOT invoked at all (fail fast)
    with pytest.raises(AIGatewayOpenError):
        breaker.call(failing, "desc")
    assert calls["n"] == 3


def test_half_open_success_closes_circuit():
    breaker = AICircuitBreaker("t", failure_threshold=1, cooldown_seconds=0.05)
    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")), "desc")
    assert breaker._state == "OPEN"

    time.sleep(0.06)  # cooldown elapsed -> HALF_OPEN trial allowed
    assert breaker.call(lambda: "recovered", "desc") == "recovered"
    assert breaker._state == "CLOSED"


def test_half_open_failure_reopens():
    breaker = AICircuitBreaker("t", failure_threshold=1, cooldown_seconds=0.05)
    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")), "desc")

    time.sleep(0.06)
    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("still down")), "desc")
    assert breaker._state == "OPEN"


def test_half_open_single_trial_in_flight():
    breaker = AICircuitBreaker("t", failure_threshold=1, cooldown_seconds=0.05)
    with pytest.raises(RuntimeError):
        breaker.call(lambda: (_ for _ in ()).throw(RuntimeError("x")), "desc")
    time.sleep(0.06)

    release = threading.Event()
    started = threading.Event()

    def slow_trial():
        started.set()
        release.wait(timeout=5)
        return "late"

    t = threading.Thread(target=lambda: breaker.call(slow_trial, "desc"))
    t.start()
    started.wait(timeout=5)

    # half_open_max_calls=1: a second concurrent call must fail fast
    with pytest.raises(AIGatewayOpenError):
        breaker.call(lambda: "second", "desc")

    release.set()
    t.join(timeout=5)
    assert breaker._state == "CLOSED"
