"""
Voice catalog service: dynamic voice discovery from the edge-tts provider,
with filtering by locale/language/gender and a spoken sample preview.

Voices are fetched live from the provider (cached per session with TTL) and
never hard-coded in the UI. The full `voice_id` is persisted in render configs,
never display labels, so configs stay valid across UI changes.
"""

import time
from typing import Any, Dict, List, Optional

from core.exceptions import RenderError
from core.logging_config import get_logger

logger = get_logger(__name__)

CACHE_TTL_SECONDS = 3600
DEFAULT_SAMPLE_TEXT = {
    "vi": "Xin chào, đây là giọng đọc mẫu cho video review của bạn.",
    "en": "Hello, this is a sample voice preview for your review video.",
}

_voice_cache: Dict[str, Any] = {"voices": None, "fetched_at": 0.0}


def list_voices(
    language: Optional[str] = None,
    gender: Optional[str] = None,
    provider: str = "edge-tts",
) -> List[Dict[str, Any]]:
    """
    List provider voices as metadata dicts, filtered by locale prefix and gender.

    Returns dicts: {id, locale, language, gender, styles}. Raises RenderError
    when the provider is unreachable and no cached copy exists.
    """
    voices = _fetch_voices(provider)
    result = voices
    if language:
        lang = language.lower()
        result = [v for v in result if v["locale"].lower().startswith(lang)]
    if gender:
        g = gender.lower()
        result = [v for v in result if v["gender"].lower() == g]
    return result


def list_languages(provider: str = "edge-tts") -> List[str]:
    """Sorted unique locale prefixes (e.g. 'vi-VN', 'en-US')."""
    voices = _fetch_voices(provider)
    return sorted({v["locale"] for v in voices})


def get_voice(voice_id: str, provider: str = "edge-tts") -> Optional[Dict[str, Any]]:
    """Look up one voice by id, or None."""
    voices = _fetch_voices(provider)
    for v in voices:
        if v["id"] == voice_id:
            return v
    return None


def validate_voice(voice_id: str, provider: str = "edge-tts") -> bool:
    """True when the provider currently offers `voice_id`."""
    return get_voice(voice_id, provider) is not None


def preview_voice(
    voice_id: str, out_path, sample_text: Optional[str] = None
) -> None:
    """Synthesize a short sample of `voice_id` to `out_path` (async via edge-tts)."""
    import asyncio

    import edge_tts

    # Pick sample text matching the voice locale.
    locale = voice_id.split("-", 1)[0].lower() if voice_id else "en"
    text = sample_text or DEFAULT_SAMPLE_TEXT.get(
        locale, DEFAULT_SAMPLE_TEXT["en"]
    )

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice_id)
        await communicate.save(str(out_path))

    try:
        asyncio.run(_run())
    except Exception as exc:
        raise RenderError(f"Không tạo được bản nghe thử cho giọng '{voice_id}': {exc}") from exc


def _fetch_voices(provider: str = "edge-tts") -> List[Dict[str, Any]]:
    """Fetch the provider voice list with a TTL cache; fall back to cache on failure."""
    now = time.time()
    if (
        _voice_cache["voices"] is not None
        and now - _voice_cache["fetched_at"] < CACHE_TTL_SECONDS
    ):
        return _voice_cache["voices"]

    import asyncio

    import edge_tts

    async def _run() -> List[Dict[str, Any]]:
        raw = await edge_tts.list_voices()
        return [
            {
                "id": v["ShortName"],
                "locale": v["Locale"],
                "gender": v["Gender"],
                "styles": v.get("VoiceTag", {}).get("ContentCategories", [])
                + v.get("VoiceTag", {}).get("VoicePersonalities", []),
            }
            for v in raw
        ]

    try:
        voices = asyncio.run(_run())
        _voice_cache["voices"] = voices
        _voice_cache["fetched_at"] = now
        return voices
    except Exception as exc:
        if _voice_cache["voices"] is not None:
            logger.warning("Voice provider unreachable (%s); using cached list.", exc)
            return _voice_cache["voices"]
        raise RenderError(f"Không tải được danh sách giọng đọc: {exc}") from exc
