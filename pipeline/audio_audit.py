"""Deterministic narration audit: does the generated audio match the approved plan?

Runs faster-whisper over the rendered VI/EN narration, aligns the recognized
words back onto the script, and reports the numbers a human reviewer needs at
Gate 2:

- word error rate of the round trip (script -> TTS -> ASR -> script)
- per-scene speech spans and drift against the approved shot durations
- local speaking rate (WPM) mean/std plus pause histogram
- the TTS rate-fit sidecar (native WPM / atempo) so an over-stretched voice is
  visible before approval
- an audio-first timing map the Gate 2 handler can copy onto the shot plan

The audit is advisory: it returns warnings/errors instead of raising, and the
Gate 2 human approval stays the hard gate.
"""

from __future__ import annotations

import difflib
import json
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logging_config import get_logger

logger = get_logger(__name__)

TARGET_WPM = 205.0
MIN_WPM = 195.0
MAX_WPM = 214.0
MIN_ATEMPO = 0.75
SCENE_MIN_DUR = 4.0
SCENE_MAX_DUR = 8.0
MIN_PAUSE_SEC = 0.15
PAUSE_BUCKETS = (
    ("0.15-0.30", 0.15, 0.30),
    ("0.30-0.60", 0.30, 0.60),
    ("0.60-1.00", 0.60, 1.00),
    (">=1.00", 1.00, float("inf")),
)
WER_WARN = 0.15
WER_ERROR = 0.35
DRIFT_WARN = 0.05
DRIFT_ERROR = 0.25

_TOKEN_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_tokens(text: str) -> List[str]:
    """Lowercase, strip punctuation, collapse every numeric token to <num>."""
    lowered = _TOKEN_RE.sub(" ", str(text).lower())
    tokens: List[str] = []
    for tok in lowered.split():
        tokens.append("<num>" if any(ch.isdigit() for ch in tok) else tok)
    return tokens


def _probe_audio_duration(audio_path: Path) -> float:
    try:
        from pipeline.tts import _probe_duration

        return float(_probe_duration(Path(audio_path)) or 0.0)
    except Exception as exc:  # pragma: no cover - ffprobe missing is env-specific
        logger.warning("Could not probe %s: %s", audio_path, exc)
        return 0.0


def transcribe_word_timings(
    audio_path: Path, language: str, model_size: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], float]:
    """Return ([{word,start,end}], audio_duration) from faster-whisper."""
    from config.settings import settings
    from pipeline.transcribe import _get_model

    model = _get_model(
        model_size or settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
    )
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
    )
    words: List[Dict[str, Any]] = []
    last_end = 0.0
    for seg in segments:
        for w in getattr(seg, "words", None) or []:
            token = str(getattr(w, "word", "")).strip()
            if not token:
                continue
            words.append({
                "word": token,
                "start": float(getattr(w, "start", 0.0) or 0.0),
                "end": float(getattr(w, "end", 0.0) or 0.0),
            })
            last_end = max(last_end, words[-1]["end"])
    duration = float(getattr(info, "duration", 0.0) or 0.0) or last_end
    return words, duration


