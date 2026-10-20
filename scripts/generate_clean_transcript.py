"""
Generate clean, natural transcript with exact second-by-second timestamps from YouTube subtitles.
Exports to SRT, TXT, and JSON.
"""
import json
import re
import sys
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SUB_FILE = Path("G:/My Drive/Video_Projects/downloads/history_of_microsoft/The ENTIRE History of Microsoft in 21 Minutes.en.json3")
OUT_DIR = SUB_FILE.parent

CHAPTERS = [
    (0, "The Invisible Giant"),
    (169, "The Two Friends Who Saw It Coming"),
    (359, "The Open Letter to Hobbyists"),
    (399, "IBM Calls: The Deal That Built Microsoft"),
    (465, "The $75,000 Operating System: MS-DOS"),
    (545, "The Microsoft Success Story: Owning the Standard"),
    (603, "Windows, the IPO and the Apple Lawsuit"),
    (673, "Windows 95 and the Midnight Lines"),
    (731, "Internet Explorer and the Antitrust Trial"),
    (783, "Saving Apple and Clippy"),
    (829, "The Ballmer Years: Xbox, iPhone and Nokia"),
    (926, "Satya Nadella's Turnaround and the OpenAI Bet"),
    (1019, "Microsoft vs Amazon: The Cloud War"),
    (1038, "Microsoft in 2026: Azure, Copilot and the AI Bill"),
    (1129, "Why It Matters: AI as the New Floor"),
    (1187, "Back to Your Laptop"),
]

def format_time_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def format_time_min_sec(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def main():
    with open(SUB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", [])
    raw_cues = []

    for ev in events:
        start = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs", 0) / 1000.0
        segs = ev.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs)
        # clean line breaks and multiple spaces
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            raw_cues.append({
                "start": start,
                "end": start + dur,
                "text": text
            })

    # Group fragmented cues into coherent sentences
    merged_cues = []
    curr_start = None
    curr_end = None
    curr_words = []

    for cue in raw_cues:
        if curr_start is None:
            curr_start = cue["start"]
        curr_end = cue["end"]
        curr_words.append(cue["text"])

        # Check if text ends a sentence
        full_text = " ".join(curr_words)
        if re.search(r"[.!?]$", cue["text"]) or (cue["end"] - curr_start > 5.0 and len(curr_words) >= 8):
            merged_cues.append({
                "start": curr_start,
                "end": curr_end,
                "text": full_text
            })
            curr_start = None
            curr_words = []

    if curr_words:
        merged_cues.append({
            "start": curr_start,
            "end": curr_end,
            "text": " ".join(curr_words)
        })

    # 1. Export SRT
    srt_file = OUT_DIR / "transcript_timeline.srt"
    with open(srt_file, "w", encoding="utf-8") as f:
        for idx, c in enumerate(merged_cues, 1):
            f.write(f"{idx}\n")
            f.write(f"{format_time_srt(c['start'])} --> {format_time_srt(c['end'])}\n")
            f.write(f"{c['text']}\n\n")

    # 2. Export TXT with Chapter Headers
    txt_file = OUT_DIR / "transcript_by_chapter.txt"
    chapter_idx = 0
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write("=== THE ENTIRE HISTORY OF MICROSOFT IN 21 MINUTES ===\n")
        f.write("Full Script with exact timestamps and chapters\n\n")

        for c in merged_cues:
            if chapter_idx == 0 and c == merged_cues[0]:
                f.write(f"{'='*60}\n")
                f.write(f"CHAPTER 1: {CHAPTERS[0][1].upper()} [{format_time_min_sec(CHAPTERS[0][0])}]\n")
                f.write(f"{'='*60}\n\n")

            while chapter_idx < len(CHAPTERS) - 1 and c["start"] >= CHAPTERS[chapter_idx + 1][0]:
                chapter_idx += 1
                f.write(f"\n{'='*60}\n")
                f.write(f"CHAPTER {chapter_idx + 1}: {CHAPTERS[chapter_idx][1].upper()} [{format_time_min_sec(CHAPTERS[chapter_idx][0])}]\n")
                f.write(f"{'='*60}\n\n")

            time_str = f"[{format_time_min_sec(c['start'])} - {format_time_min_sec(c['end'])}]"
            f.write(f"{time_str} {c['text']}\n")

    # 3. Export JSON
    json_file = OUT_DIR / "transcript_timeline.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(merged_cues, f, ensure_ascii=False, indent=2)

    print(f"Total merged sentences: {len(merged_cues)}")
    print(f"Saved SRT:  {srt_file}")
    print(f"Saved TXT:  {txt_file}")
    print(f"Saved JSON: {json_file}")

if __name__ == "__main__":
    main()
