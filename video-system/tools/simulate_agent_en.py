"""E2E G.13 — mô phỏng agent video ngoài làm đúng AGENT_SHORT_GUIDE:

1. publish_job (TTS EN thật qua edge-tts) → queued
2. "Agent" tải job package → với mỗi scene: kiếm visual thật (Pixabay photo →
   zoompan thành clip 9:16; Pexels/Pixabay video → trim) đúng duration scene
3. submit_clip từng clip (multipart-equivalent: raw_bytes qua service)
4. assemble_job → final MP4 (karaoke + BGM + QC thật)

Chạy: .venv/Scripts/python.exe -I tools/simulate_agent_en.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import providers
from backend.app.service_shorts import ShortService
from backend.app.store import SQLiteStore
from backend.app.config import db_path
from backend.app.pipeline_short import stock_media

SCRIPT_EN = {
    "title": "Three billion dong gone overnight",
    "hook": "Overnight, three billion dong vanished from dozens of bank accounts",
    "beats": [
        "By morning, customers kept calling the hotline about strange transactions they never made",
        "The bank later confirmed a gang posing as support staff had been stealing OTP codes",
        "Average losses hit fifty million dong each, with some victims losing two hundred million dong",
    ],
    "closing": "So who bears the final responsibility — the bank or the customer?",
    "hashtags": {"tiktok": ["#breakingnews", "#bankfraud", "#money"],
                 "facebook": ["#news"], "youtube": ["#shorts"]},
    "description": "Bank OTP scam drained customer accounts overnight. Stay alert.",
    "source_note": "Source: BBC Business",
}


class FakeLlm:
    def chat_json(self, system, user, schema=None):
        return SCRIPT_EN


def make_photo_clip(photo: Path, duration: float, out: Path) -> None:
    """"Agent" render: ảnh → clip 9:16 punch-zoom (Ken Burns), đúng duration."""
    frames = max(2, int(round(duration * 30)))
    vf = (
        "scale=1216:2160:force_original_aspect_ratio=increase,crop=1216:2160,setsar=1,"
        f"zoompan=z='min(zoom+0.0012,1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps=30"
    )
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{duration:.3f}",
        "-i", str(photo), "-vf", vf, "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True, timeout=300)


def make_footage_clip(footage: Path, duration: float, out: Path) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(footage),
        "-t", f"{duration:.3f}",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               "crop=1080:1920,setsar=1,fps=30",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True, timeout=300)


def main() -> None:
    store = SQLiteStore(db_path())
    shorts = ShortService(store)
    saved = providers.registry.llm
    providers.registry.llm = FakeLlm()
    try:
        short = shorts.create(mode="news", lang="en",
                              source_text=("Brief: A bank OTP scam drained customer "
                                           "accounts overnight; dozens of victims lost "
                                           "50-200 million dong each."),
                              source_note="Source: BBC Business", actor="sim-agent")
    finally:
        providers.registry.llm = saved
    sid = short["id"]
    print("[1] created:", sid, short["status"])

    job = shorts.publish_job(sid, actor="sim-agent")
    print("[2] job published:", job["status"], "| scenes:", len(job["scenes"]),
          "| total:", job["progress"]["total_duration_sec"], "s")

    work = Path(tempfile.mkdtemp(prefix="agent_clips_"))
    for sc in job["scenes"]:
        q = sc["stock_search_keywords"]
        print(f"[3] scene {sc['seq']}: dur={sc['duration_sec']}s query={q!r}")
        clip = work / f"scene_{sc['seq']:03d}.mp4"
        got = stock_media.fetch_photo(q) or stock_media.fetch_video(q)
        if got and got["kind"] == "video_clip":
            make_footage_clip(got["path"], sc["duration_sec"], clip)
        elif got:
            make_photo_clip(got["path"], sc["duration_sec"], clip)
        else:
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", f"testsrc2=size=1080x1920:rate=30:duration={sc['duration_sec']:.3f}",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", str(clip),
            ], check=True, timeout=120)
            print("    (fallback testsrc2 — không có stock)")
        res = shorts.submit_clip(sid, seq_num=sc["seq"], raw_bytes=clip.read_bytes(),
                                 filename=clip.name, actor="sim-agent")
        print(f"    submitted: covered={res['covered_scene_indexes']} "
              f"progress={res['progress']['scenes_covered']}/{res['progress']['scenes_total']}")

    done = shorts.assemble_job(sid, actor="sim-agent")
    print("[4] assembled:", done["duration_sec"], "s |", done["video_url"])
    print("[OK] final:", shorts.video_path(sid))


if __name__ == "__main__":
    main()
