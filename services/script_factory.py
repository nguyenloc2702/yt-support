"""Script factory: best-of-N grounded rewrite with LLM judging (Phase 2).

Takes the semantic slices produced by script_auditor.build_semantic_dual_scripts_from_transcript
and turns them into production-ready VI/EN scripts fused with the
UNIVERSAL_VIDEO_ALGORITHM skill pack:

1. N variants (default 3) are rewritten from the same slices with different
   creative angles, grounded strictly in the source transcript facts.
2. Every variant is scored by an LLM judge on the skill-pack rubric
   (hook paradox, open loops, 54/46 pacing, WPM, grounding, terminology).
   Mechanical metrics (durations, word counts, short-sentence ratio) are
   computed in code and recorded alongside.
3. If no variant reaches min_score, the judge feedback is fed back and up to
   `max_regen_rounds` extra rounds are generated.
4. The full decision trail is persisted to algorithm_result.json so every
   later phase (QA, gates) can audit how the script was chosen.

No silent fallbacks: slicing or all-invalid variants raise ScriptFactoryError.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logging_config import get_logger

logger = get_logger(__name__)

MIN_SCORE = 8.0
N_VARIANTS = 3
MAX_REGEN_ROUNDS = 2
VARIANT_CHUNK = 20  # scenes per rewrite call (keeps output under max_tokens)
# Skill-pack pacing anchor: scene duration is computed in code from the VI word
# count (180 WPM) instead of trusting the LLM's duration guess — keeps the plan
# timeline consistent with real TTS output (xKiro VI voice ~3.6 w/s).
WORDS_PER_SECOND = 3.0

# Skill-pack mechanical targets (mirrors scripts/apply_video_algorithm.py)
TARGET_SHORT_SENTENCE_PCT = 0.54
MIN_WPM = 160.0
MAX_WPM = 200.0
TARGET_WPM = 184.8
MIN_SCENE_DURATION = 4.0
MAX_SCENE_DURATION = 8.0
VI_MIN_WORDS = 12
VI_MAX_WORDS = 24
EN_MAX_WORDS = 16
REPAIR_BATCH = 10
MAX_REPAIR_ROUNDS = 3

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
3. NHỊP 54/46 (1.5đ): đan xen câu ngắn dứt khoát (<= 6 từ) làm dramatic pause — mục tiêu ~54% số câu ngắn.
4. TỐC ĐỘ ĐỌC (1.0đ): WPM tổng thể trong khoảng 160-200 (dựa vào độ dài lời thoại và thời lượng từng cảnh).
5. GROUNDED (1.5đ): mọi sự kiện, tên riêng, con số phải có trong transcript nguồn. Bịa bất kỳ chi tiết nào là lỗi nặng.
6. THUẬT NGỮ (1.0đ): thuật ngữ tài chính chính xác, nhất quán (tiền gốc, lãi suất, cung tiền, ngân hàng trung ương...).
7. CALLBACK (1.0đ): cảnh cuối quay lại giải tỏa câu hỏi/nghịch lý đã gieo ở hook.
Trả về duy nhất JSON object: {"score": <0-10>, "breakdown": {"hook": <0-2>, "open_loops": <0-2>, "pacing_54_46": <0-1.5>, "wpm": <0-1>, "grounded": <0-1.5>, "terminology": <0-1>, "callback": <0-1>}, "feedback": "nhận xét ngắn tiếng Việt, chỉ ra 2-3 điểm cần sửa cụ thể"}"""


REPAIR_SYSTEM_PROMPT = (
    "Bạn là biên tập viên documentary tài chính theo bộ quy tắc UNIVERSAL_VIDEO_ALGORITHM. "
    "Nhiệm vụ: sửa các cảnh vượt/dưới ngân sách từ của kịch bản song ngữ. "
    "TUYỆT ĐỐI GROUNDED: chỉ dùng sự kiện/tên riêng/con số có trong transcript nguồn, "
    "không bịa chi tiết mới, không đổi index hay thứ tự cảnh. "
    "Giữ câu ngắn dứt khoát xen kẽ, không lặp từ, không cụt lủn."
)


