"""English Script Generator & Normalizer (100% EN Documentary Pipeline).

v2 "Story Architect" engine (implements UNIVERSAL_VIDEO_ALGORITHM v1.0):

- Stage 1 (Story Plan): designs the narrative skeleton from the source material
  — chapter questions, hook spec, open-loop ledger, dialogue skits, metaphor
  table and callback plan.
- Stage 2 (Line Writer): writes narration per chapter with beat types, dual
  voices (narrator / character_2) and mandatory on-screen text, following the
  retention laws (hook formula, humanized numbers, 54/46 short-sentence rule,
  mandatory metaphors, planted & paid-off open loops, callback ending).
- Shot Router: expands every beat into one of the 4 canonical shot prompts
  (scene / quote / crowd / diagram) with per-type camera work and a rotating
  accent colour — replacing the old single generic macro-scene template that
  made every shot look identical.

Deterministic sentence-aware chunking remains the fallback when no LLM
gateway is reachable (or the script is a short demo), so the pipeline always
succeeds reliably.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from typing import Any

from .youtube_ingest import clean_rolling_stutter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------
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
    # A trailing dash makes Edge-TTS return no audio at all
    s = re.sub(r"[\u2013\u2014\s-]+$", "", s).strip()
    if s and s[-1] not in ".!?":
        s += "."
    return s


# ---------------------------------------------------------------------------
# Story engine vocabulary & constants
# ---------------------------------------------------------------------------
BEAT_TYPES = ("hook", "dialog", "explain", "number", "story", "reveal", "callback", "cta")
SHOT_TYPES = ("scene", "quote", "crowd", "diagram")
VOICES = ("narrator", "character_2")
ON_SCREEN_TYPES = ("big_number", "handwritten_label", "stamp", "speech_bubble", "none")

TRANSCRIPT_MAX_CHARS = 24_000      # ~6k words ≈ 20+ minutes of source narration
WORDS_PER_SEC = 2.65               # English documentary narration pace
WORDS_PER_SHOT = 38                # 12-18s macro-shot ≈ 25-45 words
MIN_LLM_SOURCE_WORDS = 80          # below this, deterministic path is better
LLM_TIMEOUT_SEC = 120

# Optional "film angle" block embedded in the source pack: angle, required beats
# and repetition caps. It is engine guidance, NOT narration material, so it is
# stripped from the source text and injected into both story-stage prompts.
_FILM_ANGLE_START = "<!-- FILM ANGLE START -->"
_FILM_ANGLE_END = "<!-- FILM ANGLE END -->"


def extract_film_angle(full_text: str) -> tuple[str | None, str]:
    """Split an optional FILM ANGLE block out of the source text.

    Returns (angle_or_None, cleaned_source_without_the_block)."""
    start = full_text.rfind(_FILM_ANGLE_START)
    if start == -1:
        return None, full_text
    end = full_text.find(_FILM_ANGLE_END, start)
    if end == -1:
        return None, full_text
    angle = full_text[start + len(_FILM_ANGLE_START):end].strip()
    cleaned = (full_text[:start] + full_text[end + len(_FILM_ANGLE_END):]).strip()
    # Strip any leftover HTML-comment markers so they never leak into narration
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL).strip()
    return (angle or None), cleaned

_MONEY_RE = re.compile(
    r"\$\s?\d[\d,.]*\s?(?:billion|million|trillion|thousand)?"
    r"|\d[\d,.]*\s?(?:billion|million|trillion|thousand)\s?(?:dollars)?"
    r"|\d[\d,.]*\s?(?:%|percent)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s*(?:\d{4})?",
    re.IGNORECASE,
)

# Style Lock v3 — Stick-Hero in Epic Cinematic World (shared prefix of every shot prompt)
_STYLE_LOCK = (
    "STYLE LOCKED: Hybrid cinematic editorial illustration (Stick-Hero in Epic Cinematic World), 16:9. "
    "Environment: ultra-detailed, atmospheric semi-realistic 3D cinematic world with dramatic volumetric "
    "lighting, realistic materials and shallow depth of field. "
    "Characters: clean minimalist 2D stick figures (smooth peach round head, bold dark outline, small dot "
    "eyes, thin single-line stick limbs) keeping only 2-3 signature traits (hairstyle, jacket/suit colour, "
    "glasses); crowd characters use plain white round heads with repeated grey oval bodies. "
    "Integration law: characters receive subtle ambient rim-lighting and cast soft grounded shadows so they "
    "feel naturally inside the epic world, never flat pasted stickers. "
    "Clean, uncluttered bottom 20% of the frame reserved for subtitles."
)
_NEGATIVE = (
    "Negative: flat empty white-void background, childish crayon doodle texture, distorted hands with "
    "fingers, photorealistic human anatomy on stick characters, garbled or unreadable text, cluttered HUD "
    "chaos, crowded bottom subtitle zone, fast shaky camera, morphing faces, extra limbs."
)
_ACCENTS = ("crimson red", "glowing amber", "electric cyan")
_SCENE_CAMERAS = ("slow continuous push-in", "gentle orbital pan", "slow tilt down")


# ---------------------------------------------------------------------------
# LLM transport (OpenAI-compatible gateway)
# ---------------------------------------------------------------------------
def _llm_config() -> tuple[str | None, str, str, list[str]]:
    """Resolve the LLM gateway: Xkiro-first (same defaults as the app adapter),
    falling back to OpenAI-compatible envs. Returns (key, base_url, model, fallback_models)."""
    xkiro_key = os.environ.get("XKIRO_API_KEY") or os.environ.get("VIDEO_XKIRO_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    api_key = xkiro_key or openai_key
    default_base = "https://api.xkiro.com/v1" if xkiro_key else "https://api.openai.com/v1"
    default_model = "mistralai/mistral-large-2512" if xkiro_key else "gpt-4o-mini"
    base_url = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("VIDEO_XKIRO_BASE_URL")
        or default_base
    ).rstrip("/")
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("VIDEO_XKIRO_LLM_MODEL")
        or default_model
    )
    fallbacks = [
        m.strip() for m in (os.environ.get("VIDEO_XKIRO_LLM_FALLBACK_MODELS") or "").split(",") if m.strip()
    ] or (["mistralai/mistral-medium-3.5", "mistralai/mistral-small-2603"] if xkiro_key else [])
    return api_key, base_url, model, fallbacks


_JSON_LIST_KEYS = ("shots", "expanded", "chapters", "dialogue_skits", "metaphors", "open_loops")


def _extract_json_block(content: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM reply (tolerates ``` fences).

    When the outer object is truncated (e.g. max_tokens cut the reply mid-array),
    salvage every complete object from the known list keys and re-wrap them."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        m = re.search(r"\{.*\}", content, flags=re.DOTALL)
        candidate = m.group(0) if m else None
    if candidate is not None:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Salvage pass: decode objects one-by-one after "key": [ — tolerant of a
    # reply that got cut off mid-array by max_tokens.
    dec = json.JSONDecoder()
    key_m = re.search(r'"(\w+)"\s*:\s*\[', content)
    if key_m and key_m.group(1) in _JSON_LIST_KEYS:
        idx = key_m.end()
        items: list[dict[str, Any]] = []
        while idx < len(content):
            while idx < len(content) and content[idx] in " \t\r\n,":
                idx += 1
            if idx >= len(content) or content[idx] != "{":
                break
            try:
                obj, idx = dec.raw_decode(content, idx)
            except json.JSONDecodeError:
                break
            if isinstance(obj, dict):
                items.append(obj)
        if items:
            return {key_m.group(1): items}
    return None


def _llm_chat_json(
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any] | None:
    """Call the configured OpenAI-compatible gateway and return a parsed JSON object.

    Tries the primary model, then up to two fallback models on any failure."""
    api_key, base_url, model, fallbacks = _llm_config()
    if not api_key or os.environ.get("VIDEO_DISABLE_LLM"):
        return None
    last_error: str | None = None
    for candidate in [model] + fallbacks[:2]:
        body = json.dumps({
            "model": candidate,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
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
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SEC) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
            parsed = _extract_json_block(content)
            if parsed is not None:
                return parsed
            last_error = f"unparseable JSON reply ({len(content)} chars, finish_reason={data['choices'][0].get('finish_reason')})"
            logger.warning("JSON parse failed (model=%s): %s", candidate, last_error)
        except Exception as exc:
            last_error = str(exc)
    logger.warning("LLM chat failed on all models: %s", last_error)
    return None


# ---------------------------------------------------------------------------
# Stage 1 — Story Architect (narrative skeleton)
# ---------------------------------------------------------------------------
_STORY_ARCHITECT_SYSTEM = (
    "You are the STORY ARCHITECT of a top-1% YouTube documentary channel in the style of "
    "'Paper Billions': ruthless narrative design, shocking numbers converted to human scale, "
    "everyday-object metaphors for abstract concepts, and short two-voice comedy dialogue skits. "
    "You design skeletons, you do not write prose. You output JSON only."
)


def build_story_plan(
    full_text: str,
    target_words: int,
    target_shots: int,
    film_angle: str | None = None,
) -> dict[str, Any] | None:
    """Stage 1: design chapters, hook, open loops, skits, metaphors and callback."""
    n_chapters = 5 if target_shots >= 30 else 4
    angle_block = (
        f"""
