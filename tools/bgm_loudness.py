"""Measure integrated loudness of every track in the BGM pack.

Writes `LOUDNESS.json` next to the mp3s so the planner can put every cue at the
same bed level instead of trusting each source's mastering.
"""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PACK = Path(r"C:\app\work\support-video-edit\assets\music\bgm")
WINDOW_S = 60.0
SKIP_S = 15.0


def measure(path: Path):
    cmd = ["ffmpeg", "-v", "info", "-nostats", "-ss", str(SKIP_S), "-t", str(WINDOW_S),
           "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    tail = res.stderr[res.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+([-0-9.]+) LUFS", tail)
    tp = re.search(r"Peak:\s+([-0-9.]+) dBFS", tail)
    if not i:
        return path.name, None
    return path.name, {"lufs": float(i.group(1)), "tp": float(tp.group(1)) if tp else None}


def main() -> int:
    manifest = json.loads((PACK / "MANIFEST.json").read_text(encoding="utf-8"))
    files = [PACK / t["file"] for t in manifest["tracks"] if (PACK / t["file"]).exists()]
    out = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for name, value in pool.map(measure, files):
            if value:
                out[name] = value
            else:
                print("unmeasured", name, file=sys.stderr)
    payload = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_s": WINDOW_S,
        "skip_s": SKIP_S,
        "tracks": dict(sorted(out.items())),
    }
    (PACK / "LOUDNESS.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    vals = [v["lufs"] for v in out.values()]
    print(f"measured {len(out)}/{len(files)}  min {min(vals):.1f}  max {max(vals):.1f}  "
          f"median {sorted(vals)[len(vals)//2]:.1f} LUFS")
    return 0 if len(out) == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
