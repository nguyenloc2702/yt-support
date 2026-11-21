"""Prompts + JSON schema cho 2 mode Short (Phụ lục G.1/G.5).

Kịch bản trả về shape (đồng bộ appendix + frontend):
{title, hook, beats[], closing, hashtags{tiktok,facebook,youtube}, description,
 source_note}
Dãy thoại = [hook] + beats + [closing]. Ngân sách từ tính trên toàn dãy thoại.
"""
from __future__ import annotations

import re

from ..config import SHORT_CONFIG

MODES = ("news", "decode", "daily_digest")
LANGS = ("vi", "en")

_MODE_NAME_VI = {
    "news": "Tin nóng",
    "decode": "Giải mã tiền bạc",
    "daily_digest": "Điểm tin nhanh",
}
_MODE_NAME_EN = {
    "news": "Breaking News",
    "decode": "Money Decode",
    "daily_digest": "Daily World Brief",
}

# Chủ đề né cho mảng Tin nóng (bắt buộc theo G.1 — an toàn nền tảng)
_BANNED_TOPIC_NOTE = (
    "KHÔNG chọn và KHÔNG tự chế nội dung về: chính trị, quan hệ ngoại giao, "
    "y tế/cơ thể người, tai nạn máu me, thiên tai đang gây thương vong, "
    "thông tin chưa kiểm chứng về cá nhân cụ thể. Nếu bài báo gốc rơi vào nhóm này, "
    "từ chối bằng trường \"rejected\"."
)

_SYSTEM_NEWS = """Bạn là biên tập viên video tin nhanh quốc tế dạng dọc (TikTok/Reels/Shorts) cho kênh tiền bạc & kinh doanh thế giới tiếng Việt (phong cách TK News / Global Explainer).

NHIỆM VỤ: từ tin vắn quốc tế người dùng dán vào, viết kịch bản short 25-40 giây bằng tiếng Việt theo công thức:
1. hook (0-3s): câu mở giật gân nhất — dẫn số liệu thiệt hại/biến động toàn cầu hoặc câu hỏi đánh thẳng tò mò. KHÔNG mở đầu kiểu "Theo thông tin..."
2. beats (3 beat): diễn biến cốt lõi (ai, ở đâu, cơ chế tác động, con số thực tế), câu ngắn dồn dập, cắt hết râu ria.
3. closing: 1 câu hỏi mở hai chiều để kích bình luận (vd: "Theo bạn đây là bước đi thiên tài hay tự hủy?").

QUY TẮC:
- Ngôn ngữ: tiếng Việt tự nhiên, nói được (không văn viết khô); giữ nguyên tên địa danh/tập đoàn/đơn vị tiền tệ quốc tế rõ ràng.
- Giữ đúng số liệu gốc trong tin; KHÔNG bịa số. Con số phải xuất hiện nguyên văn.
- {banned_topics}
- Nếu tin hợp lệ, KHÔNG điền trường "rejected".
- Kèm `search_keywords_en`: mảng gồm 5 cụm từ tiếng Anh ngắn (2-3 từ mỗi cụm, mô tả vật thể/bối cảnh thật để tìm video b-roll quốc tế tương ứng cho [hook, beat1, beat2, beat3, closing]).

Trả JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"search_keywords_en": ["cargo ship ocean", "shipping port cranes", "..."],
"hashtags": {{"tiktok": [...tối đa 6...], "facebook": [...tối đa 6...], "youtube": [...tối đa 5...]}},
"description": "1-2 câu mô tả đăng bài", "source_note": "Nguồn: <tên nguồn tin quốc tế>"}}"""

# Bản EN cho mảng quốc tế (lang="en") — cùng công thức, giọng TikTok news Anh/Mỹ.
_BANNED_TOPIC_NOTE_EN = (
    "Do NOT pick or invent content about: politics/diplomacy, health or human body, "
    "graphic accidents, ongoing natural disasters with casualties, or unverified claims "
    "about specific individuals. If the source story falls into these groups, refuse "
    'via the "rejected" field.'
)