FILM ANGLE & REQUIRED BEATS (higher priority than any other instinct — design the chapters around these beats):
{film_angle}
"""
        if film_angle else ""
    )
    user = f"""{angle_block}SOURCE MATERIAL:
\"\"\"{full_text}\"\"\"

Design the narrative skeleton of a documentary of ~{target_words} words (~{target_shots} shots).

Hard rules:
- Design {n_chapters} chapters. Each chapter TITLE must be a question the viewer needs answered.
- Chapter 1 role is "hook": it opens on the single most shocking fact, converted to a human-scale rate.
- Give roles from: hook, setup, escalation, twist, villain, payoff. The final chapter answers the hook question directly.
- Plant 2-4 open loops. Every loop planted early must be paid off in a specific later chapter (note the payoff chapter).
- Plan 4-8 DIALOGUE SKITS (two-voice mini-scenes: a naive worker/investor/reporter asking questions, an executive or expert answering, ending on a comedic or chilling punchline). Spread them roughly every 90-120 seconds of runtime.
- For every abstract concept that matters, plan one everyday-object metaphor (like "compute = rent for a brain", "equity promise = a lemonade-stand IOU").
- Plan how the ending calls back the opening hook and throws a question at the viewer.

Return ONLY valid JSON with exactly these keys:
{{"title": "...", "logline": "...",
 "chapters": [{{"id": 1, "title": "...", "role": "hook", "target_words": 400}}, ...],
 "hook": {{"shocking_fact": "...", "human_rate_conversion": "...", "paradox_reaction": "..."}},
 "open_loops": [{{"plant_chapter": 1, "payoff_chapter": 4, "question": "..."}}, ...],
 "dialogue_skits": [{{"chapter": 1, "setup": "...", "exchange": ["line for narrator", "line for character"], "punchline": "..."}}, ...],
 "metaphors": [{{"concept": "...", "metaphor": "..."}}, ...],
 "callback_plan": "..."}}"""
    plan = _llm_chat_json(_STORY_ARCHITECT_SYSTEM, user, temperature=0.7, max_tokens=2600)
    if not isinstance(plan, dict) or not plan.get("chapters"):
        return None
    return plan


# ---------------------------------------------------------------------------
# Stage 2 — Line Writer (narration per beat)
# ---------------------------------------------------------------------------
_LINE_WRITER_SYSTEM = (
    "You are the head scriptwriter of the same top-1% documentary channel. You write narration "
    "that follows the story plan EXACTLY and obeys every retention law. You output JSON only."
)


def _writer_user_prompt(
    full_text: str,
    plan: dict[str, Any],
    chapters_json: str,
    chapter_shots_target: int,
    feedback: str | None = None,
    film_angle: str | None = None,
) -> str:
    feedback_block = (
        f"\nCRITICAL FEEDBACK ON YOUR PREVIOUS DRAFT — FIX IT NOW:\n{feedback}\n"
        if feedback else ""
    )
    angle_block = (
        f"""
