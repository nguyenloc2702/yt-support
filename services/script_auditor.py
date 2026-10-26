"""Script Auditor & Storytelling Engine for Remake Mode (Mode 2).

Provides:
1. Dual-script generation (English & Vietnamese) with documentary pacing.
2. Custom script parsing & strict auditing (pacing, scene count, Veo safety, language detection).
3. Detailed human-friendly feedback for Telegram when custom scripts need revision.
4. Automatic voice recommendation matching script language (prioritizing Male voices: Nam Minh / Guy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from math import ceil, floor
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from services.algorithm_qa import SCENE_DUR_RANGE
from services.prompt_sanitizer import (
    clean_narration_for_tts,
    sanitize_veo_prompt,
    SENSITIVE_PATTERNS,
)

logger = logging.getLogger("script_auditor")

# Target pacing for 10-second Veo shots (Legacy Mode)
MIN_WORDS_PER_SHOT = 6
RECOMMENDED_WORDS_PER_SHOT = (14, 25)
MAX_WORDS_PER_SHOT = 28
DEFAULT_TARGET_SHOTS = 24

# Algorithm Mode constants (UNIVERSAL_VIDEO_ALGORITHM)
# The pacing law, in one place: a rate, a duration window owned by the QA
# scorecard, and the word window that follows from the two. Scene duration is
# always words / SCENE_WORDS_PER_SECOND, so no stage can plan against a
# different rate than the one that grades it.
SCENE_WORDS_PER_SECOND = 3.5
MIN_SHOT_DURATION_ALGORITHM = SCENE_DUR_RANGE[0]
MAX_SHOT_DURATION_ALGORITHM = SCENE_DUR_RANGE[1]
GENERATED_WORD_WINDOW = (
    ceil(MIN_SHOT_DURATION_ALGORITHM * SCENE_WORDS_PER_SECOND),
    floor(MAX_SHOT_DURATION_ALGORITHM * SCENE_WORDS_PER_SECOND),
)
# Custom scripts keep the historical 12-word floor; generated scripts are steered
# toward RECOMMENDED_WORDS_PER_SHOT_ALGORITHM but only gated on GENERATED_WORD_WINDOW.
MIN_WORDS_PER_SHOT_ALGORITHM = 12
# The band every prompt (and the owner-facing Gate 1 copy) asks the writer for:
# 17-21 words = 4.9-6.0s. The top edge has to sit *inside* QA S10's 6.5s mean
# ceiling — the old 18-24 band ended at 6.9s and a live run that followed it
# landed at 6.68s mean, i.e. the steering was pushing the script out of the
# band that grades it. The enforced window stays GENERATED_WORD_WINDOW.
RECOMMENDED_WORDS_PER_SHOT_ALGORITHM = (17, 21)
MAX_WORDS_PER_SHOT_ALGORITHM = GENERATED_WORD_WINDOW[1]
# EN lines follow the EN voice pace, not the VI one: documentary EN reads
# ~160 wpm (edge) so a 4-8s scene carries 12-22 words. Capping EN at the old
# 16 left every line 60% the length of its VI twin and the EN audio gasping
# between sentences. Shared by the slicer prompt and the factory's rewrite.
EN_MAX_WORDS_ALGORITHM = 22
# EN instruction floor: shorter lines leave the EN voice gasping between
# sentences (the dead air the owner rejected in job 7fb1a8).
EN_MIN_WORDS = 12
TARGET_SHOT_DURATION_ALGORITHM = 6.0  # seconds (4-8s scenes at ~3.5 words/s)
# Share of scenes allowed outside the word window. The factory gate and the
# Gate 1 audit must agree, or a script the factory accepted gets rejected one
# step later for the same outlier.
BUDGET_RATIO_FLOOR = 0.95

# Repetition law (Slice 0 / S0.2). The judge rubric bans reusing one metaphor
# more than three times, but a 130-scene script read as one block hides it: the
# live run shipped "tương lai" x16 and "thất bại" x11 and scored 7.9. Counted in
# code, per scene, so a chunk and a whole script use the same yardstick. These
# are the only repetition numbers anywhere in the pipeline.
REPEAT_WARN_SCENES = 4   # worth a repair pass
REPEAT_FAIL_SCENES = 6   # QA S18 red line
# Revision acceptance (S0.1). A revision that kills half the repetition is worth
# keeping even when the judge's float ticks down slightly — the old
# `score_after >= score_before` rule reverted exactly such a revision on job
# 986f70 and shipped the repetitive script instead.
REVISION_REPEAT_DROP_FLOOR = 0.5
REVISION_JUDGE_TOLERANCE = 0.3

# Voice mapping defaults. The owner auditioned both languages and picked the
# free edge voices (2026-10-24): Nam Minh for VI (sample C), Christopher for
# EN. xKiro voices remain available through the Gate 1 audition (/voice).
VOICE_MAP = {
    "vi": {
        "male": "vi-VN-NamMinhNeural",
        "female": "vi-VN-HoaiMyNeural",
        "default": "vi-VN-NamMinhNeural",
    },
    "en": {
        "male": "en-US-ChristopherNeural",
        "female": "en-US-JennyNeural",
        "default": "en-US-ChristopherNeural",
    },
}


def _voice_display_name(voice: str) -> str:
    """Human-readable voice label for Telegram feedback."""
    v = (voice or "").lower()
    if "vietnamese" in v:
        if "confident-male" in v:
            return "Nam Dõng Dạc (xKiro VN)"
        if "sweet-female" in v:
            return "Nữ Ngọt Ngào (xKiro VN)"
        if "gentle-female" in v:
            return "Nữ Nhẹ Nhàng (xKiro VN)"
        return "Giọng Việt (xKiro)"
    if v in ("male-voice", "deep-male", "expressive-male"):
        return "Nam Documentary (xKiro EN)"
    if v == "female-voice":
        return "Nữ Documentary (xKiro EN)"
    if "NamMinh" in voice:
        return "Nam Minh (Nam, edge-tts)"
    if "Christopher" in voice:
        return "Christopher trầm (Nam EN, edge-tts)"
    if "Guy" in voice:
        return "Guy (Nam EN, edge-tts)"
    if "HoaiMy" in voice:
        return "Hoài My (Nữ, edge-tts)"
    if "Jenny" in voice:
        return "Jenny (Nữ EN, edge-tts)"
    return f"{voice} (edge-tts)"


def detect_language(text: str) -> str:
    """Detects whether text is primarily Vietnamese ('vi') or English ('en')."""
    if not text:
        return "vi"

    # Distinctive Vietnamese diacritics
    vn_diacritics = re.findall(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", text, re.IGNORECASE)
    if len(vn_diacritics) >= 2:
        return "vi"

    # Common Vietnamese word markers
    vn_keywords = {"và", "của", "trong", "là", "những", "các", "với", "người", "khi", "đã", "được", "cho", "này", "đó"}
    words = [w.lower().strip(" ,.-!?;:") for w in text.split()]
    vn_count = sum(1 for w in words if w in vn_keywords)

    if vn_count >= 2:
        return "vi"

    return "en"


# Function words for the repetition counter — the single list the whole pipeline
# uses (ScriptFactory._overused_phrases delegates here).
VI_CONTENT_STOPWORDS = frozenset(
    "của và là những một các cho với trong khi để từ này đó thì mà nhưng nên "
    "như đã sẽ đang cũng rất chỉ còn phải không chưa vào ra lên xuống về tới "
    "theo vì nếu họ nó ông bà anh chị em chúng ta mình bạn tôi có được bị "
    "rồi vẫn đâu nào ai gì sao thế ấy đấy ở tại bằng hơn kém quá lắm năm".split()
)


def _content_grams(text: str, sizes: Tuple[int, ...] = (3, 2)) -> set:
    """Content 2-3 grams of one line, function words and bare numbers excluded."""
    words = re.findall(r"\w+", str(text).lower())
    grams = set()
    for size in sizes:
        for i in range(len(words) - size + 1):
            gram = words[i: i + size]
            if gram[0] in VI_CONTENT_STOPWORDS or gram[-1] in VI_CONTENT_STOPWORDS:
                continue
            if all(len(w) <= 2 for w in gram) or any(w.isdigit() for w in gram):
                continue
            grams.add(" ".join(gram))
    return grams


def content_repeats(
    texts: List[str], min_scenes: int = REPEAT_WARN_SCENES, limit: int = 10
) -> List[Dict[str, Any]]:
    """Content 2-3 grams recurring across scenes, with the scene indices they hit.

    Counts SCENES, not occurrences: a scene that says it twice is one hit, so a
    10-scene chunk and a 130-scene script are measured on the same yardstick. A
    gram nested inside a longer kept gram is dropped, so "nhà kho dột" is
    reported once instead of again as "kho dột".
    """
    hits: Dict[str, List[int]] = {}
    for position, text in enumerate(texts, 1):
        for gram in _content_grams(text):
            hits.setdefault(gram, []).append(position)
    ranked = sorted(
        hits.items(),
        key=lambda kv: (len(kv[1]) * len(kv[0].split()), len(kv[1])),
        reverse=True,
    )
    kept: List[Dict[str, Any]] = []
    for gram, scenes in ranked:
        if len(scenes) < min_scenes:
            continue
        if any(gram in entry["phrase"] for entry in kept):
            continue
        kept.append({"phrase": gram, "scenes": scenes, "count": len(scenes)})
        if len(kept) >= limit:
            break
    return kept


def repetition_load(
    texts: List[str], min_scenes: int = REPEAT_WARN_SCENES
) -> Dict[str, Any]:
    """Scalar repetition pressure of a script — the number S0.1 compares."""
    entries = content_repeats(texts, min_scenes=min_scenes)
    return {
        "phrases": [e["phrase"] for e in entries],
        "hits": sum(e["count"] for e in entries),
        "worst": max((e["count"] for e in entries), default=0),
        "entries": entries,
    }


_MEASURE_UNIT = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>%(?=[\s,.!?;:]|$)|phần trăm|EUR|USD|guilders|tỷ|triệu|nghìn|nm"
    r"|lần|x(?=[\s,.!?;:]|$))",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:1\d{3}|20\d{2})\b")


def numeric_conflicts(texts: List[str], limit: int = 12) -> List[Dict[str, Any]]:
    """Same measure, two different values — the 8%-vs-16% market-share defect.

    Heuristic on purpose: the noun phrase before the number is the measure, and
    two scenes naming different years are treated as explained rather than
    contradictory. The output is a CANDIDATE list for the edit pass, never a
    verdict — the source video really does restate numbers at new milestones.
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for position, text in enumerate(texts, 1):
        lowered = str(text).lower()
        years = _YEAR.findall(str(text))
        for match in _MEASURE_UNIT.finditer(lowered):
            words = re.findall(
                r"\w+", lowered[: match.start()]
            )
            measure: List[str] = []
            for word in reversed(words[-8:]):
                if word in VI_CONTENT_STOPWORDS or word.isdigit():
                    continue
                measure.append(word)
                if len(measure) == 3:
                    break
            if not measure:
                continue
            groups.setdefault(
                (match.group("unit"), " ".join(reversed(measure))), []
            ).append({
                "scene": position,
                "value": match.group("value").replace(",", "."),
                "years": years,
            })

    conflicts: List[Dict[str, Any]] = []
    for (unit, measure), rows in groups.items():
        by_value: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_value.setdefault(row["value"], []).append(row)
        if len(by_value) < 2:
            continue
        rows = [r for rs in by_value.values() for r in rs]
        if all(r["years"] for r in rows) and len(
            {tuple(r["years"]) for r in rows}
        ) == len(by_value):
            continue  # every value carries its own year: a timeline, not a clash
        conflicts.append({
            "measure": measure,
            "unit": unit,
            "values": {
                value: [r["scene"] for r in rs] for value, rs in by_value.items()
            },
        })
        if len(conflicts) >= limit:
            break
    return sorted(conflicts, key=lambda c: -sum(len(v) for v in c["values"].values()))