_SYSTEM_NEWS_EN = """You are an editor for vertical short-form news videos (TikTok/Reels/Shorts) on an international money & business channel.

TASK: from the short news brief the user pastes in, write a 25-40 second script following this formula:
1. hook (0-3s): the most shocking opener — lead with the damage figure / staggering number, or a question that hits curiosity hard. NEVER open with "According to reports...".
2. beats (3 beats): the core developments, short punchy sentences, zero filler.
3. closing: one open two-sided question to trigger comments (e.g. "So — genius move or self-destruction? You decide.").

RULES:
- Language: natural conversational English, spoken not written.
- Keep the original numbers EXACTLY as in the brief; NEVER invent figures. Numbers must appear verbatim.
- {banned_topics}
- If the story is valid, do NOT fill the "rejected" field.

Return JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"hashtags": {{"tiktok": [...max 6...], "facebook": [...max 6...], "youtube": [...max 5...]}},
"description": "1-2 sentence post description", "source_note": "Source: <source name from the brief>"}}"""

_SYSTEM_DECODE_EN = """You are a personal-finance analyst writing a 40-60 second vertical video script in English for a "Money Decode" channel.

TASK: from the user's topic, write a script following this formula:
1. hook: open with a counterintuitive claim or a number that stops the scroll (e.g. "The more you save, the poorer you get — here's why.").
2. beats (2-4 beats): explain the hidden mechanism with everyday examples (coffee, groceries, your phone...). One idea per beat, short, rhythmic.
3. closing: a surprise punchline that loops BACK to the hook, making the viewer want to rewatch.

RULES:
- Natural spoken English. Example numbers must be plausible; NEVER invent crime statistics.
- {banned_topics}

Return JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"hashtags": {{"tiktok": [...], "facebook": [...], "youtube": [...]}},
"description": "...", "source_note": ""}}"""

_SYSTEM_DECODE = """Bạn là nhà phân tích tài chính cá nhân viết kịch bản video dọc 40-60 giây tiếng Việt cho kênh "Giải mã tiền bạc".

NHIỆM VỤ: từ chủ đề người dùng nhập, viết kịch bản theo công thức:
1. hook: mở bằng NGHỊ LÝ ngược đời hoặc con số làm người xem phải dừng (vd: "Càng tiết kiệm, càng nghèo — và đây là lý do").
2. beats (2-4 beat): giải thích cơ chế ngầm bằng ví dụ đời thường (cà phê, chợ, điện thoại...). Mỗi beat 1 ý, ngắn, có nhịp.
3. closing: cú chốt bất ngờ NỐI NGƯỢC lại câu hook (loop) khiến người xem muốn xem lại.

QUY TẮC:
- Tiếng Việt nói tự nhiên. Con số ví dụ phải hợp lý, KHÔNG bịa tỷ lệ phạm pháp.
- {banned_topics}

Trả JSON: {{"title": "...", "hook": "...", "beats": ["...", "...", "..."], "closing": "...",
"hashtags": {{"tiktok": [...], "facebook": [...], "youtube": [...]}},
"description": "...", "source_note": ""}}"""

