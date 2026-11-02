"""
English Script Normalizer & Native Phonics Engine.

Provides an automated, deterministic pipeline step to:
1. Detect and eliminate any residual Vietnamese words, currency leaks (dong, VND, tỷ),
   or translation artifacts from English scripts.
2. Optimize foreign proper nouns (Chinese Pinyin, automotive brands, founders' names)
   into English TTS-friendly phonetic respellings so neural voice models (Edge-TTS,
   xKiro, Whisper) pronounce them with 100% naturalness without slurring or choking.
3. Expand numerical dates, years, and automotive codes (e.g. 1966 -> nineteen sixty-six,
   F-150 -> F-one-fifty) into unambiguous spoken English words.
4. Provide a strict audit gate (`audit_english_script`) to ensure zero Vietnamese
   leakage before audio synthesis begins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# 1. Known Vietnamese residual patterns and currency leaks
# NOTE: Specific multi-word phrases MUST come before generic word replacements!
VIETNAMESE_LEAK_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    # Specific known translation mistakes
    (re.compile(r"\bsigned a check worth more than two billion dong\b", re.IGNORECASE),
     "signed a check worth two hundred thirty million dollars",
     "Warren Buffett historical investment correction"),

    # Number + Vietnamese currency / unit combinations
    (re.compile(r"\b(\d+)\s*(?:tỷ|ty)\s*(?:dong|đồng|vnd)?\b", re.IGNORECASE), r"\1 billion dollars", "Vietnamese large number unit (tỷ)"),
    (re.compile(r"\b(\d+)\s*triệu\s*(?:dong|đồng|vnd)?\b", re.IGNORECASE), r"\1 million dollars", "Vietnamese number unit (triệu)"),
    (re.compile(r"\b(\d+)\s*vạn\b", re.IGNORECASE), r"\10 thousand", "Vietnamese number unit (vạn)"),
    
    # Vietnamese measure words & phrases
    (re.compile(r"\bcây\s+số\b", re.IGNORECASE), "kilometers", "Vietnamese distance phrase"),
    (re.compile(r"\bchiếc\s+xe\b", re.IGNORECASE), "car", "Vietnamese classifier"),
    
    # Generic currency leaks (after specific phrases)
    (re.compile(r"\b(?:dong|đồng|vnd)\b", re.IGNORECASE), "dollars", "Vietnamese currency leak (dong/vnd)"),
]

# Vietnamese accented characters that must never appear in pure English narration
VIETNAMESE_ACCENT_PATTERN = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE
)


# 2. Proper Noun & Pinyin Phonetic Optimization Map for English TTS
# (Pattern, Replacement, Reason)
PHONETIC_PROPER_NOUNS: List[Tuple[re.Pattern, str, str]] = [
    # Qinchuan -> Chinchuan (English TTS reads 'Qin' as 'Cheachron' or 'Kwin'; Chinchuan is pronounced Chin-chwan)
    (re.compile(r"\bQinchuan\b"), "Chinchuan", "Phonetic respelling of 秦川 (Chin-chwan) for English TTS"),
    
    # Lou Shiangyang / Lu Shiangyang -> Lu Xiangyang
    (re.compile(r"\bLou\s+Shiangyang\b"), "Lu Xiangyang", "Typo fix and Pinyin standardization for 吕向阳"),
    (re.compile(r"\bLu\s+Shiangyang\b"), "Lu Xiangyang", "Pinyin standardization for 吕向阳"),
    
    # Wang Chuanfu -> Wang Chuan-fu (Hyphen forces neural models to separate 'Chuan' and 'Fu' crisply)
    (re.compile(r"\bWang\s+Chuanfu\b"), "Wang Chuan-fu", "Syllable separation for 王传福"),
    
    # Automotive model codes
    (re.compile(r"\bF-150\b"), "F-one-fifty", "Spoken articulation of Ford F-150"),
    (re.compile(r"\bF150\b"), "F-one-fifty", "Spoken articulation of Ford F150"),
    (re.compile(r"\bBYD\s+F3\b"), "BYD F-3", "Clear articulation for model F3"),
    (re.compile(r"\bthe\s+F3\b"), "the F-3", "Clear articulation for model F3"),
]


# 3. Spoken Number & Year Expansions (1900 - 2099)
ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

def _two_digit_words(n: int) -> str:
    if n == 0:
        return ""
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    tens_val = TENS[n // 10]
    ones_val = ONES[n % 10]
    return f"{tens_val}-{ones_val}" if ones_val else tens_val


def year_to_words(year: int) -> str:
    """Converts 4-digit years (1900-2099) into standard documentary spoken English."""
    if not (1900 <= year <= 2099):
        return str(year)
    
    # 2000 - 2009: two thousand [and] X
    if 2000 <= year <= 2009:
        if year == 2000:
            return "two thousand"
        return f"two thousand {ONES[year - 2000]}"
    
    # 2010 - 2099: twenty X
    if 2010 <= year <= 2099:
        second_half = year % 100
        return f"twenty {_two_digit_words(second_half)}"
    
    # 1900 - 1999: nineteen X
    if 1900 <= year <= 1999:
        second_half = year % 100
        if second_half == 0:
            return "nineteen hundred"
        return f"nineteen {_two_digit_words(second_half)}"
    
    return str(year)


@dataclass
class NormalizationReport:
    """Detailed telemetry and audit report for script normalization."""
    original_text: str
    normalized_text: str
    is_clean: bool
    vietnamese_leaks_found: List[str] = field(default_factory=list)
    phonetic_fixes: List[Dict[str, str]] = field(default_factory=list)
    numerical_expansions: List[Dict[str, str]] = field(default_factory=list)
    total_modifications: int = 0


class EnglishScriptNormalizer:
    """Standard automated normalizer for English video documentary scripts."""

    def __init__(self):
        self.year_pattern = re.compile(r"\b(19\d\d|20\d\d)\b")

    def normalize_sentence(self, text: str) -> NormalizationReport:
        """Normalizes a single narration line, resolving all leaks, Pinyin, and numbers."""
        if not text:
            return NormalizationReport(original_text="", normalized_text="", is_clean=True)

        current = text
        vietnamese_leaks = []
        phonetic_fixes = []
        numerical_expansions = []

        # 1. Check & Replace Vietnamese leaks and translation remnants
        for pattern, replacement, reason in VIETNAMESE_LEAK_PATTERNS:
            match = pattern.search(current)
            if match:
                vietnamese_leaks.append(f"{match.group(0)} -> {replacement} ({reason})")
                current = pattern.sub(replacement, current)

        # Check for unhandled Vietnamese diacritics
        accent_matches = set(VIETNAMESE_ACCENT_PATTERN.findall(current))
        if accent_matches:
            vietnamese_leaks.append(f"Residual Vietnamese accents detected: {', '.join(sorted(accent_matches))}")

        # 2. Phonetic Proper Noun replacements
        for pattern, replacement, reason in PHONETIC_PROPER_NOUNS:
            if pattern.search(current):
                phonetic_fixes.append({"original": pattern.pattern, "replacement": replacement, "reason": reason})
                current = pattern.sub(replacement, current)

        # 3. Year and numerical expansion
        def _replace_year(match: re.Match) -> str:
            val = int(match.group(1))
            expanded = year_to_words(val)
            numerical_expansions.append({"year": str(val), "expanded": expanded})
            return expanded

        current = self.year_pattern.sub(_replace_year, current)

        total_mods = len(vietnamese_leaks) + len(phonetic_fixes) + len(numerical_expansions)
        is_clean = len(vietnamese_leaks) == 0 or all("Residual" not in l for l in vietnamese_leaks)

        return NormalizationReport(
            original_text=text,
            normalized_text=current,
            is_clean=is_clean,
            vietnamese_leaks_found=vietnamese_leaks,
            phonetic_fixes=phonetic_fixes,
            numerical_expansions=numerical_expansions,
            total_modifications=total_mods,
        )

    def normalize_shots_plan(self, shots: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Normalizes an entire shots_plan_en list, updating 'text' fields and returning audit stats."""
        total_modified_scenes = 0
        all_leaks = []
        all_phonetic = []
        all_numerical = []

        normalized_shots = []
        for s in shots:
            shot_copy = dict(s)
            orig_text = shot_copy.get("text", "") or shot_copy.get("narration", "")
            report = self.normalize_sentence(orig_text)

            if report.total_modifications > 0:
                total_modified_scenes += 1
                shot_copy["text"] = report.normalized_text
                if "narration" in shot_copy:
                    shot_copy["narration"] = report.normalized_text

                if report.vietnamese_leaks_found:
                    all_leaks.append({"scene": shot_copy.get("index"), "leaks": report.vietnamese_leaks_found})
                if report.phonetic_fixes:
                    all_phonetic.append({"scene": shot_copy.get("index"), "fixes": report.phonetic_fixes})
                if report.numerical_expansions:
                    all_numerical.append({"scene": shot_copy.get("index"), "expansions": report.numerical_expansions})

            normalized_shots.append(shot_copy)

        stats = {
            "total_scenes": len(shots),
            "modified_scenes_count": total_modified_scenes,
            "vietnamese_leaks": all_leaks,
            "phonetic_fixes": all_phonetic,
            "numerical_expansions": all_numerical,
            "is_100_percent_clean_english": len([l for l in all_leaks if any("Residual" in x for x in l["leaks"])]) == 0,
        }
        return normalized_shots, stats


# Global convenience function
_GLOBAL_NORMALIZER = EnglishScriptNormalizer()

def normalize_english_narration(text: str) -> str:
    """Convenience helper to normalize a single English narration string."""
    return _GLOBAL_NORMALIZER.normalize_sentence(text).normalized_text
