"""Build a 9:16 (1080x1920) Breaking News Short video from real photos and a JSON story spec.

Reuses the workspace's edge-tts + WordBoundary timing model and ASS karaoke subtitles
to produce a ready-to-publish vertical news short.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional

import edge_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("make_news_short")

WIDTH = 1080
HEIGHT = 1920
FPS = 30
PAD_BETWEEN_SCENES = 0.25

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def find_font() -> str:
    for cand in FONT_CANDIDATES:
        if Path(cand).is_file():
            return cand
    raise FileNotFoundError("No bold TTF font found for FFmpeg drawtext.")


def _ff_escape_path(path: Path) -> str:
    """Escape a Windows/POSIX path for FFmpeg filter arguments (drawtext/ass)."""
    s = path.resolve().as_posix()
    return s.replace(":", r"\:")


def _ff_escape_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
    )


def probe_duration(media_path: Path) -> float:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def probe_image_size(image_path: Path) -> tuple[int, int]:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(image_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = res.stdout.strip().split("x")
    return int(w), int(h)


async def _synth_tts(text: str, voice: str, rate: str, out_mp3: Path, out_words: Path) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, boundary="WordBoundary")
    audio_chunks = bytearray()
    words: List[Dict[str, Any]] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            offset_sec = chunk["offset"] / 10_000_000.0
            dur_sec = chunk["duration"] / 10_000_000.0
            words.append({
                "word": chunk["text"],
                "start": round(offset_sec, 4),
                "end": round(offset_sec + dur_sec, 4),
            })
    out_mp3.write_bytes(bytes(audio_chunks))
    out_words.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")


def _fmt_ass(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_karaoke_ass(word_timings: List[Dict[str, Any]], out_ass: Path) -> None:
    """Build vertical-safe karaoke ASS subtitles (4-5 words per chunk)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: NewsSub,Arial,62,&H0000D7FF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    chunk_size = 4
    for i in range(0, len(word_timings), chunk_size):
        chunk = word_timings[i : i + chunk_size]
        if not chunk:
            continue
        start = chunk[0]["start"]
        end = chunk[-1]["end"] + 0.12
        parts = []
        for idx, w in enumerate(chunk):
            w_dur = max(0.05, (chunk[idx + 1]["start"] if idx + 1 < len(chunk) else w["end"]) - w["start"])
            cs = max(5, int(round(w_dur * 100)))
            parts.append(f"{{\\kf{cs}}}{w['word']}")
        text = " ".join(parts)
        lines.append(f"Dialogue: 0,{_fmt_ass(start)},{_fmt_ass(end)},NewsSub,,0,0,0,,{text}")

    out_ass.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_kenburns_clip(
    image_path: Path,
    duration: float,
    motion: str,
    out_mp4: Path,
) -> None:
    """Render a 1080x1920 30fps Ken Burns clip from a real photo."""
    w, h = probe_image_size(image_path)
    frames = max(30, int(round(duration * FPS)))
    aspect = w / max(1, h)

    # Use zoompan on a high-res scaled canvas for smooth sub-pixel motion
    if motion == "zoom_out":
        z_expr = f"max(1.0, 1.15 - 0.15*on/{frames})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion == "pan_up":
        z_expr = "1.12"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*(1 - on/{frames})"
    elif motion == "pan_down":
        z_expr = "1.12"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"(ih-ih/zoom)*(on/{frames})"
    else:  # zoom_in
        z_expr = f"min(1.16, 1.0 + 0.16*on/{frames})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    if aspect <= 1.05:
        # Vertical or square photo -> full-bleed 9:16
        vf = (
            f"scale=2160:3840:force_original_aspect_ratio=increase,"
            f"crop=2160:3840,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"format=yuv420p"
        )
    else:
        # Wide photo -> blurred 9:16 background + sharp centered photo
        vf = (
            f"split[bg][fg];"
            f"[bg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},boxblur=24:4,eq=brightness=-0.25[bg_b];"
            f"[fg]scale=2160:-1,zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1040x780:fps={FPS}[fg_z];"
            f"[bg_b][fg_z]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
        )

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            vf,
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "19",
            "-an",
            str(out_mp4),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def build_news_short(story_path: Path, out_video: Path) -> Path:
    story = json.loads(story_path.read_text(encoding="utf-8"))
    work_dir = story_path.parent / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    voice = story.get("voice", "en-US-GuyNeural")
    rate = story.get("rate", "+6%")
    badge = story.get("badge", "BREAKING NEWS")
    headline = story.get("headline", "NEWS UPDATE")
    ticker = story.get("ticker", "")
    scenes = story["scenes"]

    font_path = _ff_escape_path(Path(find_font()))

    clip_paths: List[Path] = []
    audio_paths: List[Path] = []
    all_words: List[Dict[str, Any]] = []
    cursor = 0.0

    for idx, sc in enumerate(scenes, start=1):
        img = Path(sc["image"])
        if not img.is_absolute():
            img = (story_path.parent / img).resolve()
        narration = sc["narration"].strip()
        motion = sc.get("motion", "zoom_in")

        mp3 = work_dir / f"scene_{idx:02d}.mp3"
        wjson = work_dir / f"scene_{idx:02d}.words.json"
        logger.info("Synthesizing TTS for scene %d/%d...", idx, len(scenes))
        asyncio.run(_synth_tts(narration, voice, rate, mp3, wjson))

        audio_dur = probe_duration(mp3)
        clip_dur = audio_dur + PAD_BETWEEN_SCENES

        words = json.loads(wjson.read_text(encoding="utf-8"))
        for w in words:
            all_words.append({
                "word": w["word"],
                "start": round(cursor + w["start"], 4),
                "end": round(cursor + w["end"], 4),
            })

        # Pad audio to exact clip_dur so concat stays frame-accurate
        padded_wav = work_dir / f"scene_{idx:02d}_pad.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(mp3),
                "-af",
                f"apad=whole_dur={clip_dur:.3f}",
                "-ar",
                "44100",
                "-ac",
                "2",
                str(padded_wav),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        audio_paths.append(padded_wav)

        clip_mp4 = work_dir / f"scene_{idx:02d}.mp4"
        logger.info("Rendering Ken Burns clip %d/%d (%.2fs, %s)...", idx, len(scenes), clip_dur, motion)
        render_kenburns_clip(img, clip_dur, motion, clip_mp4)
        clip_paths.append(clip_mp4)

        cursor += clip_dur

    # Build ASS karaoke subtitles
    ass_path = work_dir / "subtitles.ass"
    build_karaoke_ass(all_words, ass_path)

    # Concat video clips
    v_list = work_dir / "video_concat.txt"
    v_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths), encoding="utf-8")
    concat_v = work_dir / "concat_video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(v_list), "-c", "copy", str(concat_v)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Concat audio tracks
    a_list = work_dir / "audio_concat.txt"
    a_list.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in audio_paths), encoding="utf-8")
    concat_a = work_dir / "concat_audio.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(a_list), "-c", "copy", str(concat_a)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Wrap headline to max 2 lines and render each line with its own drawtext
    wrapped_lines = textwrap.wrap(headline.upper(), width=24)[:2]
    badge_escaped = _ff_escape_text(badge.upper())
    ass_escaped = _ff_escape_path(ass_path)

    # Breaking News header + footer + ASS subtitles
    vf_parts = [
        # Top dark scrim for readability
        f"drawbox=x=0:y=0:w={WIDTH}:h=310:color=black@0.68:t=fill",
        # Red BREAKING NEWS badge
        f"drawbox=x=60:y=95:w=330:h=58:color=#DC2626@0.95:t=fill",
        f"drawtext=fontfile='{font_path}':text='{badge_escaped}':fontcolor=white:fontsize=34:x=82:y=108",
    ]
    for line_idx, line_text in enumerate(wrapped_lines):
        line_escaped = _ff_escape_text(line_text)
        y_pos = 172 + line_idx * 64
        vf_parts.append(
            f"drawtext=fontfile='{font_path}':text='{line_escaped}':fontcolor=white:fontsize=54:x=60:y={y_pos}"
        )
    vf_parts.append(
        # Bottom dark scrim behind subtitles & ticker
        f"drawbox=x=0:y=1520:w={WIDTH}:h=400:color=black@0.55:t=fill",
    )
    if ticker:
        ticker_escaped = _ff_escape_text(f"  {ticker.upper()}   •   {ticker.upper()}  ")
        vf_parts.append(f"drawbox=x=0:y=1830:w={WIDTH}:h=90:color=#991B1B@0.92:t=fill")
        vf_parts.append(
            f"drawtext=fontfile='{font_path}':text='{ticker_escaped}':fontcolor=white:fontsize=32:"
            f"x=w-mod(t*140\\,w+tw):y=1858"
        )

    vf_parts.append(f"ass='{ass_escaped}'")
    vf_final = ",".join(vf_parts)

    out_video.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Compositing final Breaking News short -> %s", out_video)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(concat_v),
            "-i",
            str(concat_a),
            "-vf",
            vf_final,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out_video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("Done! Total duration: %.2fs", cursor)
    return out_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a 9:16 Breaking News Short from real photos.")
    parser.add_argument("--story", required=True, help="Path to story JSON file")
    parser.add_argument("--out", required=True, help="Output MP4 path")
    args = parser.parse_args()
    build_news_short(Path(args.story), Path(args.out))


if __name__ == "__main__":
    main()
