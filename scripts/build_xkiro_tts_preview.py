"""
Generate TTS audio samples using xKiro TTS API and create dubbed video preview.
"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Force UTF-8 stdout
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("xkiro_tts")

from config.settings import settings
from services.settings_service import get_llm_api_key

PROJECT_ID = "0f1639d2"
PROJ_DIR = settings.projects_dir / PROJECT_ID
EXPORTS_DIR = PROJ_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR = Path("temp_xkiro_tts")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

CUES = [
    {
        "id": "cue_01",
        "output_start": 0.0,
        "max_duration": 3.0,
        "text": "Chào mừng các bạn đến với Đảo Rắn — vùng đất tử thần giữa đại ngàn..."
    },
    {
        "id": "cue_02",
        "output_start": 3.0,
        "max_duration": 3.1,
        "text": "...nơi cư ngụ của hàng ngàn loài rắn kịch độc bậc nhất hành tinh."
    },
    {
        "id": "cue_03",
        "output_start": 6.2,
        "max_duration": 4.7,
        "text": "Và đây: Hổ Mang Chúa. Chỉ một vết cắn duy nhất... bạn cầm chắc cái chết."
    },
    {
        "id": "cue_04",
        "output_start": 11.0,
        "max_duration": 5.0,
        "text": "Nơi này hoàn toàn không có huyết thanh cứu mạng. Và trong ba ngày tới..."
    },
    {
        "id": "cue_05",
        "output_start": 16.3,
        "max_duration": 7.0,
        "text": "...chúng tôi sẽ thâm nhập vào tận trung tâm hòn đảo, nơi những sát thủ săn mồi đang ẩn nấp ngay trên đầu."
    },
    {
        "id": "cue_06",
        "output_start": 26.5,
        "max_duration": 4.5,
        "text": "Hãy nhìn kỹ tán lá phía trước! Một con rắn đuôi chuông đang ngụy trang hoàn hảo."
    },
    {
        "id": "cue_07",
        "output_start": 31.5,
        "max_duration": 7.5,
        "text": "Nọc độc của chúng chứa độc tố phá hủy tế bào cực mạnh, có thể khiến nạn nhân hoại tử chỉ sau vài giờ."
    },
    {
        "id": "cue_08",
        "output_start": 43.5,
        "max_duration": 7.5,
        "text": "Địa hình rừng nhiệt đới ẩm ướt khiến mỗi bước chân đều trở thành một ván cược sinh tử."
    },
    {
        "id": "cue_09",
        "output_start": 51.5,
        "max_duration": 8.5,
        "text": "Dưới những gốc cây mục nát này, loài rắn Viper đầu giáo vàng đang chờ đợi con mồi sơ hở."
    },
    {
        "id": "cue_10",
        "output_start": 60.5,
        "max_duration": 11.5,
        "text": "Liệu đội thám hiểm có thể sống sót rời khỏi Đảo Rắn? Hãy theo dõi trọn vẹn hành trình nghẹt thở này ngay sau đây!"
    }
]


def synthesize_xkiro(text: str, voice: str, api_key: str, out_path: Path) -> float:
    """Call xKiro TTS API and save audio file, returning measured duration."""
    url = "https://api.xkiro.com/v1/audio/speech"
    payload = {
        "model": "xkiro-voice",
        "voice": voice,
        "input": text
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
    )
    with urllib.request.urlopen(req) as resp:
        audio_bytes = resp.read()
        out_path.write_bytes(audio_bytes)

    # Probe duration
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(out_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def build_track_for_voice(voice_id: str, voice_name_label: str, api_key: str, total_duration: float = 75.0) -> Path:
    logger.info("=== Synthesizing for voice: %s (%s) ===", voice_id, voice_name_label)
    voice_dir = TEMP_DIR / voice_id
    voice_dir.mkdir(parents=True, exist_ok=True)

    filter_inputs = []
    filter_parts = []
    input_idx = 0

    for i, cue in enumerate(CUES):
        cue_file = voice_dir / f"{cue['id']}.mp3"
        if not cue_file.exists() or cue_file.stat().st_size == 0:
            dur = synthesize_xkiro(cue["text"], voice_id, api_key, cue_file)
            time.sleep(0.3)  # Gentle rate limiting
        else:
            # Probe existing
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(cue_file)]
            dur = float(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())

        logger.info("Cue %02d [%.1fs]: dur=%.2fs | text='%s'", i + 1, cue["output_start"], dur, cue["text"][:35])

        # Delay in milliseconds
        delay_ms = int(round(cue["output_start"] * 1000))
        filter_inputs.extend(["-i", str(cue_file)])
        filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[a{input_idx}]")
        input_idx += 1

    # Merge all delayed streams with amix
    mix_inputs = "".join(f"[a{k}]" for k in range(input_idx))
    filter_complex = f"{';'.join(filter_parts)};{mix_inputs}amix=inputs={input_idx}:duration=longest:dropout_transition=0,apad=whole_dur={total_duration},atrim=0:{total_duration}[outa]"

    out_mp3 = EXPORTS_DIR / f"sample_tts_xkiro_{voice_name_label}.mp3"
    cmd = ["ffmpeg", "-y"] + filter_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outa]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(out_mp3)
    ]
    logger.info("Building full narration track with ffmpeg: %s", out_mp3.name)
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Narration track generated: %s (%.1f MB)", out_mp3, out_mp3.stat().st_size / (1024 * 1024))
    return out_mp3


def build_dubbed_preview(narration_mp3: Path, output_mp4: Path, preview_video: Path, duration: float = 75.0):
    """Mux the first 75s of preview_video with ducked original audio and narration audio."""
    logger.info("Building dubbed video preview -> %s", output_mp4.name)
    # [0:a] is original video audio ducked by -14dB (volume=0.20)
    # [1:a] is narration audio at volume=1.15
    filter_complex = (
        f"[0:v]trim=0:{duration},setpts=PTS-STARTPTS[v0];"
        f"[0:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=0.20[a_bg];"
        f"[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=1.15[a_nar];"
        f"[a_bg][a_nar]amix=inputs=2:duration=first:dropout_transition=0[a_out]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(preview_video),
        "-i", str(narration_mp3),
        "-filter_complex", filter_complex,
        "-map", "[v0]",
        "-map", "[a_out]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_mp4)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Dubbed video generated: %s (%.1f MB)", output_mp4, output_mp4.stat().st_size / (1024 * 1024))


def main():
    api_key = get_llm_api_key()
    if not api_key:
        logger.error("No xKiro API key configured!")
        return

    # 1. Build narration track for confident-male-vietnamese
    track_male = build_track_for_voice("confident-male-vietnamese", "confident_male", api_key)

    # 2. Build narration track for sweet-female-vietnamese
    track_female = build_track_for_voice("sweet-female-vietnamese", "sweet_female", api_key)

    # 3. Create dubbed video with confident male voice
    preview_source = PROJ_DIR / "previews" / "preview_v10.mp4"
    if preview_source.exists():
        dubbed_mp4 = EXPORTS_DIR / "sample_video_dubbed_xkiro.mp4"
        build_dubbed_preview(track_male, dubbed_mp4, preview_source, duration=75.0)

    print("\n=== ALL EXPORTS COMPLETED SUCCESSFULLY ===")
    print(f"1. Male TTS MP3:   {track_male}")
    print(f"2. Female TTS MP3: {track_female}")
    if preview_source.exists():
        print(f"3. Dubbed MP4:     {dubbed_mp4}")


if __name__ == "__main__":
    main()