FILM ANGLE & REQUIRED BEATS (obey — the beats listed here MUST all appear in the film; respect the repetition caps):
{film_angle}
"""
        if film_angle else ""
    )
    return f"""{feedback_block}STORY PLAN (obey exactly):
{json.dumps(plan, ensure_ascii=False)[:3500]}
{angle_block}

CHAPTERS TO WRITE NOW:
{chapters_json}

SOURCE MATERIAL (the only source of facts; compress freely, never invent big numbers that are not supported here):
\"\"\"{full_text}\"\"\"

Retention laws — ALL mandatory, violating any one ruins the video:
1. LENGTH LAW (the one writers break most — obey): write EXACTLY ~{chapter_shots_target} shots in this batch. Every single shot narration MUST be 30-45 words (3-5 short sentences = 12-18 seconds of speech). Punchy is good, but a shot under 30 words FAILS. Example of a passing shot: "Anthropic lost eighty billion dollars in six months. That is about eighteen million dollars an hour, nights included. Wall Street looked at the burn and the growth, and answered anyway. We want in."
2. The very first shot of chapter "hook" is the HOOK: shocking number + converted human rate + paradox reaction. NEVER open with "Today we...", "In this video...", or by introducing the topic.
3. Numbers: NEVER leave a big number alone. Every large number must be converted to a human scale within the same shot ("$80 billion — about 18 million dollars an hour, nights included").
4. Sentences: at least 50% of sentences must be 6 words or fewer. Fragments allowed. One idea per sentence. No filler.
5. Dialogue skits: render each planned skit as 2-4 consecutive shots. Each shot carries ONE spoken line. Character lines get voice "character_2"; narrator lines get voice "narrator". Narration text must be the spoken line only — no speaker labels, no markdown.
6. Metaphors: whenever a planned concept appears, use its everyday-object metaphor.
7. Open loops: when planting, signpost clearly ("Hold on to that. It comes back."). When paying off, reference the earlier moment ("Remember the IOUs?").
8. on_screen_text is REQUIRED for beat_type number, quote and reveal: the exact big number, date or verdict to burn into the frame (max 4 words, like "$80 BILLION", "FEBRUARY 20", "NO"). Pick on_screen_type: big_number for numbers, handwritten_label for dates/labels, stamp for verdicts, speech_bubble for dialogue lines, none otherwise.
9. beat_type must be one of: hook, dialog, explain, number, story, reveal, callback, cta.
10. shot_type must be one of: scene (places & action), quote (someone speaks / a line lands), crowd (meetings, many people), diagram (numbers, charts, comparisons). Map: number->diagram, dialog->quote or crowd, reveal/callback->scene.
11. visual_brief: one concrete sentence — WHERE the scene happens, WHO does WHAT, WHICH PROPS are visible (calendar, sign, stamp, screen, machine). Be literal: the keyframe is generated from this sentence.
12. The last shot of the final chapter: answer the hook question, then end with a direct question aimed at the viewer (beat_type "cta").

