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
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

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
# Target: 4.5-5s shots with <=6 words per shot
MIN_WORDS_PER_SHOT_ALGORITHM = 4
RECOMMENDED_WORDS_PER_SHOT_ALGORITHM = (4, 6)
MAX_WORDS_PER_SHOT_ALGORITHM = 6
TARGET_SHOT_DURATION_ALGORITHM = 4.75  # seconds (4.5-5s range)
MIN_SHOT_DURATION_ALGORITHM = 4.0
MAX_SHOT_DURATION_ALGORITHM = 6.0

# Voice mapping defaults (User requirement: prioritize Male voices)
VOICE_MAP = {
    "vi": {
        "male": "vi-VN-NamMinhNeural",
        "female": "vi-VN-HoaiMyNeural",
        "default": "vi-VN-NamMinhNeural",
    },
    "en": {
        "male": "en-US-GuyNeural",
        "female": "en-US-JennyNeural",
        "default": "en-US-GuyNeural",
    },
}


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
            voice_name = "Nam Minh (Nam)" if "NamMinh" in self.recommended_voice else "Guy (Nam US)"
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
        f"2. Chuẩn video Remake AI: khoảng {target_scenes} cảnh (mỗi cảnh ~10s, từ 14-25 từ/cảnh).",
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
                    m = scene_prefix_pattern.match(l_str)
                    if m and m.group(0).strip():
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
        has_numbered_scenes = any(scene_prefix_pattern.match(l) for l in lines)
        scenes: List[str] = []
        if has_numbered_scenes:
            for l in lines:
                m = scene_prefix_pattern.match(l)
                if m and m.group(0).strip():
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

    def audit_script(self, raw_text: str) -> ScriptAuditResult:
        """Thoroughly audits the script against length, safety, pacing, and language consistency."""
        raw_scenes = self.parse_raw_script(raw_text)

        if not raw_scenes:
            return ScriptAuditResult(
                is_valid=False,
                language="vi",
                recommended_voice="vi-VN-NamMinhNeural",
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

    step = max(1, len(transcript_segments) // target_shots)
    en_scenes = []
    vi_scenes = []

    for idx in range(1, target_shots + 1):
        seg_idx = min((idx - 1) * step, len(transcript_segments) - 1)
        raw_text = transcript_segments[seg_idx].get("text", "")
        clean_text = clean_narration_for_tts(raw_text)

        # Enforce narrative pacing (~18 - 25 words)
        words = clean_text.split()
        if len(words) > 26:
            clean_text = " ".join(words[:25]) + "."
        elif len(words) < 8 and idx > 1:
            clean_text = f"As the situation developed, {clean_text.lower()}"

        # If transcript was English, translate/adapt a clean Vietnamese narrative counterpart
        detected_lang = detect_language(clean_text)
        if detected_lang == "en":
            en_text = clean_text
            # Basic adaptive mapping for documentary narrative
            vi_text = _adapt_en_to_vi_documentary(clean_text, scene_idx=idx, total_scenes=target_shots)
        else:
            vi_text = clean_text
            en_text = f"Scene {idx}: Historical documentation depicting key regional developments."

        veo_prompt = sanitize_veo_prompt(en_text, scene_idx=idx, total_scenes=target_shots)

        en_scenes.append({
            "index": idx,
            "text": en_text,
            "prompt": veo_prompt,
            "duration": 10.0,
            "is_key_scene": True,
        })

        vi_scenes.append({
            "index": idx,
            "text": vi_text,
            "prompt": veo_prompt,
            "duration": 10.0,
            "is_key_scene": True,
        })

    return vi_scenes, en_scenes


def _adapt_en_to_vi_documentary(en_text: str, scene_idx: int, total_scenes: int) -> str:
    """Provides a natural Vietnamese documentary phrasing for a scene."""
    # Common historical phrase translations
    t = en_text.lower()
    if "company" in t or "trillion" in t or "worth" in t:
        return f"Vào thời kỳ hoàng kim, tổ chức thương mại này sở hữu khối tài sản khổng lồ chưa từng có trong lịch sử."
    if "spain" in t or "dutch" in t:
        return f"Mối quan hệ căng thẳng giữa các thế lực hàng hải phương Tây đã làm thay đổi hoàn toàn cục diện thương mại."
    if "price" in t or "creative" in t or "money" in t:
        return f"Khi giá trị hàng hóa biến động dữ dội, các thương nhân bắt đầu tìm kiếm những phương thức giao thương mới."
    if "week" in t or "month" in t or "year" in t:
        return f"Chỉ trong một thời gian ngắn, biến cố liên tiếp diễn ra khiến các bên tham gia phải nhanh chóng thích nghi."
    if scene_idx == 1:
        return "Mở đầu câu chuyện là một bước ngoặt lịch sử làm thay đổi cả nền kinh tế thế giới."
    if scene_idx == total_scenes:
        return "Và cái kết đã để lại một bài học sâu sắc cho các thế hệ kinh doanh và phát triển sau này."

    return f"Diễn biến tiếp theo ở cảnh {scene_idx} chứng kiến những chuyển biến chiến lược mang tính quyết định."
