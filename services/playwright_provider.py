"""Playwright Video Provider (Fallback).

Wraps GeminiGenerator to adhere to VideoProvider interface.
Used only as a secondary fallback if gemini-webapi encounters unpatched RPC changes.
"""

from pathlib import Path
from typing import Optional

from services.gemini_generator import GeminiGenerator
from services.video_provider import ErrorKind, ProviderResult, SessionValidationResult, VideoProvider


class PlaywrightProvider(VideoProvider):
    def __init__(self, temp_dir: Optional[Path] = None, headless: bool = True):
        self.generator = GeminiGenerator(temp_dir=temp_dir, headless=headless)

    def validate_session(self, session_file: Path) -> SessionValidationResult:
        if not session_file.exists():
            return SessionValidationResult(valid=False, error_kind=ErrorKind.AUTH, message="File does not exist")
        return SessionValidationResult(valid=True, tier="UNKNOWN", message="Playwright session file present")

    def generate(
        self,
        prompt: str,
        out_path: Path,
        session_file: Optional[Path] = None,
    ) -> ProviderResult:
        if not session_file or not session_file.exists():
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.AUTH,
                error_message=f"Session file not found: {session_file}",
            )

        success, video_path, err_msg = self.generator.generate_shot(
            prompt=prompt,
            session_file=session_file,
            expected_filename=out_path.name,
        )

        if success and video_path and video_path.exists():
            return ProviderResult(
                ok=True,
                video_path=video_path,
            )

        # Map error message
        error_kind = ErrorKind.TRANSIENT
        if err_msg:
            err_lower = err_msg.lower()
            if "rate" in err_lower or "429" in err_lower:
                error_kind = ErrorKind.RATE_LIMIT
            elif "quota" in err_lower or "limit" in err_lower:
                error_kind = ErrorKind.QUOTA
            elif "auth" in err_lower or "login" in err_lower:
                error_kind = ErrorKind.AUTH

        return ProviderResult(
            ok=False,
            error_kind=error_kind,
            error_message=err_msg or "Playwright generation failed",
        )