Return ONLY valid JSON:
{{"shots": [{{"index": 1, "chapter": 1, "beat_type": "hook", "voice": "narrator", "narration": "...", "on_screen_text": "$80 BILLION", "on_screen_type": "big_number", "shot_type": "diagram", "visual_brief": "..."}}, ...]}}"""


def _chapter_batches(plan: dict[str, Any], target_shots: int) -> list[list[dict[str, Any]]] | None:
    chapters = [c for c in (plan.get("chapters") or []) if isinstance(c, dict)]
    if not chapters:
        return None
    if target_shots <= 48 or len(chapters) <= 1:
        return [chapters]
    # Long scripts: one call per chapter keeps each JSON payload safe.
    return [[c] for c in chapters]


def _batch_shot_target(plan: dict[str, Any], batch: list[dict[str, Any]], target_shots: int) -> int:
    chapters = [c for c in (plan.get("chapters") or []) if isinstance(c, dict)]
    if not chapters or len(batch) == len(chapters):
        return target_shots
    try:
        idx = next(i for i, c in enumerate(chapters) if c is batch[0])
    except StopIteration:
        return max(4, target_shots // len(chapters))
    words = [float(c.get("target_words") or 0) for c in chapters]
    total_w = sum(words)
    if total_w <= 0:
        return max(4, int(round(target_shots / len(chapters))))
    share = words[idx] if words[idx] > 0 else total_w / len(chapters)
    return max(4, int(round(target_shots * share / total_w)))


def write_script_shots(
    full_text: str,
    plan: dict[str, Any],
    target_shots: int,
    film_angle: str | None = None,
) -> list[dict[str, Any]] | None:
    """Stage 2: write raw LLM shots following the plan. None = any batch failed.

    Each batch gets ONE expansion retry when the draft comes back far too short
    (the most common writer failure mode: punchy 8-12 word shots instead of
    30-45 word macro-shots)."""
    batches = _chapter_batches(plan, target_shots)
    if batches is None:
        return None
    all_raw: list[dict[str, Any]] = []
    for batch in batches:
        batch_target = _batch_shot_target(plan, batch, target_shots)
        batch_raw: list[dict[str, Any]] | None = None
        feedback: str | None = None
        for attempt in range(2):
            data = _llm_chat_json(
                _LINE_WRITER_SYSTEM,
                _writer_user_prompt(
                    full_text,
                    plan,
                    json.dumps(batch, ensure_ascii=False),
                    batch_target,
                    feedback,
                    film_angle,
                ),
                temperature=0.75,
                max_tokens=min(16000, max(2500, batch_target * 200 + 1200)),
            )
            shots = data.get("shots") if isinstance(data, dict) else None
            if not isinstance(shots, list) or not shots:
                return None
            batch_raw = shots
            words = sum(len(str(s.get("narration") or "").split()) for s in shots)
            if attempt == 0 and words < batch_target * 24:
                feedback = (
                    f"You wrote ~{words} words across {len(shots)} shots "
                    f"(~{words // max(1, len(shots))} words per shot) for this batch. "
                    f"The LENGTH LAW demands ~{batch_target} shots of 30-45 words each "
                    f"(~{batch_target * 37} words total). Rewrite the ENTIRE batch expanded, "
                    f"keeping the same beats, voices and on-screen text."
                )
                continue
            break
        if batch_raw is None:
            return None
        all_raw.extend(batch_raw)
    return all_raw or None


# ---------------------------------------------------------------------------
# Shot normalization (LLM output -> canonical shot dicts)
# ---------------------------------------------------------------------------
def _sanitize_on_screen_text(text: Any) -> str | None:
    s = re.sub(r"[\"“”]", "'", str(text or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:40] or None


def _looks_like_spoken_line(narration: str) -> bool:
    return bool(re.match(r"^[\"“]", narration) or re.match(r"^[-—]\s", narration) or re.search(r"\?\"|!\"", narration))


def _infer_beat(narration: str, pos: int, total: int) -> str:
    # Content signals win over position: a spoken line or a big money figure
    # must route to its dedicated shot type regardless of where it lands.
    if _looks_like_spoken_line(narration):
        return "dialog"
    if _MONEY_RE.search(narration):
        return "number"
    if pos == 1:
        return "hook"
    if pos == total:
        return "cta"
    return "story"


def _infer_on_screen(narration: str) -> tuple[str | None, str | None]:
    m = _MONEY_RE.search(narration)
    if m:
        return _sanitize_on_screen_text(m.group(0)), "big_number"
    d = _DATE_RE.search(narration)
    if d:
        return _sanitize_on_screen_text(d.group(0)), "handwritten_label"
    q = re.search(r"\"([^\"]{4,60})\"", narration)
    if q:
        return _sanitize_on_screen_text(q.group(1)), "speech_bubble"
    return None, None


def _normalize_llm_shot(s: dict[str, Any], pos: int, total: int) -> dict[str, Any] | None:
    narr = normalize_english_text(str(s.get("narration") or s.get("text") or ""))
    if not narr:
        return None
    beat = str(s.get("beat_type") or "").strip().lower()
    if beat not in BEAT_TYPES:
        beat = _infer_beat(narr, pos, total)
    voice = str(s.get("voice") or "").strip().lower()
    if voice not in VOICES:
        voice = "character_2" if beat == "dialog" and _looks_like_spoken_line(narr) else "narrator"
    shot_type = str(s.get("shot_type") or "").strip().lower()
    if shot_type not in SHOT_TYPES:
        shot_type = "diagram" if beat == "number" else ("quote" if beat == "dialog" else "scene")
    if beat == "number":
        shot_type = "diagram"
    ost = _sanitize_on_screen_text(s.get("on_screen_text"))
    ost_type = str(s.get("on_screen_type") or "").strip().lower()
    if ost_type not in ON_SCREEN_TYPES:
        ost_type = None
    if ost is None and beat in {"number", "quote", "reveal"}:
        ost, inferred_type = _infer_on_screen(narr)
        ost_type = ost_type or inferred_type
    if ost is not None and ost_type is None:
        ost_type = "big_number" if beat == "number" else "handwritten_label"
    if ost is None:
        ost_type = "none"
    brief = re.sub(r"\s+", " ", str(s.get("visual_brief") or "")).strip()
    chapter_raw = s.get("chapter")
    try:
        chapter = int(chapter_raw) if chapter_raw is not None else None
    except (TypeError, ValueError):
        chapter = None
    return {
        "index": pos,
        "shot_type": shot_type,
        "narration": narr,
        "beat_type": beat,
        "voice": voice,
        "chapter": chapter,
        "on_screen_text": ost,
        "on_screen_type": ost_type,
        "visual_brief": brief,
    }


def normalize_llm_shots(raw_shots: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in raw_shots:
        if not isinstance(s, dict):
            continue
        normalized = _normalize_llm_shot(s, len(out) + 1, len(raw_shots))
        if normalized:
            out.append(normalized)
    out = merge_undersized_shots(out)
    for i, s in enumerate(out, start=1):
        s["index"] = i
    return out


def merge_undersized_shots(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive same-voice shots that are too short for a 12-18s macro-shot.

    Structural beats (hook / dialog / cta / reveal) stay standalone on purpose:
    hook and cta live on pacing, and dialog lines each need their own voice."""
    merged: list[dict[str, Any]] = []
    for s in shots:
        prev = merged[-1] if merged else None
        if prev is not None and _can_merge(prev, s):
            prev["narration"] = prev["narration"].rstrip() + " " + s["narration"]
            if not prev.get("on_screen_text") and s.get("on_screen_text"):
                prev["on_screen_text"] = s.get("on_screen_text")
                prev["on_screen_type"] = s.get("on_screen_type")
            prev["visual_brief"] = (prev.get("visual_brief") or "") or (s.get("visual_brief") or "")
            continue
        merged.append(s)
    return merged