@dataclass
class ScriptAuditResult:
    is_valid: bool
    language: str
    recommended_voice: str
    total_scenes: int
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    scenes: List[Dict[str, Any]] = field(default_factory=list)

    def format_telegram_feedback(self, job_id: Optional[str] = None) -> str:
        """Formats a human-friendly Telegram message detailing issues and guidance."""
        job_tag = f" [Job {job_id}]" if job_id else ""
        if self.is_valid:
            lang_label = "Tiếng Việt 🇻🇳" if self.language == "vi" else "English 🇺🇸"
            voice_name = _voice_display_name(self.recommended_voice)
            lines = [
                f"✅ <b>KỊCH BẢN ĐẠT CHUẨN XUẤT SẮC!{job_tag}</b>\n",
                f"📊 <b>Số lượng cảnh:</b> {self.total_scenes} cảnh (10s/cảnh = {self.total_scenes * 10}s)",
                f"🌐 <b>Ngôn ngữ nhận diện:</b> {lang_label}",
                f"🎙 <b>Giọng đọc đề xuất:</b> <code>{self.recommended_voice}</code> ({voice_name})\n",
                "👉 Kịch bản đã sẵn sàng. Bạn có thể chọn giọng đọc hoặc duyệt bắt đầu tạo video ngay!"
            ]
            return "\n".join(lines)

        lines = [
            f"⚠️ <b>KỊCH BẢN CẦN ĐIỀU CHỈNH LẠI:{job_tag}</b>\n",
            "Hệ thống đã phân tích kịch bản bạn gửi và phát hiện một số điểm chưa tối ưu cho video 24 cảnh (240s):\n",
        ]
        for idx, issue in enumerate(self.issues, 1):
            lines.append(f"• <b>Lỗi {idx}:</b> {issue}")

        if self.suggestions:
            lines.append("\n💡 <b>Gợi ý khắc phục:</b>")
            for sug in self.suggestions:
                lines.append(f"👉 {sug}")

        lines.append(
            "\n🔄 <i>Hệ thống đang tạm dừng và chờ bạn chỉnh sửa rồi gửi lại file/text kịch bản mới!</i>"
        )
        return "\n".join(lines)


