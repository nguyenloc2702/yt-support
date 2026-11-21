"""English Script Generator & Normalizer (100% EN Documentary Pipeline)."""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from .youtube_ingest import clean_rolling_stutter

# Clean residual foreign currency or translation artifacts into natural English
_VI_LEAK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:tỷ|ty)\s*(?:đồng|vnd|usd|đô)?\b", re.IGNORECASE), r"\1 billion dollars"),
    (re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:triệu|trieu)\s*(?:đồng|vnd|usd|đô)?\b", re.IGNORECASE), r"\1 million dollars"),
    (re.compile(r"\b(?:đồng|vnd)\b", re.IGNORECASE), "dollars"),
]


def normalize_english_text(text: str) -> str:
    """Clean and normalize English narration text for natural TTS delivery."""
    s = clean_rolling_stutter(text.strip())
    for pat, repl in _VI_LEAK_PATTERNS:
        s = pat.sub(repl, s)
    # Remove markdown formatting or stray stage directions in brackets
    s = re.sub(r"\[(?:music|applause|laughter|sound|sfx)[^\]]*\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s and s[-1] not in ".!?":
        s += "."
    return s


def _default_visual_prompt(narration: str, index: int, total: int) -> str:
    """Build a structured 2D vector documentary visual prompt from the English narration."""
    clean = re.sub(r"[^a-zA-Z0-9 ,.'$-]", "", narration).strip()
    stage = "Opening hook scene" if index <= 2 else ("Closing resolution scene" if index == total else "Documentary scene")
    return (
        f"Clean 2D flat vector editorial illustration (16:9, 1280x720). "
        f"{stage} #{index}/{total} depicting: {clean} "
        f"Warm paper background, bold ink outlines, clear subject composition, no unintended text."
    )


def split_script_text_into_shots(
    script_text: str,
    target_words_per_shot: int = 20,
) -> list[dict[str, Any]]:
    """Split raw English script text into structured shots (12-28 words per shot).

    Supports either:
    - Paragraph/line-separated scenes (each non-empty line or paragraph is a scene,
      optionally formatted as `Narration || Visual Prompt`), OR
    - Continuous prose (automatically split on sentence boundaries into 12-28 word shots).
    """
    raw_lines = [ln.strip() for ln in script_text.strip().splitlines() if ln.strip()]
    if not raw_lines:
        raise ValueError("script_text is empty")

    raw_items: list[tuple[str, str | None]] = []
    # Check if user provided explicit lines with `||` or multiple distinct paragraphs
    for ln in raw_lines:
        # Strip leading numbering like "1. " or "Shot 1: "
        ln_clean = re.sub(r"^(?:shot|scene|cảnh)?\s*#?\d+\s*[:.)\-]\s*", "", ln, flags=re.IGNORECASE).strip()
        if not ln_clean:
            continue
        if "||" in ln_clean:
            parts = ln_clean.split("||", 1)
            raw_items.append((parts[0].strip(), parts[1].strip() or None))
        else:
            words = ln_clean.split()
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", ln_clean) if s.strip()]
            if len(raw_lines) > 1 and len(words) <= 28:
                raw_items.append((ln_clean, None))
            elif len(sentences) <= 1:
                raw_items.append((ln_clean, None))
            else:
                buf: list[str] = []
                buf_words = 0
                max_w = 14 if len(raw_lines) == 1 else 24
                for sent in sentences:
                    sw = len(sent.split())
                    if buf and buf_words + sw > max_w:
                        raw_items.append((" ".join(buf), None))
                        buf = [sent]
                        buf_words = sw
                    else:
                        buf.append(sent)
                        buf_words += sw
                if buf:
                    raw_items.append((" ".join(buf), None))

    total = len(raw_items)
    shots: list[dict[str, Any]] = []
    for idx, (narr, vprompt) in enumerate(raw_items, start=1):
        norm_narr = normalize_english_text(narr)
        prompt = vprompt or _default_visual_prompt(norm_narr, idx, total)
        shots.append({
            "index": idx,
            "shot_type": "scene",
            "narration": norm_narr,
            "visual_prompt": prompt,
            "on_screen_text": None,
            "on_screen_type": None,
        })
    return shots


def build_en_shots_from_transcript_segments(
    segments: list[dict],
    target_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Turn YouTube transcript segments into clean English documentary shots.

    If an OpenAI-compatible LLM API key (OPENAI_API_KEY or XKIRO_API_KEY) is set,
    uses LLM to polish and generate visual prompts; otherwise performs deterministic
    sentence-aware documentary chunking so the pipeline always succeeds reliably.
    """
    full_text = " ".join(str(s.get("text", "")).strip() for s in segments if s.get("text"))
    full_text = clean_rolling_stutter(full_text)
    if not full_text:
        raise ValueError("Transcript segments contain no text")

    # Estimate target word count (~2.65 words/sec for English documentary narration)
    if target_duration_sec and target_duration_sec > 0:
        max_words = max(30, int(round(target_duration_sec * 2.65)))
        words = full_text.split()
        if len(words) > max_words:
            full_text = " ".join(words[:max_words])

    api_key = os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if api_key and not os.environ.get("VIDEO_DISABLE_LLM"):
        try:
            target_shots = max(4, int(round((target_duration_sec or 120.0) / 6.0)))
            prompt = (
                f"Rewrite the following transcript into a high-retention English documentary script "
                f"of approximately {target_shots} shots (14-26 words per shot). "
                f"Return ONLY valid JSON: {{\"shots\": [{{\"index\": 1, \"narration\": \"...\", \"visual_prompt\": \"...\"}}]}}.\n\n"
                f"TRANSCRIPT:\n{full_text[:6000]}"
            )
            body = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are an English documentary scriptwriter. Output JSON only."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                # Extract JSON block
                m = re.search(r"\{.*\}", content, flags=re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    raw_shots = parsed.get("shots") or []
                    if raw_shots:
                        out: list[dict[str, Any]] = []
                        for i, s in enumerate(raw_shots, start=1):
                            narr = normalize_english_text(str(s.get("narration") or s.get("text") or ""))
                            if not narr:
                                continue
                            vp = str(s.get("visual_prompt") or "").strip() or _default_visual_prompt(narr, i, len(raw_shots))
                            out.append({
                                "index": len(out) + 1,
                                "shot_type": str(s.get("shot_type") or "scene"),
                                "narration": narr,
                                "visual_prompt": vp,
                                "on_screen_text": s.get("on_screen_text"),
                                "on_screen_type": s.get("on_screen_type"),
                            })
                        if out:
                            return out
        except Exception:
            # Fall through to deterministic segmentation if LLM gateway is unreachable
            pass

    return split_script_text_into_shots(full_text)
