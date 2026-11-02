"""Google Gemini / Imagen Image Generation Provider.

Generates still images using Google Gemini WebAPI (Imagen 3) via browser session cookies.
Key architectural & business guarantees:
1. Free, high-fidelity vector / explainer illustration style without third-party API costs.
2. Safe token rotation: persists updated __Secure-1PSIDTS back to session file atomically.
3. Event loop safe: wraps async GeminiClient cleanly with timeout and guaranteed connection closure.
4. Zero-fake policy: verifies byte size (>= MIN_IMAGE_BYTES) before marking successful.
5. Account pool aware: handles reservation, rate limit reporting, and kill-switch safety.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from services.gemini_webapi_provider import (
    extract_cookies_from_file,
    persist_rotated_cookie,
)
from services.prompt_sanitizer import (
    STYLE_PREFIX,
    ensure_style_prefix,
    sanitize_veo_prompt,
)

logger = logging.getLogger("gemini_image_provider")

DEFAULT_IMAGE_TIMEOUT_SECONDS = 120.0
MIN_IMAGE_BYTES = 1000


async def _async_generate_gemini_image(
    prompt: str,
    out_path: Path,
    session_file: Path,
    min_bytes: int = MIN_IMAGE_BYTES,
) -> bool:
    """Asynchronously generates an image via gemini_webapi and saves to out_path."""
    try:
        from gemini_webapi import GeminiClient
        from gemini_webapi.exceptions import AuthError, GeminiError
    except ImportError:
        logger.error("gemini-webapi is not installed. Please install gemini-webapi==2.1.1")
        return False

    psid, psidts = extract_cookies_from_file(session_file)
    if not psid:
        logger.warning("Session file %s is missing __Secure-1PSID", session_file.name)
        return False

    client = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        await client.init(timeout=60, auto_refresh=False, verbose=False)
        logger.info("🎨 Sending image generation prompt to Gemini (Imagen 3)...")
        response = await client.generate_content(prompt)

        if response and response.images:
            first_image = response.images[0]
            await first_image.save(path=str(out_path.parent), filename=out_path.name)
            if out_path.exists() and out_path.stat().st_size >= min_bytes:
                logger.info("✔ Gemini image successfully generated: %s (%d bytes)", out_path.name, out_path.stat().st_size)
                return True
            else:
                logger.warning("Gemini generated an empty or undersized image (<%d bytes)", min_bytes)
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                return False
        else:
            logger.warning("Gemini replied without images (prompt may have triggered safety or model declined)")
            return False

    except AuthError as e:
        logger.warning("Gemini image auth failed (%s): session cookies expired in %s", e, session_file.name)
        raise
    except GeminiError as e:
        logger.warning("Gemini image generation error: %s", e)
        raise
    except Exception as e:
        logger.warning("Unexpected error during Gemini image generation: %s", e)
        raise
    finally:
        try:
            persist_rotated_cookie(client, session_file, psid, psidts)
        except Exception as exc:
            logger.debug("Failed to persist rotated cookie: %s", exc)
        try:
            await client.close()
        except Exception:
            pass


def generate_gemini_image(
    prompt: str,
    out_path: Path,
    session_file: Optional[Path] = None,
    account_pool: Optional[Any] = None,
    timeout: float = DEFAULT_IMAGE_TIMEOUT_SECONDS,
    min_bytes: int = MIN_IMAGE_BYTES,
    sanitize: bool = True,
) -> bool:
    """Synchronous interface to generate an image using Google Gemini (Imagen 3).

    Args:
        prompt: Raw or prepared image prompt.
        out_path: Destination path for the saved image (.jpg or .png).
        session_file: Path to the account session JSON file. If None, acquires
                      an available account from account_pool or sessions/ directory.
        account_pool: Optional AccountPoolManager for reservation and kill-switch management.
        timeout: Timeout in seconds for generation.
        min_bytes: Minimum accepted file size in bytes (zero-fake policy).
        sanitize: Whether to sanitize prompt and apply STYLE_PREFIX.

    Returns:
        bool: True if an image was successfully created and validated, False otherwise.
    """
    final_prompt = prompt
    if sanitize:
        safe_body = sanitize_veo_prompt(prompt, for_video=False)
        final_prompt = ensure_style_prefix(safe_body)

    target_session = session_file
    account_id: Optional[str] = None

    if not target_session and account_pool:
        try:
            acc = account_pool.get_available_account(reserve=False)
            if acc and Path(acc.session_file).exists():
                target_session = Path(acc.session_file)
                account_id = getattr(acc, "acc_id", None)
        except Exception as exc:
            logger.warning("Account pool account retrieval failed for image: %s", exc)

    if not target_session or not Path(target_session).exists():
        # Fallback to scanning sessions directory
        sessions_dir = Path("sessions")
        if sessions_dir.exists():
            candidates = sorted(list(sessions_dir.glob("acc_*.json")))
            if not candidates:
                test_acc = sessions_dir / "test_account.json"
                if test_acc.exists():
                    candidates = [test_acc]
            if candidates:
                target_session = candidates[0]

    if not target_session or not Path(target_session).exists():
        logger.warning("No session file available for Gemini image generation")
        return False

    if target_session and not account_id:
        account_id = Path(target_session).stem
    if not account_pool:
        try:
            from services.account_pool import AccountPoolManager
            account_pool = AccountPoolManager()
        except Exception:
            pass

    success = False
    try:
        try:
            # Check if there is an active event loop in current thread
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Running inside an active event loop thread: create a new loop in executor
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(
                    asyncio.run,
                    asyncio.wait_for(
                        _async_generate_gemini_image(
                            prompt=final_prompt,
                            out_path=Path(out_path),
                            session_file=Path(target_session),
                            min_bytes=min_bytes,
                        ),
                        timeout=timeout,
                    )
                )
                success = bool(fut.result(timeout=timeout + 5.0))
        else:
            success = bool(
                asyncio.run(
                    asyncio.wait_for(
                        _async_generate_gemini_image(
                            prompt=final_prompt,
                            out_path=Path(out_path),
                            session_file=Path(target_session),
                            min_bytes=min_bytes,
                        ),
                        timeout=timeout,
                    )
                )
            )

        return success

    except Exception as exc:
        err_str = str(exc).lower()
        is_auth_error = (
            "auth" in err_str
            or "unauthenticated" in err_str
            or "login" in err_str
            or "401" in err_str
            or "session expired" in err_str
        )
        is_verify_error = "verify" in err_str or "captcha" in err_str or "challenge" in err_str

        if account_pool and account_id:
            if is_auth_error or is_verify_error:
                account_pool.trigger_kill_switch(account_id, f"Gemini Image Auth/Verification error: {exc}")
            elif "rate" in err_str or "429" in err_str:
                account_pool.report_rate_limit(account_id)
            elif "quota" in err_str or "exhausted" in err_str:
                account_pool.report_quota_exhausted(account_id)

        logger.warning("Gemini image generation call failed: %s", exc)
        return False
