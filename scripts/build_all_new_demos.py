"""
Generate high-quality remastered TTS audio tracks and dubbed video previews for xKiro voices.
Features:
- Perfectly paced cues with zero overlap and cinematic breathing room.
- Professional audio mixing with broadcast-standard loudness (loudnorm -14 LUFS, amix normalize=0).
- Multiple voice options: confident-male, sweet-female, gentle-female (VIP).
"""
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("xkiro_remaster")

from config.settings import settings
from services.settings_service import get_llm_api_key

PROJECT_ID = "0f1639d2"
PROJ_DIR = settings.projects_dir / PROJECT_ID
EXPORTS_DIR = PROJ_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path("temp_xkiro_tts_v2")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 10 cinematic cues with natural breathing pauses matching the video cuts
CUES_V2 = [
    {
        "id": "cue_01",
        "output_start": 0.0,
        "text": "Chào mừng các bạn đến với Đảo Rắn — vùng đất tử thần giữa đại dương..."
    },
    {
        "id": "cue_02",
        "output_start": 4.0,
        "text": "...nơi ngự trị của hàng vạn sinh vật kịch độc bậc nhất hành tinh."
    },
    {
        "id": "cue_03",
        "output_start": 8.0,
        "text": "Và đây: Hổ Mang Chúa. Chỉ cần một cú đớp duy nhất... bạn cầm chắc cái chết."
    },
    {
        "id": "cue_04",
        "output_start": 13.0,
        "text": "Nơi này hoàn toàn không có huyết thanh cứu mạng. Và trong ba ngày tới..."
    },
    {
        "id": "cue_05",
        "output_start": 17.5,
        "text": "...đoàn thám hiểm sẽ tiến sâu vào rừng rậm, đối mặt với những sát thủ ẩn mình."
    },
    # 22.5s - 26.5s: Nhường âm thanh hiện trường "Oh my god!" và tiếng động rừng hoang dã
    {
        "id": "cue_06",
        "output_start": 26.8,
        "text": "Hãy nhìn kỹ nhánh cây phía trước! Một con rắn đuôi chuông đang ngụy trang hoàn hảo."
    },
    {
        "id": "cue_07",
        "output_start": 32.0,
        "text": "Nọc độc cực mạnh có thể phá hủy tế bào và gây hoại tử chỉ sau ít giờ."
    },
    # 37.5s - 43.5s: Cận cảnh lội suối, tiếng bước chân và thở dốc
    {
        "id": "cue_08",
        "output_start": 43.8,
        "text": "Địa hình trơn trượt khiến mỗi bước chân đều trở thành một ván cược sinh tử."
    },
    {
        "id": "cue_09",
        "output_start": 51.0,
        "text": "Dưới những hốc đá mục nát này, loài rắn đầu giáo vàng đang rình rập con mồi."
    },
    {
        "id": "cue_10",
        "output_start": 59.0,
        "text": "Liệu đoàn thám hiểm có thể an toàn trở về? Hãy theo dõi trọn vẹn hành trình nghẹt thở này ngay sau đây!"
    }
]


def synthesize_xkiro(text: str, voice: str, api_key: str, out_path: Path) -> float:
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
        out_path.write_bytes(resp.read())

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(out_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def build_voice_narration_track(voice_id: str, label: str, api_key: str, total_duration: float = 75.0) -> Path:
    logger.info("=== Synthesizing track for: %s (%s) ===", voice_id, label)
    v_dir = CACHE_DIR / voice_id
    v_dir.mkdir(parents=True, exist_ok=True)

    filter_inputs = []
    filter_parts = []
    input_idx = 0

    for i, cue in enumerate(CUES_V2):
        cue_file = v_dir / f"{cue['id']}.mp3"
        if not cue_file.exists() or cue_file.stat().st_size == 0:
            dur = synthesize_xkiro(cue["text"], voice_id, api_key, cue_file)
            time.sleep(0.2)
        else:
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(cue_file)]
            dur = float(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())

        logger.info("Cue %02d [%.1fs]: dur=%.2fs -> ends at %.2fs", i + 1, cue["output_start"], dur, cue["output_start"] + dur)

        delay_ms = int(round(cue["output_start"] * 1000))
        filter_inputs.extend(["-i", str(cue_file)])
        filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[a{input_idx}]")
        input_idx += 1

    mix_inputs = "".join(f"[a{k}]" for k in range(input_idx))
    # normalize=0 prevents amix from cutting individual cue loudness by 1/N
    filter_complex = f"{';'.join(filter_parts)};{mix_inputs}amix=inputs={input_idx}:normalize=0:dropout_transition=0,apad=whole_dur={total_duration},atrim=0:{total_duration}[outa]"

    out_mp3 = EXPORTS_DIR / f"demo_xkiro_{label}.mp3"
    cmd = ["ffmpeg", "-y"] + filter_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[outa]",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(out_mp3)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Created audio narration track: %s", out_mp3.name)
    return out_mp3


def render_dubbed_video(narration_mp3: Path, output_mp4: Path, preview_video: Path, duration: float = 75.0):
    logger.info("Rendering dubbed video -> %s", output_mp4.name)
    # Background video ducked to volume=0.18 (-15dB)
    # Narration volume at 1.40
    # normalize=0 so volume isn't divided by 2
    # loudnorm applied to output audio for punchy, broadcast-standard loudness (I=-14, TP=-1.5)
    filter_complex = (
        f"[0:v]trim=0:{duration},setpts=PTS-STARTPTS[v0];"
        f"[0:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=0.18[a_bg];"
        f"[1:a]atrim=0:{duration},asetpts=PTS-STARTPTS,volume=1.40[a_nar];"
        f"[a_bg][a_nar]amix=inputs=2:normalize=0:dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=9[a_out]"
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
    logger.info("Video finished: %s (%.1f MB)", output_mp4.name, output_mp4.stat().st_size / (1024 * 1024))


def main():
    api_key = get_llm_api_key()
    if not api_key:
        logger.error("No API key!")
        return

    preview_source = PROJ_DIR / "previews" / "preview_v10.mp4"
    if not preview_source.exists():
        logger.error("Preview video not found: %s", preview_source)
        return

    voices_to_render = [
        ("confident-male-vietnamese", "nam_tu_tin"),
        ("sweet-female-vietnamese", "nu_ngot_ngao"),
        ("gentle-female-vietnamese", "nu_dieu_dang_vip"),
    ]

    outputs = []
    for voice_id, label in voices_to_render:
        track = build_voice_narration_track(voice_id, label, api_key, 75.0)
        video_out = EXPORTS_DIR / f"demo_video_{label}.mp4"
        render_dubbed_video(track, video_out, preview_source, 75.0)
        outputs.append((voice_id, label, track, video_out))

    print("\n=== ALL DEMO RENDERS FINISHED ===")
    for v_id, label, audio_p, vid_p in outputs:
        print(f"[{label.upper()}] ({v_id})")
        print(f"  - Audio: {audio_p}")
        print(f"  - Video: {vid_p}")


if __name__ == "__main__":
    main()
