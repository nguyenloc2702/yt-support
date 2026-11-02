"""Grade one render against the reference standard in plans/visual_standard.md.

    python -m tools.standard_report <video.mp4> [--out-dir DIR] [--from-report R.json]

Writes ``standard_report.json`` next to the video (or into --out-dir) and prints
the table. Informational only: this never blocks a gate.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from core.smoothness.metrics import measure_video, prepare_measurement_copy
from core.smoothness.standard import SPEC_DOC, grade, summary
from pipeline.subtitles import caption_coverage

ICONS = {"pass": "OK  ", "warn": "warn", "miss": "MISS"}


def find_subtitle_file(video: Path, job_dir: Optional[Path] = None) -> Optional[Path]:
    """The cue file this render's captions came from, when the render says so.

    A cue file outlives a failed burn, and job directories hold cue files for
    renders that never got one, so discovering a file by name alone would report
    captions the pixels never had. The ``<video>.captions.json`` the assembler
    drops beside every output is the only trusted source; no marker means the
    render predates caption burn-in and is graded as caption-free.
    """
    video = Path(video)
    marker = video.with_suffix(".captions.json")
    if not marker.is_file():
        return None
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not info.get("burned"):
        return None
    name = info.get("cue_file")
    if not name:
        return None
    for folder in [video.parent] + ([Path(job_dir)] if job_dir else []):
        hit = folder / name
        if hit.is_file():
            return hit
    return None


def report_from_video(
    video: Path,
    out_dir: Optional[Path] = None,
    subtitle_path: Optional[Path] = None,
    with_black_cards: bool = True,
) -> Dict[str, Any]:
    video = Path(video)
    measure_file, tmpdir = prepare_measurement_copy(video)
    subs = Path(subtitle_path) if subtitle_path else find_subtitle_file(video, out_dir)
    try:
        report = measure_video(
            measure_file,
            source={"type": "file", "path": str(video)},
            with_black_cards=with_black_cards,
        )
        report["video"]["captions"] = caption_coverage(subs, report["video"]["duration_s"])
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    rows = grade(report)
    return {
        "schema_version": 1,
        "spec": SPEC_DOC,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": str(video),
        "summary": summary(rows),
        "checks": rows,
        "measurement": report,
    }


def report_from_measurement(measurement: Dict[str, Any], video: Optional[str] = None) -> Dict[str, Any]:
    rows = grade(measurement)
    return {
        "schema_version": 1,
        "spec": SPEC_DOC,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": video or (measurement.get("source") or {}).get("path"),
        "summary": summary(rows),
        "checks": rows,
        "measurement": measurement,
    }


def write_standard_report(result: Dict[str, Any], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "standard_report.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_standard_report(project_dir: Path) -> Optional[Dict[str, Any]]:
    """The last standard report of a job, or None when it was never graded."""
    path = Path(project_dir) / "standard_report.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("checks") else None


def format_table(result: Dict[str, Any]) -> str:
    lines = [f"CHUẨN THAM CHIẾU ({result['spec']}) — {result['video']}"]
    for r in result["checks"]:
        value = "-" if r["value"] is None else f"{r['value']:g}{(' ' + r['unit']) if r['unit'] else ''}"
        lines.append(f"  [{ICONS[r['status']]}] {r['label']:<28} {value:>14}  target {r['target']}")
    s = result["summary"]
    lines.append(f"→ {s['pass']} đạt / {s['warn']} lệch nhẹ / {s['miss']} chưa đạt")
    return "\n".join(lines)


def format_telegram_block(result: Dict[str, Any]) -> str:
    """Compact block for the Telegram job report; never a gate."""
    s = result["summary"]
    lines = [f"\n📐 <b>CHUẨN THAM CHIẾU</b> — {s['pass']} đạt / {s['warn']} lệch / {s['miss']} thiếu"]
    icon = {"pass": "✅", "warn": "🟡", "miss": "🔴"}
    for r in result["checks"]:
        if r["status"] == "pass":
            continue
        value = "-" if r["value"] is None else f"{r['value']:g}"
        lines.append(f"{icon[r['status']]} {r['label']}: {value} (chuẩn {r['target']})")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    # Vietnamese labels on a cp1252 Windows console would crash the print.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Grade a render against the reference standard")
    parser.add_argument("video", nargs="?", help="final mp4 to measure")
    parser.add_argument("--out-dir", default=None, help="where standard_report.json goes")
    parser.add_argument("--from-report", default=None,
                        help="re-grade an existing measurement report.json without re-decoding")
    parser.add_argument("--subs", default=None,
                        help="ASS/SRT actually burned into the render (caption coverage)")
    parser.add_argument("--skip-black-scan", action="store_true",
                        help="skip the full-length black card scan (faster, less complete)")
    args = parser.parse_args(argv)

    if args.from_report:
        measurement = json.loads(Path(args.from_report).read_text(encoding="utf-8"))
        result = report_from_measurement(measurement)
        default_out = Path(args.from_report).parent
    elif args.video:
        video = Path(args.video)
        if not video.is_file():
            raise SystemExit(f"not found: {video}")
        result = report_from_video(
            video,
            subtitle_path=Path(args.subs) if args.subs else None,
            with_black_cards=not args.skip_black_scan,
        )
        default_out = video.parent
    else:
        raise SystemExit("give a video or --from-report")

    out_dir = Path(args.out_dir) if args.out_dir else default_out
    path = write_standard_report(result, out_dir)
    print(format_table(result))
    print(f"report written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