def format_srt_timestamp(seconds: float) -> str:
    """Formats seconds into standard SRT timestamp HH:MM:SS,mmm."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis = 0
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def export_segments_to_srt(segments: List[Dict[str, Any]], out_path: Path) -> Path:
    """Exports transcript segments into standard SRT subtitle format."""
    lines = []
    for idx, seg in enumerate(segments, 1):
        start_sec = float(seg.get("start", 0.0))
        end_sec = float(seg.get("end", start_sec + 2.0))
        if end_sec <= start_sec:
            end_sec = start_sec + 2.0
        start_ts = format_srt_timestamp(start_sec)
        end_ts = format_srt_timestamp(end_sec)
        text = clean_narration_for_tts(str(seg.get("text", "")).strip())
        if not text:
            continue
        lines.append(f"{idx}\n{start_ts} --> {end_ts}\n{text}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def export_segments_to_txt(
    segments: List[Dict[str, Any]],
    out_path: Path,
    title: str = "",
    url: str = "",
    target_scenes: int = 24,
) -> Path:
    """
    Exports segments into a clean, human-friendly TXT script with suggested scenes
    and original transcript for easy remixing ("xào chẻ").
    """
    total_words = sum(len(str(s.get("text", "")).split()) for s in segments)
    max_end = max((float(s.get("end", 0.0)) for s in segments), default=0.0)
    duration_str = f"{int(max_end // 60)} phút {int(max_end % 60)} giây" if max_end > 0 else "Không xác định"

    lines = [
        "============================================================",
        "📝 KỊCH BẢN GỐC & GỢI Ý PHÂN CẢNH TỪ VIDEO GỐC",
        "============================================================",
        f"📺 Tiêu đề: {title or 'Video'}",
        f"🔗 Link gốc: {url or 'N/A'}",
        f"⏱ Thời lượng gốc: {duration_str} | Tổng số từ: {total_words} từ",
        "============================================================",
        "",
        "📌 HƯỚNG DẪN XÀO CHẺ / TÙY BIẾN KỊCH BẢN:",
        "1. Bạn có thể chỉnh sửa, thêm bớt, hoặc viết lại lời kể theo văn phong riêng.",
        f"2. Chuẩn video Remake AI: khoảng {target_scenes} cảnh "
        f"(mỗi cảnh {MIN_SHOT_DURATION_ALGORITHM:.0f}-{MAX_SHOT_DURATION_ALGORITHM:.0f}s, "
        f"{GENERATED_WORD_WINDOW[0]}-{GENERATED_WORD_WINDOW[1]} từ/cảnh ở nhịp "
        f"{SCENE_WORDS_PER_SECOND * 60:.0f} WPM).",
        "3. Sau khi sửa xong, bạn chỉ cần:",
        "   • Gửi file .txt (hoặc .srt) này lên Telegram Bot",
        "   • Hoặc copy & paste trực tiếp các cảnh vào khung chat Telegram.",
        "4. Bot sẽ tự động kiểm tra nhịp đọc, từ an toàn Veo và tạo mẫu giọng đọc nghe thử!",
        "",
        f"============================================================",
        f"🎬 GỢI Ý {target_scenes} CẢNH (BẠN CÓ THỂ SỬA TRỰC TIẾP TẠI ĐÂY):",
        "============================================================",
        "",
    ]

    if segments:
        step = max(1, len(segments) // target_scenes)
        for idx in range(1, target_scenes + 1):
            seg_idx = min((idx - 1) * step, len(segments) - 1)
            raw_text = clean_narration_for_tts(str(segments[seg_idx].get("text", "")))
            start_t = float(segments[seg_idx].get("start", (idx - 1) * 10.0))
            end_t = float(segments[seg_idx].get("end", start_t + 10.0))
            lines.append(f"[CẢNH {idx}] ({format_srt_timestamp(start_t)[:8]} - {format_srt_timestamp(end_t)[:8]})")
            lines.append(f"{raw_text}\n")

    lines.extend([
        "============================================================",
        "📖 TOÀN BỘ LỜI THOẠI GỐC (DÙNG ĐỂ THAM KHẢO CHI TIẾT):",
        "============================================================",
        "",
    ])

    for seg in segments:
        start_sec = float(seg.get("start", 0.0))
        text = clean_narration_for_tts(str(seg.get("text", "")))
        if text:
            lines.append(f"[{format_srt_timestamp(start_sec)[:8]}] {text}")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


class ScriptAuditor:
    """Parses, verifies, and audits scripts before video generation."""

    def __init__(self, target_scenes: int = DEFAULT_TARGET_SHOTS, algorithm_mode: bool = False):
        self.target_scenes = target_scenes
        self.algorithm_mode = algorithm_mode
        
        # Use algorithm constants if in algorithm mode
        if algorithm_mode:
            self.min_words_per_shot = MIN_WORDS_PER_SHOT_ALGORITHM
            self.recommended_words_per_shot = RECOMMENDED_WORDS_PER_SHOT_ALGORITHM
            self.max_words_per_shot = MAX_WORDS_PER_SHOT_ALGORITHM
            self.target_shot_duration = TARGET_SHOT_DURATION_ALGORITHM
        else:
            self.min_words_per_shot = MIN_WORDS_PER_SHOT
            self.recommended_words_per_shot = RECOMMENDED_WORDS_PER_SHOT
            self.max_words_per_shot = MAX_WORDS_PER_SHOT
            self.target_shot_duration = 10.0

    def _parse_srt_or_vtt(self, text: str) -> List[str]:
        """Parses SRT or VTT content, stripping indices, timecodes, and HTML tags."""
        time_pattern = re.compile(
            r"(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s*-->\s*(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}"
        )
        blocks: List[str] = []
        current_block: List[str] = []

        for line in text.splitlines():
            l = line.strip()
            if not l or l.startswith("WEBVTT") or l.lower().startswith(("kind:", "language:")):
                if current_block:
                    blocks.append(" ".join(current_block))
                    current_block = []
                continue
            if l.isdigit():  # Subtitle index number
                if current_block:
                    blocks.append(" ".join(current_block))
                    current_block = []
                continue
            if time_pattern.search(l):  # Timestamp line
                continue
            clean_l = re.sub(r"<[^>]+>", "", l).strip()
            if clean_l:
                current_block.append(clean_l)

        if current_block:
            blocks.append(" ".join(current_block))

        if not blocks:
            return []

        # If user crafted ~target_scenes blocks (e.g. 15-30 blocks), treat each block as a scene
        if len(blocks) >= int(self.target_scenes * 0.7) and len(blocks) <= int(self.target_scenes * 1.5):
            return blocks

        # If too many small blocks (e.g. 100+ raw subtitle lines), group into target_scenes
        full_text = " ".join(blocks)
        sentences = re.split(r"(?<=[.!?…])\s+", full_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            sentences = blocks

        if len(sentences) <= self.target_scenes:
            return sentences

        step = len(sentences) / self.target_scenes
        scenes = []
        for i in range(self.target_scenes):
            start = int(i * step)
            end = int((i + 1) * step) if i < self.target_scenes - 1 else len(sentences)
            bundle = " ".join(sentences[start:end]).strip()
            if bundle:
                scenes.append(bundle)
        return scenes

    @staticmethod
    def _scene_marker(line: str, pattern: re.Pattern) -> Optional[re.Match]:
        """Accept a scene prefix only when it is decorated ("Cảnh 3:", "[CẢNH 3]", "3.").

        The pattern's keywords and brackets are all optional, so a bare "84% …"
        matches it too. Treating that as a marker split a 173-line narration into
        6 scenes of several hundred words and failed the Gate 1 audit.
        """
        m = pattern.match(line)
        if not m:
            return None
        prefix = m.group(0).strip()
        if re.fullmatch(r"\d+", prefix):
            return None  # "84% tin …": a number in the narration, not a scene.
        if re.fullmatch(r"\d{4,}[.):]", prefix):
            return None  # "2014. …" / "2003: …" is prose; scenes are numbered 1-3 digits.
        if line[m.end():m.end() + 1].isdigit():
            return None  # "10.000 đô la": the prefix is a decimal, not "scene 10."
        return m

    def parse_raw_script(self, raw_text: str) -> List[str]:
        """
        Parses raw text into individual scene narrations.
        Supports:
        - SRT / VTT subtitle files (with timecodes stripped)
        - Numbered lines: '[CẢNH 1] ...', 'Cảnh 1: ...', '1. ...', '[Shot 1] ...'
        - Paragraph / newline separated blocks.
        - Freeform narrative text automatically chunked into scenes.
        """
        cleaned = raw_text.strip()
        if not cleaned:
            return []

        # 1. Handle SRT / VTT content (contains timestamp arrow '-->')
        if "-->" in cleaned:
            srt_scenes = self._parse_srt_or_vtt(cleaned)
            if srt_scenes:
                return [clean_narration_for_tts(s) for s in srt_scenes if s.strip()]

        # 2. Check for explicit scene starts like '[CẢNH 1]', 'Cảnh 1:', 'Shot 1:'
        scene_1_pattern = re.compile(r"^\[?(?:cảnh|shot|scene)\s*1\]?(?:\s*\([^)]*\))?[\s:.-]*", re.IGNORECASE)
        scene_prefix_pattern = re.compile(
            r"^\[?(?:cảnh|shot|scene|\#)?\s*\d+\]?(?:\s*\([^)]*\))?[\s:.-]*",
            re.IGNORECASE,
        )
        has_scene_1 = any(scene_1_pattern.match(l.strip()) for l in cleaned.splitlines())

        if has_scene_1:
            scenes = []
            started = False
            for l in cleaned.splitlines():
                l_str = l.strip()
                if not started:
                    if scene_1_pattern.match(l_str):
                        started = True
                if started:
                    if l_str.startswith("===") or "TOÀN BỘ LỜI THOẠI" in l_str.upper():
                        break
                    m = self._scene_marker(l_str, scene_prefix_pattern)
                    if m:
                        content = l_str[m.end():].strip()
                        scenes.append(content)
                    elif l_str:
                        if scenes:
                            scenes[-1] = (scenes[-1] + " " + l_str).strip()
                        else:
                            scenes.append(l_str)
            return [clean_narration_for_tts(s) for s in scenes if s.strip()]

        # 3. Filter out metadata headers and template banners
        header_pattern = re.compile(
            r"^(?:={3,}|-{3,}|tiêu đề:|nguồn:|thời lượng:|tổng số từ:|hướng dẫn|toàn bộ lời thoại|gợi ý|đã gửi|job id:)",
            re.IGNORECASE,
        )
        lines = []
        for l in cleaned.splitlines():
            l_str = l.strip()
            if not l_str or header_pattern.match(l_str):
                continue
            lines.append(l_str)

        if not lines:
            return []

        # 4. Fallback numbered lines or freeform text
        has_numbered_scenes = any(self._scene_marker(l, scene_prefix_pattern) for l in lines)
        scenes: List[str] = []
        if has_numbered_scenes:
            for l in lines:
                m = self._scene_marker(l, scene_prefix_pattern)
                if m:
                    content = l[m.end():].strip()
                    scenes.append(content)
                else:
                    if scenes:
                        scenes[-1] = (scenes[-1] + " " + l).strip()
                    else:
                        scenes.append(l)
        elif len(lines) >= self.target_scenes * 0.7:
            scenes = lines
        else:
            full_text = " ".join(lines)
            sentences = re.split(r"(?<=[.!?…])\s+", full_text)
            sentences = [s.strip() for s in sentences if s.strip()]
            if len(sentences) <= self.target_scenes:
                scenes = sentences
            else:
                step = len(sentences) / self.target_scenes
                for i in range(self.target_scenes):
                    start = int(i * step)
                    end = int((i + 1) * step) if i < self.target_scenes - 1 else len(sentences)
                    bundle = " ".join(sentences[start:end]).strip()
                    if bundle:
                        scenes.append(bundle)

        return [clean_narration_for_tts(s) for s in scenes if s.strip()]

    def audit_script(
        self, raw_text: str, preparsed_scenes: Optional[List[str]] = None
    ) -> ScriptAuditResult:
        """Thoroughly audits the script against length, safety, pacing, and language consistency.

        preparsed_scenes: when the caller already segmented the script (the
        factory audits its own scene list), parse_raw_script heuristics must
        not re-split prose lines that happen to start with numbers.
        """
        if preparsed_scenes:
            raw_scenes = [clean_narration_for_tts(s) for s in preparsed_scenes if s and s.strip()]
        else:
            raw_scenes = self.parse_raw_script(raw_text)

        if not raw_scenes:
            return ScriptAuditResult(
                is_valid=False,
                language="vi",
                recommended_voice=VOICE_MAP["vi"]["default"],
                total_scenes=0,
                issues=["Nội dung kịch bản rỗng hoặc không trích xuất được câu văn nào."],
                suggestions=["Vui lòng gửi lại file .txt hoặc dán nội dung kịch bản có ít nhất vài câu kể chuyện."],
            )

        full_text = " ".join(raw_scenes)
        lang = detect_language(full_text)
        recommended_voice = VOICE_MAP[lang]["default"]

        issues: List[str] = []
        suggestions: List[str] = []
        structured_scenes: List[Dict[str, Any]] = []

        # 1. Check scene count
        num_scenes = len(raw_scenes)
        min_required = min(6, self.target_scenes)
        max_allowed = max(120, self.target_scenes + 20) if self.algorithm_mode else max(30, self.target_scenes + 6)
        if num_scenes < min_required:
            issues.append(f"Kịch bản quá ngắn (chỉ có {num_scenes} cảnh). Yêu cầu tối thiểu {min_required} cảnh.")
            suggestions.append(f"Bạn hãy bổ sung thêm các chi tiết mô tả câu chuyện để đạt ít nhất {min_required} cảnh.")
        elif num_scenes > max_allowed:
            issues.append(f"Kịch bản quá dài ({num_scenes} cảnh).")
            suggestions.append(f"Hãy cô đọng câu chuyện lại thành khoảng {self.target_scenes} cảnh.")


        # 2. Check pacing and Veo safety per scene
        too_long_scenes = []
        too_short_scenes = []
        safety_flagged_scenes = []

        for idx, text in enumerate(raw_scenes, 1):
            cleaned_text = clean_narration_for_tts(text)
            words = cleaned_text.split()
            word_count = len(words)

            if word_count > self.max_words_per_shot:
                too_long_scenes.append((idx, word_count))
            elif word_count < self.min_words_per_shot:
                too_short_scenes.append((idx, word_count))

            # Safety check against sensitive patterns
            flagged = []
            for pattern, _ in SENSITIVE_PATTERNS[:15]:
                if re.search(pattern, cleaned_text, re.IGNORECASE):
                    flagged.append(pattern)
            if flagged:
                safety_flagged_scenes.append(idx)

            prompt = sanitize_veo_prompt(cleaned_text, scene_idx=idx, total_scenes=num_scenes)

            structured_scenes.append({
                "index": idx,
                "text": cleaned_text,
                "prompt": prompt,
                "duration": self.target_shot_duration,
                "is_key_scene": True,
                "word_count": word_count,
            })

        # Algorithm mode applies the same predicate the script factory already
        # enforced: a tolerated tail of outliers is not a blocker. An absolute
        # per-scene check here would reject at Gate 1 a script the factory
        # accepted, for the very outlier the ratio rule allows.
        if self.algorithm_mode and num_scenes:
            offenders = len(too_long_scenes) + len(too_short_scenes)
            if (num_scenes - offenders) / num_scenes >= BUDGET_RATIO_FLOOR:
                too_long_scenes = []
                too_short_scenes = []

        if too_long_scenes:
            max_words_display = self.max_words_per_shot
            samples = ", ".join(f"Cảnh {i} ({cnt} từ)" for i, cnt in too_long_scenes[:4])
            if len(too_long_scenes) > 4:
                samples += f" và {len(too_long_scenes) - 4} cảnh khác"
            issues.append(
                f"Có {len(too_long_scenes)} cảnh quá dài (>{max_words_display} từ) khiến giọng đọc bị tua nhanh hoặc cắt đuôi: {samples}."
            )
            if self.algorithm_mode:
                suggestions.append(f"Rút gọn các câu trên xuống ≤{max_words_display} từ cho phù hợp với thuật toán 4.5-5s.")
            else:
                suggestions.append("Rút gọn các câu trên xuống khoảng 18-24 từ để vừa vặn thời lượng 10 giây mỗi cảnh.")

        if too_short_scenes:
            min_words_display = self.min_words_per_shot
            samples = ", ".join(f"Cảnh {i} ({cnt} từ)" for i, cnt in too_short_scenes[:4])
            issues.append(
                f"Có {len(too_short_scenes)} cảnh quá ngắn (<{min_words_display} từ): {samples}."
            )
            if self.algorithm_mode:
                suggestions.append(f"Bổ sung thêm từ để đạt tối thiểu {min_words_display} từ cho phù hợp với thuật toán.")
            else:
                suggestions.append("Bổ sung thêm 1-2 từ miêu tả cho các cảnh này để lời bình đều đặn hơn.")

        is_valid = len(issues) == 0

        # Auto-rebalance if it's just scene count slightly off and pacing is fine
        max_pacing_allowed = 120 if self.algorithm_mode else 30
        if not is_valid and num_scenes >= 6 and len(too_long_scenes) == 0 and num_scenes <= max_pacing_allowed:
            # Warning only, allow user to proceed
            pass

        return ScriptAuditResult(
            is_valid=is_valid,
            language=lang,
            recommended_voice=recommended_voice,
            total_scenes=num_scenes,
            issues=issues,
            suggestions=suggestions,
            scenes=structured_scenes,
        )


def build_dual_scripts_from_transcript(
    transcript_segments: List[Dict[str, Any]],
    target_shots: int = DEFAULT_TARGET_SHOTS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Synthesizes both Vietnamese ('vi') and English ('en') scripts from transcript segments.
    Cleans raw subtitle artifacts, creates a narrative flow, and packages into 24 scenes.
    """
    if not transcript_segments:
        # Fallback scenes
        vi_scenes = []
        en_scenes = []
        for i in range(1, target_shots + 1):
            vi_scenes.append({
                "index": i,
                "text": f"Giai đoạn lịch sử thứ {i}, mở ra những bước chuyển mình quan trọng.",
                "prompt": sanitize_veo_prompt(f"Historic milestone {i}", scene_idx=i, total_scenes=target_shots),
                "duration": 10.0,
                "is_key_scene": True,
            })
            en_scenes.append({
                "index": i,
                "text": f"Historical milestone phase {i}, marking a significant transition in maritime trade.",
                "prompt": sanitize_veo_prompt(f"Historic milestone {i}", scene_idx=i, total_scenes=target_shots),
                "duration": 10.0,
                "is_key_scene": True,
            })
        return vi_scenes, en_scenes

    # Strategy 1: AI Semantic Scene Slicing (xKiro LLM) - Slices scenes based on actual narrative situations with dynamic durations
    ai_dual = build_semantic_dual_scripts_from_transcript(transcript_segments, target_shots)
    if ai_dual:
        return ai_dual

    total_segs = len(transcript_segments)
    en_scenes = []
    vi_scenes = []

    clean_texts: List[str] = []
    for idx in range(1, target_shots + 1):
        # Divide transcript into continuous, contiguous chronological chunks
        start_seg = int((idx - 1) * total_segs / target_shots)
        end_seg = int(idx * total_segs / target_shots)
        if start_seg >= total_segs:
            start_seg = total_segs - 1
        if end_seg <= start_seg:
            end_seg = start_seg + 1

        seg_slice = transcript_segments[start_seg:end_seg]
        raw_text = " ".join(str(s.get("text", "")).strip() for s in seg_slice)
        raw_text = _clean_rolling_stutter(raw_text)
        clean_text = clean_narration_for_tts(raw_text)
        clean_text = _clean_rolling_stutter(clean_text)

        # Enforce narrative pacing (~15 - 26 words per 5-second scene)
        words = clean_text.split()
        if len(words) > 26:
            clean_text = " ".join(words[:25]) + "."
        elif len(words) < 6 and idx > 1:
            clean_text = f"As events unfolded, {clean_text.lower()}"
        clean_texts.append(clean_text)

    # Dual-script language mapping — ONE batched xKiro translation pass.
    # No silent fallbacks: a gateway failure fails the job loudly.
    from services.xkiro_translator import TranslationError, translate_batch

    base_lang = detect_language(" ".join(clean_texts))
    other_lang = "vi" if base_lang == "en" else "en"
    try:
        translations = translate_batch(
            [(str(i), t) for i, t in enumerate(clean_texts)], base_lang, other_lang
        )
    except TranslationError as exc:
        raise RuntimeError(
            f"Dịch VI/EN qua xKiro thất bại (không còn fallback dịch cứng): {exc}"
        ) from exc

    for idx, clean_text in enumerate(clean_texts, 1):
        translated = translations[idx - 1]
        if base_lang == "en":
            en_text = clean_text
            vi_text = _clean_rolling_stutter(translated)
        else:
            vi_text = clean_text
            en_text = _clean_rolling_stutter(translated)

        veo_prompt = sanitize_veo_prompt(en_text, scene_idx=idx, total_scenes=target_shots)

        en_scenes.append({
            "index": idx,
            "text": en_text,
            "prompt": veo_prompt,
            "duration": 5.0,
            "is_key_scene": True,
        })

        vi_scenes.append({
            "index": idx,
            "text": vi_text,
            "prompt": veo_prompt,
            "duration": 5.0,
            "is_key_scene": True,
        })
    return vi_scenes, en_scenes


