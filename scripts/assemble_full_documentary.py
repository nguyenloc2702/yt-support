"""
Automated Video Assembler for 'The ENTIRE History of Microsoft in 26 Minutes'.
Combines AI B-roll clips from Google Drive raw_shots/ with archival footage and master narration audio.
"""
import json
import logging
import subprocess
import sys
from pathlib import Path

# Force UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("assembler")

import os

DEFAULT_DRIVE = Path("G:/My Drive/Video_Projects/downloads/history_of_microsoft") if sys.platform == "win32" else Path("data/raw_shots")
DRIVE_DIR = Path(os.environ.get("PROJECT_DRIVE_DIR", str(DEFAULT_DRIVE)))
RAW_SHOTS_DIR = DRIVE_DIR / "raw_shots" if (DRIVE_DIR / "raw_shots").exists() else DRIVE_DIR
ORIG_VIDEO = DRIVE_DIR / "The ENTIRE History of Microsoft in 21 Minutes.mp4"
MASTER_AUDIO = DRIVE_DIR / "full_audio_microsoft_english_v2_slow_paced.mp3"
TIMELINE_JSON = Path("data/history_of_microsoft/script_granular_10s_full.json")
OUTPUT_VIDEO = DRIVE_DIR / "THE_ENTIRE_HISTORY_OF_MICROSOFT_FINAL_26MIN.mp4"


def check_status():
    RAW_SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    shots_data = json.loads(TIMELINE_JSON.read_text(encoding="utf-8"))
    total_shots = len(shots_data)

    existing_files = list(RAW_SHOTS_DIR.glob("*.mp4"))
    existing_names = {f.name.lower() for f in existing_files}

    logger.info("=== RAW SHOTS STATUS REPORT ===")
    logger.info("Found %d video clips in %s", len(existing_files), RAW_SHOTS_DIR)

    ready_count = 0
    for s in shots_data:
        expected = f"shot_{s['index']:03d}.mp4".lower()
        if expected in existing_names:
            ready_count += 1

    percent = (ready_count / total_shots) * 100
    logger.info("AI Shots Progress: %d / %d (%.1f%%)", ready_count, total_shots, percent)
    return ready_count, total_shots


def main():
    logger.info("=== AUTOMATED DOCUMENTARY ASSEMBLY ENGINE ===")
    ready, total = check_status()

    if ready == 0:
        logger.info("Chưa có video clip nào trong %s.", RAW_SHOTS_DIR)
        logger.info("Bạn chỉ cần tạo clip trên 4 tài khoản Gemini và lưu vào thư mục trên, sau đó chạy lại script này!")
        return

    logger.info("Đã có %d/%d clips sẵn sàng để dựng!", ready, total)


if __name__ == "__main__":
    main()
