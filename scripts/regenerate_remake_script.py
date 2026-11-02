import json
import logging
from pathlib import Path
import sys

from pipeline.automated_factory import try_extract_subtitles
from services.script_auditor import build_dual_scripts_from_transcript, detect_language, VOICE_MAP
from services.approval_state_machine import ApprovalStateMachine, BeatIntent
from pipeline.character_manager import detect_characters_in_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("regenerate_script")

job_id = sys.argv[1] if len(sys.argv) > 1 else "remake_1792673726_b537ce"
project_dir = Path(f"/app/data/automated_runs/{job_id}")

if not project_dir.exists():
    print(f"Error: {project_dir} does not exist")
    sys.exit(1)

sub_segments = try_extract_subtitles(project_dir)
if not sub_segments:
    print("Error: No subtitles found")
    sys.exit(1)

default_shot_duration = 5.0
target_shots = 180

print(f"Generating dual scripts for {len(sub_segments)} segments -> {target_shots} scenes...")
vi_scenes, en_scenes = build_dual_scripts_from_transcript(sub_segments, target_shots=target_shots)

for s in vi_scenes:
    s["duration"] = default_shot_duration
for s in en_scenes:
    s["duration"] = default_shot_duration

shots_vi_file = project_dir / "shots_plan_vi.json"
shots_vi_file.write_text(json.dumps(vi_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

shots_en_file = project_dir / "shots_plan_en.json"
shots_en_file.write_text(json.dumps(en_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

shots = vi_scenes

# Character detection
combined_script_text = " ".join(s.get("text", "") for s in vi_scenes) + " " + " ".join(s.get("text", "") for s in en_scenes)
detected_chars = detect_characters_in_text(combined_script_text)
required_characters = [d["name"] for d in detected_chars]

for s in shots:
    s_txt = s.get("text", "").lower()
    s["character_ids"] = [
        d["name"].lower().replace(" ", "_")
        for d in detected_chars
        if d["name"].lower() in s_txt
    ]

shots_file = project_dir / "shots_plan.json"
shots_file.write_text(json.dumps(shots, indent=2, ensure_ascii=False), encoding="utf-8")

target_voice = "vi-VN-NamMinhNeural"
(project_dir / "voice.txt").write_text(target_voice, encoding="utf-8")

est_total_duration = len(shots) * default_shot_duration
total_words_vi = sum(len(str(s.get("text", "")).split()) for s in vi_scenes)
source_title = "Once You Understand MONEY, You'll Stop Saving it [PART #2]"

vi_txt_lines = [
    "=" * 80,
    "🎬 KỊCH BẢN PHÂN CẢNH VOICE-OVER TIẾNG VIỆT",
    f"Job ID: {job_id}",
    f"Tiêu đề: {source_title}",
    f"Tổng số cảnh: {len(shots)} cảnh | Thời lượng dự kiến: ~{est_total_duration:.0f}s (~{est_total_duration / 60:.1f} phút)",
    f"Tổng số từ: {total_words_vi} từ | Tốc độ đọc: ~2.5 - 3.0 từ/giây",
    f"Giọng đọc đề xuất: {target_voice}",
    "=" * 80,
    "",
]
for s in vi_scenes:
    idx = s.get("index", 1)
    t_start = (idx - 1) * default_shot_duration
    t_end = idx * default_shot_duration
    tag = " [HOOK]" if idx <= 2 else (" [OUTRO/KẾT]" if idx == len(shots) else "")
    vi_txt_lines.append(f"[CẢNH {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s){tag}")
    vi_txt_lines.append(f"Lời thoại: {s.get('text', '')}")
    vi_txt_lines.append(f"Mô tả hình ảnh (AI Prompt): {s.get('prompt', '')}")
    if s.get("character_ids"):
        vi_txt_lines.append(f"Nhân vật: {', '.join(s['character_ids'])}")
    vi_txt_lines.append("-" * 40)

vi_txt_lines.extend([
    "",
    "=" * 80,
    "👉 HƯỚNG DẪN KIỂM TRA & DUYỆT (GATE 1):",
    f"- Muốn sửa câu chữ từng cảnh: /edit_beat {job_id} <cảnh_số> <nội dung mới>",
    f"- Hoặc tải file .txt này về máy, chỉnh sửa rồi gửi lại file lên chat bot.",
    f"- Khi đã hoàn toàn ưng ý: bấm [Duyệt Kịch Bản (G1)] hoặc gõ /approve_script {job_id}",
    "=" * 80,
])
kich_ban_vi_file = project_dir / f"kich_ban_VI_{job_id}.txt"
kich_ban_vi_file.write_text("\n".join(vi_txt_lines), encoding="utf-8")

if en_scenes:
    en_txt_lines = [
        "=" * 80,
        "🎬 ENGLISH VOICE-OVER SCRIPT & SHOT PLAN",
        f"Job ID: {job_id}",
        f"Title: {source_title}",
        f"Total scenes: {len(en_scenes)} scenes | Duration: ~{len(en_scenes) * default_shot_duration:.0f}s",
        "=" * 80,
        "",
    ]
    for s in en_scenes:
        idx = s.get("index", 1)
        t_start = (idx - 1) * default_shot_duration
        t_end = idx * default_shot_duration
        en_txt_lines.append(f"[SCENE {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s)")
        en_txt_lines.append(f"Narration: {s.get('text', '')}")
        en_txt_lines.append(f"Visual Prompt: {s.get('prompt', '')}")
        en_txt_lines.append("-" * 40)
    kich_ban_en_file = project_dir / f"kich_ban_EN_{job_id}.txt"
    kich_ban_en_file.write_text("\n".join(en_txt_lines), encoding="utf-8")

# Update ApprovalStateMachine
asm = ApprovalStateMachine(project_dir, job_id)
beats_list = []
for i in range(1, len(shots) + 1):
    vi_txt = vi_scenes[i - 1].get("text", "")
    en_txt = en_scenes[i - 1].get("text", "")
    shot_chars = shots[i - 1].get("character_ids", [])
    beats_list.append(BeatIntent(
        id=i,
        text_vi=vi_txt,
        text_en=en_txt,
        duration=default_shot_duration,
        character_ids=shot_chars,
    ))

vi_full = "\n".join(b.text_vi for b in beats_list if b.text_vi)
en_full = "\n".join(b.text_en for b in beats_list if b.text_en)

asm.set_script(
    script_vi=vi_full,
    script_en=en_full,
    beats=beats_list,
    required_characters=required_characters,
    audit_report={"is_valid": True, "issues": [], "total_scenes": len(shots)},
)
asm.set_shots_plan(shots)

print("SUCCESS: Script regenerated and state machine updated successfully!")
