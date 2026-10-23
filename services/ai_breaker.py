"""Thread-safe circuit breaker for the xKiro AI gateway.

Prevents the pipeline from hammering a failing gateway (burning retry budgets
and latency) when the provider is down or rate-limited. States:

- CLOSED: calls pass through; consecutive failures are counted.
- OPEN: calls fail fast with AIGatewayOpenError until cooldown elapses.
- HALF_OPEN: a single trial call is allowed; success closes, failure re-opens.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

from core.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class AIGatewayOpenError(RuntimeError):
    """Raised when a call is attempted while the circuit breaker is OPEN."""


class AICircuitBreaker:
    def __init__(
        self,
        name: str = "xkiro",
        failure_threshold: int = 4,
        cooldown_seconds: float = 90.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._state = "CLOSED"
        self._opened_at = 0.0
        self._half_open_in_flight = 0

    @property
    def state(self) -> str:
        return self._state

    def _can_try(self, now: float) -> bool:
        if self._state == "CLOSED":
            return True
        if self._state == "OPEN":
            if now - self._opened_at >= self.cooldown_seconds:
                self._state = "HALF_OPEN"
                self._half_open_in_flight = 0
                return True
            return False
        # HALF_OPEN
        return self._half_open_in_flight < self.half_open_max_calls

    def call(self, fn: Callable[[], T], description: str = "AI call") -> T:
        """Run `fn` under breaker protection. Raises AIGatewayOpenError when OPEN."""
        with self._lock:
            if not self._can_try(time.monotonic()):
                raise AIGatewayOpenError(
                    f"{self.name} gateway breaker OPEN — skip {description} "
                    f"(retry after cooldown {self.cooldown_seconds:.0f}s)"
                )
            if self._state == "HALF_OPEN":
                self._half_open_in_flight += 1
        try:
            result = fn()
        except Exception:
            with self._lock:
                self._consecutive_failures += 1
                if self._state == "HALF_OPEN" or self._consecutive_failures >= self.failure_threshold:
                    if self._state != "OPEN":
                        logger.warning(
                            "AI breaker '%s' OPEN after %d consecutive failures",
                            self.name,
                            self._consecutive_failures,
                        )
                    self._state = "OPEN"
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._consecutive_failures = 0
            self._state = "CLOSED"
        return result


_breakers: dict[str, AICircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_ai_breaker(model: str | None = None) -> AICircuitBreaker:
    """Breaker for one gateway model.

    Scoped per model so that a throttled primary (the free tier 409s under
    load) cannot open the circuit for its backups and strand the whole job —
    which is exactly how every script variant of one run was invalidated.
    """
    name = "xkiro" if not model else f"xkiro:{model}"
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = AICircuitBreaker(name)
            _breakers[name] = breaker
        return breaker