class ScriptFactoryError(RuntimeError):
    """Raised when the script factory cannot produce a usable script."""


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
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """Slice -> best-of-N rewrite -> judge -> regenerate. Returns (vi, en, result)."""
        started = time.time()
        slices_vi, slices_en = self._slice(transcript_segments, target_shots)
        if not slices_vi:
            raise ScriptFactoryError("Semantic slicing produced no scenes")
        expected_count = len(slices_vi)
        transcript_text = " ".join(str(s.get("text", "")) for s in transcript_segments)
        from services.feedback_store import build_feedback_brief
        feedback_brief = build_feedback_brief()
        if feedback_brief:
            logger.info("Script factory folding retention feedback brief into variants/judge")

        best: Optional[Variant] = None
        rounds: List[Dict[str, Any]] = []
        judge_feedback = ""
        total_calls = 0

        for round_idx in range(1, self.max_regen_rounds + 2):
            variants: List[Variant] = []
            for angle in VARIANT_ANGLES[: self.n_variants]:
                variant = self._make_variant(
                    angle, slices_vi, slices_en, transcript_text, judge_feedback, feedback_brief
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

            if best is None or round_best.score > best.score:
                best = round_best
            if best.valid and best.score >= self.min_score:
                break
            judge_feedback = round_best.judge_feedback or "Định dạng đầu ra không hợp lệ, cần đúng số cảnh yêu cầu."

        if best is None or not best.valid:
            raise ScriptFactoryError("All script variants were invalid across all rounds")

        result = {
            "engine": "script_factory_best_of_n",
            "chosen_angle": best.angle["id"],
            "chosen_score": best.score,
            "min_score": self.min_score,
            "rounds": rounds,
            "total_rounds": len(rounds),
            "scene_count": len(best.scenes_vi),
            "expected_scene_count": expected_count,
            "mechanical_metrics": best.metrics,
            "judge_breakdown": best.breakdown,
            "judge_feedback": best.judge_feedback,
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
    ) -> Variant:
        """Rewrite ALL base scenes into one variant, chunked to respect max_tokens."""
        variant = Variant(angle=angle)
        scenes_vi: List[Dict[str, Any]] = []
        scenes_en: List[Dict[str, Any]] = []

        for chunk_start in range(0, len(base_vi), VARIANT_CHUNK):
            chunk_vi = base_vi[chunk_start: chunk_start + VARIANT_CHUNK]
            chunk_en = base_en[chunk_start: chunk_start + VARIANT_CHUNK]
            try:
                part_vi, part_en = self._rewrite_chunk(
                    angle, chunk_vi, chunk_en, transcript_text, judge_feedback,
                    chunk_start, len(base_vi), feedback_brief,
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
        for sc in scenes_vi:
            if not str(sc.get("text", "")).strip():
                variant.invalid_reason = "empty voiceover in variant"
                return variant

        self._repair_word_budget(angle, scenes_vi, scenes_en, transcript_text)
        offenders = self._budget_offenders(scenes_vi, scenes_en)
        if offenders:
            variant.invalid_reason = (
                f"word budget not met after repair: {len(offenders)} scenes"
            )
            return variant

        variant.scenes_vi = scenes_vi
        variant.scenes_en = scenes_en
        variant.valid = True
        return variant

    @staticmethod
    def _budget_offenders(
        scenes_vi: List[Dict[str, Any]], scenes_en: List[Dict[str, Any]]
    ) -> List[int]:
        offenders: List[int] = []
        for vi, en in zip(scenes_vi, scenes_en):
            vi_words = len(str(vi.get("text", "")).split())
            en_words = len(str(en.get("text", "")).split())
            if vi_words < VI_MIN_WORDS or vi_words > VI_MAX_WORDS or en_words > EN_MAX_WORDS:
                offenders.append(int(vi.get("index", 0)))
        return offenders

    def _repair_word_budget(
        self,
        angle: Dict[str, str],
        scenes_vi: List[Dict[str, Any]],
        scenes_en: List[Dict[str, Any]],
        transcript_text: str,
    ) -> None:
        """Rewrite out-of-budget scenes in place until VI 12-24 words / EN <=16."""
        from pipeline.llm_client import chat_json

        by_index = {
            int(vi.get("index", 0)): (vi, scenes_en[i])
            for i, vi in enumerate(scenes_vi)
            if i < len(scenes_en)
        }
        for _ in range(MAX_REPAIR_ROUNDS):
            offenders = self._budget_offenders(scenes_vi, scenes_en)
            if not offenders:
                return
            for start in range(0, len(offenders), REPAIR_BATCH):
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
                        dur = max(
                            MIN_SCENE_DURATION,
                            min(len(text_vi.split()) / WORDS_PER_SECOND, MAX_SCENE_DURATION),
                        )
                        vi["duration"] = dur
                        en["duration"] = dur
                    if text_en:
                        en["text"] = text_en
        remaining = self._budget_offenders(scenes_vi, scenes_en)
        if remaining:
            logger.warning(
                "Word-budget repair left %d/%d scenes out of budget (first: %s)",
                len(remaining), len(scenes_vi), remaining[:5],
            )

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
        system_prompt = (
            "Bạn là biên kịch documentary tài chính đẳng cấp, viết theo bộ quy tắc "
            "UNIVERSAL_VIDEO_ALGORITHM (hook nghịch lý, chuỗi open loop lồng nhau, "
            "nhịp 54/46 câu ngắn, ẩn dụ 3 tầng, callback kết). "
            "TUYỆT ĐỐI GROUNDED: chỉ dùng sự kiện/tên riêng/con số có trong transcript nguồn, "
            "không bịa chi tiết mới.\n\n"
            f"GÓC KỂU CHUYÊN CỦA BẠN — {angle['label']}: {angle['brief']}\n"
            "STYLE BIBLE VISUAL (bắt buộc cho visual_prompt): Whimsical hand-drawn crayon "
            "editorial cartoon, flat 2D, giấy vẽ sần, TUYỆT ĐỐI KHÔNG photorealistic/3D."
        )
        if feedback_brief:
            system_prompt += f"\n\n{feedback_brief}"
        user_prompt = (
            f"{feedback_block}"
            f"TRANSCRIPT NGUỒN (chỉ được dùng sự kiện từ đây):\n\"\"\"{transcript_text[:6000]}\"\"\"\n\n"
            f"BỘ CẢNH CẦN VIẾT LẠI (cảnh {chunk_start + 1}-{chunk_start + len(chunk_vi)} "
            f"trên tổng {total_scenes}):\n"
            f"{json.dumps(compact_base, ensure_ascii=False, indent=1)}\n\n"
            "Yêu cầu: viết lại TỪNG cảnh đúng thứ tự, GIỮ NGUYÊN SỐ LƯỢNG CẢNH. Với mỗi cảnh:\n"
            "- voiceover_vi: lời thoại mượt mà trọn câu theo góc kể và bộ quy tắc; câu ngắn dứt khoát "
            "(<= 6 từ) xen kẽ tạo nhịp; không lặp từ, không cụt lủn. MỖI cảnh chỉ 12-24 từ "
            "(khung cảnh 4-8 giây tại nhịp 180 WPM) — ý dài phải viết cô đọng, TUYỆT ĐỐI KHÔNG dồn "
            "quá 24 từ vào một cảnh.\n"
            "- voiceover_en: bản EN đối chiếu chuẩn xác, giữ nguyên số liệu, diễn lại NGẮN GỌN theo "
            "nguyên ý (không dịch word-by-word): tối đa 16 từ mỗi cảnh vì giọng đọc EN của gateway "
            "chỉ ~110 wpm (chậm gần gấp đôi giọng VI ~200 wpm) — vượt budget sẽ lệch timeline.\n"
            "- estimated_duration: 4.5-8.0 giây, tự nhiên theo độ dài lời thoại.\n"
            "- situation: tình huống cụ thể để dựng hình.\n"
            "- visual_prompt: prompt tiếng Anh theo STYLE BIBLE crayon.\n\n"
            "Trả về duy nhất JSON object:\n"
            '{"scenes": [{"index": <int>, "situation": "...", "voiceover_vi": "...", '
            '"voiceover_en": "...", "estimated_duration": <float>, "visual_prompt": "..."}]}'
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
            text_en = str(sc.get("voiceover_en", "") or text_vi).strip()
            dur = max(
                MIN_SCENE_DURATION,
                min(len(text_vi.split()) / WORDS_PER_SECOND, MAX_SCENE_DURATION),
            )
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

    def _judge(
        self, variant: Variant, transcript_text: str, feedback_brief: str = ""
    ) -> Tuple[float, Dict[str, float], str]:
        from pipeline.llm_client import chat_json

        script_lines = "\n".join(
            f"Cảnh {sc['index']} ({sc['duration']:.1f}s): {sc['text']}"
            for sc in variant.scenes_vi
        )
        user_prompt = (
            (f"{feedback_brief}\n\n" if feedback_brief else "")
            + f"TRANSCRIPT NGUỒN (để kiểm tra GROUNDED):\n\"\"\"{transcript_text[:5000]}\"\"\"\n\n"
            f"KỊCH BẢN CẦN CHẤM:\n{script_lines[:9000]}"
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
        return {
            "scene_count": len(scenes_vi),
            "total_duration_sec": round(total_dur, 1),
            "mean_scene_duration": round(sum(durations) / len(durations), 2) if durations else 0.0,
            "min_scene_duration": round(min(durations), 2) if durations else 0.0,
            "max_scene_duration": round(max(durations), 2) if durations else 0.0,
            "sentence_count": len(sentences),
            "short_sentence_pct": round(short_pct, 3),
            "target_short_sentence_pct": TARGET_SHORT_SENTENCE_PCT,
            "estimated_wpm": round(wpm, 1),
            "wpm_in_range": MIN_WPM <= wpm <= MAX_WPM if wpm else False,
            "question_count": question_count,
            "total_words": total_words,
        }
