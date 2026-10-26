"""Script factory: best-of-N grounded rewrite with LLM judging (Phase 2).

Takes the semantic slices produced by script_auditor.build_semantic_dual_scripts_from_transcript
and turns them into production-ready VI/EN scripts fused with the
UNIVERSAL_VIDEO_ALGORITHM skill pack:

1. N variants (default 3) are rewritten from the same slices with different
   creative angles, grounded strictly in the source transcript facts.
2. Every variant is scored by an LLM judge on the skill-pack rubric
   (hook paradox, open loops, natural spoken rhythm, WPM, grounding,
   terminology). Mechanical metrics (durations, word counts, sentence-length
   variance) are computed in code and recorded alongside.
3. If no variant reaches min_score, the judge feedback is fed back and up to
   `max_regen_rounds` extra rounds are generated.
4. The full decision trail is persisted to algorithm_result.json so every
   later phase (QA, gates) can audit how the script was chosen.

No silent fallbacks: slicing or all-invalid variants raise ScriptFactoryError.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging_config import get_logger
from services.algorithm_qa import (
    MEAN_DUR_RANGE,
    PASS_SHORT_PCT,
    SCENE_DUR_RANGE,
    WPM_RANGE,
)
from services.hook_formulas import hook_formula_catalog, narration_laws_block
from services.hook_scoring import score_hook
from services.prompt_sanitizer import ensure_style_prefix
from services.script_auditor import (
    BUDGET_RATIO_FLOOR,
    EN_MAX_WORDS_ALGORITHM,
    GENERATED_WORD_WINDOW,
    RECOMMENDED_WORDS_PER_SHOT_ALGORITHM,
    REPEAT_WARN_SCENES,
    REVISION_JUDGE_TOLERANCE,
    REVISION_REPEAT_DROP_FLOOR,
    SCENE_WORDS_PER_SECOND,
    VI_CONTENT_STOPWORDS,
    content_repeats,
    numeric_conflicts,
    repetition_load,
)

logger = get_logger(__name__)

MIN_SCORE = 8.0
N_VARIANTS = 3
MAX_REGEN_ROUNDS = 2
VARIANT_CHUNK = 10  # scenes per rewrite call (20-scene replies were truncated mid-JSON)
SPLIT_MIN_CHUNK = 5  # smallest sub-chunk when a rewrite response is unusable
# Skill-pack pacing anchor: scene duration is computed in code from the VI word
# count (210 WPM) instead of trusting the LLM's duration guess. The audio stage
# targets 205 WPM with pauses; 3.5 words/s is the plan ceiling (QA S08 <=210).
WORDS_PER_SECOND = SCENE_WORDS_PER_SECOND

# Skill-pack mechanical targets (mirrors scripts/apply_video_algorithm.py).
# The 54/46 short-sentence law was retired: >50% of sentences at <=6 words is
# what made the old scripts read as staccato. QA S07 still needs >=35% short
# sentences, so the steering target sits in the 35-45% band.
TARGET_SHORT_SENTENCE_PCT = 0.38
# Cap on revision chunks per script: the global edit finds issues everywhere,
# but a repair pass must stay bounded on 130-scene scripts.
REVISION_MAX_CHUNKS = 14
MIN_WPM = 195.0
MAX_WPM = 214.0
TARGET_WPM = 205.0
MIN_SCENE_DURATION = SCENE_DUR_RANGE[0]
MAX_SCENE_DURATION = SCENE_DUR_RANGE[1]
# The per-scene word window is nothing but the QA duration window (S09: 4-8s)
# expressed in words. It used to be a free-floating 18-24 that no downstream
# check required, while the slicer feeding it deliberately emitted 6-26 words.
VI_MIN_WORDS, VI_MAX_WORDS = GENERATED_WORD_WINDOW
# Steering band quoted to the model: it aims the script at ~6s scenes without
# being the thing that invalidates a variant. Only the edges above are enforced.
VI_TARGET_WORDS = RECOMMENDED_WORDS_PER_SHOT_ALGORITHM
EN_MAX_WORDS = EN_MAX_WORDS_ALGORITHM
# EN instruction floor: a line shorter than this leaves the EN voice gasping
# between sentences (the 48%-pause "dead air" the owner rejected).
EN_MIN_WORDS = 12
# Acceptance mirrors the scorecard instead of out-tightening it: a flat AND over
# every scene asks ~170 independent LLM draws to land in one narrow window at
# once, which is unwinnable (measured 55-65% per-scene compliance => ~1e-32).
MIN_BUDGET_RATIO = BUDGET_RATIO_FLOOR
# Aggregate WPM is recorded, never vetoed here: scene duration is derived from
# the word count, so a compliant script is exactly 210 WPM by construction and
# the only way to cross S08's ceiling is the <=5% outliers the ratio rule
# deliberately tolerates. QA measures WPM on the real audio, which is the law.
# Hook window: QA S16 allows <=32s. Four hook scenes of 24-28 words land at
# 27.4-32.0s at 3.5 w/s; the repair targets 105-112 words so the 30s boundary
# falls inside scene 4 and the scene never overflows the 32s tolerance.
HOOK_SCENE_COUNT = 4
HOOK_MIN_WORDS = 24
HOOK_MAX_WORDS = 28
HOOK_TARGET_WORDS = (105, 112)
# QA S16 walks scenes until the cumulative run time crosses 30s and demands
# <=32s at that boundary. Aim at 30.6s so rounding on either side still passes.
HOOK_BOUNDARY_SEC = 30.6
REPAIR_BATCH = 10
MAX_REPAIR_ROUNDS = 3
EXPANSION_BATCHES = 4
EXPANSION_DRIFT_THRESHOLD = 0.28
EXPANSION_TARGET_WORDS = (23, 24)
# Cross-chunk continuity (S0.3). The phrase registry alone was not enough: scenes
# 2 and 25 told the same beat in different words, so a chunk now also gets the
# last lines written before it and is told not to re-narrate an idea.
BEAT_TAIL_SCENES = 3
BEAT_TEXT_CHARS = 180

# --- Phase 8: external prose polish ------------------------------------------
# The factory's own chain drafts the script; an xKiro prose model locks the words
# afterwards. It runs LAST, after every internal repair, so its output is graded
# by exactly the hard QA the internal script already passed — and is thrown away
# if it breaks any of it.
POLISH_MODEL = "openai/gpt-5.6-terra"
POLISH_CHUNK = 8  # a 130-scene script answered in one call came back truncated
POLISH_MAX_WORDS_DRIFT = 3  # keeps the reading duration of every scene intact
# A rewrite that has to drop this much of the script is not an editor. Measured on
# the first live chunk: 8 scenes, every one of them over the drift limit.
POLISH_MAX_SKIP_RATIO = 0.25
POLISH_SYSTEM_PROMPT = """Bạn là editor lời bình (voiceover) cho video tài liệu. Nhiệm vụ: viết lại lời bình cho MỊN,
TỰ NHIÊN, SÂU SẮC như người bản xứ viết — nhưng tuyệt đối không làm hỏng cấu trúc video.
Kịch bản VI + EN đính kèm phía dưới.

QUY TẮC CỨNG (vi phạm là bản viết lại bị loại):
1. Giữ đúng số cảnh và thứ tự: N cảnh, index 1..N. Không gộp, không tách, không thêm, không bỏ.
2. Mỗi cảnh giữ đúng chức năng kể chuyện của nó (setup / bằng chứng / giải thích / xung đột /
   hệ quả / payoff) và đúng vị trí trên dòng thời gian.
3. KHÔNG đổi, KHÔNG thêm, KHÔNG bịa: số liệu, năm, tên riêng, tên công ty, đơn vị, quan hệ nhân quả.
   Chỉ được sửa CÁCH NÓI.
4. Nếu thấy hai cảnh mâu thuẫn số liệu hoặc kể trùng cùng một sự kiện: KHÔNG tự quyết.
   Giữ nguyên, và ghi cảnh đó vào trường "notes".
5. Độ dài mỗi cảnh xấp xỉ bản gốc (chênh ≤3 từ) để thời lượng đọc không đổi.
   VI: 17–21 từ/cảnh. EN: 12–22 từ/cảnh. Không thêm câu thừa chỉ để đủ số từ.

VĂN PHONG:
- Viết để NÓI, không viết như bài báo. Câu phải đọc được thành một hơi, không hụt hơi, không dồn nhịp.
- Bỏ câu cụt kiểu điện tín: mỗi câu trọn nghĩa.
- Chặn lặp ở mức Ý, không chỉ mức từ: không kể trùng cùng một beat ở hai cảnh. Nếu lặp là callback
  có chủ đích, lần sau phải cho thông tin mới hoặc trả lời câu hỏi đã mở.
- Không dùng quá 2 lần trong toàn bài: "tương lai", "thất bại", "bức tường", "nhà kho", "bước ngoặt",
  "điều đáng nói là", "câu hỏi thực sự là", "what really happened", "but this was not".
- VI: collocation tiếng Việt tự nhiên, không dịch word-by-word từ EN, chủ ngữ/đối tượng rõ,
  không chuỗi danh từ hoá dài, không mở đầu liên tiếp nhiều câu bằng cùng một mẫu.
- EN: tiếng Anh nói kiểu documentary, KHÔNG phải bản dịch của VI. Giữ tense và subject reference
  nhất quán. Không dùng câu fragment chỉ để nhét vừa số từ.
- Dấu câu phục vụ TTS: dấu phẩy đặt ở ranh giới hơi thở thật, không rải đều mọi câu.

