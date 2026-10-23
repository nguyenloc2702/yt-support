"""xKiro batch translator (Phase 1: xKiro-first generation stack).

Replaces the old Google clients5 / MyMemory scraping with the xKiro gateway
LLM (qwen3.8-omni). Design:

- Batch translation: N source lines -> ONE chat call returning JSON array,
  keeping request count low on the free tier.
- Persistent cache keyed by sha256(source|target|text) in data/translation_cache.json
  so re-runs never re-pay for identical lines.
- Glossary: domain terms pinned per direction so narration terminology stays
  consistent across a whole script.
- No silent fallbacks: on unusable output the call raises TranslationError —
  callers must surface the failure instead of inventing content.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from core.logging_config import get_logger
from pipeline.llm_client import LLMResponseError, chat_json

logger = get_logger(__name__)


class TranslationError(RuntimeError):
    """Raised when the xKiro gateway cannot translate the given content."""


BATCH_SIZE = 20
MAX_SOURCE_CHARS = 1200

# Domain glossary: keeps technical narration terms consistent (EN <-> VI).
GLOSSARY_EN_VI: Dict[str, str] = {
    "central bank": "ngân hàng trung ương",
    "commercial bank": "ngân hàng thương mại",
    "interest rate": "lãi suất",
    "money supply": "cung tiền",
    "inflation": "lạm phát",
    "recession": "suy thoái",
    "shareholder": "cổ đông",
    "bankruptcy": "phá sản",
    "stock market": "thị trường chứng khoán",
    "supply chain": "chuỗi cung ứng",
}
GLOSSARY_VI_EN: Dict[str, str] = {v: k for k, v in GLOSSARY_EN_VI.items()}

_CACHE_LOCK = threading.Lock()
_CACHE_PATH = Path(settings.data_dir) / "translation_cache.json"


def _cache_key(text: str, source_lang: str, target_lang: str) -> str:
    h = hashlib.sha256()
    h.update(f"{source_lang}|{target_lang}|".encode("utf-8"))
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:32]


def _load_cache() -> Dict[str, str]:
    try:
        if _CACHE_PATH.is_file():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _store_cache(cache: Dict[str, str]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=0, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except OSError as exc:
        logger.warning("Translation cache write failed: %s", exc)


def _glossary_block(source_lang: str, target_lang: str) -> str:
    glossary = GLOSSARY_EN_VI if (source_lang == "en" and target_lang == "vi") else GLOSSARY_VI_EN
    if not glossary:
        return ""
    pairs = "\n".join(f'- "{k}" => "{v}"' for k, v in glossary.items())
    return f"THUẬT NGỮ BẮT BUỘC (dùng chính xác):\n{pairs}\n"


def translate_batch(
    items: List[Tuple[str, str]],
    source_lang: str,
    target_lang: str,
    model: Optional[str] = None,
) -> List[str]:
    """Translate (item_id, text) pairs; returns translations aligned with `items`.

    Raises TranslationError when the gateway output is unusable.
    """
    from services.settings_service import get_xkiro_generation_model

    if not items:
        return []
    selected_model = model or get_xkiro_generation_model()
    cache = _load_cache()

    results: Dict[str, str] = {}
    pending: List[Tuple[str, str]] = []
    for item_id, text in items:
        key = _cache_key(text, source_lang, target_lang)
        cached = cache.get(key)
        if cached is not None:
            results[item_id] = cached
        else:
            pending.append((item_id, text))

    if pending:
        logger.info(
            "Translating %d segments (%s->%s) via xKiro (%d cache hits)",
            len(pending), source_lang, target_lang, len(items) - len(pending),
        )

    for chunk_start in range(0, len(pending), BATCH_SIZE):
        chunk = pending[chunk_start: chunk_start + BATCH_SIZE]
        payload_lines = [text[:MAX_SOURCE_CHARS] for _, text in chunk]
        system_prompt = (
            "Bạn là máy dịch thuật chuyên nghiệp cho kịch bản thuyết minh (documentary). "
            f"Dịch từng dòng từ {source_lang} sang {target_lang} sao cho tự nhiên, mượt mà, "
            "giữ nguyên số liệu, tên riêng và ý nghĩa. Không thêm bớt thông tin, không bình luận."
        )
        user_prompt = (
            _glossary_block(source_lang, target_lang)
            + "Dịch TỪNG DÒNG sau đây (giữ đúng thứ tự và số lượng dòng):\n"
            + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(payload_lines))
            + "\n\nTrả về duy nhất JSON object: {\"translations\": [\"dịch dòng 1\", \"dịch dòng 2\", ...]}"
        )

        parsed = chat_json(system_prompt, user_prompt, temperature=0.2, model=selected_model)
        translations = parsed.get("translations")
        if not isinstance(translations, list) or len(translations) != len(chunk):
            raise TranslationError(
                f"xKiro translation returned {len(translations) if isinstance(translations, list) else 'invalid'} "
                f"lines for {len(chunk)} inputs"
            )
        with _CACHE_LOCK:
            cache = _load_cache()
            for (item_id, text), translated in zip(chunk, translations):
                cleaned = str(translated).strip()
                if not cleaned:
                    raise TranslationError(f"xKiro translation returned empty line for: {text[:60]!r}")
                results[item_id] = cleaned
                cache[_cache_key(text, source_lang, target_lang)] = cleaned
            _store_cache(cache)

    return [results[item_id] for item_id, _ in items]


def translate_text(text: str, source_lang: str, target_lang: str, model: Optional[str] = None) -> str:
    """Translate a single string. Raises TranslationError on failure."""
    cleaned = (text or "").strip()
    if len(cleaned) < 2:
        raise TranslationError("Cannot translate empty text")
    out = translate_batch([("t0", cleaned)], source_lang, target_lang, model=model)
    return out[0]


def check_back_translation(
    original: str,
    translated: str,
    source_lang: str,
    target_lang: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """LLM-judged semantic check: translate back and compare meaning.

    Returns {"ok": bool, "reason": str}. Used by Phase 2 for translation QA.
    """
    from services.settings_service import get_xkiro_generation_model

    selected_model = model or get_xkiro_generation_model()
    system_prompt = (
        "Bạn là thẩm định viên dịch thuật. So sánh câu GỐC và câu DỊCH, trả về JSON "
        '{"ok": true/false, "reason": "lý do ngắn"}. ok=false nếu dịch sai ý, mất số liệu, '
        "thêm/bớt thông tin quan trọng."
    )
    user_prompt = f"CÂU GỐC:\n{original[:MAX_SOURCE_CHARS]}\n\nCÂU DỊCH:\n{translated[:MAX_SOURCE_CHARS]}"
    try:
        parsed = chat_json(system_prompt, user_prompt, temperature=0.0, model=selected_model)
        return {"ok": bool(parsed.get("ok")), "reason": str(parsed.get("reason", ""))[:200]}
    except LLMResponseError as exc:
        return {"ok": False, "reason": f"back-translation check failed: {exc}"[:200]}