def _can_merge(prev: dict[str, Any], s: dict[str, Any]) -> bool:
    prev_words = len(prev["narration"].split())
    words = len(s["narration"].split())
    structural = {"hook", "dialog", "cta", "reveal"}
    return (
        prev.get("voice") == s.get("voice")
        and prev.get("chapter") == s.get("chapter")
        and prev.get("beat_type") not in structural
        and s.get("beat_type") not in structural
        and prev_words < 28
        and prev_words + words <= 48
    )


def _expand_shots(
    full_text: str,
    plan: dict[str, Any],
    shots: list[dict[str, Any]],
    words_target: int,
) -> list[dict[str, Any]]:
    """One bounded LLM pass that expands under-length narration shots.

    The writer models chronically produce punchy 10-20 word shots; this pass
    stretches every short narrator shot to the 30-45 word macro-shot law using
    source facts, leaving short dialogue punchlines untouched."""
    total_words = sum(len(s["narration"].split()) for s in shots)
    if total_words >= int(words_target * 0.8):
        return shots
    payload = [
        {
            "index": s["index"],
            "beat_type": s.get("beat_type"),
            "narration": s["narration"],
        }
        for s in shots
        if s.get("voice") != "character_2" and len(s["narration"].split()) < 30
    ]
    if not payload:
        return shots
    user = f"""STORY PLAN:
{json.dumps(plan, ensure_ascii=False)[:2000]}

SOURCE MATERIAL:
\"\"\"{full_text[:TRANSCRIPT_MAX_CHARS]}\"\"\"

The script below is TOO SHORT ({total_words} words written, ~{words_target} words required).
Expand ONLY the listed short shots: grow each narration to 30-45 words (3-5 short sentences)
using concrete detail from the source material — numbers stay humanized, facts stay supported.
Do NOT change beats, ordering or the word counts of shots that are not listed. Keep dialogue punchlines short.

SHORT SHOTS TO EXPAND:
{json.dumps(payload, ensure_ascii=False)[:9000]}

Return ONLY valid JSON: {{"expanded": [{{"index": 7, "narration": "..."}}, ...]}}"""
    data = _llm_chat_json(_LINE_WRITER_SYSTEM, user, temperature=0.6, max_tokens=8000)
    expanded = data.get("expanded") if isinstance(data, dict) else None
    if not isinstance(expanded, list):
        return shots
    by_idx: dict[int, str] = {}
    for e in expanded:
        if isinstance(e, dict) and isinstance(e.get("narration"), str):
            try:
                by_idx[int(e.get("index"))] = e["narration"]
            except (TypeError, ValueError):
                continue
    for s in shots:
        new_narr = by_idx.get(s["index"])
        if new_narr and len(new_narr.split()) > len(s["narration"].split()):
            s["narration"] = normalize_english_text(new_narr)
            s["visual_brief"] = ""  # narration changed; router re-derives the brief
    return shots


