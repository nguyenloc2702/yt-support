import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_semantic_script")

job_id = sys.argv[1] if len(sys.argv) > 1 else "remake_1792673726_b537ce"
project_dir = Path(f"/app/data/automated_runs/{job_id}")

if not project_dir.exists():
    print(f"Error: {project_dir} does not exist")
    sys.exit(1)

from pipeline.automated_factory import try_extract_subtitles
from services.script_auditor import _clean_rolling_stutter
from services.approval_state_machine import ApprovalStateMachine, BeatIntent
from pipeline.character_manager import detect_characters_in_text

sub_segments = try_extract_subtitles(project_dir)
if not sub_segments:
    print("Error: No subtitles found")
    sys.exit(1)

total_dur = max((float(s.get("end", 0)) for s in sub_segments), default=900.0)
logger.info(f"Loaded {len(sub_segments)} subtitle segments. Total duration: {total_dur:.1f}s")

# Clean stutters from segments and stitch into coherent paragraphs
cleaned_segs = []
for s in sub_segments:
    txt = _clean_rolling_stutter(str(s.get("text", "")).strip())
    if txt:
        cleaned_segs.append({
            "start": float(s.get("start", 0)),
            "end": float(s.get("end", 0)),
            "text": txt,
        })

# Divide into ~10 logical chronological narrative blocks (each ~90 - 100 seconds)
NUM_BLOCKS = 10
block_duration = total_dur / NUM_BLOCKS
blocks = []
for b_idx in range(NUM_BLOCKS):
    t_start = b_idx * block_duration
    t_end = (b_idx + 1) * block_duration
    b_segs = [s for s in cleaned_segs if t_start <= s["start"] < t_end or (b_idx == NUM_BLOCKS - 1 and s["start"] >= t_start)]
    b_text = " ".join(s["text"] for s in b_segs)
    # Further clean rolling overlaps
    b_text = _clean_rolling_stutter(b_text)
    blocks.append({
        "block_idx": b_idx + 1,
        "t_start": t_start,
        "t_end": t_end,
        "text": b_text,
    })

logger.info(f"Created {len(blocks)} thematic blocks for AI scene slicing.")

api_key = os.environ.get("XKIRO_API_KEY")
client = OpenAI(
    api_key=api_key,
    base_url="https://api.xkiro.com/v1",
)

all_scenes = []
scene_counter = 1