@dataclass
class SceneSync:
    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    plan_duration_sec: float
    drift: float
    words: int
    wpm: float
    missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioAuditResult:
    language: str
    audio_path: str
    audio_duration_sec: float
    plan_duration_sec: float
    duration_drift: float
    wer: float
    word_accuracy: float
    matched_words: int
    expected_words: int
    asr_words: int
    local_wpm_mean: float
    local_wpm_std: float
    global_wpm: float
    pause_count: int
    pause_total_sec: float
    pause_ratio: float
    pause_histogram: Dict[str, int]
    rate_fit: Optional[Dict[str, Any]] = None
    atempo: Optional[float] = None
    naturalness: Dict[str, Any] = field(default_factory=dict)
    scenes: List[SceneSync] = field(default_factory=list)
    timing_map: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    transcriber: str = "faster-whisper"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["scenes"] = [sc.to_dict() for sc in self.scenes]
        return data

    def summary_vi(self) -> str:
        lines = [
            f"🎧 AUDIO AUDIT ({self.language.upper()}): WER {self.wer:.1%} | "
            f"{self.audio_duration_sec:.1f}s / plan {self.plan_duration_sec:.1f}s "
            f"(drift {self.duration_drift:+.1%})",
            f"🗣 WPM cục bộ {self.local_wpm_mean:.1f} ± {self.local_wpm_std:.1f} "
            f"(mục tiêu {TARGET_WPM:.0f}, band {MIN_WPM:.0f}-{MAX_WPM:.0f}) | "
            f"{self.pause_count} khoảng nghỉ",
        ]
        if self.atempo is not None:
            lines.append(f"🎚 atempo {self.atempo:.3f} (min {MIN_ATEMPO})")
        for warn in self.warnings[:6]:
            lines.append(f"⚠️ {warn}")
        for err in self.errors[:6]:
            lines.append(f"❌ {err}")
        return "\n".join(lines)


def load_rate_fit(audio_path: Path) -> Optional[Dict[str, Any]]:
    sidecar = Path(audio_path).with_name(f"{Path(audio_path).name}.rate_fit.json")
    if not sidecar.is_file():
        return None
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable rate-fit sidecar %s: %s", sidecar, exc)
        return None