# ---------------------------------------------------------------------------
# Shot Router — one of 4 canonical shot prompts per beat (never one template)
# ---------------------------------------------------------------------------
def _brief_from_narration(narration: str) -> str:
    clean = re.sub(r"\[(?:music|applause|laughter|sound|sfx)[^\]]*\]", "", narration, flags=re.IGNORECASE)
    clean = re.sub(r"[^a-zA-Z0-9 ,.'$%\"-]", "", clean).strip()
    return clean[:200] or "a single striking moment of the story"


def _bubble_text(shot: dict[str, Any]) -> str:
    """Pick the punchiest readable line for a speech-bubble shot (first sentence wins)."""
    ost = shot.get("on_screen_text")
    if ost:
        return str(ost)
    narr = str(shot.get("narration") or "")
    first = re.split(r"(?<=[.!?])\s+", narr)[0].strip()
    if len(first) > 60:
        first = first[:57].rstrip() + "..."
    return first or narr[:60]


def build_visual_prompt(shot: dict[str, Any], index: int, total: int) -> str:
    """Expand a beat into a full keyframe/shot prompt using the 4-type framework.

    Every shot type gets its own composition law, camera behaviour and accent
    handling — this variety is what keeps the video visually alive.
    """
    st = str(shot.get("shot_type") or "scene").lower()
    beat = str(shot.get("beat_type") or "story").lower()
    brief = str(shot.get("visual_brief") or "").strip() or _brief_from_narration(str(shot.get("narration") or ""))
    ost = shot.get("on_screen_text")
    accent = _ACCENTS[(index - 1) % len(_ACCENTS)]
    camera = _SCENE_CAMERAS[(index - 1) % len(_SCENE_CAMERAS)]
    ost_line = f' with large on-screen text "{ost}"' if ost else ""

    blocks = [_STYLE_LOCK]

    if st == "diagram":
        blocks.append(
            f"Diagram shot: {brief}. "
            "Layout: two flat bands split by a thick black horizontal line, reading left to right; "
            "oversized objects drawn with silhouette-first, only 3-4 details inside; "
            f'a large crimson handwritten "{ost or "KEY NUMBER"}" is the single focal accent{ost_line}. '
            "Camera: static camera; numbers count up, bars grow, a red stamp slams down; final frame holds still so the number can be read."
        )
    elif st == "quote":
        quoted = _bubble_text(shot)
        blocks.append(
            f"Quote/dialogue shot: {brief}. "
            f'The line "{quoted}" appears as a large white cloud speech/thought bubble with a thick black outline '
            "(or handwritten caption) — the text is the FIRST focal point, the speaker's face is the second. "
            "Any crowd keeps flat unchanged expressions. "
            "Camera: static camera; the words appear gradually, final frame holds still so the line can be read."
        )
    elif st == "crowd":
        blocks.append(
            f"Crowd/meeting shot: {brief}. "
            "Composition: 3 layers — foreground elements cut off by the frame edge, the peach-skin main "
            "character midground as the ONLY detailed figure, a repeating array of plain white-faced crowd "
            f"characters fading into the background. Accent colour {accent} reserved for one focal object. "
            "Camera: static camera; the main character changes expression slightly, the crowd stays nearly still."
        )
    else:
        mood = {
            "hook": "shocking, high-stakes mood",
            "reveal": "dark ominous mood, everything dim except one glowing focal object",
            "callback": "echoing composition of the opening scene, resolution mood",
            "cta": "direct-address mood, main character looks toward the viewer",
        }.get(beat, "immersive cinematic mood")
        blocks.append(
            f"Scene shot: {brief}. "
            "Composition: 3 layers — a large foreground element cut off by the frame edge, the main "
            "character midground performing the action, a repeating array of objects fading in the background. "
            f"Accent colour {accent} reserved only for the focal object or number{ost_line}. "
            f"Mood: {mood}. "
            f"Camera: {camera}, one continuous smooth movement, one clear focal point first, second point second."
        )

    blocks.append(_NEGATIVE)
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Deterministic fallback chunking (no LLM) — public behaviour unchanged
# ---------------------------------------------------------------------------
def split_script_text_into_shots(
    script_text: str,
    target_words_per_shot: int = 38,
) -> list[dict[str, Any]]:
    """Split raw English script text into macro-shots (~12-18s / 35-45 words per shot).

    Supports either:
    - Paragraph/line-separated scenes (each non-empty line or paragraph is a macro-scene,
      optionally formatted as `Narration || Visual Prompt`), OR
    - Continuous prose (automatically grouped on sentence boundaries into 12-18s macro-shots,
      while short <65-word demo scripts still split cleanly into >= 2 shots).

    Every shot is routed through the 4-type shot framework (scene / quote / crowd /
    diagram) with heuristic beat classification, so even the no-LLM fallback never
    repeats one identical visual template.
    """
    raw_lines = [ln.strip() for ln in script_text.strip().splitlines() if ln.strip()]
    if not raw_lines:
        raise ValueError("script_text is empty")

    total_words_all = len(script_text.split())
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
            if len(raw_lines) > 1 and len(words) <= 50:
                raw_items.append((ln_clean, None))
            elif len(sentences) <= 1:
                raw_items.append((ln_clean, None))
            else:
                buf: list[str] = []
                buf_words = 0
                if len(raw_lines) == 1 and total_words_all < 70:
                    max_w = 18
                else:
                    max_w = max(28, int(target_words_per_shot))
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
        beat = _infer_beat(norm_narr, idx, total)
        voice = "character_2" if beat == "dialog" and _looks_like_spoken_line(norm_narr) else "narrator"
        ost, ost_type = _infer_on_screen(norm_narr)
        shot_type = "diagram" if beat == "number" else ("quote" if beat == "dialog" else "scene")
        shot = {
            "index": idx,
            "shot_type": shot_type,
            "narration": norm_narr,
            "beat_type": beat,
            "voice": voice,
            "on_screen_text": ost,
            "on_screen_type": ost_type or ("none" if ost is None else ost_type),
            "visual_brief": "",
        }
        prompt = vprompt or build_visual_prompt(shot, idx, total)
        shot["visual_prompt"] = prompt
        shots.append(shot)
    return shots


