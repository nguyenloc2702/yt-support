"""Gemini WebAPI Provider (Internal Veo RPC via gemini-webapi).

Primary video generator:
- Extremely lightweight (<50MB RAM).
- Connects using Google browser session cookies (__Secure-1PSID, __Secure-1PSIDTS).
- Auto-polls Veo RPC 206 status until 720p/1080p MP4 clip is rendered and downloaded.
- Atomic session persistence: updates rotated PSIDTS back to disk safely.
- Session validator to check cookie freshness, tier (PRO/FREE), and remaining credits.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Optional, Tuple

from services.video_provider import ErrorKind, ProviderResult, SessionValidationResult, VideoProvider
from services.prompt_sanitizer import sanitize_veo_prompt

try:
    from gemini_webapi import GeminiClient
    from gemini_webapi.exceptions import (
        APIError,
        AuthError,
        GeminiError,
        TemporarilyBlockedError,
        TimeoutError as GeminiTimeoutError,
        UsageLimitExceededError,
    )
except ImportError:
    GeminiClient = None

logger = logging.getLogger("gemini_webapi_provider")


def extract_cookies_from_file(session_file: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts __Secure-1PSID and __Secure-1PSIDTS from various session file formats:
    - Dict: {"__Secure-1PSID": "...", "__Secure-1PSIDTS": "..."}
    - Playwright storage_state: {"cookies": [{"name": "...", "value": "..."}, ...]}
    - Cookie list: [{"name": "...", "value": "..."}, ...]
    """
    if not session_file.exists():
        return None, None

    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse session file %s: %s", session_file, e)
        return None, None

    psid = None
    psidts = None

    if isinstance(data, dict):
        # Direct dictionary format
        psid = data.get("__Secure-1PSID") or data.get("psid")
        psidts = data.get("__Secure-1PSIDTS") or data.get("psidts")

        # Playwright storage_state format
        if not psid and "cookies" in data and isinstance(data["cookies"], list):
            for c in data["cookies"]:
                if c.get("name") == "__Secure-1PSID":
                    psid = c.get("value")
                elif c.get("name") == "__Secure-1PSIDTS":
                    psidts = c.get("value")

    elif isinstance(data, list):
        # List of cookies format
        for c in data:
            if isinstance(c, dict):
                if c.get("name") == "__Secure-1PSID":
                    psid = c.get("value")
                elif c.get("name") == "__Secure-1PSIDTS":
                    psidts = c.get("value")

    return psid, psidts


def atomic_save_session(session_file: Path, psid: str, psidts: str):
    """
    Safely and atomically writes updated cookies to session_file.
    Uses tempfile + os.replace to prevent 0-byte or corrupted files on crash.
    """
    session_file.parent.mkdir(parents=True, exist_ok=True)
    dir_name = str(session_file.parent)

    data = {
        "__Secure-1PSID": psid,
        "__Secure-1PSIDTS": psidts,
        "updated_at": time.time(),
    }

    # Write to a temporary file in the same directory first
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name

    # Atomic rename / replace
    os.replace(tmp_name, str(session_file))

    # Set 600 permissions on Unix-like environments
    if hasattr(os, "chmod") and os.name != "nt":
        try:
            os.chmod(str(session_file), 0o600)
        except Exception:
            pass