def _slice_one_block(
    b_segs: List[Dict[str, Any]],
    block_role: str,
    scenes_per_block: int,
    generation_model: str,
) -> List[Dict[str, Any]]:
    """Slice one transcript block into scenes; raises when the LLM keeps
    returning unusable output."""
    from pipeline.llm_client import chat_json

    b_text = _clean_rolling_stutter(" ".join(s["text"] for s in b_segs))
    if not b_text or len(b_text) < 15:
        return []
    t_start = float(b_segs[0].get("start", 0.0))
    t_end = float(b_segs[-1].get("end", t_start))
    rec_lo, rec_hi = RECOMMENDED_WORDS_PER_SHOT_ALGORITHM
    rec_sec_lo = rec_lo / SCENE_WORDS_PER_SECOND
    rec_sec_hi = rec_hi / SCENE_WORDS_PER_SECOND
    plan_wpm = SCENE_WORDS_PER_SECOND * 60

    prompt = f"""Bạn là Đạo diễn kiêm Biên tập viên Phóng sự Tài liệu chuyên nghiệp.
Nhiệm vụ: Phân tích đoạn nội dung thuyết minh tài chính ({t_start:.0f}s - {t_end:.0f}s, vai trò: {block_role}) và băm thành các CẢNH THEO TÌNH HUỐNG NỘI DUNG CỤ THỂ, KHÔNG băm 5s máy móc.

Nội dung:
\"\"\"{b_text}\"\"\"

ÁP DỤNG BỘ QUY TẮC NGHỆ THUẬT UNIVERSAL_VIDEO_ALGORITHM (HIGH-RETENTION EXPLAINER):
1. NGUYÊN TẮC CỐT LÕI: "Người xem không rời đi vì họ muốn biết câu trả lời cho câu hỏi bạn đã đặt ra." Tạo và duy trì sự tò mò xuyên suốt.
2. CÔNG THỨC HOOK (Nếu là đoạn mở đầu 0-30s):
   - Chứa: [Số liệu khủng / Nghịch lý] + [So sánh bất ngờ] + [Câu hỏi treo/Open loop].
   - Đi thẳng vào sự thật chấn động, không mở đầu bằng lời chào hỏi hay giới thiệu lan man.
3. CHUỖI VÒNG TÒ MÒ LỒNG NHAU (OPEN LOOPS):
   - Mở vòng tò mò tiếp theo TRƯỚC KHI đóng hoàn toàn vòng tò mò cũ.
   - Luôn gieo các câu bắc cầu kích thích: "Nhưng đó chưa phải điều bất ngờ nhất...", "Điều này dẫn tới một nghịch lý trớ trêu...", "Câu trả lời sẽ khiến bạn phải suy ngẫm...".
4. NHỊP KỂ TỰ NHIÊN:
   - Câu đủ chủ ngữ vị ngữ, đọc lên trôi như lời nói; câu ngắn (<= 6 từ) chỉ dùng để chốt ý,
     không băm một ý thành nhiều mảnh cụt.
   - Xen câu dài có mệnh đề phụ với câu ngắn, không để 3 câu liên tiếp cùng độ dài cùng cấu trúc.
5. ẨN DỤ 3 TẦNG (3-TIER METAPHOR):
   - Biến các khái niệm tài chính trừu tượng thành đồ vật đời thường và hành động có xung đột gay gắt (như chiếc xe hơi vô hình, bàn phím gõ ra tiền).
6. NGUYÊN TẮC CALLBACK (Nếu là khối kết thúc / Outro):
   - Lời thoại kết thúc video BẮT BUỘC phải quay lại giải tỏa và trả lời câu hỏi/nghịch lý đã đặt ra ở phần Hook mở đầu.
7. ĐIỆN ẢNH VEO PROMPT (STYLE BIBLE — CRAYON HAND-DRAWN):
   - Whimsical hand-drawn editorial cartoon: nét sáp crayon / bút chì màu thô, texture giấy vẽ sần, flat 2D minimalist.
   - TUYỆT ĐỐI KHÔNG: photorealistic, 3D render, gradient shading, glossy, photo.
   - Bố cục 60-70% Negative Space, 1-3 tiêu điểm rõ ràng (không rối rắm).
   - Màu nhấn crayon: vàng sáp cho tiền bạc/ý tưởng, đỏ cảnh báo cho khủng hoảng, xanh dương sáp cho đồ vật trung tính.
   - Nền: giấy vẽ off-white khi bình thường; bảng đen chì khi khủng hoảng; sepia đất nung khi hồi ức lịch sử.

Yêu cầu định dạng đầu ra:
- CHIA ĐÚNG {scenes_per_block} CẢNH cho khối này (sai số tối đa ±1), dựa trên sự chuyển biến của tình huống cụ thể. Không gộp nhiều ý vào một cảnh.
- situation: Mô tả tình huống cụ thể (hành động, bối cảnh diễn ra).
- voiceover_vi: Lời thoại tiếng Việt mượt mà, truyền cảm, tròn câu trọn ý. MỖI cảnh {rec_lo}-{rec_hi} từ ({rec_sec_lo:.1f}-{rec_sec_hi:.1f} giây ở nhịp {plan_wpm:.0f} WPM); câu ngắn chỉ để chốt, tuyệt đối không ngắt câu cụt lủn, không lặp từ, đúng thuật ngữ tài chính (tiền gốc, lãi suất, dự trữ bắt buộc, ngân hàng thương mại).
- voiceover_en: Bản EN trọn ý, mạch kể tự nhiên như người dẫn chương trình (không dịch word-by-word, không cụt câu): {EN_MIN_WORDS}-{EN_MAX_WORDS_ALGORITHM} từ mỗi cảnh vì giọng EN đọc ~160 wpm.
- estimated_duration: Thời lượng tính bằng giây phù hợp tự nhiên với độ dài lời thoại (từ {rec_sec_lo:.1f} đến {rec_sec_hi:.1f} giây).
- visual_prompt: Prompt tiếng Anh chi tiết theo STYLE BIBLE crayon hand-drawn cho Veo.

Trả về duy nhất JSON array:
[
  {{
    "situation": "...",
    "voiceover_vi": "...",
    "voiceover_en": "...",
    "estimated_duration": 6.0,
    "visual_prompt": "Whimsical hand-drawn crayon editorial cartoon illustration of..."
  }}
]
Chỉ trả về JSON thuần túy."""

    scenes_data: Optional[List[Dict[str, Any]]] = None
    block_feedback = ""
    min_acceptable = max(3, int(scenes_per_block * 0.7))
    last_err: Optional[Exception] = None
    for _attempt in range(2):
        try:
            parsed = chat_json(
                "Bạn là trợ lý chỉ trả về JSON thuần túy, không giải thích.",
                prompt + block_feedback,
                temperature=0.3,
                model=generation_model,
            )
        except Exception as exc:
            last_err = exc
            continue
        candidate = None
        for key in ("items", "scenes", "data"):
            if isinstance(parsed.get(key), list):
                candidate = parsed[key]
                break
        if candidate is None:
            last_err = ValueError("semantic slicing returned unexpected JSON shape")
            continue
        scenes_data = candidate
        if len(scenes_data) >= min_acceptable:
            break
        logger.warning(
            "Slicer block returned %d/%d scenes; retrying with stronger instruction",
            len(scenes_data), scenes_per_block,
        )
        block_feedback = (
            f"\n\nLẦN TRƯỚC BẠN CHỈ CHIA {len(scenes_data)} CẢNH. "
            f"Khối này PHẢI có đúng {scenes_per_block} cảnh (sai số ±1), "
            f"mỗi cảnh {rec_lo}-{rec_hi} từ, không gộp nhiều ý vào một cảnh."
        )
    if scenes_data is None:
        raise last_err or RuntimeError("Slicer block returned no usable scenes")
    return scenes_data