# ---------------------------------------------------------------------------
# Public entry point — YouTube transcript / source text -> Story Architect shots
# ---------------------------------------------------------------------------
def build_en_shots_from_transcript_segments(
    segments: list[dict],
    target_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Turn source material (e.g. YouTube transcript segments) into a fully
    structured English documentary script.

    With an LLM gateway configured this runs the two-stage Story Architect:
    Stage 1 designs chapters/hook/open-loops/skits/metaphors, Stage 2 writes
    per-beat narration with dual voices and on-screen text, and the shot router
    expands each beat into one of the 4 canonical shot prompts.

    Without an LLM (no API key, VIDEO_DISABLE_LLM, unreachable gateway, or a
    short demo transcript) it falls back to deterministic sentence-aware
    documentary chunking so the pipeline always succeeds reliably.
    """
    full_text = " ".join(str(s.get("text", "")).strip() for s in segments if s.get("text"))
    full_text = clean_rolling_stutter(full_text)
    if not full_text:
        raise ValueError("Transcript segments contain no text")

    # Pull out the optional FILM ANGLE block (engine guidance, not narration material)
    film_angle, full_text = extract_film_angle(full_text)

    # Estimate target word count (~2.65 words/sec for English documentary narration)
    words = full_text.split()
    if target_duration_sec and target_duration_sec > 0:
        max_words = max(30, int(round(target_duration_sec * WORDS_PER_SEC)))
        if len(words) > max_words:
            full_text = " ".join(words[:max_words])
            words = full_text.split()

    full_text = full_text[:TRANSCRIPT_MAX_CHARS]
    source_words = len(words)

    api_key = os.environ.get("XKIRO_API_KEY") or os.environ.get("OPENAI_API_KEY")
    use_llm = bool(api_key) and not os.environ.get("VIDEO_DISABLE_LLM") and source_words >= MIN_LLM_SOURCE_WORDS

    if use_llm:
        try:
            words_target = (
                int(round(target_duration_sec * WORDS_PER_SEC))
                if target_duration_sec and target_duration_sec > 0
                else source_words
            )
            target_shots = max(8, min(80, int(round(words_target / WORDS_PER_SHOT))))

            plan = build_story_plan(full_text, words_target, target_shots, film_angle)
            if plan:
                raw_shots = write_script_shots(full_text, plan, target_shots, film_angle)
                if raw_shots:
                    shots = normalize_llm_shots(raw_shots)
                    if len(shots) >= 4:
                        shots = _expand_shots(full_text, plan, shots, words_target)
                        total = len(shots)
                        for i, s in enumerate(shots, start=1):
                            s["visual_prompt"] = build_visual_prompt(s, i, total)
                        return shots
        except Exception:
            # Fall through to deterministic segmentation if the LLM gateway fails
            pass

    return split_script_text_into_shots(full_text)
