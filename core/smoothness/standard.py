"""The reference standard as machine-readable targets.

Human-readable spec: ``plans/visual_standard.md`` section 6, measured from 17
reference clips (bands are the p10-p90 of the 14 Crayon Capital clips; six
bounds are deliberate production caps/floors, listed in the same table). This
grades a render for information only — it is never a gate, and it must not be
made stricter than the QA scorecard (services/algorithm_qa.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DOC = "plans/visual_standard.md"

# Bounds we set tighter than the measured reference band, for a product reason
# spelled out in plans/visual_standard.md section 6. Everything else is p10-p90.
CAP_KEYS = {"pan_px_s", "static_share", "rigid_share", "caption_share", "bed_side_quiet_db"}


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    low: Optional[float]
    high: Optional[float]
    measure: Callable[[Dict[str, Any]], Optional[float]]
    unit: str = ""
    better: str = "band"   # band | min | max


def _v(report: Dict[str, Any], *path: str) -> Optional[float]:
    node: Any = report
    for k in path:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node if isinstance(node, (int, float)) else None


CHECKS: List[Check] = [
    Check("fps", "Tốc độ khung hình", 30.0, 30.0, lambda r: _v(r, "video", "fps"), "fps", "min"),
    Check("cuts_per_min", "Cảnh cắt mỗi phút", 9.05, 16.2, lambda r: _v(r, "video", "cuts_per_min"), "cảnh/phút"),
    Check("shot_median", "Độ dài cảnh (median)", 3.1, 5.49,
          lambda r: _v(r, "video", "motion", "shot_dur_s", "median"), "s"),
    Check("pan_px_s", "Pan (median)", 0.0, 14.2,
          lambda r: _v(r, "video", "motion", "camera", "trans_px_s_median"), "px/s", "max"),
    Check("zoom_pct_s", "Zoom (median)", 0.01, 3.2,
          lambda r: _v(r, "video", "motion", "camera", "zoom_pct_s_median"), "%/s"),
    Check("zoom_share", "Tỉ lệ cảnh có zoom", 0.12, 0.71,
          lambda r: _v(r, "video", "motion", "camera", "share_zoom_gt0_5pct_s"), "", "min"),
    Check("static_share", "Cảnh đứng yên", 0.06, 0.26,
          lambda r: _v(r, "video", "motion", "local_motion", "share_static_rawdiff_lt0_5"), ""),
    Check("local_share", "Động nội khung mạnh", 0.05, 0.22,
          lambda r: _v(r, "video", "motion", "local_motion", "share_strong_local_residual_ge6"), "", "min"),
    Check("rigid_share", "Chỉ camera chạy", 0.0, 0.60,
          lambda r: _v(r, "video", "motion", "local_motion", "share_rigid_residual_lt2"), "", "max"),
    Check("black_per_min", "Thẻ đen mỗi phút", 0.51, 2.2,
          lambda r: (_v(r, "video", "black_cards", "count") or 0) * 60.0 / (_v(r, "video", "duration_s") or 1)
          if _v(r, "video", "black_cards", "count") is not None else None, "thẻ/phút"),
    Check("caption_share", "Phủ caption", 0.85, 1.0,
          lambda r: _v(r, "video", "captions", "caption_share"), "", "min"),
    Check("integrated_lufs", "Độ lớn âm", -21.2, -17.05,
          lambda r: _v(r, "audio", "integrated_lufs"), "LUFS"),
    Check("lra_lu", "Dải động (LRA)", 3.45, 4.95, lambda r: _v(r, "audio", "lra_lu"), "LU"),
    Check("bed_side_quiet_db", "Bed stereo khi giọng nghỉ", -20.0, 0.0,
          lambda r: _v(r, "audio", "side_minus_mid_when_quiet_db"), "dB", "min"),
]


def grade(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per check: value, target text and pass | warn | miss."""
    rows: List[Dict[str, Any]] = []
    for c in CHECKS:
        try:
            value = c.measure(report)
        except (TypeError, ZeroDivisionError):
            value = None
        row: Dict[str, Any] = {"key": c.key, "label": c.label, "unit": c.unit,
                               "value": None if value is None else round(float(value), 3),
                               "target": _target_text(c)}
        if value is None:
            row["status"] = "miss"
            row["detail"] = "không đo được"
        else:
            row["status"] = _status(c, float(value))
        rows.append(row)
    return rows


def _target_text(c: Check) -> str:
    if c.better == "min" and c.low is not None:
        return f"≥ {c.low:g}{c.unit and ' ' + c.unit}"
    if c.better == "max" and c.high is not None:
        return f"≤ {c.high:g}{c.unit and ' ' + c.unit}"
    if c.low == c.high:
        return f"{c.low:g}{c.unit and ' ' + c.unit}"
    return f"{c.low:g}–{c.high:g}{c.unit and ' ' + c.unit}"


def _status(c: Check, value: float) -> str:
    if c.better == "min":
        lo, hi = c.low, None
    elif c.better == "max":
        lo, hi = None, c.high
    else:
        lo, hi = c.low, c.high
    if lo is not None and value < lo:
        span = abs(lo) or 1.0
        return "warn" if value >= lo - 0.5 * span else "miss"
    if hi is not None and value > hi:
        span = abs(hi) or 1.0
        return "warn" if value <= hi + 0.5 * span else "miss"
    return "pass"


def summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"pass": 0, "warn": 0, "miss": 0}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


_TARGET_RE = re.compile(
    r"^(\w+)\s+(?:(?:>=|<=)\s*-?[\d.]+|-?[\d.]+\.\.-?[\d.]+)\s+(?:measured|cap)\b", re.MULTILINE)


def spec_targets(spec_path: Path = None) -> Dict[str, Dict[str, Any]]:
    """Parse the ```targets block of plans/visual_standard.md section 6.

    The block is the contract between the written spec and CHECKS; the drift
    test fails if either side moves alone.
    """
    path = Path(spec_path) if spec_path else REPO_ROOT / SPEC_DOC
    text = path.read_text(encoding="utf-8")
    out: Dict[str, Dict[str, Any]] = {}
    for match in _TARGET_RE.finditer(text):
        line = match.group(0).split()
        key, bound = line[0], line[1]
        if bound.startswith(">="):
            low, high = float(bound[2:]), None
        elif bound.startswith("<="):
            low, high = None, float(bound[2:])
        else:
            a, b = bound.split("..")
            low, high = (float(a) if a else None), (float(b) if b else None)
        out[key] = {"low": low, "high": high, "kind": line[2]}
    return out
