"""Machine hook scoring, ported from youtube-agent-skill's hookscore.py.

Five properties scored 0-100, verdict = 60% mean + 40% weakest property: a hook
strong everywhere except one dead property leaks at that property, and a plain
mean hides it. Phrase lists are Vietnamese first because the hook is graded on
what the listener hears; the bands follow the factory's own window (scene 1 is
up to 28 words at ~3.5 words/second, not the English 10-second take).
"""

from __future__ import annotations

import re
from typing import Dict

CONCRETE_RE = re.compile(
    r"(\d[\d.,]*\s?(%|tỷ|triệu|nghìn|ngàn|đô\b|usd|đ\b|k\b|m\b|x\b)"
    r"|\b(1[5-9]\d{2}|20\d{2})\b|\$\d)",
    re.IGNORECASE,
)
VAGUE_RE = re.compile(
    r"\b(tuyệt vời|đáng kinh ngạc|điên rồ|khủng khiếp|khổng lồ|mạnh mẽ|tối thượng"
    r"|bậc nhất|đỉnh cao|siêu|bí mật|cực kỳ|vô cùng|nhàm chán)\b",
    re.IGNORECASE,
)
FILLER_RE = re.compile(
    r"\b(thật ra|tất nhiên|chào mừng|đăng ký|đến với|kênh|hôm nay|vậy là|nào)\b"
    r"|\b(rất|quá|khá|oke|ok)\b",
    re.IGNORECASE,
)
YOU_RE = re.compile(r"\b(bạn|các bạn|của bạn|anh|chị|em|ông|bà|chúng ta)\b", re.IGNORECASE)
STAKE_RE = re.compile(
    r"\b(mất|đánh mất|lỗ|phá sản|thua|rủi ro|nguy hiểm|cảnh báo|đừng|dừng"
    r"|chết|sụp đổ|cạn kiệt|cháy túi|trước khi|không bao giờ)\b",
    re.IGNORECASE,
)
CURIOSITY_RE = re.compile(
    r"\b(tại sao|vì sao|như thế nào|thế nào|cách|điều gì|cái gì|khi nào"
    r"|không ai|ít ai|hóa ra|thực ra|thật sự|bí ẩn|ai)\b",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(r"\b(bởi vì|vì vậy|nghĩa là|do đó|tại vì)\b", re.IGNORECASE)

# Spoken-hook band for this pipeline: scene 1 is 9-28 words at ~3.5 words/s.
MIN_HOOK_WORDS = 9
MAX_HOOK_WORDS = 28


def _words(text: str):
    return re.findall(r"[^\s]+", text)


def _clamp(value: float) -> int:
    return max(0, min(100, int(value)))


def specificity(text: str) -> int:
    w = _words(text)
    if not w:
        return 0
    nums = len(CONCRETE_RE.findall(text))
    vague = len(VAGUE_RE.findall(text))
    filler = len(FILLER_RE.findall(text))
    score = 34 + nums * 22 - vague * 16 - filler * 5
    # Proper nouns that are not sentence-initial read as named things.
    score += min(18, 6 * sum(1 for x in w[1:] if x[:1].isupper()))
    return _clamp(score)


def address(text: str) -> int:
    n = len(YOU_RE.findall(text))
    first = 30 if YOU_RE.search(" ".join(_words(text)[:6])) else 0
    return _clamp(26 + n * 20 + first)


def stakes(text: str) -> int:
    n = len(STAKE_RE.findall(text))
    return _clamp(22 + n * 26 + (14 if CONCRETE_RE.search(text) else 0))


def curiosity(text: str) -> int:
    n = len(CURIOSITY_RE.findall(text))
    q = 18 if text.strip().endswith("?") else 0
    # A hook that resolves itself has no gap left to keep the viewer.
    closed = -18 if CLOSED_RE.search(text) else 0
    return _clamp(24 + n * 17 + q + closed)


def brevity(text: str) -> int:
    n = len(_words(text))
    if n == 0:
        return 0
    if MIN_HOOK_WORDS <= n <= MAX_HOOK_WORDS:
        return 100
    if n < MIN_HOOK_WORDS:
        return _clamp(100 - (MIN_HOOK_WORDS - n) * 11)
    return _clamp(100 - (n - MAX_HOOK_WORDS) * 7)


PROPERTIES = (
    ("SPECIFICITY", specificity),
    ("ADDRESS", address),
    ("STAKES", stakes),
    ("CURIOSITY", curiosity),
    ("BREVITY", brevity),
)


def score_hook(text: str) -> Dict[str, float]:
    """Score the opening hook line; weakest property names where it leaks."""
    parts = {name: fn(text or "") for name, fn in PROPERTIES}
    verdict = round(0.6 * (sum(parts.values()) / len(parts)) + 0.4 * min(parts.values()))
    weakest = min(parts, key=parts.get).lower()
    out = {name.lower(): value for name, value in parts.items()}
    out["verdict"] = verdict
    out["weakest"] = weakest
    return out