TRẢ VỀ duy nhất một JSON object, không thêm text ngoài JSON:
{"scenes":[{"index":1,"voiceover_vi":"...","voiceover_en":"..."},... đúng N phần tử],
 "notes":[{"index":<int>,"issue":"mâu thuẫn số liệu | lặp beat | vượt quyền sửa","detail":"..."}]}"""

_POLISH_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# An all-caps name must END on a letter or digit: "ASML." is the fact ASML, and
# a polish that moves a full stop is not a polish that invents a company.
_POLISH_ACRONYM_RE = re.compile(r"[A-Z][A-Z0-9+.\-]*[A-Z0-9]")
# A separator that groups thousands (always followed by exactly three digits) is
# formatting; anything else ("2,5") is the value itself and must not be merged.
_POLISH_THOUSANDS_RE = re.compile(r"[.,](?=\d{3}(?!\d))")


def _locked_tokens(*texts: str) -> Counter:
    """Facts a polish pass may not touch: every number and every all-caps name.

    Compared on the value, not the spelling: "50,000" and "50.000" are the same
    quantity, while "50,000" and "60,000" — or "AI" written as "ai" — are not.
    """
    joined = " ".join(texts)
    numbers = [
        _POLISH_THOUSANDS_RE.sub("", n) for n in _POLISH_NUMBER_RE.findall(joined)
    ]
    return Counter(numbers + _POLISH_ACRONYM_RE.findall(joined))


def _band_breaks(vi_words: int, en_words: int) -> Tuple[str, ...]:
    """Which word-budget rules a scene of these two lengths breaks."""
    kinds: List[str] = []
    if vi_words < VI_MIN_WORDS:
        kinds.append("vi_low")
    elif vi_words > VI_MAX_WORDS:
        kinds.append("vi_high")
    if en_words > EN_MAX_WORDS:
        kinds.append("en_high")
    return tuple(kinds)


def _polish_violations(
    origin_vi: str, origin_en: str, new_vi: str, new_en: str
) -> List[str]:
    """Why this rewritten scene cannot be ingested (empty list == accepted)."""
    if not new_vi.strip() or not new_en.strip():
        return ["empty"]
    kinds: List[str] = []
    vi_drift = abs(len(new_vi.split()) - len(origin_vi.split()))
    en_drift = abs(len(new_en.split()) - len(origin_en.split()))
    if vi_drift > POLISH_MAX_WORDS_DRIFT:
        kinds.append(f"vi_drift:{vi_drift}")
    if en_drift > POLISH_MAX_WORDS_DRIFT:
        kinds.append(f"en_drift:{en_drift}")
    # A +-3 drift is legal arithmetic on a scene that already sits on the band
    # edge, and the band is what QA grades: 28 -> 31 VI words is a new offender
    # even though the editor obeyed its own budget line. Measured live: the edit
    # moved 2 offenders to 13 that way. A scene may inherit the band break it
    # arrived with, never add one.
    origin_breaks = _band_breaks(len(origin_vi.split()), len(origin_en.split()))
    for kind in _band_breaks(len(new_vi.split()), len(new_en.split())):
        if kind not in origin_breaks:
            kinds.append(f"budget:{kind}")
    before, after = _locked_tokens(origin_vi, origin_en), _locked_tokens(new_vi, new_en)
    if before != after:
        kinds.append(
            "locked_tokens:"
            + json.dumps(
                {"before": dict(before), "after": dict(after)}, ensure_ascii=False
            )[:200]
        )
    return kinds


VARIANT_ANGLES = [
    {
        "id": "analyst",
        "label": "Nhà phân tích",
        "brief": (
            "Giọng học thuật đầy tò mò: dẫn số liệu làm điểm neo bất ngờ, nghịch lý "
            "được đặt ngay đầu, lập luận chặt, mỗi vòng tò mò mở bằng một con số hoặc "
            "sự kiện trái trực giác từ transcript."
        ),
    },
    {
        "id": "dramatist",
        "label": "Dramatist",
        "brief": (
            "Giọng kịch tính gay cấn: xung đột và biến động liên tiếp, chồng các open "
            "loop lên nhau trước khi đóng vòng cũ, nhịp câu ngắn dồn dập tạo dramatic "
            "pause, cao trào đặt giữa và cuối kịch bản."
        ),
    },
    {
        "id": "teacher",
        "label": "Người giảng giải",
        "brief": (
            "Giọng gần gũi dạy học: mọi khái niệm trừu tượng đều qua ẩn dụ 3 tầng bằng "
            "vật dụng đời thường có xung đột (ví dụ: chiếc xe hơi vô hình, bàn phím gõ "
            "ra tiền), thân thiện với người mới nhưng không mất chiều sâu."
        ),
    },
]

JUDGE_RUBRIC = """Bạn là tổng biên tập khắt khe nhất đài documentary. Chấm kịch bản theo bộ quy tắc UNIVERSAL_VIDEO_ALGORITHM trên thang 10 (một chữ số thập phân):
1. HOOK (2.0đ): 30 giây đầu có [số liệu khủng / nghịch lý] + [so sánh bất ngờ] + [câu hỏi treo]. Không chào hỏi, không giới thiệu lan man.
2. OPEN LOOPS (2.0đ): ít nhất 4 vòng tò mò; vòng mới mở TRƯỚC khi vòng cũ đóng hoàn toàn; có câu bắc cầu ("Nhưng đó chưa phải điều bất ngờ nhất...").
3. NHỊP KỂ TỰ NHIÊN (1.5đ): câu đủ chủ ngữ vị ngữ, đọc lên trôi chảy như người kể chuyện thật; độ dài câu đa dạng (câu dài có mệnh đề phụ xen câu ngắn), khoảng 35-40% câu ngắn (<= 6 từ) làm điểm nhấn nhưng KHÔNG quá 45%; có từ nối tự nhiên; cấm chuỗi câu cụt cùng cấu trúc và cấm lặp một cụm ẩn dụ/từ khoá quá 3 lần trong toàn bài.
4. TỐC ĐỘ ĐỌC (1.0đ): WPM tổng thể trong khoảng 195-214 (dựa vào độ dài lời thoại và thời lượng từng cảnh).
5. GROUNDED (1.5đ): mọi sự kiện, tên riêng, con số phải có trong transcript nguồn. Bịa bất kỳ chi tiết nào là lỗi nặng.
6. THUẬT NGỮ (1.0đ): thuật ngữ tài chính chính xác, nhất quán (tiền gốc, lãi suất, cung tiền, ngân hàng trung ương...).
7. CALLBACK (1.0đ): cảnh cuối quay lại giải tỏa câu hỏi/nghịch lý đã gieo ở hook.
Trả về duy nhất JSON object: {"score": <0-10>, "breakdown": {"hook": <0-2>, "open_loops": <0-2>, "narration_rhythm": <0-1.5>, "wpm": <0-1>, "grounded": <0-1.5>, "terminology": <0-1>, "callback": <0-1>}, "feedback": "nhận xét ngắn tiếng Việt, chỉ ra 2-3 điểm cần sửa cụ thể"}"""


REPAIR_SYSTEM_PROMPT = (
    "Bạn là biên tập viên documentary tài chính theo bộ quy tắc UNIVERSAL_VIDEO_ALGORITHM. "
    "Nhiệm vụ: sửa các cảnh vượt/dưới ngân sách từ hoặc cảnh đọc lên gượng của kịch bản song ngữ. "
    "TUYỆT ĐỐI GROUNDED: chỉ dùng sự kiện/tên riêng/con số có trong transcript nguồn, "
    "không bịa chi tiết mới, không đổi index hay thứ tự cảnh. "
    "Câu nào cũng phải đủ chủ ngữ vị ngữ và đọc lên xuôi tai; câu ngắn chỉ dùng để chốt, "
    "không băm một ý thành mảnh cụt; không lặp từ, không lặp ẩn dụ."
)


class ScriptFactoryError(RuntimeError):
    """Raised when the script factory cannot produce a usable script."""


def _scene_violations(
    vi_scene: Dict[str, Any], en_scene: Dict[str, Any]
) -> Tuple[str, ...]:
    """Which budget rules this scene breaks ("" when it satisfies all of them)."""
    return _band_breaks(
        len(str(vi_scene.get("text", "")).split()),
        len(str(en_scene.get("text", "")).split()),
    )


def _reading_duration(word_count: int) -> float:
    """Scene seconds for a word count, rounded up to the millisecond.

    Durations are derived from words so a compliant script reads at exactly the
    plan rate. Rounding down even a little puts the aggregate fractionally above
    the 210 WPM ceiling QA S08 enforces.
    """
    natural = word_count / WORDS_PER_SECOND
    if natural >= MAX_SCENE_DURATION:
        return MAX_SCENE_DURATION
    return max(MIN_SCENE_DURATION, math.ceil(natural * 1000) / 1000)


def _trim_to_word_cap(text: str, max_words: int) -> str:
    """Drop whole sentences until the line fits `max_words`.

    Hook repair overshoots its own 24-28 band; the 8s scene ceiling then forces
    a >210 WPM read and QA S08 fails. The closing sentence is kept first because
    it carries the opening question, then earlier sentences are added while they
    fit, so the paradox at the front survives too.
    """
    words = text.split()
    if len(words) <= max_words:
        return text
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if len(sentences) > 1:
        tail = sentences[-1]
        head = []
        budget = max_words - (len(tail.split()) if len(tail.split()) <= max_words else 0)
        for sentence in sentences[:-1]:
            size = len(sentence.split())
            if size <= budget:
                head.append(sentence)
                budget -= size
        kept = head + ([tail] if len(tail.split()) <= max_words else [])
        if kept:
            return " ".join(kept)
    return " ".join(words[:max_words])


def _beat(on_progress: Optional[Callable[[], None]]) -> None:
    """Report liveness; a heartbeat failure must never sink an expensive script."""
    if on_progress is None:
        return
    try:
        on_progress()
    except Exception as exc:
        logger.warning("Script heartbeat skipped: %s", exc)


@dataclass
class Variant:
    angle: Dict[str, str]
    scenes_vi: List[Dict[str, Any]] = field(default_factory=list)
    scenes_en: List[Dict[str, Any]] = field(default_factory=list)
    valid: bool = False
    invalid_reason: str = ""
    score: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    judge_feedback: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    budget_report: Dict[str, Any] = field(default_factory=dict)


class ScriptFactory:
    def __init__(
        self,
        n_variants: int = N_VARIANTS,
        max_regen_rounds: int = MAX_REGEN_ROUNDS,
        min_score: float = MIN_SCORE,
        model: Optional[str] = None,
    ):
        if n_variants < 1 or n_variants > len(VARIANT_ANGLES):
            raise ValueError(f"n_variants must be 1..{len(VARIANT_ANGLES)}")
        self.n_variants = n_variants
        self.max_regen_rounds = max_regen_rounds
        self.min_score = min_score
        self.model = model

    # ------------------------------------------------------------------ API

    def run(
        self,
        transcript_segments: List[Dict[str, Any]],
        target_shots: int,
        project_dir: Optional[Path] = None,
        target_duration: Optional[float] = None,
        on_progress: Optional[Callable[[], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """Slice -> best-of-N rewrite -> judge -> regenerate. Returns (vi, en, result)."""
        started = time.time()
        _beat(on_progress)
        slices_vi, slices_en = self._slice(transcript_segments, target_shots)
        if not slices_vi:
            raise ScriptFactoryError("Semantic slicing produced no scenes")
        expected_count = len(slices_vi)
        transcript_text = " ".join(str(s.get("text", "")) for s in transcript_segments)
        outline = self._build_outline(transcript_text, on_progress)
        outline_text = self._outline_block(outline)
        if outline:
            logger.info(
                "Script outline: %d timeline events, %d acts, banned=%s",
                len(outline.get("timeline") or []),
                len(outline.get("acts") or []),
                (outline.get("banned") or [])[:6],
            )
        else:
            logger.warning("Global outline unavailable; variants write without a timeline spine")
        from services.feedback_store import build_feedback_brief
        feedback_brief = build_feedback_brief()
        if feedback_brief:
            logger.info("Script factory folding retention feedback brief into variants/judge")

        best: Optional[Variant] = None
        rounds: List[Dict[str, Any]] = []
        judge_feedback = ""
        total_calls = 0

        for round_idx in range(1, self.max_regen_rounds + 2):
            _beat(on_progress)
            variants: List[Variant] = []
            for angle in VARIANT_ANGLES[: self.n_variants]:
                variant = self._make_variant(
                    angle, slices_vi, slices_en, transcript_text, judge_feedback,
                    feedback_brief, on_progress, outline_text,
                )
                total_calls += max(1, (len(slices_vi) + VARIANT_CHUNK - 1) // VARIANT_CHUNK)
                if variant.valid:
                    score, breakdown, fb = self._judge(variant, transcript_text, feedback_brief)
                    total_calls += 1
                    variant.score, variant.breakdown, variant.judge_feedback = score, breakdown, fb
                else:
                    variant.score = 0.0
                variant.metrics = self._mechanical_metrics(variant.scenes_vi)
                variants.append(variant)

            round_best = max(variants, key=lambda v: v.score)
            rounds.append({
                "round": round_idx,
                "scores": [
                    {"angle": v.angle["id"], "score": v.score, "valid": v.valid,
                     "invalid_reason": v.invalid_reason}
                    for v in variants
                ],
                "best_angle": round_best.angle["id"] if round_best.valid else None,
                "best_score": round_best.score if round_best.valid else 0.0,
            })
            logger.info(
                "Script factory round %d: scores=%s",
                round_idx,
                {v.angle["id"]: round(v.score, 1) for v in variants},
            )
            for v in variants:
                if not v.valid:
                    logger.warning(
                        "Variant %s invalid: %s", v.angle["id"], v.invalid_reason
                    )

            if best is None or round_best.score > best.score:
                best = round_best
            if best.valid and best.score >= self.min_score:
                break
            judge_feedback = round_best.judge_feedback or "Định dạng đầu ra không hợp lệ, cần đúng số cảnh yêu cầu."

        if best is None or not best.valid:
            raise ScriptFactoryError("All script variants were invalid across all rounds")

        # The global edit + targeted repair runs on the winner only: chunked
        # writing cannot see its own repetition or its own chronology, so this
        # is the only pass that judges the script as one text.
        revision = self._revise_script(
            best, transcript_text, outline_text, feedback_brief, on_progress
        )

        # Post-selection repairs fix the structural checks the judge scores but
        # cannot enforce (hook window, callback echo) and top up a script that is
        # still far short of the requested duration.
        post_repairs = self._post_process_best(
            best, transcript_text, target_duration, on_progress
        )

        # Phase 8: the external prose editor is the last stage allowed to touch the
        # words, and only if its rewrite turns no QA check that the internal script
        # had passed and actually rewrote some of them.
        drafted = [
            (sc.get("text"), en.get("text"), sc.get("duration"), en.get("duration"))
            for sc, en in zip(best.scenes_vi, best.scenes_en)
        ]
        polish = self.polish_scenes(best.scenes_vi, best.scenes_en, on_progress)
        if polish.get("accepted"):
            score_before = best.score
            polish["score_before"] = round(score_before, 1)
            score_after, breakdown, fb = self._judge(best, transcript_text, feedback_brief)
            polish["score_after"] = round(score_after, 1)
            if score_after < self.min_score <= score_before:
                # QA A01 grades chosen_score against this same floor, so a rewrite
                # that tips a passing script under it would only be discovered at
                # the last gate — after the money was spent. The drafted words come
                # back; the polish report keeps the evidence.
                for scene, en_scene, row in zip(best.scenes_vi, best.scenes_en, drafted):
                    scene["text"], en_scene["text"] = row[0], row[1]
                    scene["duration"], en_scene["duration"] = row[2], row[3]
                polish["accepted"] = False
                polish["decision"] = "judge_below_floor"
                polish["metrics_after"] = self._mechanical_metrics(best.scenes_vi)
                logger.warning(
                    "Prose polish reverted: judge %.1f -> %.1f, floor %.1f",
                    score_before, score_after, self.min_score,
                )
            else:
                best.score, best.breakdown, best.judge_feedback = score_after, breakdown, fb
        best.metrics = polish.get("metrics_after") or best.metrics

        result = {
            "engine": "script_factory_best_of_n",
            "chosen_angle": best.angle["id"],
            "chosen_score": best.score,
            "min_score": self.min_score,
            "rounds": rounds,
            "total_rounds": len(rounds),
            "outline": outline,
            "revision": revision,
            "scene_count": len(best.scenes_vi),
            "expected_scene_count": expected_count,
            "budget_report": best.budget_report,
            "mechanical_metrics": best.metrics,
            "judge_breakdown": best.breakdown,
            "judge_feedback": best.judge_feedback,
            "post_repairs": post_repairs,
            "polish": polish,
            "duration_sec": round(time.time() - started, 1),
        }
        if project_dir is not None:
            out = Path(project_dir) / "algorithm_result.json"
            out.write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return best.scenes_vi, best.scenes_en, result

    # -------------------------------------------------------------- steps

    def _slice(
        self, transcript_segments: List[Dict[str, Any]], target_shots: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from services.script_auditor import build_semantic_dual_scripts_from_transcript

        slices = build_semantic_dual_scripts_from_transcript(
            transcript_segments, target_shots=target_shots
        )
        if not slices:
            raise ScriptFactoryError(
                "Semantic slicing unavailable (xKiro gateway failure or no key)"
            )
        return slices

    def _make_variant(
        self,
        angle: Dict[str, str],
        base_vi: List[Dict[str, Any]],
        base_en: List[Dict[str, Any]],
        transcript_text: str,
        judge_feedback: str,
        feedback_brief: str = "",
        on_progress: Optional[Callable[[], None]] = None,
        outline: str = "",
    ) -> Variant:
        """Rewrite ALL base scenes into one variant, chunked to respect max_tokens."""
        variant = Variant(angle=angle)
        scenes_vi: List[Dict[str, Any]] = []
        scenes_en: List[Dict[str, Any]] = []

        for chunk_start in range(0, len(base_vi), VARIANT_CHUNK):
            _beat(on_progress)
            chunk_vi = base_vi[chunk_start: chunk_start + VARIANT_CHUNK]
            chunk_en = base_en[chunk_start: chunk_start + VARIANT_CHUNK]
            # Phrases this variant already leaned on. Without it every chunk
            # independently picks the same metaphor ("nhà kho dột" x8).
            used_phrases = self._overused_phrases(
                [str(sc.get("text", "")) for sc in scenes_vi], min_count=2, limit=12
            )
            # ...and the beats it already told, so the next chunk does not
            # re-narrate the same event in fresh words.
            beats = self._beats_already_told(scenes_vi)
            try:
                part_vi, part_en = self._rewrite_chunk_robust(
                    angle, chunk_vi, chunk_en, transcript_text, judge_feedback,
                    chunk_start, len(base_vi), feedback_brief, outline, used_phrases,
                    beats,
                )
            except Exception as exc:
                variant.invalid_reason = f"rewrite failed: {exc}"[:200]
                return variant
            scenes_vi.extend(part_vi)
            scenes_en.extend(part_en)

        if len(scenes_vi) != len(base_vi):
            variant.invalid_reason = (
                f"expected {len(base_vi)} scenes, got {len(scenes_vi)}"
            )
            return variant

        self._repair_word_budget(
            angle, scenes_vi, scenes_en, transcript_text, on_progress
        )
        report = self._acceptance_report(scenes_vi, scenes_en)
        variant.budget_report = report
        if not report["ok"]:
            variant.invalid_reason = report["reason"]
            return variant

        variant.scenes_vi = scenes_vi
        variant.scenes_en = scenes_en
        variant.valid = True
        return variant

    def _acceptance_report(
        self, scenes_vi: List[Dict[str, Any]], scenes_en: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Variant verdict derived from the QA scorecard, not a stricter copy.

        S09 grades scene durations and S10 the mean, and both are already
        enforced by deriving every duration from its word count inside the 4-8s
        window — a 13-word scene is a legal 195 WPM read at the 4s floor. The
        per-scene word ratio is a repair target (it aims the script at ~6s
        scenes), never a veto: rejecting on it discarded two of three variants
        in a live run, spending a third of the script phase on rewrites whose
        only flaw was pacing the scorecard accepts.

        The mean-duration band is a planning target too, not a verdict. A live
        run rejected a variant whose only flaw was 6.68s mean against a 6.5s
        ceiling — content the scorecard's own S11 accepts and the owner could
        judge at Gate 1. It is reported as a warning; S10 still grades it.
        """
        total = len(scenes_vi)
        if not total:
            return {"ok": False, "reason": "no scenes produced", "ratio": 0.0}
        violations = [_scene_violations(vi, en) for vi, en in zip(scenes_vi, scenes_en)]
        kinds = [k for row in violations for k in row]
        offenders = sum(1 for row in violations if row)
        ratio = (total - offenders) / total
        metrics = self._mechanical_metrics(scenes_vi)
        wpm = float(metrics.get("estimated_wpm") or 0.0)
        mean_dur = float(metrics.get("mean_scene_duration") or 0.0)
        # No single duration value vetoes a variant: durations are derived from
        # the legal word window, so a mean outside the band is content S10 grades
        # and S11 tolerates. Keep it visible in the artifact instead of discarding.
        warnings: List[str] = []
        if not MEAN_DUR_RANGE[0] <= mean_dur <= MEAN_DUR_RANGE[1]:
            warnings.append(
                f"mean scene duration {mean_dur:.2f}s outside "
                f"{MEAN_DUR_RANGE[0]}-{MEAN_DUR_RANGE[1]}s"
            )
        return {
            "ok": True,
            "target_met": ratio >= MIN_BUDGET_RATIO,
            "reason": "",
            "warnings": warnings,
            "ratio": round(ratio, 3),
            "offenders": offenders,
            "vi_low": kinds.count("vi_low"),
            "vi_high": kinds.count("vi_high"),
            "en_high": kinds.count("en_high"),
            "wpm": wpm,
            "mean_scene_duration": mean_dur,
        }

    @staticmethod
    def _budget_offenders(
        scenes_vi: List[Dict[str, Any]], scenes_en: List[Dict[str, Any]]
    ) -> List[int]:
        """Work list for the repair loop: indices of scenes breaking any rule."""
        return [
            int(vi.get("index", 0))
            for vi, en in zip(scenes_vi, scenes_en)
            if _scene_violations(vi, en)
        ]

    # -------------------------------------------------- post-selection repairs

    def _post_process_best(
        self,
        best: Variant,
        transcript_text: str,
        target_duration: Optional[float],
        on_progress: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        vi, en = best.scenes_vi, best.scenes_en
        _beat(on_progress)
        hook = self._repair_hook(best.angle, vi, en, transcript_text)
        _beat(on_progress)
        callback = self._repair_callback(best.angle, vi, en, transcript_text)
        _beat(on_progress)
        expansion = self._expand_toward_target(
            best.angle, vi, en, transcript_text, target_duration, on_progress
        )
        # QA S13 keys off style keywords, and the rewrite model drops the style
        # prefix on some scenes and repeats "photorealistic" even when forbidding
        # it. Normalize once here so every stored shot prompt carries the look.
        restyled = 0
        for scene in (*vi, *en):
            before = str(scene.get("prompt", ""))
            after = ensure_style_prefix(before)
            if after != before:
                scene["prompt"] = after
                restyled += 1
        padded = self._fit_reading_rate(vi, en)
        best.metrics = self._mechanical_metrics(vi)
        logger.info(
            "Post-repairs: hook=%s callback=%s expansion=%s restyled_prompts=%d padded_sec=%.2f",
            {k: hook.get(k) for k in ("word_counts", "duration_acc", "ok")},
            {k: callback.get(k) for k in ("ok", "has_close")},
            {k: expansion.get(k) for k in ("calls", "final_duration_sec", "final_drift")},
            restyled,
            padded,
        )
        return {"hook": hook, "callback": callback, "duration_expansion": expansion}

    @staticmethod
    def _hook_counts(scenes_vi: List[Dict[str, Any]]) -> List[int]:
        return [
            len(str(sc.get("text", "")).split())
            for sc in scenes_vi[:HOOK_SCENE_COUNT]
        ]

    @classmethod
    def _hook_compliant(cls, scenes_vi: List[Dict[str, Any]]) -> bool:
        if len(scenes_vi) < HOOK_SCENE_COUNT:
            return True
        counts = cls._hook_counts(scenes_vi)
        total = sum(counts)
        return (
            all(HOOK_MIN_WORDS <= c <= HOOK_MAX_WORDS for c in counts)
            and HOOK_TARGET_WORDS[0] <= total <= HOOK_TARGET_WORDS[1]
        )

    @staticmethod
    def _callback_compliant(scenes_vi: List[Dict[str, Any]]) -> bool:
        if len(scenes_vi) < 2:
            return True
        first_words = set(re.findall(r"\w{5,}", str(scenes_vi[0].get("text", "")).lower()))
        last_text = str(scenes_vi[-1].get("text", "")).lower()
        last_words = set(re.findall(r"\w{5,}", last_text))
        has_close = any(
            w in last_text
            for w in ("quay lại", "câu trả lời", "bài học", "như vậy", "chính là")
        )
        return bool(first_words & last_words) or has_close

    def _repair_hook(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        transcript_text: str,
    ) -> Dict[str, Any]:
        """Force the four opening scenes to cover the 30s hook (QA S16)."""
        if len(scenes_vi) < HOOK_SCENE_COUNT:
            return {"applicable": False}
        from pipeline.llm_client import chat_json

        feedback = ""
        calls = 0
        formula_id = ""
        for _ in range(MAX_REPAIR_ROUNDS):
            if self._hook_compliant(scenes_vi):
                break
            items = [
                {
                    "index": int(scenes_vi[i].get("index", i + 1)),
                    "situation": str(scenes_vi[i].get("situation", ""))[:300],
                    "voiceover_vi": str(scenes_vi[i].get("text", "")),
                    "voiceover_en": str(scenes_en[i].get("text", "")),
                }
                for i in range(HOOK_SCENE_COUNT)
            ]
            user_prompt = (
                f"{feedback}"
                f"GÓC KỂ: {angle['label']} — {angle['brief']}\n\n"
                f"TRANSCRIPT NGUỒN (chỉ dùng sự kiện từ đây):\n\"\"\"{transcript_text[:4000]}\"\"\"\n\n"
                "CHỌN ĐÚNG MỘT CÔNG THỨC HOOK dưới đây và viết 4 cảnh theo đúng công thức đó "
                "(ghi \"cong_thuc\": \"<id>\" vào JSON trả về):\n"
                f"{hook_formula_catalog()}\n\n"
                "BỐN CẢNH MỞ ĐẦU CẦN VIẾT LẠI (HOOK 30 GIÂY):\n"
                f"{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
                f"Yêu cầu: giữ nguyên index và thứ tự. MỖI voiceover_vi {HOOK_MIN_WORDS}-{HOOK_MAX_WORDS} từ; "
                f"TỔNG 4 cảnh {HOOK_TARGET_WORDS[0]}-{HOOK_TARGET_WORDS[1]} từ. "
                "Cảnh 1 phải mở bằng số liệu khủng hoặc nghịch lý và kết bằng câu hỏi treo. "
                "Đan xen câu ngắn dứt khoát (<= 6 từ), không chào hỏi, chỉ dùng số liệu có trong transcript. "
                f"voiceover_en tối đa {EN_MAX_WORDS} từ, khớp ý từng cảnh.\n"
                "Trả về duy nhất JSON object: "
                '{"scenes": [{"index": <int>, "voiceover_vi": "...", "voiceover_en": "..."}], "cong_thuc": "<id>"}'
            )
            try:
                parsed = chat_json(
                    REPAIR_SYSTEM_PROMPT, user_prompt,
                    temperature=0.4, max_tokens=3000, model=self.model,
                )
                calls += 1
            except Exception as exc:
                logger.warning("Hook repair call failed: %s", exc)
                break
            if isinstance(parsed, dict):
                formula_id = str(parsed.get("cong_thuc") or "").strip() or formula_id
            repaired = parsed.get("scenes") if isinstance(parsed, dict) else None
            if not isinstance(repaired, list):
                continue
            by_index = {int(scenes_vi[i].get("index", i + 1)): i for i in range(HOOK_SCENE_COUNT)}
            for rec in repaired:
                if not isinstance(rec, dict):
                    continue
                try:
                    pos = by_index.get(int(rec.get("index")))
                except (TypeError, ValueError):
                    continue
                if pos is None:
                    continue
                text_vi = str(rec.get("voiceover_vi", "")).strip()
                text_en = str(rec.get("voiceover_en", "")).strip()
                if text_vi:
                    scenes_vi[pos]["text"] = text_vi
                    dur = _reading_duration(len(text_vi.split()))
                    scenes_vi[pos]["duration"] = dur
                    scenes_en[pos]["duration"] = dur
                if text_en:
                    scenes_en[pos]["text"] = text_en
            counts = self._hook_counts(scenes_vi)
            feedback = (
                f"LẦN TRƯỚC CHƯA ĐẠT: số từ từng cảnh {counts} (tổng {sum(counts)}). "
                f"Cần {HOOK_MIN_WORDS}-{HOOK_MAX_WORDS} từ mỗi cảnh và tổng "
                f"{HOOK_TARGET_WORDS[0]}-{HOOK_TARGET_WORDS[1]}.\n\n"
            )

        # The repair loop above accepts whatever the model last returned. A
        # 34-word line cannot be read inside the 8s scene ceiling, so cap the
        # four hook scenes deterministically before fitting the 30s window.
        trimmed = 0
        for i in range(HOOK_SCENE_COUNT):
            text = str(scenes_vi[i].get("text", ""))
            capped = _trim_to_word_cap(text, HOOK_MAX_WORDS)
            if capped != text:
                scenes_vi[i]["text"] = capped
                dur = _reading_duration(len(capped.split()))
                scenes_vi[i]["duration"] = dur
                scenes_en[i]["duration"] = dur
                trimmed += 1

        if not self._hook_compliant(scenes_vi):
            logger.warning(
                "Hook repair left counts %s (target %s, total %s)",
                self._hook_counts(scenes_vi), (HOOK_MIN_WORDS, HOOK_MAX_WORDS), HOOK_TARGET_WORDS,
            )
        acc = self._fit_hook_window(scenes_vi, scenes_en)
        return {
            "applicable": True,
            "calls": calls,
            "trimmed": trimmed,
            "formula": formula_id,
            "word_counts": self._hook_counts(scenes_vi),
            "machine_hook_score": score_hook(str(scenes_vi[0].get("text", ""))),
            "duration_acc": round(acc, 2),
            "ok": self._hook_compliant(scenes_vi) and acc <= 32.0,
        }

    @staticmethod
    def _fit_reading_rate(
        scenes_vi: List[Dict[str, Any]], scenes_en: List[Dict[str, Any]]
    ) -> float:
        """Stretch placeholder durations until the aggregate rate clears S08.

        A scene over the word window is capped at 8s, which on its own pushes the
        whole script above the 210 WPM ceiling. Durations are planning
        placeholders (Gate 2 re-derives them from real speech), so the missing
        seconds go to scenes that still have room inside the 4-8s window.
        """
        words = sum(len(str(sc.get("text", "")).split()) for sc in scenes_vi)
        total = sum(float(sc.get("duration", 0.0)) for sc in scenes_vi)
        if not words or not total:
            return 0.0
        needed = words * 60.0 / WPM_RANGE[1] - total
        added = 0.0
        for i, scene in enumerate(scenes_vi):
            if needed - added <= 0.0005:
                break
            current = float(scene.get("duration", 0.0))
            headroom = MAX_SCENE_DURATION - current
            if headroom <= 0:
                continue
            stretched = min(
                MAX_SCENE_DURATION,
                math.ceil((current + min(headroom, needed - added)) * 1000) / 1000,
            )
            scene["duration"] = stretched
            if i < len(scenes_en):
                scenes_en[i]["duration"] = stretched
            added += stretched - current
        return round(added, 3)

    @staticmethod
    def _fit_hook_window(
        scenes_vi: List[Dict[str, Any]], scenes_en: List[Dict[str, Any]]
    ) -> float:
        """Land QA S16's 30s window inside the hook and under its 32s ceiling.

        S16 reads the cumulative duration up to the scene that reaches 30s. Four
        hook scenes adding up to less than that push the window into scene 5,
        where one more scene overshoots 32s (live: 28.86s of hook + a 4.3s scene
        5 = 33.15s). Durations are placeholders that Gate 2 re-derives from real
        speech, so the hook may be stretched inside the 4-8s band.
        """
        hook_count = min(HOOK_SCENE_COUNT, len(scenes_vi))
        if not hook_count:
            return 0.0
        hook = scenes_vi[:hook_count]
        acc = sum(float(sc.get("duration", 0.0)) for sc in hook)

        if acc < HOOK_BOUNDARY_SEC:
            # Stretch back from the last hook scene: it is the scene that decides
            # where the 30s window closes.
            need = HOOK_BOUNDARY_SEC + 0.05 - acc
            for i in range(hook_count - 1, -1, -1):
                if need <= 0.0005:
                    break
                current = float(hook[i].get("duration", 0.0))
                grow = min(MAX_SCENE_DURATION - current, need)
                if grow <= 0:
                    continue
                new_dur = round(current + grow, 3)
                hook[i]["duration"] = new_dur
                if i < len(scenes_en):
                    scenes_en[i]["duration"] = new_dur
                need -= new_dur - current
        elif acc > HOOK_BOUNDARY_SEC + 1.2:
            # Over the ceiling: trim, but never below the natural reading time or
            # the aggregate rate breaks S08.
            overshoot = acc - (HOOK_BOUNDARY_SEC + 0.4)
            for i in range(hook_count - 1, -1, -1):
                if overshoot <= 0.0005:
                    break
                current = float(hook[i].get("duration", 0.0))
                natural = _reading_duration(len(str(hook[i].get("text", "")).split()))
                new_dur = round(max(natural, current - overshoot), 3)
                overshoot -= current - new_dur
                hook[i]["duration"] = new_dur
                if i < len(scenes_en):
                    scenes_en[i]["duration"] = new_dur

        return sum(float(sc.get("duration", 0.0)) for sc in hook)

    def _repair_callback(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        transcript_text: str,
    ) -> Dict[str, Any]:
        """Make the last scene echo the hook scene or land an explicit close (QA S17)."""
        if len(scenes_vi) < 2:
            return {"applicable": False, "ok": True}
        if self._callback_compliant(scenes_vi):
            return {"applicable": True, "rewritten": False, "ok": True}
        from pipeline.llm_client import chat_json

        hook_text = " ".join(str(sc.get("text", "")) for sc in scenes_vi[:HOOK_SCENE_COUNT])
        last = scenes_vi[-1]
        feedback = ""
        calls = 0
        for _ in range(MAX_REPAIR_ROUNDS):
            user_prompt = (
                f"{feedback}"
                f"GÓC KỂ: {angle['label']} — {angle['brief']}\n\n"
                f"TRANSCRIPT NGUỒN (chỉ dùng sự kiện từ đây):\n\"\"\"{transcript_text[:4000]}\"\"\"\n\n"
                "CẢNH KẾT CẦN VIẾT LẠI (CALLBACK):\n"
                f"{json.dumps([{ 'index': int(last.get('index', len(scenes_vi))), 'situation': str(last.get('situation', ''))[:300], 'voiceover_vi': str(last.get('text', '')), 'voiceover_en': str(scenes_en[-1].get('text', '')) }], ensure_ascii=False, indent=1)}\n\n"
                f"HOOK MỞ ĐẦU (phải được giải tỏa / nhắc lại):\n\"\"\"{hook_text[:1200]}\"\"\"\n\n"
                f"Yêu cầu: viết lại CHỈ cảnh cuối thành câu chốt {VI_MIN_WORDS}-{VI_MAX_WORDS} từ. "
                "BẮT BUỘC lặp lại một từ khóa dài (>= 5 ký tự) đã xuất hiện ở cảnh 1, "
                "hoặc dùng đúng một cụm chốt: 'quay lại', 'câu trả lời', 'bài học', 'như vậy', 'chính là'. "
                "Câu chốt phải trả lời trực tiếp câu hỏi treo của hook, không mở vòng tò mò mới. "
                f"voiceover_en tối đa {EN_MAX_WORDS} từ.\n"
                "Trả về duy nhất JSON object: "
                '{"scenes": [{"index": <int>, "voiceover_vi": "...", "voiceover_en": "..."}]}'
            )
            try:
                parsed = chat_json(
                    REPAIR_SYSTEM_PROMPT, user_prompt,
                    temperature=0.4, max_tokens=1200, model=self.model,
                )
                calls += 1
            except Exception as exc:
                logger.warning("Callback repair call failed: %s", exc)
                break
            repaired = parsed.get("scenes") if isinstance(parsed, dict) else None
            rec = repaired[0] if isinstance(repaired, list) and repaired else None
            if isinstance(rec, dict):
                text_vi = str(rec.get("voiceover_vi", "")).strip()
                text_en = str(rec.get("voiceover_en", "")).strip()
                if text_vi:
                    last["text"] = text_vi
                    dur = _reading_duration(len(text_vi.split()))
                    last["duration"] = dur
                    scenes_en[-1]["duration"] = dur
                if text_en:
                    scenes_en[-1]["text"] = text_en
            if self._callback_compliant(scenes_vi):
                break
            feedback = (
                "LẦN TRƯỚC CHƯA ĐẠT CALLBACK: cảnh cuối không lặp từ khóa nào của cảnh 1 "
                "và không có cụm chốt. Viết lại đúng yêu cầu.\n\n"
            )
        ok = self._callback_compliant(scenes_vi)
        if not ok:
            logger.warning("Callback repair still non-compliant after %d calls", calls)
        last_text = str(scenes_vi[-1].get("text", "")).lower()
        return {
            "applicable": True,
            "calls": calls,
            "rewritten": calls > 0,
            "ok": ok,
            "has_close": any(
                w in last_text
                for w in ("quay lại", "câu trả lời", "bài học", "như vậy", "chính là")
            ),
        }

    def _expand_toward_target(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        transcript_text: str,
        target_duration: Optional[float],
        on_progress: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Top up short scripts by rewriting the leanest scenes to 23-24 words."""
        if not target_duration or target_duration <= 0:
            return {"applicable": False}
        target = float(target_duration)
        start_total = sum(float(sc.get("duration", 0.0)) for sc in scenes_vi)
        calls = 0
        for _ in range(EXPANSION_BATCHES):
            _beat(on_progress)
            total = sum(float(sc.get("duration", 0.0)) for sc in scenes_vi)
            drift = abs(total - target) / target
            if drift <= EXPANSION_DRIFT_THRESHOLD or total > target:
                break
            candidates = [
                i
                for i in range(HOOK_SCENE_COUNT, len(scenes_vi) - 1)
                if len(str(scenes_vi[i].get("text", "")).split()) < EXPANSION_TARGET_WORDS[0]
            ]
            if not candidates:
                break
            candidates.sort(key=lambda i: len(str(scenes_vi[i].get("text", "")).split()))
            batch = candidates[:REPAIR_BATCH]
            calls += self._rewrite_scene_indices(
                angle, scenes_vi, scenes_en, batch, transcript_text,
                f"Nâng MỖI cảnh trên lên {EXPANSION_TARGET_WORDS[0]}-{EXPANSION_TARGET_WORDS[1]} từ "
                f"({EXPANSION_TARGET_WORDS[0] / WORDS_PER_SECOND:.1f}-"
                f"{EXPANSION_TARGET_WORDS[1] / WORDS_PER_SECOND:.1f} giây) bằng chi tiết grounded "
                "từ transcript; giữ nguyên ý, số liệu, tên riêng, index. "
                f"voiceover_en tối đa {EN_MAX_WORDS} từ.",
            )
        final_total = sum(float(sc.get("duration", 0.0)) for sc in scenes_vi)
        return {
            "applicable": True,
            "calls": calls,
            "target_sec": round(target, 1),
            "start_duration_sec": round(start_total, 1),
            "final_duration_sec": round(final_total, 1),
            "final_drift": round(abs(final_total - target) / target, 3),
        }

    def _rewrite_scene_indices(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        indices: List[int],
        transcript_text: str,
        instruction: str,
        context: str = "",
    ) -> int:
        from pipeline.llm_client import chat_json

        items = [
            {
                "index": int(scenes_vi[i].get("index", i + 1)),
                "situation": str(scenes_vi[i].get("situation", ""))[:300],
                "voiceover_vi": str(scenes_vi[i].get("text", "")),
                "voiceover_en": str(scenes_en[i].get("text", "")),
            }
            for i in indices
        ]
        if not items:
            return 0
        user_prompt = (
            (f"BỐI CẢNH TOÀN CỤC:\n{context}\n\n" if context else "")
            + f"GÓC KỂ: {angle['label']} — {angle['brief']}\n\n"
            f"TRANSCRIPT NGUỒN (chỉ dùng sự kiện từ đây):\n\"\"\"{transcript_text[:4000]}\"\"\"\n\n"
            "DANH SÁCH CẢNH CẦN SỬA:\n"
            f"{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
            f"{instruction} Trả về duy nhất JSON object: "
            '{"scenes": [{"index": <int>, "voiceover_vi": "...", "voiceover_en": "..."}]}'
        )
        try:
            parsed = chat_json(
                REPAIR_SYSTEM_PROMPT, user_prompt,
                temperature=0.3, max_tokens=6000, model=self.model,
            )
        except Exception as exc:
            logger.warning("Scene expansion call failed: %s", exc)
            return 0
        repaired = parsed.get("scenes") if isinstance(parsed, dict) else None
        if not isinstance(repaired, list):
            return 0
        by_index = {int(scenes_vi[i].get("index", i + 1)): i for i in indices}
        updated = 0
        for rec in repaired:
            if not isinstance(rec, dict):
                continue
            try:
                pos = by_index.get(int(rec.get("index")))
            except (TypeError, ValueError):
                continue
            if pos is None:
                continue
            text_vi = str(rec.get("voiceover_vi", "")).strip()
            text_en = str(rec.get("voiceover_en", "")).strip()
            if text_vi:
                scenes_vi[pos]["text"] = text_vi
                dur = _reading_duration(len(text_vi.split()))
                scenes_vi[pos]["duration"] = dur
                scenes_en[pos]["duration"] = dur
                updated += 1
            if text_en:
                scenes_en[pos]["text"] = text_en
        return updated

    def _repair_word_budget(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        transcript_text: str,
        on_progress: Optional[Callable[[], None]] = None,
    ) -> None:
        """Rewrite out-of-budget scenes in place until the acceptance ratio holds."""
        from pipeline.llm_client import chat_json

        by_index = {
            int(vi.get("index", 0)): (vi, scenes_en[i])
            for i, vi in enumerate(scenes_vi)
            if i < len(scenes_en)
        }
        for _ in range(MAX_REPAIR_ROUNDS):
            # The word ratio is a repair target, not a veto: keep rewriting while
            # it is short of 95%, but never spend calls once it is met.
            report = self._acceptance_report(scenes_vi, scenes_en)
            if report["ok"] and report["target_met"]:
                return
            offenders = self._budget_offenders(scenes_vi, scenes_en)
            if not offenders:
                return
            for start in range(0, len(offenders), REPAIR_BATCH):
                _beat(on_progress)
                batch = offenders[start: start + REPAIR_BATCH]
                items = []
                for idx in batch:
                    vi, en = by_index.get(idx, (None, None))
                    if vi is None:
                        continue
                    items.append({
                        "index": idx,
                        "situation": str(vi.get("situation", ""))[:300],
                        "voiceover_vi": str(vi.get("text", "")),
                        "voiceover_en": str(en.get("text", "")),
                    })
                if not items:
                    continue
                user_prompt = (
                    f"GÓC KỂ: {angle['label']} — {angle['brief']}\n\n"
                    f"TRANSCRIPT NGUỒN (chỉ dùng sự kiện từ đây):\n\"\"\"{transcript_text[:4000]}\"\"\"\n\n"
                    "DANH SÁCH CẢNH CẦN SỬA NGÂN SÁCH TỪ:\n"
                    f"{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
                    f"Viết lại CHỈ các cảnh trên: voiceover_vi đúng {VI_MIN_WORDS}-{VI_MAX_WORDS} từ, "
                    f"voiceover_en tối đa {EN_MAX_WORDS} từ, giữ nguyên index, ý nghĩa, số liệu và tên riêng. "
                    "Trả về duy nhất JSON object: "
                    '{"scenes": [{"index": <int>, "voiceover_vi": "...", "voiceover_en": "..."}]}'
                )
                try:
                    parsed = chat_json(
                        REPAIR_SYSTEM_PROMPT, user_prompt,
                        temperature=0.3, max_tokens=6000, model=self.model,
                    )
                except Exception as exc:
                    logger.warning("Word-budget repair call failed: %s", exc)
                    continue
                repaired = parsed.get("scenes") if isinstance(parsed, dict) else None
                if not isinstance(repaired, list):
                    continue
                for rec in repaired:
                    if not isinstance(rec, dict):
                        continue
                    try:
                        idx = int(rec.get("index"))
                    except (TypeError, ValueError):
                        continue
                    pair = by_index.get(idx)
                    if pair is None:
                        continue
                    vi, en = pair
                    text_vi = str(rec.get("voiceover_vi", "")).strip()
                    text_en = str(rec.get("voiceover_en", "")).strip()
                    if text_vi:
                        vi["text"] = text_vi
                        dur = _reading_duration(len(text_vi.split()))
                        vi["duration"] = dur
                        en["duration"] = dur
                    if text_en:
                        en["text"] = text_en
        report = self._acceptance_report(scenes_vi, scenes_en)
        if report["warnings"]:
            logger.warning(
                "Word-budget repair: %s (mean=%.2fs WPM=%.0f)",
                "; ".join(report["warnings"]),
                report["mean_scene_duration"], report["wpm"],
            )
        if not report["target_met"]:
            logger.warning(
                "Word-budget repair left %d/%d scenes out of budget "
                "(vi_low=%d vi_high=%d en_high=%d WPM=%.0f mean=%.2fs) — first: %s",
                report["offenders"], len(scenes_vi), report["vi_low"], report["vi_high"],
                report["en_high"], report["wpm"], report["mean_scene_duration"],
                self._budget_offenders(scenes_vi, scenes_en)[:5],
            )

    @staticmethod
    def _beats_already_told(
        scenes_vi: List[Dict[str, Any]], tail: int = BEAT_TAIL_SCENES
    ) -> List[str]:
        """The last lines written, verbatim — what the next chunk must not re-tell."""
        return [
            f"Cảnh {sc.get('index')}: {str(sc.get('text', '')).strip()[:BEAT_TEXT_CHARS]}"
            for sc in scenes_vi[-tail:]
            if str(sc.get("text", "")).strip()
        ]

    def _rewrite_chunk_robust(
        self,
        angle: Dict[str, str],
        chunk_vi: List[Dict[str, Any]],
        chunk_en: List[Dict[str, Any]],
        transcript_text: str,
        judge_feedback: str,
        chunk_start: int,
        total_scenes: int,
        feedback_brief: str = "",
        outline: str = "",
        used_phrases: Optional[List[str]] = None,
        beats: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Rewrite a chunk, halving it when the gateway returns unusable JSON."""
        try:
            return self._rewrite_chunk(
                angle, chunk_vi, chunk_en, transcript_text, judge_feedback,
                chunk_start, total_scenes, feedback_brief, outline, used_phrases,
                beats=beats,
            )
        except Exception as exc:
            if len(chunk_vi) <= SPLIT_MIN_CHUNK:
                # At the split floor there is no smaller chunk to fall back to,
                # and a 5-scene request occasionally comes back as a rewrite of
                # the whole story (23 scenes for 5 inputs). One stern retry is
                # cheaper than losing the variant to a single stray response.
                logger.warning(
                    "Rewrite chunk %d-%d failed (%s); retrying once with an exact count",
                    chunk_start + 1, chunk_start + len(chunk_vi), exc,
                )
                return self._rewrite_chunk(
                    angle, chunk_vi, chunk_en, transcript_text, judge_feedback,
                    chunk_start, total_scenes, feedback_brief, outline, used_phrases,
                    beats=beats, strict=True,
                )
            mid = len(chunk_vi) // 2
            logger.warning(
                "Rewrite chunk %d-%d failed (%s); splitting into %d+%d scenes",
                chunk_start + 1, chunk_start + len(chunk_vi), exc, mid,
                len(chunk_vi) - mid,
            )
            left_vi, left_en = self._rewrite_chunk_robust(
                angle, chunk_vi[:mid], chunk_en[:mid], transcript_text,
                judge_feedback, chunk_start, total_scenes, feedback_brief,
                outline, used_phrases, beats,
            )
            right_vi, right_en = self._rewrite_chunk_robust(
                angle, chunk_vi[mid:], chunk_en[mid:], transcript_text,
                judge_feedback, chunk_start + mid, total_scenes, feedback_brief,
                outline, used_phrases, beats,
            )
            return left_vi + right_vi, left_en + right_en

    def _rewrite_chunk(
        self,
        angle: Dict[str, str],
        chunk_vi: List[Dict[str, Any]],
        chunk_en: List[Dict[str, Any]],
        transcript_text: str,
        judge_feedback: str,
        chunk_start: int,
        total_scenes: int,
        feedback_brief: str = "",
        outline: str = "",
        used_phrases: Optional[List[str]] = None,
        beats: Optional[List[str]] = None,
        strict: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        from pipeline.llm_client import chat_json

        compact_base = [
            {
                "index": sc.get("index"),
                "situation": str(sc.get("situation", ""))[:300],
                "voiceover_vi": str(sc.get("text", "")),
                "voiceover_en": str(chunk_en[i].get("text", "")),
                "estimated_duration": sc.get("duration", 5.0),
            }
            for i, sc in enumerate(chunk_vi)
        ]

        feedback_block = (
            f"\nFEEDBACK VÒNG TRƯỚC CẦN KHẮC PHỤC (bắt buộc sửa):\n{judge_feedback}\n"
            if judge_feedback
            else ""
        )
        outline_block = (
            "\nOUTLINE TOÀN CỤC (trục thời gian + beat đã duyệt; bám đúng, "
            "KHÔNG kéo sự kiện của đoạn khác về đây):\n"
            f"{outline}\n"
            if outline
            else ""
        )
        used_block = (
            "\nCỤM TỪ/ẨN DỤ ĐÃ DÙNG Ở CÁC CẢNH TRƯỚC (cấm lặp lại; "
            "nếu buộc phải nhắc sự kiện thì đổi cách gọi):\n"
            f"{', '.join(used_phrases)}\n"
            if used_phrases
            else ""
        )
        beats_block = (
            "\nBEATS ĐÃ KỂ Ở CÁC CẢNH TRƯỚC (đọc trước khi viết — đoạn này KHÔNG được "
            "kể lại nguyên một ý đã có):\n" + "\n".join(beats) + "\n"
            "Cảnh nào trùng một beat trên thì phải mang tới dữ kiện mới, hoặc nối tiếp "
            "ý đó đi xa hơn; cấm paraphrase lại cùng một sự kiện.\n"
            if beats
            else ""
        )
        system_prompt = (
            "Bạn là biên kịch documentary tài chính đẳng cấp, viết theo bộ quy tắc "
            "UNIVERSAL_VIDEO_ALGORITHM (hook nghịch lý, chuỗi open loop lồng nhau, "
            "nhịp kể tự nhiên với câu đủ ý, ẩn dụ 3 tầng, callback kết). "
            "TUYỆT ĐỐI GROUNDED: chỉ dùng sự kiện/tên riêng/con số có trong transcript nguồn, "
            "không bịa chi tiết mới.\n\n"
            f"GÓC KỂU CHUYÊN CỦA BẠN — {angle['label']}: {angle['brief']}\n"
            "STYLE BIBLE VISUAL (bắt buộc cho visual_prompt): Clean flat vector "
            "cartoon illustration, modern explainer style, nét đều đậm vừa, "
            "gradient nhẹ trong khối phẳng, nền sáng gọn gàng có chiều sâu, "
            "TUYỆT ĐỐI KHÔNG photorealistic/3D/crayon texture.\n\n"
            + narration_laws_block()
        )
        if feedback_brief:
            system_prompt += f"\n\n{feedback_brief}"
        user_prompt = (
            f"{feedback_block}"
            f"TRANSCRIPT NGUỒN (chỉ được dùng sự kiện từ đây):\n\"\"\"{transcript_text[:6000]}\"\"\"\n\n"
            f"{outline_block}"
            f"{used_block}"
            f"{beats_block}"
            f"BỘ CẢNH CẦN VIẾT LẠI (cảnh {chunk_start + 1}-{chunk_start + len(chunk_vi)} "
            f"trên tổng {total_scenes}):\n"
            f"{json.dumps(compact_base, ensure_ascii=False, indent=1)}\n\n"
            f"Yêu cầu: viết lại TỪNG cảnh đúng thứ tự, GIỮ NGUYÊN SỐ LƯỢNG CẢNH — mảng "
            f"\"scenes\" phải có ĐÚNG {len(chunk_vi)} phần tử, không gộp cảnh, "
            f"không thêm cảnh ngoài danh sách. Với mỗi cảnh:\n"
            f"- voiceover_vi: viết như người kể chuyện đang kể cho một người bạn thông minh, "
            f"không đọc kiểu báo cáo: nối ý bằng từ nối tự nhiên ('Nhưng', 'Mà', 'Và rồi', 'Thực ra', "
            f"'Vậy là', 'Điều đáng nói là'), mỗi cảnh có ít nhất một câu dài có mệnh đề phụ; "
            f"câu rất ngắn (<= 6 từ) chỉ dùng làm đòn nhịp, TỐI ĐA 1 câu mỗi cảnh; "
            f"cấm 3 câu liên tiếp cùng cấu trúc cùng độ dài. MỖI cảnh "
            f"{VI_MIN_WORDS}-{VI_MAX_WORDS} từ ({MIN_SCENE_DURATION:.1f}-{MAX_SCENE_DURATION:.1f} giây tại nhịp "
            f"210 WPM), ưu tiên {VI_TARGET_WORDS[0]}-{VI_TARGET_WORDS[1]} từ để tổng kịch bản đủ dài; "
            f"TUYỆT ĐỐI KHÔNG dồn quá {VI_MAX_WORDS} từ vào một cảnh.\n"
            f"- Giữ đúng mốc thời gian và sự kiện của từng cảnh base: không đảo thứ tự, "
            f"không lấy sự kiện của cảnh khác đưa vào.\n"
            f"- voiceover_en: bản EN trọn ý, giữ mạch kể tự nhiên như người dẫn chương trình "
            f"(không dịch word-by-word, không cụt câu): {EN_MIN_WORDS}-"
            f"{EN_MAX_WORDS} từ mỗi cảnh vì giọng EN đọc ~160 wpm — dòng quá ngắn làm "
            f"audio EN lửng thở giữa các câu.\n"
            f"- estimated_duration: {MIN_SCENE_DURATION:.1f}-{MAX_SCENE_DURATION:.1f} giây, tự nhiên theo độ dài lời thoại.\n"
            "- situation: tình huống cụ thể để dựng hình.\n"
            "- visual_prompt: prompt tiếng Anh theo STYLE BIBLE vector cartoon.\n\n"
            "Trả về duy nhất JSON object:\n"
            '{"scenes": [{"index": <int>, "situation": "...", "voiceover_vi": "...", '
            '"voiceover_en": "...", "estimated_duration": <float>, "visual_prompt": "..."}]}'
        )
        if strict:
            user_prompt += (
                f"\n\nLƯU Ý BẮT BUỘC: lần trả lời trước sai số cảnh. Mảng \"scenes\" phải "
                f"có CHÍNH XÁC {len(chunk_vi)} phần tử — đúng {len(chunk_vi)} cảnh trong "
                f"danh sách trên, không gộp cảnh, không thêm cảnh mới, không viết lại "
                f"phần khác của bài."
            )

        parsed = chat_json(
            system_prompt, user_prompt, temperature=0.8, max_tokens=8000, model=self.model
        )
        scenes = parsed.get("scenes")
        if not isinstance(scenes, list) or len(scenes) != len(chunk_vi):
            got = len(scenes) if isinstance(scenes, list) else "invalid"
            raise ScriptFactoryError(f"variant chunk returned {got} scenes for {len(chunk_vi)} inputs")

        vi_out: List[Dict[str, Any]] = []
        en_out: List[Dict[str, Any]] = []
        for i, (sc, base) in enumerate(zip(scenes, chunk_vi)):
            text_vi = str(sc.get("voiceover_vi", "")).strip()
            if not text_vi:
                # Raise instead of returning: _rewrite_chunk_robust halves the
                # chunk and retries, rather than condemning 170 good scenes over
                # one blank line.
                raise ScriptFactoryError(
                    f"scene {chunk_start + i + 1} returned an empty voiceover_vi"
                )
            text_en = str(sc.get("voiceover_en", "") or text_vi).strip()
            dur = _reading_duration(len(text_vi.split()))
            prompt = str(sc.get("visual_prompt", "")).strip() or str(base.get("prompt", "")).strip()
            vi_out.append({
                "index": chunk_start + i + 1,
                "situation": str(sc.get("situation", base.get("situation", ""))).strip(),
                "text": text_vi,
                "prompt": prompt,
                "duration": dur,
                "is_key_scene": True,
            })
            en_out.append({
                "index": chunk_start + i + 1,
                "situation": vi_out[-1]["situation"],
                "text": text_en,
                "prompt": prompt,
                "duration": dur,
                "is_key_scene": True,
            })
        return vi_out, en_out

    # --------------------------------------------------- global coherence pass

    def _build_outline(
        self, transcript_text: str, on_progress: Optional[Callable[[], None]] = None
    ) -> Dict[str, Any]:
        """One global pass before any chunk is written.

        Chunked rewriting only ever sees ten scenes, so each chunk invents its
        own order and re-uses the same metaphor. A single outline (timeline +
        acts + open loops + banned phrases) gives every chunk one spine.
        """
        from pipeline.llm_client import chat_json

        _beat(on_progress)
        system_prompt = (
            "Bạn là kiến trúc sư kịch bản documentary. Chỉ trả về JSON thuần túy, "
            "không giải thích, không markdown."
        )
        user_prompt = (
            f"TRANSCRIPT NGUỒN:\n\"\"\"{transcript_text[:14000]}\"\"\"\n\n"
            "Dựng outline cho kịch bản documentary kể lại câu chuyện này:\n"
            "- paradox: nghịch lý/câu hỏi lớn mở video.\n"
            "- timeline: các mốc sự kiện CÓ THẬT trong transcript, ĐÚNG thứ tự thời gian "
            "(không bịa mốc mới).\n"
            "- acts: 3-6 chặng, mỗi chặng một beat kể + phạm vi cảnh ước lượng.\n"
            "- open_loops: các vòng tò mò mở/đóng theo đúng thứ tự.\n"
            "- callback: câu kết quay lại nghịch lý mở đầu.\n"
            "- banned: 5-8 cụm từ/ẩn dụ quá dễ lặp để cấm dùng lại.\n\n"
            "Trả về duy nhất JSON object: "
            '{"paradox": "...", "timeline": [{"year": "...", "event": "..."}], '
            '"acts": [{"range": "...", "beat": "..."}], "open_loops": ["..."], '
            '"callback": "...", "banned": ["..."]}'
        )
        try:
            parsed = chat_json(
                system_prompt, user_prompt, temperature=0.4, max_tokens=2500,
                model=self.model,
            )
        except Exception as exc:
            logger.warning("Outline pass failed: %s", exc)
            return {}
        if not isinstance(parsed, dict):
            raise ScriptFactoryError("outline pass returned a non-object")
        if not (parsed.get("timeline") or parsed.get("acts")):
            logger.warning("Outline pass returned no timeline/acts; ignoring it")
            return {}
        return parsed

    @staticmethod
    def _outline_block(outline: Dict[str, Any]) -> str:
        if not outline:
            return ""
        return json.dumps(outline, ensure_ascii=False)[:3000]

    _VI_STOPWORDS = VI_CONTENT_STOPWORDS

    @classmethod
    def _overused_phrases(
        cls, texts: List[str], min_count: int = 2, limit: int = 12
    ) -> List[str]:
        """Content 2-3 grams recurring across scenes (counter lives in the auditor)."""
        return [
            entry["phrase"]
            for entry in content_repeats(texts, min_scenes=min_count, limit=limit)
        ]

    def _global_edit_issues(
        self,
        scenes_vi: List[Dict[str, Any]],
        transcript_text: str,
        outline_text: str,
    ) -> List[Dict[str, Any]]:
        """Read the whole script once and return concrete, scene-targeted fixes."""
        from pipeline.llm_client import chat_json

        script_lines = "\n".join(
            f"Cảnh {sc['index']}: {sc['text']}" for sc in scenes_vi
        )
        system_prompt = (
            "Bạn là biên tập viên tổng của một kênh documentary. Nhiệm vụ: rà TOÀN BỘ "
            "kịch bản và chỉ ra lỗi cần sửa. Chỉ trả về JSON thuần túy."
        )
        user_prompt = (
            (f"OUTLINE ĐÃ DUYỆT:\n{outline_text}\n\n" if outline_text else "")
            + f"TRANSCRIPT NGUỒN (để kiểm tra bịa đặt):\n\"\"\"{transcript_text[:5000]}\"\"\"\n\n"
            f"TOÀN BỘ KỊCH BẢN:\n{script_lines}\n\n"
            "Rà các lỗi sau và chỉ rõ số cảnh:\n"
            "- repeat: một cụm từ/ẩn dụ bị dùng lặp quá 3 lần trong bài.\n"
            "- chronology: cảnh nhắc mốc thời gian lệch khỏi trục outline hoặc đảo thứ tự.\n"
            "- choppy: chuỗi câu cụt, cùng cấu trúc, đọc lên gượng.\n"
            "- filler: câu không thêm thông tin (sáo rỗng, cảm xúc bịa).\n"
            "- confusing: mạch kể nhảy cóc, đại từ không rõ.\n"
            "Tối đa 20 lỗi, mỗi lỗi nêu cảnh cụ thể và cách sửa cụ thể.\n"
            'Trả về duy nhất JSON object: {"issues": [{"type": "...", '
            '"scenes": [<int>], "fix": "..."}]}'
        )
        try:
            parsed = chat_json(
                system_prompt, user_prompt, temperature=0.2, max_tokens=2500,
                model=self.model,
            )
        except Exception as exc:
            logger.warning("Global edit pass failed: %s", exc)
            return []
        issues = parsed.get("issues") if isinstance(parsed, dict) else None
        if not isinstance(issues, list):
            return []
        index_set = {int(sc.get("index", i + 1)) for i, sc in enumerate(scenes_vi)}
        valid: List[Dict[str, Any]] = []
        for issue in issues[:20]:
            if not isinstance(issue, dict):
                continue
            try:
                nums = [int(n) for n in issue.get("scenes") or [] if int(n) in index_set]
            except (TypeError, ValueError):
                continue
            if not nums:
                continue
            valid.append({
                "type": str(issue.get("type", ""))[:40],
                "scenes": nums,
                "fix": str(issue.get("fix", ""))[:400],
            })
        return valid

    def _revise_script(
        self,
        variant: "Variant",
        transcript_text: str,
        outline_text: str,
        feedback_brief: str = "",
        on_progress: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Global audit -> targeted chunk repairs on the winning variant.

        Acceptance is lexicographic (S0.1): hard QA first, then repetition load,
        then the judge float. A bare `score_after >= score_before` reverted a live
        revision that had just cleared the flagged repeats out of 85 scenes and
        shipped the repetitive script instead, so a repeat-killing edit that costs
        the judge less than its noise band is kept on purpose now.
        """
        vi, en = variant.scenes_vi, variant.scenes_en
        if not vi:
            return {"applicable": False}
        _beat(on_progress)
        issues = self._global_edit_issues(vi, transcript_text, outline_text)

        texts_before = [str(sc.get("text", "")) for sc in vi]
        scene_ids = [int(sc.get("index", i + 1)) for i, sc in enumerate(vi)]
        load_before = repetition_load(texts_before, min_scenes=REPEAT_WARN_SCENES)
        repeated = load_before["phrases"]
        for entry in load_before["entries"]:
            hit_ids = [scene_ids[p - 1] for p in entry["scenes"]]
            issues.append({
                "type": "repeat",
                "scenes": hit_ids,
                "fix": (
                    f"cụm '{entry['phrase']}' xuất hiện ở {entry['count']} cảnh "
                    f"({', '.join('S%d' % s for s in hit_ids)}) — đổi cách gọi "
                    "ở các cảnh này; cảnh nào cố ý nhắc lại thì phải thêm dữ kiện mới "
                    "hoặc gói thành callback, không kể lại nguyên ý"
                ),
            })

        conflicts = numeric_conflicts(texts_before)
        for conflict in conflicts:
            scenes = sorted({scene_ids[s - 1] for g in conflict["values"].values() for s in g})
            issues.append({
                "type": "numbers",
                "scenes": scenes,
                "fix": (
                    f"'{conflict['measure']}' đang gắn {conflict['unit']} với "
                    f"{len(conflict['values'])} giá trị khác nhau "
                    f"({json.dumps(conflict['values'], ensure_ascii=False)}) — đối chiếu "
                    "transcript: nguồn có hai mốc thì nêu rõ mốc/năm ngay trong câu, "
                    "không tự chọn một số, không bịa số mới"
                ),
            })

        # Staccato detector: a scene whose sentences are nearly all <=6 words is
        # the "cụt ngủn" read-aloud failure the judge is asked to punish.
        for i, sc in enumerate(vi):
            sentences = [
                s.strip() for s in re.split(r"[.!?…]+", str(sc.get("text", ""))) if s.strip()
            ]
            if len(sentences) < 3:
                continue
            short = sum(1 for s in sentences if len(s.split()) <= 6)
            if short / len(sentences) >= 0.7:
                issues.append({
                    "type": "choppy",
                    "scenes": [int(sc.get("index", i + 1))],
                    "fix": "câu bị băm cụt — gộp thành câu đủ ý, chỉ giữ tối đa một câu ngắn để chốt",
                })

        if not issues:
            return {"applicable": True, "issues": 0, "chunks": 0, "revised_scenes": 0, "accepted": False}

        pos_by_index = {int(sc.get("index", i + 1)): i for i, sc in enumerate(vi)}
        chunk_issues: Dict[int, List[Dict[str, Any]]] = {}
        positions = set()
        for issue in issues:
            for idx in issue["scenes"]:
                pos = pos_by_index.get(idx)
                if pos is None:
                    continue
                positions.add(pos)
                chunk_issues.setdefault(pos // VARIANT_CHUNK, []).append(issue)

        snapshot_vi = [dict(sc) for sc in vi]
        snapshot_en = [dict(sc) for sc in en]
        score_before = variant.score
        revised = 0
        chunks = 0
        for chunk_id in sorted(chunk_issues)[:REVISION_MAX_CHUNKS]:
            start = chunk_id * VARIANT_CHUNK
            end = min(start + VARIANT_CHUNK, len(vi))
            # _rewrite_scene_indices takes zero-based positions, not scene numbers.
            indices = [p for p in range(start, end) if p in positions]
            if not indices:
                continue
            _beat(on_progress)
            instruction = (
                "Chỉ sửa các lỗi được liệt kê; cảnh nào không có lỗi thì giữ nguyên từng chữ.\n"
                f"LỖI CẦN SỬA: {json.dumps(chunk_issues[chunk_id], ensure_ascii=False)}\n"
                + (
                    f"CỤM TỪ BỊ LẶP TOÀN BÀI (cấm dùng lại): {', '.join(repeated)}\n"
                    if repeated
                    else ""
                )
                + f"Mỗi cảnh VI vẫn phải {VI_MIN_WORDS}-{VI_MAX_WORDS} từ, EN "
                f"{EN_MIN_WORDS}-{EN_MAX_WORDS} từ, đọc lên tự nhiên, đủ ý, không cụt; "
                "giữ nguyên số liệu, tên riêng và ý nghĩa."
            )
            updated = self._rewrite_scene_indices(
                variant.angle, vi, en, indices, transcript_text, instruction,
                context=outline_text,
            )
            chunks += 1
            revised += updated

        if not revised:
            return {
                "applicable": True, "issues": len(issues), "chunks": chunks,
                "revised_scenes": 0, "accepted": False,
            }

        self._repair_word_budget(variant.angle, vi, en, transcript_text, on_progress)
        score_after, breakdown, fb = self._judge(variant, transcript_text, feedback_brief)
        report_after = self._acceptance_report(vi, en)
        load_after = repetition_load(
            [str(sc.get("text", "")) for sc in vi], min_scenes=REPEAT_WARN_SCENES
        )
        conflicts_after = numeric_conflicts([str(sc.get("text", "")) for sc in vi])
        offenders_before = int((variant.budget_report or {}).get("offenders") or 0)
        drop = (
            (load_before["hits"] - load_after["hits"]) / load_before["hits"]
            if load_before["hits"] else 0.0
        )
        # Hard QA outranks the float: a revision that regresses word budget,
        # scene count, number conflicts or the worst repeat is thrown away even
        # if the judge liked it.
        hard_clauses = {
            "scene_count": len(vi) == len(snapshot_vi),
            "word_budget": report_after["offenders"] <= offenders_before,
            "number_conflicts": len(conflicts_after) <= len(conflicts),
            "worst_repeat": load_after["worst"] <= load_before["worst"],
        }
        hard_qa_detail = [name for name, ok in hard_clauses.items() if not ok]
        hard_qa_ok = not hard_qa_detail
        if not hard_qa_ok:
            reason = "hard_qa_regressed:" + ",".join(hard_qa_detail)
        elif score_after >= score_before:
            reason = "judge_not_worse"
        elif (
            drop >= REVISION_REPEAT_DROP_FLOOR
            and score_before - score_after <= REVISION_JUDGE_TOLERANCE
        ):
            reason = "repeat_load_dropped_within_judge_tolerance"
        else:
            reason = "judge_fell_without_repeat_gain"
        accepted = hard_qa_ok and reason != "judge_fell_without_repeat_gain"
        if accepted:
            variant.score, variant.breakdown, variant.judge_feedback = (
                score_after, breakdown, fb
            )
            variant.budget_report = report_after
        else:
            vi[:] = snapshot_vi
            en[:] = snapshot_en
            score_after = score_before
            load_after = load_before
            conflicts_after = conflicts
        logger.info(
            "Global revision: issues=%d chunks=%d scenes=%d score %.1f -> %.1f "
            "repeats %d -> %d offenders %d -> %d conflicts %d -> %d (%s)",
            len(issues), chunks, revised, score_before, score_after,
            load_before["hits"], load_after["hits"],
            offenders_before, report_after["offenders"],
            len(conflicts), len(conflicts_after),
            "kept:" + reason if accepted else "reverted:" + reason,
        )
        return {
            "applicable": True,
            "issues": len(issues),
            "chunks": chunks,
            "revised_scenes": revised,
            "score_before": round(score_before, 1),
            "score_after": round(score_after, 1),
            "accepted": accepted,
            "decision": reason,
            "hard_qa_detail": hard_qa_detail,
            "repeated_phrases": load_before["phrases"],
            "repetition_hits_before": load_before["hits"],
            "repetition_hits_after": load_after["hits"],
            "repetition_worst_before": load_before["worst"],
            "repetition_worst_after": load_after["worst"],
            "repeat_drop_pct": round(drop * 100.0, 1) if accepted else 0.0,
            "number_conflicts": len(conflicts),
            "number_conflict_measures": [c["measure"] for c in conflicts][:8],
        }

    # -------------------------------------------------- Phase 8 prose polish

    def _polish_batch_prompt(
        self,
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        start: int,
        total: int,
        corrections: Optional[Dict[int, str]] = None,
    ) -> str:
        """One chunk of the script, addressed by its ABSOLUTE scene indices.

        Each scene carries its own word budget. Rule 5 of the approved prompt asks
        for the original length, but the same prompt also quotes the 17-21 window,
        and on the first live chunk the editor aimed at the window instead: a
        14-word EN line came back at 23. The per-scene budget is the only number
        that can actually be obeyed.
        """
        size = len(scenes_vi)
        lines = []
        budgets = []
        for offset in range(size):
            index = start + offset + 1
            vi_text = str(scenes_vi[offset].get("text", ""))
            en = scenes_en[start + offset].get("text", "") if start + offset < len(scenes_en) else ""
            en_text = str(en)
            lines.append(f"[{index}] VI: {vi_text}")
            lines.append(f"[{index}] EN: {en_text}")
            budgets.append(
                f"[{index}] VI {len(vi_text.split())} từ / EN {len(en_text.split())} từ"
            )
        prompt = (
            f"Bài hoàn chỉnh có {total} cảnh (index 1..{total}). "
            f"Đợt này chỉ gửi {size} cảnh, index {start + 1}..{start + size}.\n"
            f"Viết lại đúng {size} cảnh đó và trả về đúng {size} phần tử với chính "
            "các index trên; không xuất hiện index nào khác, không dịch qua lại giữa "
            "VI và EN.\n\n"
            "KỊCH BẢN:\n" + "\n".join(lines) + "\n\n"
            "NGÂN SÁCH TỪ của TỪNG cảnh (tuỳ chỉnh 1-5 của QUY TẮC CỨNG được hiểu là "
            "số dưới đây, KHÔNG phải dải 17-21 chung chung; viết lại đúng số từ đó, "
            "chênh tối đa 3, không thêm câu chỉ để cho dài). Dải cứng máy chấm: VI "
            f"{VI_MIN_WORDS}-{VI_MAX_WORDS} từ, EN tối đa {EN_MAX_WORDS} từ; cảnh nào "
            "đã sát mép dải thì GIỮ NGUYÊN hoặc rút ngắn, tuyệt đối không nới thêm — "
            "cảnh vượt mép bị loại khỏi bản chốt:\n" + "\n".join(budgets)
        )
        if corrections:
            prompt += (
                "\n\nLẦN TRƯỚC bản này bị LOẠI vì phạm quy tắc ở chính các cảnh sau — "
                "sửa đúng lỗi đó, mọi cảnh khác giữ cách viết lần trước:\n"
                + "\n".join(f"[{index}] {detail}" for index, detail in sorted(corrections.items()))
                + "\nMã lỗi: vi_drift/en_drift = đổi quá 3 số từ; budget:vi_high = VI "
                f"vượt {VI_MAX_WORDS} từ, budget:vi_low = VI dưới {VI_MIN_WORDS} từ, "
                f"budget:en_high = EN vượt {EN_MAX_WORDS} từ; locked_tokens = con số "
                "hoặc tên viết tắt HOẶC tên riêng bị thêm/thừa/mất (trả lại đúng bộ số "
                "liệu và tên đó, không lặp lại một con số hai lần trong cùng cảnh); "
                "empty = cảnh trống."
            )
        return prompt

    def _polish_attempt(
        self,
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        start: int,
        total: int,
        corrections: Optional[Dict[int, str]] = None,
    ) -> Tuple[Optional[Dict[int, Tuple[str, str]]], Optional[Dict[str, Any]], Dict[int, List[str]], List[Any]]:
        """Ask the editor for one chunk and grade the answer scene by scene.

        (rows, chunk failure, per-scene violations, notes). A chunk failure is the
        answer not being addressable at all; a violation is one scene's text that
        cannot be re-ingested.
        """
        from pipeline.llm_client import chat_json

        chunk = scenes_vi[start:start + POLISH_CHUNK]
        want = list(range(start + 1, start + len(chunk) + 1))
        try:
            payload = chat_json(
                POLISH_SYSTEM_PROMPT,
                self._polish_batch_prompt(chunk, scenes_en, start, total, corrections),
                temperature=0.4,
                max_tokens=3000,
                model=POLISH_MODEL,
            )
        except Exception as exc:
            return None, {"stage": "call", "index": want[0], "detail": str(exc)[:300]}, {}, []
        rows = payload.get("scenes")
        if not isinstance(rows, list):
            return None, {"stage": "shape", "index": want[0], "detail": "no scenes list"}, {}, []
        by_index: Dict[int, Tuple[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                return None, {"stage": "shape", "index": want[0], "detail": "scene not an object"}, {}, []
            try:
                index = int(row.get("index"))
            except (TypeError, ValueError):
                index = -1
            by_index[index] = (
                str(row.get("voiceover_vi", "")),
                str(row.get("voiceover_en", "")),
            )
        if sorted(by_index) != want:
            return None, {
                "stage": "scene_count",
                "index": want[0],
                "detail": f"expected {want}, got {sorted(by_index)}",
            }, {}, []
        violations: Dict[int, List[str]] = {}
        for index in want:
            position = index - 1
            origin_vi = str(scenes_vi[position].get("text", ""))
            origin_en = str(scenes_en[position].get("text", "")) if position < len(scenes_en) else ""
            kinds = _polish_violations(origin_vi, origin_en, *by_index[index])
            if kinds:
                violations[index] = kinds
        notes = [n for n in (payload.get("notes") or []) if isinstance(n, dict)]
        return by_index, None, violations, notes

    def polish_scenes(
        self,
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        on_progress: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """Rewrite the finished script with an xKiro prose model, contract-gated.

        The external editor is untrusted by construction: it does not know the
        machine constraints, so every scene it returns is checked against the
        re-ingest contract (same indices, the scene's own word budget +-3 without
        leaving the band it already sat in, identical numbers and all-caps names).
        A scene that breaks it is asked for once more with the exact reason and, if
        it breaks again, is dropped while its chunk neighbours are kept — but a
        rewrite that has to drop a quarter of the script is not an editor, it is
        noise, and the whole pass is thrown away. What survives is graded by the
        scorecard, not by a taste of the editor's work: no cross-scene check that
        the internal script passed may turn red (number conflicts, the S18 worst
        repeated phrase, the S07 short-sentence floor), and the pass has to have
        actually changed words.
        """
        total = len(scenes_vi)
        if not total or not scenes_en:
            return {"applicable": False, "accepted": False, "decision": "no scenes"}

        texts_before = [str(sc.get("text", "")) for sc in scenes_vi]
        load_before = repetition_load(texts_before, min_scenes=REPEAT_WARN_SCENES)
        conflicts_before = numeric_conflicts(texts_before)
        offenders_before = sum(
            1 for vi, en in zip(scenes_vi, scenes_en) if _scene_violations(vi, en)
        )
        nat_before = self._naturalness_report("\n".join(texts_before))
        duration_before = sum(float(sc.get("duration") or 0.0) for sc in scenes_vi)
        snapshot = [
            (
                str(sc.get("text", "")),
                str(en.get("text", "")),
                float(sc.get("duration") or 0.0),
            )
            for sc, en in zip(scenes_vi, scenes_en)
        ]

        notes: List[Dict[str, Any]] = []
        rejection: Optional[Dict[str, Any]] = None
        calls = 0
        retries = 0
        skipped: List[Dict[str, Any]] = []
        pending: Dict[int, Tuple[str, str]] = {}

        for start in range(0, total, POLISH_CHUNK):
            chunk_size = len(scenes_vi[start:start + POLISH_CHUNK])
            want = list(range(start + 1, start + chunk_size + 1))
            _beat(on_progress)
            by_index, failure, violations, chunk_notes = self._polish_attempt(
                scenes_vi, scenes_en, start, total
            )
            calls += 1
            if by_index is None:
                # One retry per chunk: the gateway answers a chunk with mojibake or
                # with the wrong scene count often enough that a single miss is not
                # a reason to lose the whole script.
                _beat(on_progress)
                by_index, retry_failure, violations, chunk_notes = self._polish_attempt(
                    scenes_vi, scenes_en, start, total
                )
                calls += 1
                retries += 1
                if by_index is None:
                    rejection = retry_failure or failure
                    break
            if violations:
                _beat(on_progress)
                corrections = {
                    index: "; ".join(violations[index])[:180] for index in violations
                }
                retry_by, _, retry_violations, retry_notes = self._polish_attempt(
                    scenes_vi, scenes_en, start, total, corrections=corrections
                )
                calls += 1
                retries += 1
                if retry_by is not None and len(retry_violations) <= len(violations):
                    by_index, violations = retry_by, retry_violations
                    chunk_notes = retry_notes
            for index in want:
                if index in violations:
                    skipped.append({
                        "index": index,
                        "detail": "; ".join(violations[index])[:180],
                        "before": snapshot[index - 1][0],
                    })
                    continue
                pending[index - 1] = by_index[index]
            for note in chunk_notes:
                notes.append({**note, "chunk_first_index": want[0]})

        if rejection is None and len(skipped) > total * POLISH_MAX_SKIP_RATIO:
            rejection = {
                "stage": "scene_drops",
                "index": skipped[0]["index"],
                "detail": f"{len(skipped)}/{total} cảnh không vào được: "
                + "; ".join(str(s["detail"]) for s in skipped[:3])[:220],
            }

        report = {
            "applicable": True,
            "model": POLISH_MODEL,
            "chunks": math.ceil(total / POLISH_CHUNK),
            "calls": calls,
            "retries": retries,
            "scenes_rewritten": len(pending) if rejection is None else 0,
            "scenes_skipped": len(skipped),
            "skipped": skipped[:20],
            "notes": notes[:40],
            "rejection": rejection,
            "repetition_phrases": load_before["phrases"],
            "repetition_hits_before": load_before["hits"],
            "repetition_worst_before": load_before["worst"],
            "number_conflicts_before": len(conflicts_before),
            "offenders_before": offenders_before,
            "planned_duration_before": round(duration_before, 1),
            "naturalness_before": nat_before,
        }

        if rejection is not None:
            logger.warning(
                "Polish rejected (%s, cảnh %s): %s",
                rejection["stage"], rejection["index"], rejection["detail"][:160],
            )
            return {
                **report,
                "accepted": False,
                "decision": "rejected_" + rejection["stage"],
                "metrics_after": self._mechanical_metrics(scenes_vi),
            }

        for position, (vi_text, en_text) in pending.items():
            scenes_vi[position]["text"] = vi_text
            scenes_en[position]["text"] = en_text
            natural = _reading_duration(len(vi_text.split()))
            duration = min(
                MAX_SCENE_DURATION, max(float(scenes_vi[position].get("duration") or 0.0), natural)
            )
            scenes_vi[position]["duration"] = duration
            scenes_en[position]["duration"] = duration
        self._fit_hook_window(scenes_vi, scenes_en)
        self._fit_reading_rate(scenes_vi, scenes_en)

        texts_after = [str(sc.get("text", "")) for sc in scenes_vi]
        load_after = repetition_load(texts_after, min_scenes=REPEAT_WARN_SCENES)
        conflicts_after = numeric_conflicts(texts_after)
        offenders_after = sum(
            1 for vi, en in zip(scenes_vi, scenes_en) if _scene_violations(vi, en)
        )
        nat_after = self._naturalness_report("\n".join(texts_after))
        changed = sum(
            1 for position, (vi_text, en_text) in pending.items()
            if (vi_text, en_text) != snapshot[position][:2]
        )
        short_floor = PASS_SHORT_PCT * 100.0
        # The word budget is not graded here: the per-scene contract already refused
        # any rewrite that adds a band break, so offenders_after can only ever be
        # offenders_before or fewer. These three are the cross-scene laws a scene by
        # scene check cannot see.
        hard_clauses = {
            "number_conflicts": len(conflicts_after) <= len(conflicts_before),
            "worst_repeat": load_after["worst"] <= load_before["worst"],
            # QA S07 grades exactly one thing: the short-sentence share against its
            # floor. A polish that welds every punchline into a long clause must not
            # turn that green check red. A script already under the floor cannot be
            # pushed further down the same check, so grading the raw number here
            # would be a gate the scorecard itself does not run.
            "short_sentences": not (nat_before["punch_pct"] >= short_floor > nat_after["punch_pct"]),
        }
        hard_failed = [name for name, ok in hard_clauses.items() if not ok]
        if hard_failed:
            decision = "hard_qa_regressed:" + ",".join(hard_failed)
        elif not changed:
            # Every scene came back byte-identical: shipping this would bump
            # script_version and reopen Gate 1 for an unchanged script.
            decision = "editor_changed_nothing"
        else:
            decision = "polish_within_hard_qa"
        accepted = not hard_failed and decision == "polish_within_hard_qa"

        if not accepted:
            for position, (vi_text, en_text, duration) in enumerate(snapshot):
                scenes_vi[position]["text"] = vi_text
                scenes_en[position]["text"] = en_text
                scenes_vi[position]["duration"] = duration
                scenes_en[position]["duration"] = duration
            # *_after fields keep describing the rewrite that was thrown away:
            # that is the evidence for why the internal script stayed.

        logger.info(
            "Prose polish (%s): %d/%d cảnh viết lại (%d cảnh đổi chữ), %d cảnh bị bỏ, "
            "repeats %d -> %d, worst %d -> %d, offenders %d -> %d, conflicts %d -> %d, "
            "cv %.2f -> %.2f, câu ngắn %.1f%% -> %.1f%% => %s%s",
            POLISH_MODEL, len(pending), total, changed, len(skipped),
            load_before["hits"], load_after["hits"],
            load_before["worst"], load_after["worst"],
            offenders_before, offenders_after,
            len(conflicts_before), len(conflicts_after),
            nat_before["cv"], nat_after["cv"],
            nat_before["punch_pct"], nat_after["punch_pct"],
            "kept" if accepted else "reverted:" + decision,
            f" notes={len(notes)}" if notes else "",
        )
        return {
            **report,
            "accepted": accepted,
            "decision": decision,
            "scenes_changed": changed,
            "repetition_hits_after": load_after["hits"],
            "repetition_worst_after": load_after["worst"],
            "repeat_drop_pct": (
                round((load_before["hits"] - load_after["hits"]) / load_before["hits"] * 100.0, 1)
                if accepted and load_before["hits"] else 0.0
            ),
            "number_conflicts_after": len(conflicts_after),
            "offenders_after": offenders_after,
            "planned_duration_after": round(
                sum(float(sc.get("duration") or 0.0) for sc in scenes_vi), 1
            ),
            "naturalness_after": nat_after,
            "metrics_after": self._mechanical_metrics(scenes_vi),
        }

    def _judge(
        self, variant: Variant, transcript_text: str, feedback_brief: str = ""
    ) -> Tuple[float, Dict[str, float], str]:
        from pipeline.llm_client import chat_json

        # The judge must see the ending: rubric items 2 (open loops) and 7
        # (callback) are unjudgeable on a truncated script, and the 173-scene
        # scripts here are ~17k chars, so the old 9000-char cap hid the whole
        # back half.
        script_lines = "\n".join(
            f"Cảnh {sc['index']} ({sc['duration']:.1f}s): {sc['text']}"
            for sc in variant.scenes_vi
        )
        # The LLM judge is soft on weak hooks; give it the machine verdict on
        # the five measurable properties and force it to enforce the floor.
        hook_report = score_hook(str(variant.scenes_vi[0].get("text", "")))
        machine_hook = (
            f"\n\nĐIỂM HOOK MÁY (thang 0-100 = 60% trung bình 5 chỉ tiêu + 40% chỉ tiêu yếu nhất): "
            f"{hook_report['verdict']}; yếu nhất: {hook_report['weakest']}. "
            "Hook dưới 70 PHẢI bị trừ điểm mục 'hook' và feedback phải chỉ đúng chỉ tiêu yếu nhất cần sửa."
        )
        nat = self._naturalness_report(
            "\n".join(str(sc.get("text", "")) for sc in variant.scenes_vi)
        )
        repeated = self._overused_phrases(
            [str(sc.get("text", "")) for sc in variant.scenes_vi],
            min_count=REPEAT_WARN_SCENES,
            limit=8,
        )
        rhythm = (
            f"\n\nNHỊP ĐỌC MÁY: CV độ dài câu {nat['cv']:.2f} (tự nhiên >= {self.NATURAL_MIN_CV}); "
            f"{nat['punch_pct']:.0f}% câu ngắn <= 6 từ (dải tự nhiên "
            f"{self.NATURAL_MIN_PUNCH_PCT:.0f}-{self.NATURAL_MAX_PUNCH_PCT:.0f}%, "
            f"mục tiêu ~{TARGET_SHORT_SENTENCE_PCT * 100:.0f}%); "
            f"{nat['connectors_per_100']:.1f} từ nối/100 từ (tự nhiên >= {self.NATURAL_MIN_CONNECTORS}). "
            "CV dưới ngưỡng = câu đều đều như máy; punch_pct trên 45% = kịch bản bị băm cụt. "
            "Chỉ số nào lệch thì trừ điểm mục 'narration_rhythm' và feedback chỉ đúng lỗi nhịp cần sửa."
            + (
                f"\nCỤM TỪ LẶP TOÀN BÀI (mỗi cụm quá 3 lần phải bị trừ điểm): {', '.join(repeated)}."
                if repeated
                else ""
            )
        )
        user_prompt = (
            (f"{feedback_brief}\n\n" if feedback_brief else "")
            + f"GÓC KỂ: {variant.angle['label']} — {variant.angle['brief']}\n"
            + machine_hook
            + rhythm
            + "\n\n"
            + f"TRANSCRIPT NGUỒN (để kiểm tra GROUNDED):\n\"\"\"{transcript_text[:5000]}\"\"\"\n\n"
            f"KỊCH BẢN CẦN CHẤM:\n{script_lines}"
        )
        parsed = chat_json(
            JUDGE_RUBRIC, user_prompt, temperature=0.2, max_tokens=1000, model=self.model
        )
        try:
            score = float(parsed.get("score", 0.0))
        except (TypeError, ValueError):
            raise ScriptFactoryError(f"judge returned non-numeric score: {parsed.get('score')!r}")
        score = max(0.0, min(10.0, score))
        breakdown = parsed.get("breakdown") if isinstance(parsed.get("breakdown"), dict) else {}
        feedback = str(parsed.get("feedback", ""))[:600]
        return score, breakdown, feedback

    # ------------------------------------------------------------ metrics

    # Spoken-rhythm fingerprints. A stiff script reads as machine-gun prose:
    # every sentence the same length, no connective tissue, half the sentences
    # <= 6 words. The judge sees these numbers next to the script.
    _NATURAL_CONNECTOR_RE = re.compile(
        r"\b(nhưng|mà\b|và rồi|thực ra|điều đáng nói|vậy là|cuối cùng|hóa ra"
        r"|bởi vậy|chính vì|ngoài ra|vì thế|nói cách khác|vấn đề là|điều đó nghĩa là)\b",
        re.IGNORECASE,
    )
    NATURAL_MIN_CV = 0.45
    NATURAL_MIN_PUNCH_PCT = 35.0
    NATURAL_MAX_PUNCH_PCT = 45.0
    NATURAL_MIN_CONNECTORS = 1.5

    @classmethod
    def _naturalness_report(cls, script_text: str) -> Dict[str, float]:
        sentences = [
            s.strip() for s in re.split(r"[.!?…]+", str(script_text)) if s.strip()
        ]
        if len(sentences) < 4:
            return {"cv": 0.0, "punch_pct": 100.0, "connectors_per_100": 0.0, "sentences": len(sentences)}
        lengths = [len(s.split()) for s in sentences]
        mean = statistics.fmean(lengths)
        cv = (statistics.pstdev(lengths) / mean) if mean else 0.0
        punch_pct = 100.0 * sum(1 for n in lengths if n <= 6) / len(lengths)
        words = sum(lengths)
        connectors = len(cls._NATURAL_CONNECTOR_RE.findall(script_text))
        return {
            "cv": round(cv, 3),
            "punch_pct": round(punch_pct, 1),
            "connectors_per_100": round(connectors / words * 100.0, 2) if words else 0.0,
            "sentences": len(sentences),
        }

    def _mechanical_metrics(self, scenes_vi: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Skill-pack metrics computed in code (recorded for the Phase 4 scorecard)."""
        import re

        texts = [str(sc.get("text", "")).strip() for sc in scenes_vi if str(sc.get("text", "")).strip()]
        sentences: List[str] = []
        for t in texts:
            sentences.extend([s.strip() for s in re.split(r"[.!?…]+", t) if s.strip()])
        word_counts = [len(s.split()) for s in sentences]
        short_pct = (
            sum(1 for wc in word_counts if wc <= 6) / len(word_counts) if word_counts else 0.0
        )
        total_words = sum(len(t.split()) for t in texts)
        total_dur = sum(float(sc.get("duration", 0.0)) for sc in scenes_vi)
        wpm = (total_words / total_dur * 60.0) if total_dur > 0 else 0.0
        question_count = sum(1 for s in sentences if s.endswith("?"))
        durations = [float(sc.get("duration", 0.0)) for sc in scenes_vi]
        natural = self._naturalness_report("\n".join(texts))
        load = repetition_load(texts)
        conflicts = numeric_conflicts(texts)
        return {
            "scene_count": len(scenes_vi),
            "repeat_phrases": load["phrases"],
            "repeat_hits": load["hits"],
            "repeat_worst_scenes": load["worst"],
            "number_conflicts": [
                f"{c['measure']} {c['unit']}: "
                + ", ".join(f"{v}×S{'/'.join(map(str, s))}" for v, s in c["values"].items())
                for c in conflicts
            ][:6],
            "total_duration_sec": round(total_dur, 1),
            "mean_scene_duration": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "min_scene_duration": round(min(durations), 2) if durations else 0.0,
            "max_scene_duration": round(max(durations), 2) if durations else 0.0,
            "sentence_count": len(sentences),
            "short_sentence_pct": round(short_pct, 3),
            "target_short_sentence_pct": TARGET_SHORT_SENTENCE_PCT,
            "sentence_length_cv": natural["cv"],
            "connectors_per_100": natural["connectors_per_100"],
            "estimated_wpm": round(wpm, 1),
            "wpm_in_range": MIN_WPM <= wpm <= MAX_WPM if wpm else False,
            "question_count": question_count,
            "total_words": total_words,
        }
