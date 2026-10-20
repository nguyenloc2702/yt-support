"""
Slow-Paced Full Audio Narration for 'The ENTIRE History of Microsoft in 21 Minutes' (Version 2 - Witty Edition).
Uses a calm, deliberate documentary pacing (male-narrator) with natural breathing pauses between thoughts and chapters.
No artificial time limits, no tempo compression.
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
logger = logging.getLogger("slow_microsoft_audio")

from config.settings import settings
from services.settings_service import get_llm_api_key

SCRIPT_TXT = Path("data/history_of_microsoft/transcript_english_v2_witty.txt")
OUTPUT_DIR_DRIVE = Path("G:/My Drive/Video_Projects/downloads/history_of_microsoft")
OUTPUT_DIR_LOCAL = Path("data/history_of_microsoft")
TEMP_DIR = Path("temp_microsoft_slow_v2")

VIDEO_FILE = OUTPUT_DIR_DRIVE / "The ENTIRE History of Microsoft in 21 Minutes.mp4"

PAUSE_BETWEEN_CUES = 0.8       # Natural pause between paragraphs
PAUSE_BETWEEN_CHAPTERS = 2.0   # Generous pause between major chapter transitions


def parse_script_with_chapters(txt_path: Path):
    text = txt_path.read_text(encoding="utf-8")
    sections = re.split(r'={10,}\nCHAPTER\s+(\d+):\s+([^\n\[]+)\s*\[\d{2}:\d{2}\]\n={10,}', text)
    all_cues = []
    cue_idx = 1

    for i in range(1, len(sections), 3):
        ch_num = int(sections[i])
        ch_title = sections[i + 1].strip()
        ch_body = sections[i + 2]
        cues_in_chapter = re.findall(r'\[(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})\]\s*\n(.*?)(?=\n\[|\Z)', ch_body, re.DOTALL)

        for j, (m1, s1, m2, s2, content) in enumerate(cues_in_chapter):
            clean_text = " ".join(content.strip().split())
            is_chapter_first = (j == 0)
            all_cues.append({
                "id": f"cue_{cue_idx:02d}",
                "index": cue_idx,
                "chapter_num": ch_num,
                "chapter_title": ch_title,
                "is_chapter_first": is_chapter_first,
                "text": clean_text
            })
            cue_idx += 1

    return all_cues


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
            with urllib.request.urlopen(req, timeout=35) as resp:
                audio = resp.read()
                out_path.write_bytes(audio)
                return
        except Exception as e:
            logger.warning("Attempt %d failed for cue: %s", attempt, e)
            if attempt < max_retries:
                time.sleep(2.0 * attempt)
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


def convert_to_clean_wav(in_mp3: Path, out_wav: Path):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_mp3),
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(out_wav)
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Synthesize slow-paced natural documentary audio")
    parser.add_argument("--voice", default="male-narrator", help="Voice to use (default: male-narrator)")
    args = parser.parse_args()

    api_key = get_llm_api_key()
    if not api_key:
        raise ValueError("No LLM API key found in settings or environment!")

    logger.info("=== Starting Slow-Paced Audio Generation (Voice: %s) ===", args.voice)

    cues = parse_script_with_chapters(SCRIPT_TXT)
    logger.info("Parsed %d cues across 16 chapters from %s", len(cues), SCRIPT_TXT)

    voice_dir = TEMP_DIR / args.voice
    raw_dir = voice_dir / "raw"
    wav_dir = voice_dir / "wav"
    raw_dir.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)

    # 1. Synthesize all cues at 100% natural speed
    logger.info("Synthesizing cues at natural, relaxed pace...")
    for i, cue in enumerate(cues):
        raw_mp3 = raw_dir / f"{cue['id']}.mp3"
        if not raw_mp3.exists() or raw_mp3.stat().st_size == 0:
            logger.info("[%02d/%02d] Ch.%02d: %s | '%s...'", i + 1, len(cues), cue['chapter_num'], cue['id'], cue['text'][:45])
            synthesize_cue(cue['text'], args.voice, api_key, raw_mp3)
            time.sleep(0.3)
        else:
            logger.debug("Cached %s", raw_mp3.name)

    # 2. Build audio sequence with natural breathers
    concat_list = []
    total_speech_dur = 0.0

    for i, cue in enumerate(cues):
        raw_mp3 = raw_dir / f"{cue['id']}.mp3"
        cue_wav = wav_dir / f"{cue['id']}.wav"
        convert_to_clean_wav(raw_mp3, cue_wav)
        cue_dur = get_audio_duration(cue_wav)
        total_speech_dur += cue_dur

        # Insert pause before chapter (except the very first cue)
        if cue['is_chapter_first'] and i > 0:
            sil_chap = wav_dir / f"sil_chapter_{cue['chapter_num']}.wav"
            generate_silence(PAUSE_BETWEEN_CHAPTERS, sil_chap)
            concat_list.append(sil_chap)
        elif i > 0:
            # Normal paragraph pause
            sil_cue = wav_dir / f"sil_cue_{cue['id']}.wav"
            generate_silence(PAUSE_BETWEEN_CUES, sil_cue)
            concat_list.append(sil_cue)

        concat_list.append(cue_wav)

    logger.info("Total pure speech duration: %.2fs (%.2f mins)", total_speech_dur, total_speech_dur / 60.0)

    # 3. Concatenate all segments
    concat_txt = voice_dir / "concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in concat_list:
            clean_path = str(p.resolve()).replace("\\", "/")
            f.write(f"file '{clean_path}'\n")

    master_wav = voice_dir / "master_narration_slow.wav"
    logger.info("Concatenating %d audio + silence segments into master WAV...", len(concat_list))
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
    m = int(master_dur // 60)
    s = int(master_dur % 60)
    logger.info("Master slow-paced WAV assembled: %02d:%02d (%.2f seconds)", m, s, master_dur)

    # 4. Loudness Normalization to MP3
    out_files = [
        OUTPUT_DIR_LOCAL / "full_audio_microsoft_english_v2_slow_paced.mp3",
        OUTPUT_DIR_DRIVE / "full_audio_microsoft_english_v2_slow_paced.mp3",
        OUTPUT_DIR_LOCAL / "full_audio_microsoft_english_v2.mp3",
        OUTPUT_DIR_DRIVE / "full_audio_microsoft_english_v2.mp3"
    ]

    primary_local = out_files[0]
    logger.info("Applying broadcast loudnorm (-16 LUFS) to %s...", primary_local.name)
    cmd_encode = [
        "ffmpeg", "-y",
        "-i", str(master_wav),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=10",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(primary_local)
    ]
    subprocess.run(cmd_encode, check=True, capture_output=True)

    # Distribute copies
    OUTPUT_DIR_DRIVE.mkdir(parents=True, exist_ok=True)
    for dst in out_files[1:]:
        shutil.copy2(primary_local, dst)
        logger.info("Copied -> %s (%.2f MB)", dst, dst.stat().st_size / (1024 * 1024))

    # 5. Create 90s preview video with slow narration
    if VIDEO_FILE.exists():
        preview_mp4 = OUTPUT_DIR_DRIVE / "preview_slow_paced_v2_90s.mp4"
        logger.info("Generating 90-second dubbed preview video -> %s", preview_mp4.name)
        cmd_preview = [
            "ffmpeg", "-y",
            "-i", str(VIDEO_FILE),
            "-i", str(primary_local),
            "-filter_complex",
            "[0:v]trim=0:90,setpts=PTS-STARTPTS[v0];"
            "[0:a]atrim=0:90,asetpts=PTS-STARTPTS,volume=0.15[a_orig];"
            "[1:a]atrim=0:90,asetpts=PTS-STARTPTS,volume=1.25[a_nar];"
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
        local_preview = OUTPUT_DIR_LOCAL / "preview_slow_paced_v2_90s.mp4"
        shutil.copy2(preview_mp4, local_preview)
        logger.info("Preview video generated: %s (%.2f MB)", preview_mp4, preview_mp4.stat().st_size / (1024 * 1024))

    logger.info("=== SLOW-PACED AUDIO BUILD COMPLETE ===")
    logger.info("Duration: %02d:%02d", m, s)


if __name__ == "__main__":
    main()