_SYSTEM_DAILY_DIGEST_EN = """You are the senior producer for "@StickSto · Daily Brief", an English-language rapid-fire international news video (YouTube Shorts / TikTok / Reels, strictly under 3 minutes: target 140-165 seconds).

TASK: From the candidate international news articles (numbered [1], [2], ...), evaluate and select the **12 to 15 HIGHEST-IMPACT, MOST FASCINATING stories** spanning diverse global topics (`WORLD`, `MARKETS`, `TECH`, `SCIENCE`, `SPORTS`).
STRICT QUALITY FILTER:
- REJECT soft fluff, opinion columns, celebrity lifestyle/perfume stories, video game reviews, local council charity pledges, routine conference PR with no concrete news, or vague Q&A features.
- PICK stories with real stakes, major shocks, billion-dollar deals, breakthroughs, geopolitical flashpoints, bans, lawsuits, or dramatic sports upsets.
- Merge duplicates (never pick two articles covering the exact same event) and order them for maximum pacing (biggest geopolitical/market leads -> tech shocks -> science breakthroughs -> sports drama).

STRUCTURE:
1. `hook` (~3s, 10-14 words): Crisp punchy opener announcing the story count, e.g.: "Here are 14 major stories shaking the world today, in under three minutes."
2. `stories` (12-15 items, ~8-11s per story, 18-26 spoken words each):
   - `source_index`: exact 1-based integer index `[k]` from the candidate list.
   - `category`: one of `"WORLD"`, `"MARKETS"`, `"TECH"`, `"SCIENCE"`, `"SPORTS"`.
   - `headline`: crisp scannable 7-12 word tabloid-clear headline for top card.
   - `hero_stat`: ONE high-contrast visual anchor — **STRONGLY PREFER concrete numbers, money, dates, percentages, scores, or dramatic verdict words** (`"$1.24T"`, `"€2.5B"`, `"56-44"`, `"38 YRS"`, `"BANNED"`, `"NOV 19"`, `"6TH TIER"`). Avoid generic country names like `"IRAN"` or `"SUDAN"` when a real number or punchy action exists!
   - `hero_sub`: crisp 4-9 word context sub-line under hero_stat (e.g., `"crude oil tanker hit near Hormuz"`).
   - `narration`: 1-2 tight spoken sentences — **hook immediately with the twist, shock, or consequence** in natural conversational American English (NOT stiff wire-service phrasing). NEVER invent statistics.
   - `PRONUNCIATION RULES FOR NARRATION`: Write for clean Text-to-Speech pronunciation! Spell out currencies/units/quarters (`"two point five billion euros"`, `"one point two four trillion dollars"`, `"third quarter"`, `"15 percent"`), write acronyms cleanly without periods (`"US"`, `"UN"`, `"EU"`, `"UK"` — never `"U.S."`), clarify tricky foreign proper names phonetically only when standard spelling reads awkwardly, and keep compact symbols like `$1.24T` / `€2.5B` strictly in `hero_stat` instead of `narration`.
   - `search_keywords_en`: 2-3 word stock/visual search query for fallback B-roll.
   - `image_queries`: array of 2 concrete visual search queries naming the real people, cities, landmarks, vessels, or companies in THIS story (e.g. `["Strait of Hormuz oil tanker", "crude oil supertanker sea"]`) for photo search.
3. `closing` (~3s, 10-14 words): Clean closer mentioning Stick Sto (written `"Stick Sto"` in `closing` for crisp TTS pronunciation), e.g.: "That's your global brief for today — follow Stick Sto and let's get to work."

SAFETY & FRESHNESS: Keep factual and advertiser-friendly; avoid graphic gore, hate speech, or unverified personal rumors. Geopolitics, summits, diplomacy, economy, science, tech, and sports are WELCOME. If a `RECENTLY AIRED` list is provided, DO NOT repeat those stories unless there is a major new outcome.

Return valid JSON matching the schema with `title`, `hook`, `stories`, `beats` (same narration list), `closing`, `hashtags`, `description`, and `source_note`. Total spoken words across `hook` + all `stories[].narration` + `closing` MUST be between 260 and 390 English words so the finished clip is ~140-165 seconds (strictly under 180 seconds)."""

_SYSTEM_DAILY_DIGEST_VI = """Bạn là biên tập viên bản tin nhanh quốc tế "@StickSto · Điểm Tin" dưới 3 phút. Chọn 12-15 tin đáng chú ý nhất từ danh sách tin ứng viên, không trùng lặp chủ đề, viết câu thoại súc tích kèm category, headline, hero_stat, hero_sub, narration, source_index, search_keywords_en trong mảng `stories`, plus hook và closing nhắc @StickSto."""

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "string"}},
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_index": {"type": "integer"},
                    "category": {"type": "string"},
                    "headline": {"type": "string"},
                    "hero_stat": {"type": "string"},
                    "hero_sub": {"type": "string"},
                    "narration": {"type": "string"},
                    "search_keywords_en": {"type": "string"},
                    "image_queries": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["category", "headline", "hero_stat", "narration"],
            },
        },
        "closing": {"type": "string"},
        "search_keywords_en": {"type": "array", "items": {"type": "string"}},
        "hashtags": {
            "type": "object",
            "properties": {
                "tiktok": {"type": "array", "items": {"type": "string"}},
                "facebook": {"type": "array", "items": {"type": "string"}},
                "youtube": {"type": "array", "items": {"type": "string"}},
            },
        },
        "description": {"type": "string"},
        "source_note": {"type": "string"},
        "rejected": {"type": "string"},
    },
    "required": ["title", "hook", "beats", "closing"],
}


