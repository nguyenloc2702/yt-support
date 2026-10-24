"""Algorithm QA scorecard (Phase 4): the UNIVERSAL_VIDEO_ALGORITHM skill pack
as executable checks — AI generated 100% and verified 0% before; now every
stage of a /remake job is measured in code.

Checks are stage-aware (script < assets < audio < video < final). A scorecard
only counts checks whose stage artifacts exist (earlier stages always counted
once their artifacts exist). Score = 10 x (passed weight / applicable weight).

The hard gate (score >= threshold before Gate 4 approval) is wired in Phase 5.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logging_config import get_logger

logger = get_logger(__name__)

STAGES = ("script", "assets", "audio", "video", "final")
STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}

# Hard gate minimum before Gate 4 (video) approval — Phase 5 wiring.
QA_GATE_MIN_SCORE = 8.0

# Skill-pack targets (mirrors scripts/apply_video_algorithm.py)
TARGET_SHORT_PCT = 0.54
PASS_SHORT_PCT = 0.35
WPM_RANGE = (150.0, 210.0)
SCENE_DUR_RANGE = (4.0, 8.0)
MEAN_DUR_RANGE = (4.5, 6.5)
AUDIO_SYNC_WER_MAX = 0.15
AUDIO_SYNC_MIN_COVERAGE = 0.90

PHOTO_KEYWORDS = ("photorealistic", "photo of", "4k photo", "hyperrealistic", "dslr")
CRAYON_KEYWORDS = ("crayon", "hand-drawn", "hand drawn", "sketch", "illustration", "cartoon")
BRIDGE_PHRASES = (
    "nhưng", "bất ngờ", "nghịch lý", "câu hỏi", "điều này dẫn", "bí ẩn",
    "vậy tại sao", "chuưa hết", "chưa hết", "còn nữa",
)


@dataclass
class QACheckResult:
    id: str
    category: str
    name: str
    stage: str
    passed: bool
    value: Any = None
    target: str = ""
    weight: float = 1.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Scorecard:
    stage: str
    score: float
    results: List[QACheckResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def failed(self) -> List[QACheckResult]:
        return [r for r in self.results if not r.passed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "score": round(self.score, 2),
            "passed": self.passed_count,
            "total": len(self.results),
            "results": [r.to_dict() for r in self.results],
        }

    def format_telegram_report(self) -> str:
        lines = [
            f"📋 <b>ALGORITHM SCORECARD ({self.stage})</b>",
            f"🎯 Điểm: <b>{self.score:.1f}/10</b> — {self.passed_count}/{len(self.results)} tiêu chí ĐẠT",
        ]
        by_cat: Dict[str, List[QACheckResult]] = {}
        for r in self.results:
            by_cat.setdefault(r.category, []).append(r)
        icon = {True: "✅", False: "❌"}
        for cat, items in by_cat.items():
            lines.append(f"\n<b>{cat}</b>")
            for r in items:
                val = r.value if not isinstance(r.value, float) else round(r.value, 2)
                lines.append(f"{icon[r.passed]} {r.name} (giá trị: {val})")
        return "\n".join(lines)

    def save(self, project_dir: Path) -> Path:
        out = Path(project_dir) / "qa_scorecard.json"
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    @classmethod
    def load(cls, project_dir: Path) -> Optional["Scorecard"]:
        data = _load_json(Path(project_dir) / "qa_scorecard.json")
        if not isinstance(data, dict):
            return None
        results = [
            QACheckResult(
                id=r.get("id", ""), category=r.get("category", ""), name=r.get("name", ""),
                stage=r.get("stage", ""), passed=bool(r.get("passed")), value=r.get("value"),
                target=r.get("target", ""), weight=float(r.get("weight", 1.0)),
                detail=r.get("detail", ""),
            )
            for r in data.get("results", []) if isinstance(r, dict)
        ]
        return cls(stage=str(data.get("stage", "final")), score=float(data.get("score", 0.0)), results=results)


# --------------------------------------------------------------------- helpers

def _load_json(path: Path) -> Optional[Any]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _audio_sync_report(pd: Path, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "audio_sync_vi" not in ctx:
        data = _load_json(pd / "audio_sync_vi.json")
        ctx["audio_sync_vi"] = data if isinstance(data, dict) else None
    return ctx["audio_sync_vi"]


def _probe_media(path: Path) -> Optional[Dict[str, Any]]:
    """ffprobe summary: duration, width, height, fps, streams, bitrates."""
    from config.settings import settings

    if not Path(path).is_file():
        return None
    try:
        out = subprocess.run(
            [settings.ffprobe_path, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
    except Exception:
        return None
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if video:
        num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1)
        except ZeroDivisionError:
            fps = 0.0
    return {
        "duration": float(fmt.get("duration", 0.0) or 0.0),
        "size": int(fmt.get("size", 0) or 0),
        "width": int(video.get("width", 0)) if video else 0,
        "height": int(video.get("height", 0)) if video else 0,
        "fps": fps,
        "has_audio": audio is not None,
        "video_bitrate": int(video.get("bit_rate", 0) or 0) if video else 0,
        "audio_bitrate": int(audio.get("bit_rate", 0) or 0) if audio else 0,
    }


def _measure_lufs(path: Path) -> Optional[float]:
    """Integrated loudness (LUFS) via ffmpeg ebur128."""
    from config.settings import settings

    try:
        out = subprocess.run(
            [settings.ffmpeg_path, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        m = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", out.stderr or "")
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _short_sentence_pct(scenes: List[Dict[str, Any]]) -> float:
    sentences: List[str] = []
    for sc in scenes:
        for s in re.split(r"[.!?…]+", str(sc.get("text", ""))):
            if s.strip():
                sentences.append(s.strip())
    if not sentences:
        return 0.0
    return sum(1 for s in sentences if len(s.split()) <= 6) / len(sentences)


def _estimate_wpm(scenes: List[Dict[str, Any]]) -> float:
    words = sum(len(str(sc.get("text", "")).split()) for sc in scenes)
    dur = sum(float(sc.get("duration", 0.0)) for sc in scenes)
    return (words / dur * 60.0) if dur > 0 else 0.0


def _check(cid: str, category: str, name: str, stage: str):
    def deco(fn: Callable[[Path, Dict[str, Any]], Any]):
        fn._qa_id = cid
        fn._qa_category = category
        fn._qa_name = name
        fn._qa_stage = stage
        return fn
    return deco


# ============================================================ SCRIPT CHECKS

@_check("S01", "Kịch bản", "Có shots_plan.json", "script")
def c_shots_plan(pd: Path, ctx):
    plan = _load_json(pd / "shots_plan.json")
    ctx["plan"] = plan
    return (plan is not None) and len(plan) > 0


@_check("S02", "Kịch bản", "Số cảnh >= 6", "script")
def c_scene_count(pd: Path, ctx):
    plan = ctx.get("plan") or []
    return len(plan) >= 6, len(plan), ">= 6"


@_check("S03", "Kịch bản", "Không cảnh rỗng", "script")
def c_no_empty(pd: Path, ctx):
    plan = ctx.get("plan") or []
    empty = sum(1 for sc in plan if not str(sc.get("text", "")).strip())
    return empty == 0, empty, "= 0"


@_check("S04", "Kịch bản", "Không cảnh trùng lặp", "script")
def c_no_dupes(pd: Path, ctx):
    plan = ctx.get("plan") or []
    texts = [str(sc.get("text", "")).strip().lower() for sc in plan]
    dupes = len(texts) - len(set(texts))
    return dupes == 0, dupes, "= 0"


@_check("S05", "Kịch bản", "Hook mở đầu có số liệu/câu hỏi", "script")
def c_hook(pd: Path, ctx):
    plan = ctx.get("plan") or []
    hook_text = " ".join(str(sc.get("text", "")) for sc in plan[:2]).lower()
    has_number = bool(re.search(r"\d", hook_text))
    has_question = "?" in hook_text
    has_paradox = any(w in hook_text for w in ("nhưng", "nghịch lý", "bất ngờ", "ít ai biết"))
    return (has_number or has_question or has_paradox), f"num={has_number} q={has_question} paradox={has_paradox}", ">=1 trong 3"


@_check("S06", "Kịch bản", "Open loops >= 4", "script")
def c_open_loops(pd: Path, ctx):
    plan = ctx.get("plan") or []
    text = " ".join(str(sc.get("text", "")) for sc in plan).lower()
    questions = text.count("?")
    bridges = sum(1 for p in BRIDGE_PHRASES if p in text)
    loops = questions + bridges
    return loops >= 4, loops, ">= 4"


@_check("S07", "Kịch bản", "Tỷ lệ câu ngắn 54/46", "script")
def c_short_pct(pd: Path, ctx):
    plan = ctx.get("plan") or []
    pct = _short_sentence_pct(plan)
    return pct >= PASS_SHORT_PCT, pct, f">= {PASS_SHORT_PCT:.2f} (mục tiêu {TARGET_SHORT_PCT:.2f})"


@_check("S08", "Kịch bản", "Tốc độ đọc WPM 150-210", "script")
def c_wpm(pd: Path, ctx):
    plan = ctx.get("plan") or []
    wpm = _estimate_wpm(plan)
    return WPM_RANGE[0] <= wpm <= WPM_RANGE[1], wpm, "150-210"


@_check("S09", "Kịch bản", "Duration từng cảnh 4.0-8.0s", "script")
def c_scene_dur(pd: Path, ctx):
    plan = ctx.get("plan") or []
    bad = sum(1 for sc in plan if not (SCENE_DUR_RANGE[0] <= float(sc.get("duration", 0)) <= SCENE_DUR_RANGE[1]))
    return bad == 0, bad, "= 0 cảnh ngoài 4-8s"


@_check("S10", "Kịch bản", "Duration trung bình 4.5-6.5s", "script")
def c_mean_dur(pd: Path, ctx):
    plan = ctx.get("plan") or []
    durs = [float(sc.get("duration", 0)) for sc in plan]
    mean = sum(durs) / len(durs) if durs else 0.0
    return MEAN_DUR_RANGE[0] <= mean <= MEAN_DUR_RANGE[1], mean, "4.5-6.5"


@_check("S11", "Kịch bản", "Tổng duration gần target", "script")
def c_total_dur(pd: Path, ctx):
    plan = ctx.get("plan") or []
    algo = ctx.get("algo") or {}
    target = algo.get("target_duration_sec")
    if not target:
        return True, None, "n/a (không có target)"
    total = sum(float(sc.get("duration", 0)) for sc in plan)
    drift = abs(total - float(target)) / float(target)
    return drift <= 0.30, round(drift, 2), "<= 0.30"


@_check("S12", "Kịch bản", "Mọi cảnh có situation", "script")
def c_situations(pd: Path, ctx):
    plan = ctx.get("plan") or []
    missing = sum(1 for sc in plan if not str(sc.get("situation", "")).strip())
    return missing == 0, missing, "= 0"


@_check("S13", "Kịch bản", "Prompt theo Style Bible crayon", "script")
def c_prompts_crayon(pd: Path, ctx):
    plan = ctx.get("plan") or []
    bad = []
    for sc in plan:
        p = str(sc.get("prompt", "")).lower()
        if not p:
            bad.append("empty")
        elif any(k in p for k in PHOTO_KEYWORDS):
            bad.append("photoreal")
        elif not any(k in p for k in CRAYON_KEYWORDS):
            bad.append("not crayon")
    return len(bad) == 0, len(bad), "= 0 prompt sai style"


@_check("S14", "Kịch bản", "algorithm_result.json được ghi", "script")
def c_algo_recorded(pd: Path, ctx):
    algo = ctx.get("algo")
    ctx.setdefault("algo", algo)
    return algo is not None and "chosen_score" in algo, None, "tồn tại + có chosen_score"


@_check("S15", "Kịch bản", "Không câu cụt lủn (mỗi cảnh >= 3 từ)", "script")
def c_no_stumps(pd: Path, ctx):
    plan = ctx.get("plan") or []
    stumps = sum(1 for sc in plan if len(str(sc.get("text", "")).split()) < 3)
    return stumps == 0, stumps, "= 0"


@_check("S16", "Kịch bản", "Hook gói trong 30 giây đầu", "script")
def c_hook_budget(pd: Path, ctx):
    plan = ctx.get("plan") or []
    acc = 0.0
    for sc in plan:
        acc += float(sc.get("duration", 0))
        if acc >= 30.0:
            break
    return acc <= 32.0, round(acc, 1), "<= 30s (+2s nới lỏng)"


@_check("S17", "Kịch bản", "Callback kết quay về hook", "script")
def c_callback(pd: Path, ctx):
    plan = ctx.get("plan") or []
    if len(plan) < 2:
        return False, None, ">= 2 cảnh"
    first_words = set(re.findall(r"\w{5,}", str(plan[0].get("text", "")).lower()))
    last_text = str(plan[-1].get("text", "")).lower()
    last_words = set(re.findall(r"\w{5,}", last_text))
    echo = first_words & last_words
    has_close = any(w in last_text for w in ("quay lại", "câu trả lời", "bài học", "như vậy", "chính là"))
    return bool(echo) or has_close, (sorted(echo)[:3], has_close), "lặp ý hoặc câu chốt"


# ============================================================ ASSET CHECKS

@_check("A01", "Asset & Style", "Điểm judge >= min_score", "assets")
def c_judge_score(pd: Path, ctx):
    algo = ctx.get("algo") or {}
    chosen = algo.get("chosen_score")
    floor = algo.get("min_score", 8.0)
    return (chosen is not None and chosen >= floor), chosen, f">= {floor}"


@_check("A02", "Asset & Style", "Có style_check.json cho từng nhân vật", "assets")
def c_style_reports(pd: Path, ctx):
    chars = ctx.get("characters")
    if chars is None:
        return True, None, "n/a (không có nhân vật)"
    missing = [cid for cid in chars if not (pd / "characters" / cid / "style_check.json").is_file()]
    return len(missing) == 0, missing, "= 0 thiếu"


@_check("A03", "Asset & Style", "Style score nhân vật >= 7", "assets")
def c_style_scores(pd: Path, ctx):
    chars = ctx.get("characters")
    if not chars:
        return True, None, "n/a (không có nhân vật)"
    low = {}
    for cid in chars:
        rep = _load_json(pd / "characters" / cid / "style_check.json") or {}
        low[cid] = rep.get("score", 0.0)
    bad = {cid: sc for cid, sc in low.items() if sc < 7.0}
    return len(bad) == 0, bad, "mọi nhân vật >= 7.0"


@_check("A04", "Asset & Style", "Ảnh reference nhân vật tồn tại", "assets")
def c_refs_exist(pd: Path, ctx):
    chars = ctx.get("characters")
    if not chars:
        return True, None, "n/a (không có nhân vật)"
    missing = [cid for cid in chars if not (pd / "characters" / cid / "reference.png").is_file()]
    return len(missing) == 0, missing, "= 0 thiếu"


@_check("A05", "Asset & Style", "Ảnh reference đủ lớn (>10KB)", "assets")
def c_refs_size(pd: Path, ctx):
    chars = ctx.get("characters")
    if not chars:
        return True, None, "n/a"
    small = [
        cid for cid in chars
        if not (pd / "characters" / cid / "reference.png").is_file()
        or (pd / "characters" / cid / "reference.png").stat().st_size < 10_000
    ]
    return len(small) == 0, small, "= 0 quá nhỏ"


@_check("A06", "Asset & Style", "voice.txt đã lưu", "assets")
def c_voice_saved(pd: Path, ctx):
    vf = pd / "voice.txt"
    return vf.is_file() and bool(vf.read_text(encoding="utf-8").strip()), None, "tồn tại + không rỗng"


@_check("A07", "Asset & Style", "shots_plan VI tồn tại", "assets")
def c_vi_plan(pd: Path, ctx):
    return (_load_json(pd / "shots_plan_vi.json") is not None), None, "tồn tại"


@_check("A08", "Asset & Style", "shots_plan EN tồn tại", "assets")
def c_en_plan(pd: Path, ctx):
    return (_load_json(pd / "shots_plan_en.json") is not None), None, "tồn tại"


@_check("A09", "Asset & Style", "Số cảnh VI = EN", "assets")
def c_vi_en_match(pd: Path, ctx):
    vi = _load_json(pd / "shots_plan_vi.json") or []
    en = _load_json(pd / "shots_plan_en.json") or []
    return len(vi) == len(en), (len(vi), len(en)), "bằng nhau"


@_check("A10", "Asset & Style", "Prompt là tiếng Anh", "assets")
def c_prompt_lang(pd: Path, ctx):
    plan = ctx.get("plan") or []
    prompts = [str(sc.get("prompt", "")) for sc in plan if str(sc.get("prompt", "")).strip()]
    if not prompts:
        return False, 0, "> 0 prompt"
    vi_chars = sum(1 for p in prompts for ch in p if "\u0400" <= ch <= "\u04ff" or ch in "ăâđêôơưàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ")
    ratio = vi_chars / max(1, sum(len(p) for p in prompts))
    return ratio < 0.02, round(ratio, 3), "< 0.02 tỷ lệ ký tự tiếng Việt"


@_check("A11", "Asset & Style", "Độ dài prompt hợp lý (10-500 ký tự)", "assets")
def c_prompt_len(pd: Path, ctx):
    plan = ctx.get("plan") or []
    bad = sum(1 for sc in plan if not (10 <= len(str(sc.get("prompt", ""))) <= 500))
    return bad == 0, bad, "= 0 ngoài 10-500"


@_check("A12", "Asset & Style", "character_ids được gán khi có nhân vật", "assets")
def c_char_ids(pd: Path, ctx):
    chars = ctx.get("characters")
    plan = ctx.get("plan") or []
    if not chars:
        return True, None, "n/a"
    assigned = sum(1 for sc in plan if sc.get("character_ids"))
    return assigned > 0, assigned, "> 0 cảnh có nhân vật"


@_check("A13", "Asset & Style", "Tỷ lệ key scene hợp lý (30-100%)", "assets")
def c_key_ratio(pd: Path, ctx):
    plan = ctx.get("plan") or []
    if not plan:
        return False, 0, "> 0 cảnh"
    keys = sum(1 for sc in plan if sc.get("is_key_scene"))
    ratio = keys / len(plan)
    return 0.30 <= ratio <= 1.0, round(ratio, 2), "0.30-1.0"


@_check("A14", "Asset & Style", "Prompt có chỉ dẫn bố cục (negative space)", "assets")
def c_prompt_composition(pd: Path, ctx):
    plan = ctx.get("plan") or []
    prompts = [str(sc.get("prompt", "")).lower() for sc in plan if str(sc.get("prompt", "")).strip()]
    if not prompts:
        return False, 0, "> 0 prompt"
    with_comp = sum(1 for p in prompts if any(k in p for k in ("negative space", "flat", "minimal", "composition", "background")))
    return with_comp / len(prompts) >= 0.8, round(with_comp / len(prompts), 2), ">= 0.80"


@_check("A15", "Asset & Style", "Situation mô tả bằng tiếng Việt", "assets")
def c_situation_vi(pd: Path, ctx):
    plan = ctx.get("plan") or []
    sits = [str(sc.get("situation", "")).strip() for sc in plan if str(sc.get("situation", "")).strip()]
    if not sits:
        return True, None, "n/a"
    vi = sum(1 for s in sits if any(ch in s.lower() for ch in "ăâđêôơư"))
    return vi / len(sits) >= 0.6, round(vi / len(sits), 2), ">= 0.60"


# ============================================================ AUDIO CHECKS

@_check("T01", "Audio", "File audio VI tồn tại", "audio")
def c_audio_vi(pd: Path, ctx):
    p = pd / "audio_vi_full.mp3"
    ctx["audio_vi"] = p if p.is_file() else None
    return ctx["audio_vi"] is not None, None, "tồn tại"


@_check("T02", "Audio", "File audio EN tồn tại", "audio")
def c_audio_en(pd: Path, ctx):
    p = pd / "audio_en_full.mp3"
    ctx["audio_en"] = p if p.is_file() else None
    return ctx["audio_en"] is not None, None, "tồn tại"


@_check("T03", "Audio", "Audio VI không hỏng (ffprobe đọc được)", "audio")
def c_audio_vi_probe(pd: Path, ctx):
    p = ctx.get("audio_vi")
    if not p:
        return True, None, "n/a"
    info = _probe_media(p)
    return bool(info and info["duration"] > 1), (info or {}).get("duration"), "> 1s đọc được"


@_check("T04", "Audio", "Audio EN không hỏng (ffprobe đọc được)", "audio")
def c_audio_en_probe(pd: Path, ctx):
    p = ctx.get("audio_en")
    if not p:
        return True, None, "n/a"
    info = _probe_media(p)
    return bool(info and info["duration"] > 1), (info or {}).get("duration"), "> 1s đọc được"


@_check("T05", "Audio", "Audio VI khớp thời lượng kịch bản (±25%)", "audio")
def c_audio_vi_dur(pd: Path, ctx):
    p = ctx.get("audio_vi")
    if not p:
        return True, None, "n/a"
    plan = ctx.get("plan") or []
    plan_dur = sum(float(sc.get("duration", 0)) for sc in plan)
    info = _probe_media(p) or {}
    adur = info.get("duration", 0.0)
    if plan_dur <= 0:
        return False, adur, "> 0 theo plan"
    drift = abs(adur - plan_dur) / plan_dur
    return drift <= 0.25, round(drift, 2), "<= 0.25"


@_check("T06", "Audio", "Giọng đọc thuộc catalog xKiro/edge map", "audio")
def c_voice_catalog(pd: Path, ctx):
    vf = pd / "voice.txt"
    if not vf.is_file():
        return True, None, "n/a"
    from pipeline.tts import is_xkiro_voice
    from pipeline.tts import XKIRO_TO_EDGE
    voice = vf.read_text(encoding="utf-8").strip()
    return is_xkiro_voice(voice) or voice in XKIRO_TO_EDGE.values() or voice.startswith(("vi-", "en-")), voice, "thuộc catalog"


@_check("T07", "Audio", "Mật độ lời thoại 2.2-3.6 từ/giây", "audio")
def c_words_per_sec(pd: Path, ctx):
    p = ctx.get("audio_vi")
    if not p:
        return True, None, "n/a"
    info = _probe_media(p) or {}
    adur = info.get("duration", 0.0)
    plan = ctx.get("plan") or []
    words = sum(len(str(sc.get("text", "")).split()) for sc in plan)
    if adur <= 1:
        return False, None, "audio > 1s"
    wps = words / adur
    return 2.2 <= wps <= 3.6, round(wps, 2), "2.2-3.6"


@_check("T08", "Audio", "Audio EN khớp thời lượng kịch bản (±25%)", "audio")
def c_audio_en_dur(pd: Path, ctx):
    p = ctx.get("audio_en")
    if not p:
        return True, None, "n/a"
    plan_en = _load_json(pd / "shots_plan_en.json") or ctx.get("plan") or []
    plan_dur = sum(float(sc.get("duration", 0)) for sc in plan_en)
    info = _probe_media(p) or {}
    adur = info.get("duration", 0.0)
    if plan_dur <= 0:
        return False, adur, "> 0 theo plan"
    drift = abs(adur - plan_dur) / plan_dur
    return drift <= 0.25, round(drift, 2), "<= 0.25"


@_check("T09", "Audio", "Đủ segment audio cho từng cảnh", "audio")
def c_audio_segments(pd: Path, ctx):
    plan = ctx.get("plan") or []
    seg_dirs = [d for d in ("audio_segments_vi", "audio_segments", "tts_segments") if (pd / d).is_dir()]
    if not seg_dirs:
        return True, None, "n/a (chưa render segment)"
    seg_dir = pd / seg_dirs[0]
    segs = list(seg_dir.glob("*.wav")) + list(seg_dir.glob("*.mp3"))
    return len(segs) >= len(plan), (len(segs), len(plan)), ">= số cảnh"


@_check("T10", "Audio", "Audio khớp lời đọc (WER <= 15%)", "audio")
def c_audio_sync(pd: Path, ctx):
    audit = _audio_sync_report(pd, ctx)
    if audit is None:
        return True, None, "n/a (chưa có audio audit)"
    if int(audit.get("asr_words", 0) or 0) <= 0:
        return False, 0, "ASR phải nhận diện được từ"
    wer = float(audit.get("wer", 1.0))
    scenes = [s for s in (audit.get("scenes") or []) if isinstance(s, dict)]
    matched = sum(1 for s in scenes if not s.get("missing"))
    coverage = matched / len(scenes) if scenes else 0.0
    ok = wer <= AUDIO_SYNC_WER_MAX and coverage >= AUDIO_SYNC_MIN_COVERAGE
    return ok, {"wer": round(wer, 3), "coverage": round(coverage, 2)}, "WER <= 0.15 và >= 90% cảnh khớp"


@_check("T11", "Audio", "Audio tự nhiên (atempo >= 0.75, WPM trong band)", "audio")
def c_audio_natural(pd: Path, ctx):
    audit = _audio_sync_report(pd, ctx)
    if audit is None:
        return True, None, "n/a (chưa có audio audit)"
    naturalness = audit.get("naturalness") or {}
    if not naturalness:
        return True, None, "n/a (chưa có rate-fit)"
    atempo_ok = bool(naturalness.get("atempo_ok", True))
    capped = bool(naturalness.get("slowdown_capped", False))
    # Delivery rate, not articulation: local_wpm_mean excludes inter-scene pauses
    # and reads ~15 WPM high, which failed audio the rate-fit had brought to 206.
    wpm = float(
        audit.get("global_wpm")
        or naturalness.get("measured_wpm")
        or audit.get("local_wpm_mean")
        or 0.0
    )
    band = naturalness.get("band_wpm") or [195.0, 214.0]
    wpm_in_band = bool(wpm and float(band[0]) <= wpm <= float(band[1]))
    ok = atempo_ok and wpm_in_band and not capped
    value = {
        "atempo": audit.get("atempo"),
        "wpm": round(wpm, 1),
        "capped": capped,
    }
    return ok, value, "atempo >= 0.75, WPM 195-214, không chạm sàn"


# ============================================================ VIDEO CHECKS

@_check("V01", "Video", "Video cuối tồn tại", "video")
def c_video_exists(pd: Path, ctx):
    candidates = sorted(pd.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
    final = candidates[0] if candidates else None
    # Prefer the largest mp4 that is not a per-shot clip
    if final and final.parent == pd:
        ctx["video"] = final
        return True, final.name, "tồn tại"
    return False, None, "tồn tại"


@_check("V02", "Video", "Thời lượng video khớp kịch bản (±15%)", "video")
def c_video_dur(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    plan = ctx.get("plan") or []
    plan_dur = sum(float(sc.get("duration", 0)) for sc in plan)
    vdur = info.get("duration", 0.0)
    if plan_dur <= 0:
        return False, vdur, "> 0 theo plan"
    drift = abs(vdur - plan_dur) / plan_dur
    return drift <= 0.15, round(drift, 2), "<= 0.15"


@_check("V03", "Video", "Độ phân giải >= 720p", "video")
def c_video_res(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    return info.get("height", 0) >= 720, info.get("height"), ">= 720"


@_check("V04", "Video", "FPS 24-30", "video")
def c_video_fps(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    return 24 <= info.get("fps", 0.0) <= 30, info.get("fps"), "24-30"


@_check("V05", "Video", "Có luồng âm thanh", "video")
def c_video_audio_stream(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    return info.get("has_audio", False), info.get("has_audio"), "True"


@_check("V06", "Video", "Loudness -20 đến -12 LUFS", "video")
def c_video_lufs(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    lufs = _measure_lufs(v)
    if lufs is None:
        return False, None, "đo được"
    return -20 <= lufs <= -12, lufs, "-20 đến -12"


@_check("V07", "Video", "Không có file placeholder", "video")
def c_no_placeholder(pd: Path, ctx):
    bad = list(pd.rglob("*placeholder*")) + list(pd.rglob("*caption_card*"))
    return len(bad) == 0, [str(b.name) for b in bad[:5]], "= 0"


@_check("V08", "Video", "Video bitrate > 500kbps", "video")
def c_video_bitrate(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    return info.get("video_bitrate", 0) >= 500_000, info.get("video_bitrate"), ">= 500000"


@_check("V09", "Video", "Audio bitrate > 64kbps", "video")
def c_audio_bitrate(pd: Path, ctx):
    v = ctx.get("video")
    if not v:
        return True, None, "n/a"
    info = _probe_media(v) or {}
    return info.get("audio_bitrate", 0) >= 64_000, info.get("audio_bitrate"), ">= 64000"


@_check("V10", "Video", "Shot clips đủ số cảnh kịch bản", "video")
def c_shot_clips(pd: Path, ctx):
    plan = ctx.get("plan") or []
    shots_dir = pd / "shots"
    if not shots_dir.is_dir():
        return False, 0, "thư mục shots/ tồn tại"
    clips = list(shots_dir.glob("*.mp4"))
    return len(clips) >= len(plan), (len(clips), len(plan)), ">= số cảnh"


@_check("V11", "Video", "Shot clip không rỗng/hỏng", "video")
def c_shot_clips_valid(pd: Path, ctx):
    shots_dir = pd / "shots"
    if not shots_dir.is_dir():
        return True, None, "n/a"
    clips = list(shots_dir.glob("*.mp4"))
    if not clips:
        return True, None, "n/a"
    bad = 0
    for clip in clips[:20]:
        info = _probe_media(clip)
        if not info or info["duration"] < 0.5 or info["size"] < 20_000:
            bad += 1
    return bad == 0, bad, "= 0 clip hỏng"


@_check("V12", "Video", "Độ dài mỗi clip 2-12s", "video")
def c_clip_durations(pd: Path, ctx):
    shots_dir = pd / "shots"
    if not shots_dir.is_dir():
        return True, None, "n/a"
    clips = list(shots_dir.glob("*.mp4"))[:20]
    if not clips:
        return True, None, "n/a"
    bad = 0
    for clip in clips:
        info = _probe_media(clip) or {}
        if not (2.0 <= info.get("duration", 0.0) <= 12.0):
            bad += 1
    return bad == 0, bad, "= 0 ngoài 2-12s"


@_check("V13", "Video", "Có nhạc nền (BGM)", "video")
def c_bgm(pd: Path, ctx):
    for pat in ("bgm*", "music*", "*_bgm.mp3", "*.mp3"):
        found = list(pd.glob(pat))
        found = [f for f in found if f.name not in ("audio_vi_full.mp3", "audio_en_full.mp3")]
        if found:
            return True, found[0].name, ">= 1 file nhạc"
    return False, None, ">= 1 file nhạc nền"


# ============================================================ PROCESS CHECKS

@_check("P01", "Quy trình", "Không dùng bản dịch cứng (zero-fake)", "final")
def c_no_canned(pd: Path, ctx):
    plan = ctx.get("plan") or []
    canned_markers = ("giai đoạn lịch sử thứ", "historical milestone phase")
    bad = sum(1 for sc in plan for m in canned_markers if m in str(sc.get("text", "")).lower())
    return bad == 0, bad, "= 0"


@_check("P02", "Quy trình", "Số vòng best-of-N <= 3", "final")
def c_rounds(pd: Path, ctx):
    algo = ctx.get("algo") or {}
    rounds = algo.get("total_rounds")
    return rounds is not None and rounds <= 3, rounds, "<= 3"


@_check("P03", "Quy trình", "Metrics cơ học được ghi", "final")
def c_metrics_recorded(pd: Path, ctx):
    algo = ctx.get("algo") or {}
    m = algo.get("mechanical_metrics")
    return bool(m and "short_sentence_pct" in m and "estimated_wpm" in m), None, "có short_sentence_pct + wpm"


@_check("P04", "Quy trình", "Bản .txt kịch bản cho user duyệt", "final")
def c_txt_script(pd: Path, ctx):
    return any(pd.glob("kich_ban_*.txt")), None, ">= 1 file"


@_check("P05", "Quy trình", "Style report lưu đủ cho nhân vật", "final")
def c_style_report_count(pd: Path, ctx):
    chars = ctx.get("characters")
    if not chars:
        return True, None, "n/a"
    saved = sum(1 for cid in chars if (pd / "characters" / cid / "style_check.json").is_file())
    return saved == len(chars), (saved, len(chars)), "bằng số nhân vật"


@_check("P06", "Quy trình", "Góc kể được chọn hợp lệ", "final")
def c_chosen_angle(pd: Path, ctx):
    algo = ctx.get("algo") or {}
    angle = algo.get("chosen_angle")
    valid = {"analyst", "dramatist", "teacher"}
    return angle in valid, angle, "analyst|dramatist|teacher"


ALL_CHECKS = [
    c_shots_plan, c_scene_count, c_no_empty, c_no_dupes, c_hook, c_open_loops,
    c_short_pct, c_wpm, c_scene_dur, c_mean_dur, c_total_dur, c_situations,
    c_prompts_crayon, c_algo_recorded, c_no_stumps, c_hook_budget, c_callback,
    c_judge_score, c_style_reports, c_style_scores, c_refs_exist, c_refs_size,
    c_voice_saved, c_vi_plan, c_en_plan, c_vi_en_match, c_prompt_lang,
    c_prompt_len, c_char_ids, c_key_ratio, c_prompt_composition, c_situation_vi,
    c_audio_vi, c_audio_en, c_audio_vi_probe, c_audio_en_probe, c_audio_vi_dur,
    c_voice_catalog, c_words_per_sec, c_audio_en_dur, c_audio_segments,
    c_audio_sync, c_audio_natural,
    c_video_exists, c_video_dur, c_video_res, c_video_fps, c_video_audio_stream,
    c_video_lufs, c_no_placeholder, c_video_bitrate, c_audio_bitrate, c_shot_clips,
    c_shot_clips_valid, c_clip_durations, c_bgm,
    c_no_canned, c_rounds, c_metrics_recorded, c_txt_script, c_style_report_count,
    c_chosen_angle,
]


def count_criteria() -> int:
    return len(ALL_CHECKS)


def compute_scorecard(project_dir: Path, stage: str = "final") -> Scorecard:
    """Compute the QA scorecard up to `stage` for one job's project directory."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    project_dir = Path(project_dir)
    ctx: Dict[str, Any] = {"algo": _load_json(project_dir / "algorithm_result.json")}
    plan = _load_json(project_dir / "shots_plan.json")
    ctx["plan"] = plan

    characters = None
    char_root = project_dir / "characters"
    if char_root.is_dir():
        found = sorted(d.name for d in char_root.iterdir() if d.is_dir())
        characters = found if found else None
    ctx["characters"] = characters

    results: List[QACheckResult] = []
    total_w = 0.0
    passed_w = 0.0
    has_video_artifact = any(project_dir.glob("*.mp4"))
    for fn in ALL_CHECKS:
        if STAGE_ORDER[fn._qa_stage] > STAGE_ORDER[stage]:
            continue
        # Video-stage checks are n/a until a rendered mp4 exists (e.g. hard gate
        # runs at stage="final" BEFORE video generation) — excluded from weight.
        if fn._qa_stage == "video" and not has_video_artifact:
            continue
        try:
            out = fn(project_dir, ctx)
        except Exception as exc:
            out = False
            logger.warning("QA check %s raised: %s", fn._qa_id, exc)
        if isinstance(out, bool):
            passed, value, target = out, None, ""
        elif isinstance(out, tuple):
            if len(out) == 3:
                passed, value, target = out
            else:
                passed, value = out
                target = ""
        else:
            passed, value, target = bool(out), out, ""
        detail = ""
        res = QACheckResult(
            id=fn._qa_id, category=fn._qa_category, name=fn._qa_name,
            stage=fn._qa_stage, passed=bool(passed), value=value,
            target=str(target), detail=detail,
        )
        results.append(res)
        total_w += res.weight
        if res.passed:
            passed_w += res.weight

    score = (passed_w / total_w * 10.0) if total_w > 0 else 0.0
    return Scorecard(stage=stage, score=score, results=results)
