"""
Download YouTube video and extract audio track.
"""
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from config.settings import settings

OUT_DIR = settings.downloads_dir / "history_of_microsoft"
OUT_DIR.mkdir(parents=True, exist_ok=True)
URL = "https://www.youtube.com/watch?v=8oGSERdqoUI"

video_template = str(OUT_DIR / "%(title)s.%(ext)s")
audio_template = str(OUT_DIR / "%(title)s_audio.%(ext)s")

print(f"Target directory: {OUT_DIR.resolve()}")

# 1. Download audio as MP3
print("\n--- 1. Downloading MP3 audio ---")
cmd_audio = [
    "yt-dlp",
    "-x",
    "--audio-format", "mp3",
    "--audio-quality", "0",
    "-o", audio_template,
    "--no-playlist",
    URL
]
res_audio = subprocess.run(cmd_audio, check=True)

# 2. Download video as MP4 (1080p/720p with aac/mp4 container)
print("\n--- 2. Downloading MP4 video ---")
cmd_video = [
    "yt-dlp",
    "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/bestvideo[height<=1080]+bestaudio/best",
    "--merge-output-format", "mp4",
    "-o", video_template,
    "--no-playlist",
    URL
]
res_video = subprocess.run(cmd_video, check=True)

print("\n--- 3. Verifying downloaded files ---")
for f in OUT_DIR.iterdir():
    if f.is_file():
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"File: {f.name} ({size_mb:.2f} MB)")
