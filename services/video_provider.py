"""Video Provider Interface & Shared Abstractions.

Defines the contract for all video generation backends:
- GeminiWebApiProvider (Primary, internal RPC via gemini-webapi)
- PlaywrightProvider (Fallback, headless browser)
- ImageKenBurnsProvider (Secondary, still image pan/zoom for B-roll)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class ErrorKind(str, Enum):
    OK = "OK"
    RATE_LIMIT = "RATE_LIMIT"      # 429 or temporary throttle
    QUOTA = "QUOTA"                # Credits exhausted for window / daily
    AUTH = "AUTH"                  # Cookie invalid, expired, or logged out
    VERIFY = "VERIFY"              # Captcha / suspicious login verification required
    TRANSIENT = "TRANSIENT"        # Network timeout / connection drop (safe to retry)
    FATAL = "FATAL"                # Permanent error (bad prompt, format unrecoverable)


@dataclass
class ProviderResult:
    ok: bool
    video_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None
    error_kind: Optional[ErrorKind] = None
    error_message: Optional[str] = None
    duration_sec: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class SessionValidationResult:
    valid: bool
    tier: str = "UNKNOWN"          # "PRO", "FREE", "UNKNOWN"
    credits_remaining: Optional[int] = None
    reset_at: Optional[str] = None
    error_kind: Optional[ErrorKind] = None
    message: str = ""


class VideoProvider(ABC):
    """Abstract interface for video generation providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        out_path: Path,
        session_file: Optional[Path] = None,
    ) -> ProviderResult:
        """Generate a video clip from a text prompt and save to out_path."""
        pass

    @abstractmethod
    def validate_session(self, session_file: Path) -> SessionValidationResult:
        """Validate if the session cookies in session_file are active and check tier."""
        pass
