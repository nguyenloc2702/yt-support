"""Ingest a reference video (URL or local file) into a measurable report.

    python -m tools.reference_bench ingest <url|file> [--out-dir analysis/refs]
                                           [--keep-source] [--id ID]

A downloaded reference is deleted after measuring: only the report (numbers,
no frames or audio) is kept, so nothing copyrightable accumulates locally.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

from core.smoothness.metrics import measure_video, prepare_measurement_copy, write_report


def _yt_dlp() -> Optional[str]:
    return shutil.which("yt-dlp")


def download(url: str, workdir: Path, max_height: int = 480) -> tuple[Path, Dict[str, str]]:
    exe = _yt_dlp()
    if not exe:
        raise RuntimeError("yt-dlp not found on PATH")
    meta_file = workdir / "meta.txt"
    cmd = [
        exe, "--no-playlist",
        "-f", (f"bv*[height<={max_height}][vcodec^=avc1]+ba"
               f"/b[height<={max_height}]/18"),
        "-P", str(workdir), "-o", "%(id)s.%(ext)s",
        "--print-to-file", "%(id)s\t%(title)s\t%(channel)s\t%(duration)s\t%(view_count)s", str(meta_file),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[-600:]}")

    media = [p for p in workdir.iterdir() if p.is_file() and p.name != "meta.txt"]
    if not media:
        raise RuntimeError("yt-dlp produced no media file")

    meta: Dict[str, str] = {}
    if meta_file.exists():
        parts = meta_file.read_text(encoding="utf-8").strip().split("\t")
        if len(parts) >= 4:
            meta = {"id": parts[0], "title": parts[1], "channel": parts[2], "duration_s": parts[3]}
        if len(parts) >= 5 and parts[4] not in ("", "NA", "None"):
            meta["view_count"] = parts[4]
    return max(media, key=lambda p: p.stat().st_size), meta


def ingest(
    target: str,
    out_dir: Path,
    *,
    keep_source: bool = False,
    video_id: Optional[str] = None,
    cut_threshold: float = 27.0,
    max_height: int = 480,
    audio_path: Optional[str] = None,
) -> Path:
    is_url = target.startswith(("http://", "https://"))
    measure_dir = None
    if is_url:
        workdir = Path(tempfile.mkdtemp(prefix="refbench_"))
        try:
            media, meta = download(target, workdir, max_height=max_height)
            rid = video_id or meta.get("id") or media.stem
            source = {"type": "url", "url": target, **meta}
            measure_file, measure_dir = prepare_measurement_copy(media, max_height)
            report = measure_video(
                measure_file, video_id=rid, source=source, cut_threshold=cut_threshold,
                audio_source=audio_path,
            )
        finally:
            if measure_dir:
                shutil.rmtree(measure_dir, ignore_errors=True)
            if not keep_source:
                shutil.rmtree(workdir, ignore_errors=True)
    else:
        media = Path(target)
        if not media.is_file():
            raise FileNotFoundError(media)
        rid = video_id or media.stem
        try:
            measure_file, measure_dir = prepare_measurement_copy(media, max_height)
            report = measure_video(
                measure_file,
                video_id=rid,
                source={"type": "file", "path": str(media)},
                cut_threshold=cut_threshold,
                audio_source=audio_path,
            )
        finally:
            if measure_dir:
                shutil.rmtree(measure_dir, ignore_errors=True)

    out_path = out_dir / rid / "report.json"
    write_report(report, out_path)
    return out_path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Measure a reference video into a report")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("ingest", help="measure a URL or local file into analysis/refs/<id>/report.json")
    cmd.add_argument("target", help="YouTube URL or local video file")
    cmd.add_argument("--out-dir", default="analysis/refs")
    cmd.add_argument("--keep-source", action="store_true",
                     help="keep the downloaded file (local files are never deleted)")
    cmd.add_argument("--id", dest="video_id", default=None)
    cmd.add_argument("--cut-threshold", type=float, default=27.0)
    cmd.add_argument("--max-height", type=int, default=480,
                     help="download/measurement height cap (measurement only)")
    cmd.add_argument("--audio", default=None,
                     help="separate audio file to measure when the video has none")
    args = parser.parse_args(argv)

    out_path = ingest(
        args.target,
        Path(args.out_dir),
        keep_source=args.keep_source,
        video_id=args.video_id,
        cut_threshold=args.cut_threshold,
        max_height=args.max_height,
        audio_path=args.audio,
    )
    print(f"report written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
