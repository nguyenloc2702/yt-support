"""YouTube English transcript & subtitle extractor."""
from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path


def extract_youtube_id(url: str) -> str | None:
    """Extract 11-char YouTube video ID from standard, short, or embed URLs."""
    if not url:
        return None
    patterns = [
        r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v%3D|watch\?feature=player_embedded&v=|%2Fvideos%2F|embed%\u200C\u200B2F|youtu.be%2F|%2Fv%2F)([0-9A-Za-z_-]{11})",
        r"^([0-9A-Za-z_-]{11})$",
    ]
    for pat in patterns:
        m = re.search(pat, url.strip())
        if m:
            return m.group(1)
    return None


def clean_rolling_stutter(text: str) -> str:
    """Remove rolling caption duplicate phrases common in YouTube auto-generated VTTs."""
    words = text.split()
    for span in range(min(8, len(words) // 2), 1, -1):
        i = 0
        while i + 2 * span <= len(words):
            p1 = [w.lower().strip(".,!?\"'") for w in words[i : i + span]]
            p2 = [w.lower().strip(".,!?\"'") for w in words[i + span : i + 2 * span]]
            if p1 == p2 and any(len(x) > 2 for x in p1):
                words = words[: i + span] + words[i + 2 * span :]
            else:
                i += 1
    return " ".join(words)


def _parse_vtt_to_segments(vtt_path: Path) -> list[dict]:
    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    time_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
    )

    def _to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    segments: list[dict] = []
    cur_start, cur_end = 0.0, 0.0
    cur_lines: list[str] = []

    for line in lines:
        s = line.strip()
        m = time_re.search(s)
        if m:
            if cur_lines:
                txt = " ".join(cur_lines).strip()
                if txt and (not segments or segments[-1]["text"] != txt):
                    segments.append({"start": cur_start, "end": cur_end, "text": txt})
                cur_lines = []
            cur_start = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
            cur_end = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
            continue
        if not s or s.startswith("WEBVTT") or s.startswith("Kind:") or s.startswith("Language:"):
            continue
        if s.isdigit() or "-->" in s:
            continue
        # Strip VTT inline tags like <00:00:01.200><c>
        cleaned = re.sub(r"<[^>]+>", "", s)
        cleaned = html.unescape(cleaned).strip()
        if cleaned and (not cur_lines or cur_lines[-1] != cleaned):
            cur_lines.append(cleaned)

    if cur_lines:
        txt = " ".join(cur_lines).strip()
        if txt and (not segments or segments[-1]["text"] != txt):
            segments.append({"start": cur_start, "end": cur_end, "text": txt})

    return segments


def download_youtube_transcript_en(youtube_url: str, work_dir: Path) -> dict:
    """Download English subtitles via yt-dlp and return {title, duration_sec, segments}."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(work_dir / "yt_sub")
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-sub",
        "--write-auto-sub",
        "--sub-lang", "en.*,en",
        "--sub-format", "vtt",
        "--print-json",
        "-o", out_tmpl,
        youtube_url,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    title = "YouTube Video"
    duration_sec = 180.0
    if res.stdout:
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    info = json.loads(line)
                    title = info.get("title") or title
                    duration_sec = float(info.get("duration") or duration_sec)
                    break
                except Exception:
                    pass

    vtt_files = sorted(work_dir.glob("yt_sub*.vtt"))
    if not vtt_files:
        raise RuntimeError(
            f"Could not extract English subtitles from YouTube URL: {youtube_url}. "
            f"stderr: {(res.stderr or '')[:300]}"
        )
    segments = _parse_vtt_to_segments(vtt_files[0])
    if not segments:
        raise RuntimeError("Extracted subtitle file is empty.")
    return {
        "title": title,
        "duration_sec": duration_sec,
        "segments": segments,
    }
