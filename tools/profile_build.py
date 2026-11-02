"""Aggregate reference reports into a smoothness profile (p10/p50/p90 bands).

    python -m tools.profile_build [--refs analysis/refs]
                                  [--out analysis/refs/reference_profile.json]
                                  [--require 5]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from core.smoothness.metrics import SCHEMA_VERSION, music_bed_present

METRIC_PATHS: Dict[str, tuple] = {
    "cuts_per_min": ("video", "cuts_per_min"),
    "shot_dur_median_s": ("video", "motion", "shot_dur_s", "median"),
    "shot_dur_p90_s": ("video", "motion", "shot_dur_s", "p90"),
    "trans_px_s_median": ("video", "motion", "camera", "trans_px_s_median"),
    "zoom_pct_s_median": ("video", "motion", "camera", "zoom_pct_s_median"),
    "share_trans_gt2px_s": ("video", "motion", "camera", "share_trans_gt2px_s"),
    "share_zoom_gt0_5pct_s": ("video", "motion", "camera", "share_zoom_gt0_5pct_s"),
    "share_static": ("video", "motion", "local_motion", "share_static_rawdiff_lt0_5"),
    "share_rigid": ("video", "motion", "local_motion", "share_rigid_residual_lt2"),
    "share_local": ("video", "motion", "local_motion", "share_local_residual_2to6"),
    "share_strong_local": ("video", "motion", "local_motion", "share_strong_local_residual_ge6"),
    "fps": ("video", "fps"),
    "integrated_lufs": ("audio", "integrated_lufs"),
    "lra_lu": ("audio", "lra_lu"),
    "side_minus_mid_mean_db": ("audio", "side_minus_mid_mean_db"),
    "side_minus_mid_when_quiet_db": ("audio", "side_minus_mid_when_quiet_db"),
    "side_active_share": ("audio", "side_active_share"),
}

SHARE_METRICS = {
    "share_trans_gt2px_s", "share_zoom_gt0_5pct_s", "share_static",
    "share_rigid", "share_local", "share_strong_local", "side_active_share",
}


def _dig(data: Any, path: tuple) -> Optional[float]:
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, bool) or cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def collect_refs(refs_dir: Path) -> List[Dict[str, Any]]:
    reports = []
    for path in sorted(Path(refs_dir).glob("*/report.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("schema_version") == SCHEMA_VERSION:
            reports.append(data)
    return reports


def build_profile(refs_dir: Path) -> Dict[str, Any]:
    refs_dir = Path(refs_dir)
    reports = collect_refs(refs_dir)
    metrics: Dict[str, Dict[str, Any]] = {}
    for name, path in METRIC_PATHS.items():
        values = [v for v in (_dig(r, path) for r in reports) if v is not None]
        if not values:
            continue
        arr = np.array(values, dtype=float)
        metrics[name] = {
            "p10": round(float(np.percentile(arr, 10)), 3),
            "p50": round(float(np.percentile(arr, 50)), 3),
            "p90": round(float(np.percentile(arr, 90)), 3),
            "min": round(float(arr.min()), 3),
            "max": round(float(arr.max()), 3),
            "n": int(arr.size),
        }

    music_flags = [music_bed_present(r.get("audio") or {}) for r in reports]
    fps_values = [r.get("video", {}).get("fps") for r in reports if r.get("video", {}).get("fps")]
    fps_mode = None
    if fps_values:
        vals, counts = np.unique(np.array(fps_values, dtype=float), return_counts=True)
        fps_mode = float(vals[int(np.argmax(counts))])

    bands = {}
    for name, stat in metrics.items():
        low, high = stat["p10"], stat["p90"]
        if name in SHARE_METRICS:
            low, high = max(0.0, low), min(1.0, high)
        bands[name] = {"low": low, "high": high}

    profile = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "refs": [r.get("id") for r in reports],
        "count": len(reports),
        "fps_mode": fps_mode,
        "music_bed_share": round(float(np.mean(music_flags)), 3) if music_flags else None,
        "metrics": metrics,
        "bands": bands,
    }
    return profile


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Build reference_profile.json from reports")
    parser.add_argument("--refs", default="analysis/refs")
    parser.add_argument("--out", default=None)
    parser.add_argument("--require", type=int, default=0,
                        help="fail when fewer than N usable reports")
    args = parser.parse_args(argv)

    profile = build_profile(Path(args.refs))
    out_path = Path(args.out) if args.out else Path(args.refs) / "reference_profile.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"refs={profile['count']} music_bed_share={profile['music_bed_share']} -> {out_path}")
    for name in ("cuts_per_min", "shot_dur_median_s", "share_zoom_gt0_5pct_s",
                 "share_strong_local", "side_minus_mid_when_quiet_db", "integrated_lufs"):
        stat = profile["metrics"].get(name)
        if stat:
            print(f"  {name:32s} p10={stat['p10']:<8} p50={stat['p50']:<8} p90={stat['p90']}")

    if args.require and profile["count"] < args.require:
        print(f"ERROR: need >= {args.require} usable reference reports, got {profile['count']}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