def _map_reference_to_times(
    ref_tokens: List[str], asr_words: List[Dict[str, Any]]
) -> Tuple[List[Optional[Tuple[float, float]]], List[bool]]:
    """Map every reference token to a (start, end) span via sequence alignment.

    The boolean mask marks tokens that matched ASR words exactly; interpolated
    spans (fuzzy replace blocks) are timed but still count as unrecognized.
    """
    usable: List[Dict[str, Any]] = []
    asr_tokens: List[str] = []
    for w in asr_words:
        for token in normalize_tokens(w["word"]):
            asr_tokens.append(token)
            usable.append(w)
    mapping: List[Optional[Tuple[float, float]]] = [None] * len(ref_tokens)
    exact = [False] * len(ref_tokens)
    if not ref_tokens or not usable:
        return mapping, exact
    matcher = difflib.SequenceMatcher(None, ref_tokens, asr_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                w = usable[j1 + k]
                mapping[i1 + k] = (w["start"], w["end"])
                exact[i1 + k] = True
        elif tag == "replace":
            span_start = usable[j1]["start"]
            span_end = usable[j2 - 1]["end"]
            n = max(1, i2 - i1)
            step = max(0.0, span_end - span_start) / n
            for k in range(i2 - i1):
                mapping[i1 + k] = (span_start + step * k, span_start + step * (k + 1))
    return mapping, exact


def _interpolate_missing(
    mapping: List[Optional[Tuple[float, float]]], default_end: float
) -> List[Tuple[float, float]]:
    """Fill alignment gaps by spreading time between the nearest known spans."""
    known = [i for i, t in enumerate(mapping) if t is not None]
    filled: List[Tuple[float, float]] = [
        t if t is not None else (0.0, 0.0) for t in mapping
    ]
    if not known:
        return [(0.0, 0.0)] * len(mapping)
    first = known[0]
    if first > 0:
        step = filled[first][0] / first if first else 0.0
        for i in range(first):
            filled[i] = (step * i, step * (i + 1))
    for left, right in zip(known, known[1:]):
        if right - left <= 1:
            continue
        gap_start = filled[left][1]
        gap_end = filled[right][0]
        step = max(0.0, gap_end - gap_start) / (right - left)
        for k in range(1, right - left):
            filled[left + k] = (gap_start + step * (k - 1), gap_start + step * k)
    last = known[-1]
    if last < len(filled) - 1:
        gap_start = filled[last][1]
        step = max(0.02, (default_end - gap_start) / (len(filled) - last))
        for k in range(1, len(filled) - last):
            filled[last + k] = (gap_start + step * (k - 1), gap_start + step * k)
    return filled


def _word_error_rate(ref_tokens: List[str], asr_tokens: List[str]) -> Tuple[float, int]:
    if not ref_tokens:
        return 0.0, 0
    matcher = difflib.SequenceMatcher(None, ref_tokens, asr_tokens, autojunk=False)
    errors = 0
    matched = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
        elif tag == "replace":
            errors += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            errors += i2 - i1
        elif tag == "insert":
            errors += j2 - j1
    return min(1.0, errors / len(ref_tokens)), matched


def _pause_stats(asr_words: List[Dict[str, Any]], audio_duration: float) -> Dict[str, Any]:
    ordered = sorted(asr_words, key=lambda w: w["start"])
    histogram = {label: 0 for label, _, _ in PAUSE_BUCKETS}
    gaps: List[float] = []
    for prev, nxt in zip(ordered, ordered[1:]):
        gap = float(nxt["start"]) - float(prev["end"])
        if gap < MIN_PAUSE_SEC:
            continue
        gaps.append(gap)
        for label, low, high in PAUSE_BUCKETS:
            if low <= gap < high:
                histogram[label] += 1
                break
    total = sum(gaps)
    return {
        "count": len(gaps),
        "total_sec": round(total, 2),
        "ratio": round(total / audio_duration, 3) if audio_duration > 0 else 0.0,
        "histogram": histogram,
    }


def audit_audio(
    audio_path: Path,
    plan_scenes: List[Dict[str, Any]],
    language: str,
    model_size: Optional[str] = None,
    transcriber: Optional[Callable[[Path, str], Tuple[List[Dict[str, Any]], float]]] = None,
) -> AudioAuditResult:
    audio_path = Path(audio_path)
    plan = list(plan_scenes or [])
    ref_per_scene: List[List[str]] = [
        normalize_tokens(str(sc.get("text", ""))) for sc in plan
    ]
    plan_durations = [float(sc.get("duration", 0.0)) for sc in plan]
    plan_total = sum(plan_durations)

    warnings: List[str] = []
    errors: List[str] = []
    if not audio_path.is_file():
        errors.append(f"Audio không tồn tại: {audio_path}")
        return AudioAuditResult(
            language=language, audio_path=str(audio_path), audio_duration_sec=0.0,
            plan_duration_sec=round(plan_total, 2), duration_drift=-1.0, wer=1.0,
            word_accuracy=0.0, matched_words=0, expected_words=sum(len(t) for t in ref_per_scene),
            asr_words=0, local_wpm_mean=0.0, local_wpm_std=0.0, global_wpm=0.0,
            pause_count=0, pause_total_sec=0.0, pause_ratio=0.0,
            pause_histogram={label: 0 for label, _, _ in PAUSE_BUCKETS},
            warnings=warnings, errors=errors,
        )

    try:
        fn = transcriber or transcribe_word_timings
        asr_words, asr_duration = fn(audio_path, language)
    except Exception as exc:
        errors.append(f"Transcribe thất bại: {exc}")
        return AudioAuditResult(
            language=language, audio_path=str(audio_path), audio_duration_sec=0.0,
            plan_duration_sec=round(plan_total, 2), duration_drift=-1.0, wer=1.0,
            word_accuracy=0.0, matched_words=0, expected_words=sum(len(t) for t in ref_per_scene),
            asr_words=0, local_wpm_mean=0.0, local_wpm_std=0.0, global_wpm=0.0,
            pause_count=0, pause_total_sec=0.0, pause_ratio=0.0,
            pause_histogram={label: 0 for label, _, _ in PAUSE_BUCKETS},
            warnings=warnings, errors=errors,
        )

    probed = _probe_audio_duration(audio_path)
    audio_duration = probed or float(asr_duration or 0.0)
    if asr_words:
        audio_duration = max(audio_duration, max(w["end"] for w in asr_words))
    if not asr_words:
        errors.append("Không nhận diện được từ nào trong audio")
    if audio_duration <= 0:
        errors.append("Không xác định được thời lượng audio")

    ref_tokens: List[str] = []
    ref_scene_slices: List[Tuple[int, int]] = []
    for tokens in ref_per_scene:
        start = len(ref_tokens)
        ref_tokens.extend(tokens)
        ref_scene_slices.append((start, len(ref_tokens)))

    asr_tokens = [t for w in asr_words for t in normalize_tokens(w["word"])]
    wer, matched = _word_error_rate(ref_tokens, asr_tokens)
    if wer > WER_ERROR:
        errors.append(f"WER {wer:.1%} quá cao — giọng đọc có thể sai từ/số liệu")
    elif wer > WER_WARN:
        warnings.append(f"WER {wer:.1%} cao hơn ngưỡng {WER_WARN:.0%}")

    raw_mapping, exact_mask = _map_reference_to_times(ref_tokens, asr_words)
    mapping = _interpolate_missing(raw_mapping, audio_duration)

    scenes: List[SceneSync] = []
    for idx, ((start_idx, end_idx), plan_dur) in enumerate(
        zip(ref_scene_slices, plan_durations), start=1
    ):
        scene_times = mapping[start_idx:end_idx]
        if not scene_times:
            continue
        start = min(t[0] for t in scene_times)
        end = max(t[1] for t in scene_times)
        duration = max(0.0, end - start)
        words = len(ref_per_scene[idx - 1])
        missing = not any(exact_mask[start_idx:end_idx])
        drift = (duration - plan_dur) / plan_dur if plan_dur > 0 else 0.0
        wpm = (words / duration * 60.0) if duration > 0.05 else 0.0
        if missing:
            warnings.append(f"Cảnh {idx}: không khớp được từ nào với audio")
        scenes.append(SceneSync(
            index=int(plan[idx - 1].get("index", idx)),
            start_sec=round(start, 2),
            end_sec=round(end, 2),
            duration_sec=round(duration, 2),
            plan_duration_sec=round(plan_dur, 2),
            drift=round(drift, 3),
            words=words,
            wpm=round(wpm, 1),
            missing=missing,
        ))

    # Audio-first timing: a shot holds until the next scene starts, so inter-scene
    # pauses are absorbed by the shot instead of vanishing from the plan.
    timing_map: List[Dict[str, Any]] = []
    clamped = 0
    for i, sc in enumerate(scenes):
        if sc.missing:
            hold = sc.plan_duration_sec
        elif i + 1 < len(scenes):
            hold = max(0.0, scenes[i + 1].start_sec - sc.start_sec)
        else:
            hold = max(0.0, audio_duration - sc.start_sec)
        target_dur = min(SCENE_MAX_DUR, max(SCENE_MIN_DUR, hold))
        if abs(target_dur - hold) > 0.05:
            clamped += 1
        timing_map.append({
            "index": sc.index,
            "start_sec": sc.start_sec,
            "end_sec": sc.end_sec,
            "audio_duration_sec": sc.duration_sec,
            "plan_duration_sec": sc.plan_duration_sec,
            "hold_sec": round(hold, 2),
            "duration_sec": round(target_dur, 2),
            "drift": sc.drift,
        })

    if clamped:
        warnings.append(f"{clamped} cảnh có thời lượng giữ khung ngoài 4-8s (đã kẹp trong timing map)")

    valid_wpm = [sc.wpm for sc in scenes if sc.wpm > 0 and not sc.missing]
    local_wpm_mean = statistics.fmean(valid_wpm) if valid_wpm else 0.0
    local_wpm_std = statistics.pstdev(valid_wpm) if len(valid_wpm) > 1 else 0.0
    speech_span = (
        max(w["end"] for w in asr_words) - min(w["start"] for w in asr_words)
        if asr_words else 0.0
    )
    global_wpm = (
        sum(len(t) for t in ref_per_scene) / speech_span * 60.0
        if speech_span > 0 else 0.0
    )
    duration_drift = (
        (audio_duration - plan_total) / plan_total if plan_total > 0 else 0.0
    )
    if abs(duration_drift) > DRIFT_ERROR:
        errors.append(
            f"Tổng audio lệch plan {duration_drift:+.1%} (>{DRIFT_ERROR:.0%})"
        )
    elif abs(duration_drift) > DRIFT_WARN:
        warnings.append(f"Tổng audio lệch plan {duration_drift:+.1%}")
    if local_wpm_mean >= MAX_WPM + 20 or (local_wpm_mean and local_wpm_mean <= MIN_WPM - 20):
        errors.append(f"Tốc độ đọc cục bộ {local_wpm_mean:.0f} WPM lệch xa band")
    elif local_wpm_mean and not (MIN_WPM <= local_wpm_mean <= MAX_WPM):
        warnings.append(f"Tốc độ đọc cục bộ {local_wpm_mean:.0f} WPM ngoài band {MIN_WPM:.0f}-{MAX_WPM:.0f}")

    pauses = _pause_stats(asr_words, audio_duration)
    if pauses["ratio"] > 0.25:
        warnings.append(f"Tỷ lệ khoảng nghỉ {pauses['ratio']:.0%} cao bất thường")

    rate_fit = load_rate_fit(audio_path)
    atempo = None
    naturalness: Dict[str, Any] = {
        "target_wpm": TARGET_WPM,
        "band_wpm": [MIN_WPM, MAX_WPM],
        "min_atempo": MIN_ATEMPO,
    }
    if rate_fit:
        try:
            atempo = float(rate_fit.get("atempo", 1.0))
        except (TypeError, ValueError):
            atempo = None
        naturalness.update({
            "native_wpm": rate_fit.get("native_wpm"),
            "fitted_wpm": rate_fit.get("fitted_wpm"),
            "slowdown_capped": bool(rate_fit.get("slowdown_capped", False)),
        })
        if atempo is not None and atempo < MIN_ATEMPO - 1e-6:
            errors.append(f"atempo {atempo:.3f} dưới ngưỡng tự nhiên {MIN_ATEMPO}")
        elif rate_fit.get("slowdown_capped"):
            warnings.append("Audio phải làm chậm tới mức tối đa (atempo 0.75) — kiểm tra độ tự nhiên")
    naturalness["atempo"] = atempo
    naturalness["atempo_ok"] = atempo is None or atempo >= MIN_ATEMPO - 1e-6
    naturalness["wpm_in_band"] = bool(local_wpm_mean and MIN_WPM <= local_wpm_mean <= MAX_WPM)

    return AudioAuditResult(
        language=language,
        audio_path=str(audio_path),
        audio_duration_sec=round(audio_duration, 2),
        plan_duration_sec=round(plan_total, 2),
        duration_drift=round(duration_drift, 3),
        wer=round(wer, 3),
        word_accuracy=round(1.0 - wer, 3),
        matched_words=matched,
        expected_words=len(ref_tokens),
        asr_words=len(asr_tokens),
        local_wpm_mean=round(local_wpm_mean, 1),
        local_wpm_std=round(local_wpm_std, 1),
        global_wpm=round(global_wpm, 1),
        pause_count=pauses["count"],
        pause_total_sec=pauses["total_sec"],
        pause_ratio=pauses["ratio"],
        pause_histogram=pauses["histogram"],
        rate_fit=rate_fit,
        atempo=atempo,
        naturalness=naturalness,
        scenes=scenes,
        timing_map=timing_map,
        warnings=warnings,
        errors=errors,
    )


def save_audio_audit(result: AudioAuditResult, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return out_path


def load_audio_audit(path: Path) -> Optional[AudioAuditResult]:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable audio audit %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    scenes = []
    for sc in data.get("scenes") or []:
        try:
            scenes.append(SceneSync(**sc))
        except TypeError:
            continue
    fields = {
        k: v for k, v in data.items()
        if k in AudioAuditResult.__dataclass_fields__ and k != "scenes"
    }
    fields["scenes"] = scenes
    try:
        return AudioAuditResult(**fields)
    except TypeError as exc:
        logger.warning("Malformed audio audit %s: %s", path, exc)
        return None