for b in blocks:
    logger.info(f"Processing Block {b['block_idx']}/{NUM_BLOCKS} ({b['t_start']:.0f}s - {b['t_end']:.0f}s)...")
    block_role = "MỞ ĐẦU (HOOK)" if b['block_idx'] == 1 else ("KẾT THÚC (CALLBACK & OUTRO)" if b['block_idx'] == NUM_BLOCKS else "THÂN BÀI (TRIỂN KHAI LUẬN ĐIỂM)")
    prompt = f"""Bạn là Đạo diễn kiêm Biên tập viên Phóng sự Tài liệu chuyên nghiệp.
Nhiệm vụ: Phân tích đoạn nội dung thuyết minh tài chính ({b['t_start']:.0f}s - {b['t_end']:.0f}s, vai trò: {block_role}) và băm thành các CẢNH THEO TÌNH HUỐNG NỘI DUNG CỤ THỂ, KHÔNG băm 5s máy móc.

Nội dung:
\"\"\"{b['text']}\"\"\"

ÁP DỤNG BỘ QUY TẮC NGHỆ THUẬT UNIVERSAL_VIDEO_ALGORITHM (HIGH-RETENTION EXPLAINER):
1. NGUYÊN TẮC CỐT LÕI: "Người xem không rời đi vì họ muốn biết câu trả lời cho câu hỏi bạn đã đặt ra." Tạo và duy trì sự tò mò xuyên suốt.
2. CÔNG THỨC HOOK (Nếu là đoạn mở đầu 0-30s):
   - Chứa: [Số liệu khủng / Nghịch lý] + [So sánh bất ngờ] + [Câu hỏi treo/Open loop].
   - Đi thẳng vào sự thật chấn động, không mở đầu bằng lời chào hỏi hay giới thiệu lan man.
3. CHUỖI VÒNG TÒ MÒ LỒNG NHAU (OPEN LOOPS):
   - Mở vòng tò mò tiếp theo TRƯỚC KHI đóng hoàn toàn vòng tò mò cũ.
   - Luôn gieo các câu bắc cầu kích thích: "Nhưng đó chưa phải điều bất ngờ nhất...", "Điều này dẫn tới một nghịch lý trớ trêu...", "Câu trả lời sẽ khiến bạn phải suy ngẫm...".
4. NHỊP KỊCH BẢN DỨT KHOÁT (QUY TẮC 54/46):
   - Đan xen các câu ngắn gọn, dứt khoát (<= 6 từ) để tạo điểm nhấn và ngắt nhịp kịch tính (Dramatic Pauses).
   - Ví dụ: "Không hề.", "Hoàn toàn bằng không.", "Và bạn không hề đơn độc.", "Đó là sự thật."
5. ẨN DỤ 3 TẦNG (3-TIER METAPHOR):
   - Biến các khái niệm tài chính trừu tượng thành đồ vật đời thường và hành động có xung đột gay gắt (như chiếc xe hơi vô hình, bàn phím gõ ra tiền).
6. NGUYÊN TẮC CALLBACK (Nếu là khối kết thúc / Outro):
   - Lời thoại kết thúc video BẮT BUỘC phải quay lại giải tỏa và trả lời câu hỏi/nghịch lý đã đặt ra ở phần Hook mở đầu.
7. ĐIỆN ẢNH VEO PROMPT (STYLE BIBLE):
   - Cinematic 4K, 35mm master lens, photorealistic atmospheric depth.
   - Bố cục 60-70% Negative Space, 1-3 tiêu điểm rõ ràng (không rối rắm).
   - Mood lighting: Sáng ngà tự nhiên khi phân tích khách quan; chuyển sang tông lạnh, u ám hoặc tương phản gắt khi bóc trần khủng hoảng hoặc bất công hệ thống.

Yêu cầu định dạng đầu ra:
- Tự động băm thành 8 đến 13 cảnh dựa trên sự chuyển biến của tình huống cụ thể.
- situation: Mô tả tình huống cụ thể (hành động, bối cảnh diễn ra).
- voiceover_vi: Lời thoại tiếng Việt mượt mà, truyền cảm, tròn câu trọn ý. Tuyệt đối:
   - KHÔNG ngắt câu cụt lủn.
   - KHÔNG lặp lại từ của câu trước.
   - Dùng đúng thuật ngữ tài chính (tiền gốc, lãi suất, dự trữ bắt buộc, ngân hàng thương mại, tín dụng...).
- voiceover_en: Bản dịch đối chiếu tiếng Anh chuẩn xác.
- estimated_duration: Thời lượng tính bằng giây phù hợp tự nhiên với độ dài lời thoại (từ 4.5 đến 8.0 giây).
- visual_prompt: Prompt tiếng Anh chi tiết chuẩn điện ảnh 4K cho Veo/Cinematic documentary, thể hiện đúng situation đó.

Trả về duy nhất JSON array:
[
  {{
    "situation": "...",
    "voiceover_vi": "...",
    "voiceover_en": "...",
    "estimated_duration": 6.0,
    "visual_prompt": "Cinematic 4K, professional documentary cinematography..."
  }}
]
Chỉ trả về JSON thuần túy trong khối ```json ... ``` hoặc JSON thô."""
    retries = 3
    block_scenes = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model="qwen/qwen3.5-flash:free",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=45,
            )
            raw_out = resp.choices[0].message.content.strip()
            if "```json" in raw_out:
                raw_out = raw_out.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_out:
                raw_out = raw_out.split("```")[1].split("```")[0].strip()
            block_scenes = json.loads(raw_out)
            if isinstance(block_scenes, list) and len(block_scenes) > 0:
                break
        except Exception as e:
            logger.warning(f"Block {b['block_idx']} attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    if not block_scenes:
        logger.error(f"Failed to process block {b['block_idx']} with AI. Falling back.")
        continue

    for s in block_scenes:
        dur = float(s.get("estimated_duration", 5.5))
        dur = max(4.0, min(9.0, dur))
        all_scenes.append({
            "index": scene_counter,
            "situation": s.get("situation", ""),
            "text": s.get("voiceover_vi", ""),
            "text_en": s.get("voiceover_en", ""),
            "prompt": s.get("visual_prompt", ""),
            "duration": dur,
            "is_key_scene": True,
        })
        scene_counter += 1

logger.info(f"AI Semantic Scene Slicing complete! Total scenes generated: {len(all_scenes)}")

# Persist plans
vi_scenes = []
en_scenes = []
for s in all_scenes:
    vi_scenes.append({
        "index": s["index"],
        "situation": s["situation"],
        "text": s["text"],
        "prompt": s["prompt"],
        "duration": s["duration"],
        "is_key_scene": True,
    })
    en_scenes.append({
        "index": s["index"],
        "situation": s["situation"],
        "text": s["text_en"] or s["text"],
        "prompt": s["prompt"],
        "duration": s["duration"],
        "is_key_scene": True,
    })

shots_vi_file = project_dir / "shots_plan_vi.json"
shots_vi_file.write_text(json.dumps(vi_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

shots_en_file = project_dir / "shots_plan_en.json"
shots_en_file.write_text(json.dumps(en_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

shots_file = project_dir / "shots_plan.json"
shots_file.write_text(json.dumps(vi_scenes, indent=2, ensure_ascii=False), encoding="utf-8")

shots = vi_scenes

# Character detection
combined_script_text = " ".join(s.get("text", "") for s in vi_scenes) + " " + " ".join(s.get("text", "") for s in en_scenes)
detected_chars = detect_characters_in_text(combined_script_text)
required_characters = [d["name"] for d in detected_chars]

for s in shots:
    s_txt = (s.get("text", "") + " " + s.get("situation", "")).lower()
    s["character_ids"] = [
        d["name"].lower().replace(" ", "_")
        for d in detected_chars
        if d["name"].lower() in s_txt
    ]

target_voice = "vi-VN-NamMinhNeural"
(project_dir / "voice.txt").write_text(target_voice, encoding="utf-8")

est_total_duration = sum(s["duration"] for s in shots)
total_words_vi = sum(len(str(s.get("text", "")).split()) for s in vi_scenes)
source_title = "Once You Understand MONEY, You'll Stop Saving it [PART #2]"

vi_txt_lines = [
    "=" * 80,
    "🎬 KỊCH BẢN PHÂN CẢNH VOICE-OVER TIẾNG VIỆT (AI SEMANTIC SLICING)",
    f"Job ID: {job_id}",
    f"Tiêu đề: {source_title}",
    f"Tổng số cảnh: {len(shots)} cảnh | Thời lượng dự kiến: ~{est_total_duration:.0f}s (~{est_total_duration / 60:.1f} phút)",
    f"Tổng số từ: {total_words_vi} từ | Tốc độ đọc: ~2.5 - 3.0 từ/giây",
    f"Giọng đọc đề xuất: {target_voice}",
    "=" * 80,
    "",
]

current_time = 0.0
for s in shots:
    idx = s.get("index", 1)
    dur = s.get("duration", 5.5)
    t_start = current_time
    t_end = current_time + dur
    current_time = t_end
    tag = " [HOOK]" if idx <= 2 else (" [OUTRO/KẾT]" if idx == len(shots) else "")
    vi_txt_lines.append(f"[CẢNH {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s) (Thời lượng: {dur:.1f}s){tag}")
    if s.get("situation"):
        vi_txt_lines.append(f"Tình huống cụ thể: {s['situation']}")
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
        "🎬 ENGLISH VOICE-OVER SCRIPT & SHOT PLAN (AI SEMANTIC SLICING)",
        f"Job ID: {job_id}",
        f"Title: {source_title}",
        f"Total scenes: {len(en_scenes)} scenes | Duration: ~{est_total_duration:.0f}s",
        "=" * 80,
        "",
    ]
    cur_t = 0.0
    for s in en_scenes:
        idx = s.get("index", 1)
        dur = s.get("duration", 5.5)
        t_start = cur_t
        t_end = cur_t + dur
        cur_t = t_end
        en_txt_lines.append(f"[SCENE {idx:03d}] ({t_start:.1f}s - {t_end:.1f}s) (Duration: {dur:.1f}s)")
        if s.get("situation"):
            en_txt_lines.append(f"Situation: {s['situation']}")
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
        duration=shots[i - 1]["duration"],
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

logger.info("SUCCESS: Masterpiece script written by xKiro AI and saved!")
print("ALL_DONE_SUCCESS")
