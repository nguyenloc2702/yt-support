"""
AI Native English Script Localizer & Phonetic Director.

Leverages our available AI providers (xKiro LLM and Google Gemini) to perform
deep semantic localization, acoustic phonetic optimization, and native voiceover
polishing on English documentary scripts.

Replaces brittle handcrafted regex with context-aware AI intelligence:
- Detects and converts foreign currencies and translation glitches in context.
- Transliterates non-intuitive Chinese/foreign proper nouns into English TTS-friendly
  phonetic respellings (e.g. Qinchuan -> Chin-chuan).
- Expands years and numbers into natural spoken documentary phrasing.
- Elevates prose to native BBC / PBS / Netflix Documentary standards while
  preserving strict timing boundaries (+/- 3 words per scene).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from pipeline.llm_client import chat_json
from services.english_script_normalizer import EnglishScriptNormalizer

logger = logging.getLogger("ai_english_localizer")

AI_LOCALIZER_SYSTEM_PROMPT = """You are a Master English Documentary Scriptwriter & Voiceover Director (BBC, Netflix, Vox).
Your task is to polish English documentary voiceover scripts translated or adapted from Asian sources.

CRITICAL INSTRUCTIONS:
1. PURGE ALL RESIDUAL FOREIGN TRANSLATION GLITCHES:
   - Eliminate any foreign currency terms ('dong', 'vnd', 'tỷ', 'triệu'). Convert to authentic international figures in USD.
     (Historical fact: Berkshire Hathaway's investment in BYD was two hundred thirty million dollars, NOT dong).
   - Eradicate stiff or literal translations; rewrite with natural, high-retention documentary cadence.

2. PHONETIC OPTIMIZATION FOR ENGLISH TTS:
   - Neural English TTS models (Christopher, Guy, American Male) stumble on Chinese Pinyin.
   - Respellate non-intuitive names phonetically for English ears:
     * 'Qinchuan' -> 'Chin-chuan' (or 'Chinchuan')
     * 'Wang Chuanfu' -> 'Wang Chuan-fu' (hyphenated to preserve syllable separation)
     * 'Lu Shiangyang' / 'Lou' -> 'Lu Xiangyang' (or 'Lu Shang-yang')
     * 'Anhui' -> 'Anhui' (or 'Ahn-hway')

3. SPOKEN VOICE-OVER CADENCE & NUMBERS:
   - Expand 4-digit years into spoken words (e.g. 1966 -> 'nineteen sixty-six', 2003 -> 'two thousand three', 2020 -> 'twenty twenty').
   - Expand automotive codes for clear pronunciation (e.g. F-150 -> 'F-one-fifty', F3 -> 'F-3').
   - Keep sentences punchy, rhythmic, and breathable.

4. CONTRACT INVARIANTS (STRICT):
   - Output exact same number of scenes with identical indices (1..N).
   - Maintain scene word count within +/- 3 words of the original to prevent video duration drift.
   - Do NOT invent or alter factual historical events.

OUTPUT JSON FORMAT:
{
  "scenes": [
    {
      "index": int,
      "text": str,
      "phonetic_notes": str
    }
  ]
}
"""

CHUNK_SIZE = 10  # Optimal chunk size for high-fidelity LLM response without truncation


class AIEnglishLocalizer:
    """Production-grade AI localizer for English documentary narration."""

    def __init__(self, model: Optional[str] = None):
        self.model = model
        self.safety_normalizer = EnglishScriptNormalizer()

    def polish_chunk(self, chunk: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Polishes a single chunk of scenes using the AI provider."""
        payload_in = [
            {"index": s.get("index"), "text": s.get("text", "") or s.get("narration", "")}
            for s in chunk
        ]

        user_prompt = (
            f"Please localize and phonetically optimize these {len(payload_in)} scenes for native English voiceover:\n\n"
            f"{json.dumps(payload_in, indent=2, ensure_ascii=False)}"
        )

        try:
            res = chat_json(
                system_prompt=AI_LOCALIZER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                model=self.model,
            )
            polished_rows = res.get("scenes", [])
            by_idx = {r["index"]: r["text"] for r in polished_rows if "index" in r and "text" in r}

            output_chunk = []
            for s in chunk:
                idx = s.get("index")
                new_s = dict(s)
                if idx in by_idx and by_idx[idx]:
                    # Apply AI text, then pass through safety normalizer to ensure zero leaks
                    safe_text = self.safety_normalizer.normalize_sentence(by_idx[idx]).normalized_text
                    new_s["text"] = safe_text
                    if "narration" in new_s:
                        new_s["narration"] = safe_text
                output_chunk.append(new_s)
            return output_chunk

        except Exception as exc:
            logger.warning("AI Polish Chunk failed (%s); keeping existing text with safety normalizer.", exc)
            return [
                {**s, "text": self.safety_normalizer.normalize_sentence(s.get("text", "")).normalized_text}
                for s in chunk
            ]

    def polish_full_script(
        self,
        shots: List[Dict[str, Any]],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Runs the AI localization pass across all scenes in sequential chunks."""
        total = len(shots)
        all_polished: List[Dict[str, Any]] = []
        chunks_count = (total + CHUNK_SIZE - 1) // CHUNK_SIZE

        for c_idx in range(chunks_count):
            start = c_idx * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, total)
            chunk = shots[start:end]

            polished_chunk = self.polish_chunk(chunk)
            all_polished.extend(polished_chunk)

            if on_progress:
                on_progress(end, total)

        # Audit stats
        _, audit_stats = self.safety_normalizer.normalize_shots_plan(all_polished)
        audit_stats["ai_provider_used"] = "xKiro / Gemini Hybrid"
        audit_stats["total_chunks_processed"] = chunks_count
        return all_polished, audit_stats
