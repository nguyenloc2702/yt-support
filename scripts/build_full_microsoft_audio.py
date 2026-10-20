"""
Full Audio Synthesis and Assembly for 'The ENTIRE History of Microsoft in 21 Minutes' (Version 2 - Witty Edition).
Synthesizes all 61 cues using xKiro TTS API and creates a continuous 20m59s narration track.
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# Force UTF-8 encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("full_microsoft_audio")

from config.settings import settings
from services.settings_service import get_llm_api_key

SCRIPT_TXT = Path("data/history_of_microsoft/transcript_english_v2_witty.txt")
CUES_JSON = Path("data/history_of_microsoft/cues_english_v2_witty.json")
OUTPUT_DIR_DRIVE = Path("G:/My Drive/Video_Projects/downloads/history_of_microsoft")
OUTPUT_DIR_LOCAL = Path("data/history_of_microsoft")
TEMP_DIR = Path("temp_microsoft_v2")

VIDEO_FILE = OUTPUT_DIR_DRIVE / "The ENTIRE History of Microsoft in 21 Minutes.mp4"
TOTAL_VIDEO_DURATION = 1259.0  # 20 minutes 59 seconds


def parse_script_cues(txt_path: Path):
    text = txt_path.read_text(encoding="utf-8")
    pattern = r'\[(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\]\s*\n(.*?)(?=\n\[|\n===|\Z)'
    matches = re.findall(pattern, text, re.DOTALL)
    cues = []
    for i, (m1, s1, m2, s2, content) in enumerate(matches):
        t1 = int(m1) * 60 + int(s1)
        t2 = int(m2) * 60 + int(s2)
        clean_text = " ".join(content.strip().split())
        cues.append({
            "id": f"cue_{i+1:02d}",
            "index": i + 1,
            "start": float(t1),
            "end": float(t2),
            "allocated_window": float(t2 - t1),
            "text": clean_text
        })
    return cues


def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def synthesize_cue(text: str, voice: str, api_key: str, out_path: Path, max_retries: int = 3):
    url = "https://api.xkiro.com/v1/audio/speech"
    payload = {
        "model": "xkiro-voice",
        "voice": voice,
        "input": text
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
    )

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio = resp.read()
                out_path.write_bytes(audio)
                return
        except Exception as e:
            logger.warning("Attempt %d failed for cue: %s", attempt, e)
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
            else:
                raise


def generate_silence(duration: float, out_path: Path):
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", f"{duration:.3f}",
        "-c:a", "pcm_s16le",
        str(out_path)
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def convert_and_retimed_wav(raw_mp3: Path, out_wav: Path, max_allowed_duration: float):
    raw_dur = get_audio_duration(raw_mp3)
    if raw_dur > max_allowed_duration and max_allowed_duration > 1.0:
        tempo = min(1.30, raw_dur / (max_allowed_duration - 0.15))
        logger.info("Retiming %s: raw %.2fs > max %.2fs (atempo=%.2f)", raw_mp3.name, raw_dur, max_allowed_duration, tempo)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_mp3),
            "-filter:a", f"atempo={tempo:.2f}",
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(out_wav)
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_mp3),
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(out_wav)
        ]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Synthesize full English Version 2 Witty Audio")
    parser.add_argument("--voice", default="tech-blogger", help="xKiro voice ID (e.g. tech-blogger, male-narrator, boman-h)")
    args = parser.parse_args()

    api_key = get_llm_api_key()
    if not api_key:
        raise ValueError("No LLM API key found in settings or environment!")

    logger.info("=== Starting Full Audio Generation for Microsoft History (Version 2 Witty) ===")
    logger.info("Voice: %s", args.voice)

    # 1. Parse Cues
    cues = parse_script_cues(SCRIPT_TXT)
    logger.info("Parsed %d cues from %s", len(cues), SCRIPT_TXT)
    CUES_JSON.write_text(json.dumps(cues, indent=2, ensure_ascii=False), encoding="utf-8")

    voice_dir = TEMP_DIR / args.voice
    raw_dir = voice_dir / "raw"
    wav_dir = voice_dir / "wav"
    raw_dir.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)

    # 2. Synthesize each cue
    logger.info("Synthesizing %d cues via xKiro TTS...", len(cues))
    for i, cue in enumerate(cues):
        raw_mp3 = raw_dir / f"{cue['id']}.mp3"
        if not raw_mp3.exists() or raw_mp3.stat().st_size == 0:
            logger.info("[%02d/%02d] Synthesizing %s [%.0fs]: '%s...'", i + 1, len(cues), cue['id'], cue['start'], cue['text'][:40])
            synthesize_cue(cue['text'], args.voice, api_key, raw_mp3)
            time.sleep(0.25)
        else:
            logger.debug("Reusing cached %s", raw_mp3.name)

    # 3. Retime and generate exact timeline
    concat_list = []
    current_timeline_pos = 0.0

    for i, cue in enumerate(cues):
        raw_mp3 = raw_dir / f"{cue['id']}.mp3"
        target_start = cue['start']
        next_target_start = cues[i + 1]['start'] if i + 1 < len(cues) else TOTAL_VIDEO_DURATION

        # Gap before this cue starts
        pre_gap = target_start - current_timeline_pos
        if pre_gap > 0.05:
            sil_wav = wav_dir / f"silence_before_{cue['id']}.wav"
            generate_silence(pre_gap, sil_wav)
            concat_list.append(sil_wav)
            current_timeline_pos += pre_gap

        # Max duration cue is allowed to occupy before next cue begins
        max_allowed = max(2.0, next_target_start - target_start - 0.2)
        cue_wav = wav_dir / f"norm_{cue['id']}.wav"
        convert_and_retimed_wav(raw_mp3, cue_wav, max_allowed)
        dur = get_audio_duration(cue_wav)
        concat_list.append(cue_wav)
        current_timeline_pos += dur

    # Trailing silence to reach exact TOTAL_VIDEO_DURATION
    if current_timeline_pos < TOTAL_VIDEO_DURATION:
        post_gap = TOTAL_VIDEO_DURATION - current_timeline_pos
        sil_end = wav_dir / "silence_end.wav"
        generate_silence(post_gap, sil_end)
        concat_list.append(sil_end)
        current_timeline_pos += post_gap

    # 4. Write concat file
    concat_txt = voice_dir / "concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in concat_list:
            clean_path = str(p.resolve()).replace("\\", "/")
            f.write(f"file '{clean_path}'\n")

    # 5. Assemble master narration track
    master_wav = voice_dir / "master_narration.wav"
    logger.info("Concatenating %d audio segments into master WAV...", len(concat_list))
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_txt),
        "-c:a", "pcm_s16le",
        str(master_wav)
    ]
    subprocess.run(cmd_concat, check=True, capture_output=True)

    master_dur = get_audio_duration(master_wav)
    logger.info("Master narration WAV created: %.2fs (target: %.2fs)", master_dur, TOTAL_VIDEO_DURATION)

    # 6. Encode to broadcast-standard 192k MP3 with loudness normalization
    local_mp3 = OUTPUT_DIR_LOCAL / "full_audio_microsoft_english_v2.mp3"
    drive_mp3 = OUTPUT_DIR_DRIVE / "full_audio_microsoft_english_v2.mp3"

    logger.info("Encoding master MP3 with loudnorm to %s...", local_mp3)
    cmd_encode = [
        "ffmpeg", "-y",
        "-i", str(master_wav),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=10",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(local_mp3)
    ]
    subprocess.run(cmd_encode, check=True, capture_output=True)

    # Copy to Google Drive
    OUTPUT_DIR_DRIVE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_mp3, drive_mp3)
    logger.info("Copied to Google Drive: %s (%.2f MB)", drive_mp3, drive_mp3.stat().st_size / (1024 * 1024))

    # 7. Create a 90-second dubbed preview video for instant playback verification
    preview_mp4 = OUTPUT_DIR_DRIVE / "preview_witty_v2_90s.mp4"
    if VIDEO_FILE.exists():
        logger.info("Generating 90-second dubbed preview video -> %s", preview_mp4.name)
        cmd_preview = [
            "ffmpeg", "-y",
            "-i", str(VIDEO_FILE),
            "-i", str(local_mp3),
            "-filter_complex",
            "[0:v]trim=0:90,setpts=PTS-STARTPTS[v0];"
            "[0:a]atrim=0:90,asetpts=PTS-STARTPTS,volume=0.18[a_orig];"
            "[1:a]atrim=0:90,asetpts=PTS-STARTPTS,volume=1.20[a_nar];"
            "[a_orig][a_nar]amix=inputs=2:duration=first:dropout_transition=0[a_out]",
            "-map", "[v0]",
            "-map", "[a_out]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            str(preview_mp4)
        ]
        subprocess.run(cmd_preview, check=True, capture_output=True)
        local_preview = OUTPUT_DIR_LOCAL / "preview_witty_v2_90s.mp4"
        shutil.copy2(preview_mp4, local_preview)
        logger.info("Preview video generated: %s (%.2f MB)", preview_mp4, preview_mp4.stat().st_size / (1024 * 1024))

    logger.info("=== FULL AUDIO EXPORT COMPLETE ===")
    logger.info("Local Audio: %s", local_mp3)
    logger.info("Drive Audio: %s", drive_mp3)


if __name__ == "__main__":
    main()