def system_prompt(mode: str, lang: str = "vi") -> str:
    _check_mode(mode)
    _check_lang(lang)
    if lang == "en":
        if mode == "daily_digest":
            return _SYSTEM_DAILY_DIGEST_EN
        tpl = _SYSTEM_NEWS_EN if mode == "news" else _SYSTEM_DECODE_EN
        return tpl.replace("{banned_topics}", _BANNED_TOPIC_NOTE_EN)
    if mode == "daily_digest":
        return _SYSTEM_DAILY_DIGEST_VI
    tpl = _SYSTEM_NEWS if mode == "news" else _SYSTEM_DECODE
    return tpl.replace("{banned_topics}", _BANNED_TOPIC_NOTE)


def user_prompt(mode: str, source_text: str, target_seconds: float | None,
                source_note: str | None, lang: str = "vi") -> str:
    _check_lang(lang)
    cfg = SHORT_CONFIG["modes"][mode]
    if lang == "en":
        lo, hi = _budget_en(mode)
    else:
        lo, hi = cfg["min_words"], cfg["max_words"]
    target = target_seconds or cfg["target_seconds"]
    if lang == "en":
        name = _MODE_NAME_EN[mode]
        note = f"\nSOURCE (shown on video): {source_note.strip()}" if source_note else ""
        return (
            f"TOPIC/NEWS BRIEF (mode {name}, target ~{int(target)}s, "
            f"budget {lo}-{hi} words total):\n"
            f"\"\"\"\n{source_text.strip()}\n\"\"\"{note}\n"
            f"Write the JSON script per the system requirements."
        )
    note = f"\nNGUỒN TIN (ghi trên video): {source_note.strip()}" if source_note else ""
    return (
        f"CHỦ ĐỀ/TIN GỐC (mode {_MODE_NAME_VI[mode]}, mục tiêu ~{int(target)}s, "
        f"ngân sách {lo}-{hi} từ toàn kịch bản):\n"
        f"\"\"\"\n{source_text.strip()}\n\"\"\"{note}\n"
        f"Viết kịch bản JSON theo yêu cầu hệ thống."
    )


def schema(mode: str | None = None) -> dict:
    if mode == "daily_digest":
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "hook": {"type": "string"},
                "stories": _JSON_SCHEMA["properties"]["stories"],
                "beats": {"type": "array", "items": {"type": "string"}},
                "closing": {"type": "string"},
                "hashtags": _JSON_SCHEMA["properties"]["hashtags"],
                "description": {"type": "string"},
                "source_note": {"type": "string"},
            },
            "required": ["title", "hook", "stories", "closing"],
        }
    return _JSON_SCHEMA


def narration_texts(script: dict) -> list[str]:
    """Dãy thoại theo thứ tự phát: hook → beats → closing."""
    texts = [str(script["hook"]).strip()]
    texts += [str(b).strip() for b in script["beats"]]
    texts.append(str(script["closing"]).strip())
    return [t for t in texts if t]


def count_words(script: dict) -> int:
    return sum(len(t.split()) for t in narration_texts(script))


def _clean_stories(raw_stories: list, beats: list[str]) -> list[dict]:
    """Chuẩn hóa metadata từng tin cho preset daily_digest."""
    cleaned: list[dict] = []
    valid_cats = {"WORLD", "MARKETS", "BUSINESS", "TECH", "SCIENCE", "SPORTS", "ENERGY"}
    for idx, item in enumerate(raw_stories or []):
        if not isinstance(item, dict):
            continue
        narr = str(item.get("narration") or "").strip()
        if not narr and idx < len(beats):
            narr = beats[idx]
        if not narr:
            continue
        cat = str(item.get("category") or "WORLD").strip().upper()
        if cat not in valid_cats:
            cat = "WORLD"
        src_idx = item.get("source_index")
        try:
            src_idx = int(src_idx) if src_idx is not None else None
        except (TypeError, ValueError):
            src_idx = None
        cleaned.append({
            "index": len(cleaned) + 1,
            "source_index": src_idx,
            "category": cat,
            "headline": str(item.get("headline") or narr)[:110].strip(),
            "hero_stat": str(item.get("hero_stat") or "").strip()[:26],
            "hero_sub": str(item.get("hero_sub") or "").strip()[:76],
            "narration": narr,
            "search_keywords_en": str(item.get("search_keywords_en") or "").strip()[:60],
            "image_queries": [
                str(q).strip()[:80]
                for q in (item.get("image_queries") or [])
                if str(q).strip()
            ][:3],
        })
    return cleaned