def _slice_block_or_split(
    b_segs: List[Dict[str, Any]],
    block_role: str,
    scenes_per_block: int,
    generation_model: str,
    depth: int = 0,
) -> List[Dict[str, Any]]:
    """Slice a block, splitting it in half when the LLM output is unusable.

    Large blocks overflow the gateway's response budget; smaller halves parse
    reliably (same failure class as the rewrite-chunk truncation fix).
    """
    try:
        return _slice_one_block(b_segs, block_role, scenes_per_block, generation_model)
    except Exception:
        if depth >= 3 or len(b_segs) < 2:
            raise
    mid = max(1, len(b_segs) // 2)
    logger.warning(
        "Slicer block unusable at depth %d (%d segs); splitting in half", depth, len(b_segs)
    )
    out: List[Dict[str, Any]] = []
    for i, half in enumerate((b_segs[:mid], b_segs[mid:])):
        half_role = block_role
        if "MỞ ĐẦU" in block_role and i == 1:
            half_role = "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)"
        if "KẾT THÚC" in block_role and i == 0:
            half_role = "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)"
        out.extend(_slice_block_or_split(
            half, half_role, max(2, scenes_per_block // 2), generation_model, depth + 1
        ))
    return out


def build_semantic_dual_scripts_from_transcript(
    transcript_segments: List[Dict[str, Any]],
    target_shots: int = DEFAULT_TARGET_SHOTS,
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """
    Intelligent AI Scriptwriter & Semantic Scene Slicer using xKiro LLM.
    Bypasses dumb fixed 5-second mechanical slicing.
    Slices scenes naturally based on specific dramatic & narrative situations,
    with flexible dynamic durations (4.5s - 8.0s) and fluent documentary prose.
    """
    import os
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from services.settings_service import get_xkiro_generation_model

        generation_model = get_xkiro_generation_model()
        if not generation_model:
            return None

        # 1. Clean subtitle stutters and chronological stitching
        cleaned_segs = []
        for s in transcript_segments:
            txt = _clean_rolling_stutter(str(s.get("text", "")).strip())
            if txt:
                cleaned_segs.append({
                    "start": float(s.get("start", 0)),
                    "end": float(s.get("end", 0)),
                    "text": txt,
                })
        if not cleaned_segs:
            return None

        total_dur = max((s["end"] for s in cleaned_segs), default=180.0)
        # ~6s of finished narration per scene (17-21 words at 3.5 w/s). Blocks are
        # sized so each call emits a manageable scene count while the whole script
        # still reaches the requested target_shots.
        target_scenes = max(6, int(target_shots))
        num_blocks = max(1, min(16, int(round(total_dur / 75.0))))
        block_dur = total_dur / num_blocks
        scenes_per_block = max(3, int(round(target_scenes / num_blocks)))

        vi_scenes: List[Dict[str, Any]] = []
        en_scenes: List[Dict[str, Any]] = []
        scene_counter = 1

        for b_idx in range(num_blocks):
            t_start = b_idx * block_dur
            t_end = (b_idx + 1) * block_dur
            b_segs = [s for s in cleaned_segs if t_start <= s["start"] < t_end or (b_idx == num_blocks - 1 and s["start"] >= t_start)]
            b_text = _clean_rolling_stutter(" ".join(s["text"] for s in b_segs))
            if not b_text or len(b_text) < 15:
                continue

            block_role = "MỞ ĐẦU (HOOK)" if b_idx == 0 else ("KẾT THÚC (CALLBACK & OUTRO)" if b_idx == num_blocks - 1 else "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)")
            scenes_data = _slice_block_or_split(
                b_segs, block_role, scenes_per_block, generation_model
            )
            for sc in scenes_data:
                v_vi = sc.get("voiceover_vi", "").strip()
                v_en = (sc.get("voiceover_en") or v_vi).strip()
                # Duration is derived from the line's own length, never from the
                # model's estimate: the QA scorecard walks these durations (S09,
                # S10) and the TTS stage paces against them.
                dur = max(
                    MIN_SHOT_DURATION_ALGORITHM,
                    min(
                        MAX_SHOT_DURATION_ALGORITHM,
                        len(v_vi.split()) / SCENE_WORDS_PER_SECOND,
                    ),
                )
                p = (sc.get("visual_prompt") or sanitize_veo_prompt(v_en, scene_counter, target_shots)).strip()
                sit = sc.get("situation", "").strip()

                vi_scenes.append({
                    "index": scene_counter,
                    "situation": sit,
                    "text": v_vi,
                    "prompt": p,
                    "duration": dur,
                    "is_key_scene": True,
                })
                en_scenes.append({
                    "index": scene_counter,
                    "situation": sit,
                    "text": v_en,
                    "prompt": p,
                    "duration": dur,
                    "is_key_scene": True,
                })
                scene_counter += 1

        if vi_scenes:
            return vi_scenes, en_scenes

    except Exception as e:
        logger.warning("AI semantic scene slicing failed or unavailable: %s. Falling back to documentary slice mode.", e)

    return None


def _clean_rolling_stutter(text: str) -> str:
    """Removes rolling caption duplicate phrases common in YouTube auto-generated VTT subtitles."""
    words = text.split()
    for span in range(min(8, len(words) // 2), 1, -1):
        i = 0
        while i + 2 * span <= len(words):
            p1 = [w.lower().strip(".,!?\"'") for w in words[i : i + span]]
            p2 = [w.lower().strip(".,!?\"'") for w in words[i + span : i + 2 * span]]
            if p1 == p2 and any(len(x) > 2 for x in p1):
                words = words[: i + span] + words[i + 2 * span :]
            else:
                i += 1
    return " ".join(words)