class GeminiWebApiProvider(VideoProvider):
    """Video provider using internal RPC via gemini-webapi."""

    def __init__(self, proxy: Optional[str] = None):
        if GeminiClient is None:
            raise RuntimeError("gemini-webapi is not installed. Please run: pip install gemini-webapi==2.1.1")
        self.proxy = proxy

    def validate_session(self, session_file: Path) -> SessionValidationResult:
        """
        Validates cookies in session_file:
        - Authenticates session with Google.
        - Checks account status (clean or blocked).
        - Inspects tier (PRO vs FREE).
        - Extracts remaining credits and reset timestamp.
        """
        psid, psidts = extract_cookies_from_file(session_file)
        if not psid:
            return SessionValidationResult(
                valid=False,
                error_kind=ErrorKind.AUTH,
                message=f"Missing __Secure-1PSID in {session_file.name}",
            )

        return asyncio.run(self._async_validate_session(session_file, psid, psidts))

    async def _async_validate_session(
        self, session_file: Path, psid: str, psidts: Optional[str]
    ) -> SessionValidationResult:
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=self.proxy,
        )

        try:
            await client.init(timeout=60, auto_refresh=True, verbose=False)

            tier = "UNKNOWN"
            credits_remaining = None
            reset_at = None

            if not client.usage_info:
                return SessionValidationResult(
                    valid=False,
                    error_kind=ErrorKind.AUTH,
                    message="Cookie không hợp lệ hoặc đã hết hạn đăng nhập (UNAUTHENTICATED). Vui lòng cập nhật cookie mới.",
                )

            tier_info = client.usage_info.get("tier", {})
            tier = tier_info.get("label", "UNKNOWN")
            current_window = client.usage_info.get("current_5h", {})
            credits_remaining = current_window.get("remaining_credits")
            reset_at = current_window.get("reset_at")

            # Check if cookie was updated during init
            new_psidts = client.cookies.get("__Secure-1PSIDTS") if hasattr(client, "cookies") else None
            if new_psidts and new_psidts != psidts:
                atomic_save_session(session_file, psid, new_psidts)

            if tier == "FREE":
                return SessionValidationResult(
                    valid=True,
                    tier="FREE",
                    credits_remaining=credits_remaining,
                    reset_at=reset_at,
                    message="Valid cookie, but account is FREE tier (Veo requires PRO/Advanced).",
                )

            return SessionValidationResult(
                valid=True,
                tier=tier,
                credits_remaining=credits_remaining,
                reset_at=reset_at,
                message=f"Valid {tier} account. Credits: {credits_remaining}. Reset: {reset_at}",
            )

        except AuthError as e:
            return SessionValidationResult(
                valid=False,
                error_kind=ErrorKind.AUTH,
                message=f"Authentication expired or invalid: {e}",
            )
        except TemporarilyBlockedError as e:
            return SessionValidationResult(
                valid=False,
                error_kind=ErrorKind.RATE_LIMIT,
                message=f"Account temporarily blocked / rate limited: {e}",
            )
        except Exception as e:
            err_name = type(e).__name__
            err_str = str(e)
            if "verify" in err_str.lower() or "challenge" in err_str.lower() or "captcha" in err_str.lower():
                return SessionValidationResult(
                    valid=False,
                    error_kind=ErrorKind.VERIFY,
                    message=f"Security verification required: {err_str}",
                )
            return SessionValidationResult(
                valid=False,
                error_kind=ErrorKind.TRANSIENT,
                message=f"Validation failed ({err_name}): {err_str}",
            )
        finally:
            await client.close()

    def generate(
        self,
        prompt: str,
        out_path: Path,
        session_file: Optional[Path] = None,
    ) -> ProviderResult:
        """Synchronous wrapper to generate a video shot via async RPC."""
        if not session_file or not session_file.exists():
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.AUTH,
                error_message=f"Session file not found: {session_file}",
            )

        psid, psidts = extract_cookies_from_file(session_file)
        if not psid:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.AUTH,
                error_message=f"Cannot extract __Secure-1PSID from {session_file}",
            )

        return asyncio.run(self._async_generate(prompt, out_path, session_file, psid, psidts))

    async def _async_generate(
        self,
        prompt: str,
        out_path: Path,
        session_file: Path,
        psid: str,
        psidts: Optional[str],
    ) -> ProviderResult:
        start_time = time.time()
        client = GeminiClient(
            secure_1psid=psid,
            secure_1psidts=psidts,
            proxy=self.proxy,
        )

        try:
            logger.info("Initializing Gemini Web client for shot...")
            await client.init(timeout=300, auto_refresh=True, verbose=False)

            # Check tier
            if client.usage_info:
                tier = client.usage_info.get("tier", {}).get("label")
                if tier == "FREE":
                    return ProviderResult(
                        ok=False,
                        error_kind=ErrorKind.QUOTA,
                        error_message="Account is FREE tier. Veo video generation is disabled.",
                        duration_sec=time.time() - start_time,
                    )

            veo_prompt = sanitize_veo_prompt(prompt)

            logger.info("Submitting Veo prompt: '%s'...", veo_prompt[:80])
            response = await client.generate_content(prompt=veo_prompt)

            if not response.videos:
                resp_text = (response.text or "").strip()
                logger.warning("No video in response. Response text: %s", resp_text[:200])

                resp_lower = resp_text.lower()
                # Check for Google Veo quota limits (5h rolling or daily)
                quota_keywords = [
                    "limit reset", "limit resets", "as soon as your limit",
                    "more videos as soon as", "usage limit", "quota",
                    "reach your limit", "reached the limit", "exceeded your limit",
                ]
                if any(kw in resp_lower for kw in quota_keywords):
                    return ProviderResult(
                        ok=False,
                        error_kind=ErrorKind.QUOTA,
                        error_message=f"Veo video quota exhausted: {resp_text[:200]}",
                        duration_sec=time.time() - start_time,
                    )

                if "capacity" in resp_lower or "traffic" in resp_lower or "try again later" in resp_lower:
                    return ProviderResult(
                        ok=False,
                        error_kind=ErrorKind.RATE_LIMIT,
                        error_message=f"Server overloaded: {resp_text[:200]}",
                        duration_sec=time.time() - start_time,
                    )
                return ProviderResult(
                    ok=False,
                    error_kind=ErrorKind.FATAL,
                    error_message=f"Model returned no video: {resp_text[:200]}",
                    duration_sec=time.time() - start_time,
                )

            video = response.videos[0]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            filename = out_path.name
            target_dir = out_path.parent

            logger.info("Video stream ready. Polling & saving to %s...", out_path)
            save_dict = await video.save(
                path=str(target_dir),
                filename=filename,
                verbose=False,
            )

            # Check file on disk
            final_path = out_path
            if not final_path.exists() and isinstance(save_dict, dict) and save_dict.get("video"):
                final_path = Path(save_dict["video"])

            thumb_path = None
            if isinstance(save_dict, dict) and save_dict.get("video_thumbnail"):
                thumb_path = Path(save_dict["video_thumbnail"])

            total_duration = time.time() - start_time

            if final_path.exists() and final_path.stat().st_size > 10000:
                logger.info("Shot generated successfully: %s (%.2f MB in %.1fs)", final_path, final_path.stat().st_size / (1024 * 1024), total_duration)

                # Persist updated session cookie if changed
                new_psidts = client.cookies.get("__Secure-1PSIDTS") if hasattr(client, "cookies") else None
                if new_psidts and new_psidts != psidts:
                    atomic_save_session(session_file, psid, new_psidts)

                return ProviderResult(
                    ok=True,
                    video_path=final_path,
                    thumbnail_path=thumb_path,
                    duration_sec=total_duration,
                )
            else:
                return ProviderResult(
                    ok=False,
                    error_kind=ErrorKind.TRANSIENT,
                    error_message=f"Generated file is empty or missing: {final_path}",
                    duration_sec=total_duration,
                )

        except UsageLimitExceededError as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.QUOTA,
                error_message=str(e),
                duration_sec=time.time() - start_time,
            )
        except TemporarilyBlockedError as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.RATE_LIMIT,
                error_message=str(e),
                duration_sec=time.time() - start_time,
            )
        except AuthError as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.AUTH,
                error_message=str(e),
                duration_sec=time.time() - start_time,
            )
        except GeminiTimeoutError as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.TRANSIENT,
                error_message=f"Timeout: {e}",
                duration_sec=time.time() - start_time,
            )
        except APIError as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.TRANSIENT,
                error_message=f"RPC API error: {e}",
                duration_sec=time.time() - start_time,
            )
        except Exception as e:
            err_str = str(e)
            if "verify" in err_str.lower() or "challenge" in err_str.lower():
                return ProviderResult(
                    ok=False,
                    error_kind=ErrorKind.VERIFY,
                    error_message=f"Security verification required: {err_str}",
                    duration_sec=time.time() - start_time,
                )
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.TRANSIENT,
                error_message=f"Unexpected generation error ({type(e).__name__}): {err_str}",
                duration_sec=time.time() - start_time,
            )
        finally:
            await client.close()