def validate_script(mode: str, script: dict, source_note_fallback: str = "",
                    lang: str = "vi") -> dict:
    """Validate tiếng người (fail-closed). Trả script đã chuẩn hóa + word_count."""
    _check_mode(mode)
    _check_lang(lang)
    has_content = bool((script.get("stories") or []) or (script.get("beats") or []))
    if script.get("rejected") and not (mode == "daily_digest" and has_content):
        raise ValueError(f"Kịch bản bị từ chối vì chủ đề nhạy cảm: {script['rejected']}")
    for field in ("title", "hook", "closing"):
        if not str(script.get(field) or "").strip():
            raise ValueError(f"Kịch bản thiếu trường bắt buộc '{field}' từ LLM.")
    raw_stories = script.get("stories") or []
    beats = [str(b).strip() for b in (script.get("beats") or []) if str(b).strip()]
    if mode == "daily_digest" and raw_stories:
        # Ưu tiên danh sách narration trong stories nếu LLM trả đủ cấu trúc
        story_beats = [
            str(s.get("narration") or "").strip()
            for s in raw_stories
            if isinstance(s, dict) and str(s.get("narration") or "").strip()
        ]
        if story_beats:
            beats = story_beats
    if not beats:
        raise ValueError("Kịch bản không có beat nào từ LLM.")
    max_beats = 15 if mode == "daily_digest" else 4
    if len(beats) > max_beats:
        beats = beats[:max_beats]
    if mode == "daily_digest" and len(beats) < 8:
        raise ValueError(
            f"Daily Digest chỉ có {len(beats)} tin (yêu cầu ít nhất 8–15 tin)."
        )

    if lang == "en":
        lo, hi = _budget_en(mode)
        name = _MODE_NAME_EN[mode]
    else:
        cfg = SHORT_CONFIG["modes"][mode]
        lo, hi = int(cfg["min_words"]), int(cfg["max_words"])
        name = _MODE_NAME_VI[mode]

    stories_clean = _clean_stories(raw_stories, beats)[:len(beats)] if mode == "daily_digest" else []
    kw_list = [str(k).strip() for k in (script.get("search_keywords_en") or []) if str(k).strip()]
    if stories_clean and not kw_list:
        kw_list = [""] + [s.get("search_keywords_en") or s.get("headline") or "" for s in stories_clean] + [""]

    clean = {
        "title": str(script["title"]).strip()[:200],
        "hook": str(script["hook"]).strip(),
        "beats": beats,
        "closing": str(script["closing"]).strip(),
        "search_keywords_en": kw_list,
        "hashtags": _clean_hashtags(script.get("hashtags") or {}),
        "description": str(script.get("description") or "").strip(),
        "source_note": str(script.get("source_note") or "").strip() or source_note_fallback,
    }
    if stories_clean:
        clean["stories"] = stories_clean
    wc = count_words(clean)
    if not (lo <= wc <= hi):
        raise ValueError(
            f"Kịch bản {wc} từ, lệch ngân sách {lo}-{hi} từ cho mode {name}. "
            f"Bấm 'Đổi kịch bản' để gen lại."
        )
    clean["word_count"] = wc
    clean["lang"] = lang
    return clean


def _clean_hashtags(tags: dict) -> dict:
    out = {}
    for platform, limit in (("tiktok", 6), ("facebook", 6), ("youtube", 5)):
        raw = tags.get(platform) or []
        out[platform] = [
            "#" + re.sub(r"[^0-9a-zA-Zàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ_]", "", str(t).lstrip("#"), flags=re.IGNORECASE)
            for t in raw
        ][:limit]
        out[platform] = [t for t in out[platform] if len(t) > 1]
    return out


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode không hợp lệ: {mode} (chấp nhận: {', '.join(MODES)})")


def _check_lang(lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"lang không hợp lệ: {lang} (chấp nhận: {', '.join(LANGS)})")


def _budget_en(mode: str) -> tuple[int, int]:
    """Ngân sách từ cho kịch bản EN — nói chậm hơn VI (~2.45 từ/s) nên giảm ~20%.
    Thiếu key trong config → fallback ngân sách VI (honest, không tự đoán)."""
    cfg = SHORT_CONFIG["modes"][mode]
    lo = cfg.get("min_words_en") or int(cfg["min_words"])
    hi = cfg.get("max_words_en") or int(cfg["max_words"])
    return int(lo), int(hi)
