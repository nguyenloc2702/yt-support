import json
import time
from pathlib import Path

import streamlit as st

from core.database import init_db
from services.project_service import ProjectService
from services.job_service import JobService
from core.enums import JobType
from utils.ui import inject_pro_css, render_sidebar_hardware_status, render_studio_header

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Analysis Studio - Local Video Editor",
    page_icon="📊",
    layout="wide",
)

if "project_id" not in st.session_state:
    # Auto select latest project if exists for smoother preview
    service = ProjectService()
    projs = service.list_projects()
    if projs:
        st.session_state.project_id = projs[0].id
    else:
        st.error("Chưa chọn project. Vui lòng vào trang Projects và tạo/mở một project.")
        st.stop()

project_id = st.session_state.project_id
service = ProjectService()
job_service = JobService()

try:
    project = service.get_project(project_id)
except Exception as e:
    st.error(f"Không tìm thấy project: {e}")
    st.stop()

# Sidebar
render_sidebar_hardware_status(
    whisper_status="Đang xử lý" if st.session_state.get("analysis_job_id") else "Sẵn sàng",
    is_busy=bool(st.session_state.get("analysis_job_id")),
)

# Format timecode and info
total_seconds = project.duration_seconds or 872.10
hours = int(total_seconds // 3600)
mins = int((total_seconds % 3600) // 60)
secs = int(total_seconds % 60)
frames = int((total_seconds - int(total_seconds)) * 25)
formatted_timecode = f"{hours:02d}:{mins:02d}:{secs:02d}:{frames:02d}"

res_text = f"{project.width}x{project.height}" if project.width else "1080p60"
codec_text = "ProRes 422"

# Studio Top Header
render_studio_header(
    project_name=f"{project.name}",
    resolution=f"{res_text} • {project.fps:.0f}fps" if project.fps else "1080p60",
    codec=codec_text,
    timecode=formatted_timecode,
)

analysis_dir = Path(f"projects/{project_id}/analysis")
options_file = analysis_dir / "options.json"

CACHED_ARTEFACTS = [
    "transcription.json",
    "transcription_meta.json",
    "transcription.partial.json",
    "scenes.json",
    "silences.json",
    "candidates.json",
    "ranked.json",
]

def _load_saved_options() -> dict:
    try:
        with open(options_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

saved_options = _load_saved_options()

# Check resumable / interrupted state
resumable = job_service.find_resumable_job(project_id, JobType.ANALYSIS.value)
active_job_id = st.session_state.get("analysis_job_id")

if not active_job_id:
    recovered = job_service.get_active_job(project_id, JobType.ANALYSIS.value)
    if recovered is not None:
        active_job_id = recovered.id
        st.session_state.analysis_job_id = active_job_id

# 1. Alert Banner (Khôi phục phiên làm việc)
if resumable or (analysis_dir.exists() and not active_job_id):
    st.markdown(
        """
        <div class="pro-banner-alert">
            <div style="display: flex; align-items: center; gap: 14px;">
                <span style="font-size: 1.6rem; color: #ef4444;">⚠️</span>
                <div>
                    <div class="pro-banner-alert-title">Khôi phục phiên làm việc trước đó</div>
                    <div class="pro-banner-alert-desc">Phát hiện dữ liệu cache phân tích các bước trước. Đã khôi phục bộ nhớ đệm an toàn.</div>
                </div>
            </div>
            <div style="display: flex; gap: 8px;">
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# 2. Main 2-Column Layout (Tệp nguồn & Cấu hình AI)
col_left, col_right = st.columns([1, 1.25], gap="medium")

with col_left:
    st.markdown(
        """
        <div class="pro-card">
            <div class="pro-card-header">
                <span class="pro-card-title">📹 Tệp nguồn đang xử lý</span>
                <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.74rem; font-weight: 600; padding: 2px 8px; border-radius: 4px;">
                    Sẵn sàng phân tích
                </span>
            </div>
        """,
        unsafe_allow_html=True,
    )

    source_path = Path(f"projects/{project_id}/source/input.mp4")
    proxy_path = Path(f"projects/{project_id}/proxy/proxy_720p.mp4")
    
    if proxy_path.exists():
        st.video(str(proxy_path))
    elif source_path.exists():
        st.video(str(source_path))
    else:
        st.info("Chưa tìm thấy video proxy hoặc video nguồn. Vui lòng tải video tại trang Quản lý Dự án.")

    # Specs Grid
    file_size_mb = 0.0
    if source_path.exists():
        file_size_mb = source_path.stat().st_size / (1024 * 1024)

    st.markdown(
        f"""
            <div class="pro-specs-grid">
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Độ phân giải</div>
                    <div class="pro-spec-value">{project.width or 1920}x{project.height or 1080}</div>
                </div>
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Khung hình / giây</div>
                    <div class="pro-spec-value">{project.fps or 30.0:.0f} fps</div>
                </div>
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Thời lượng gốc</div>
                    <div class="pro-spec-value">{formatted_timecode}</div>
                </div>
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Kích thước tệp</div>
                    <div class="pro-spec-value">{file_size_mb:.1f} MB</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col_right:
    st.markdown(
        """
        <div class="pro-card">
            <div class="pro-card-header">
                <span class="pro-card-title">⚙ Cấu hình phân tích AI (Analysis Settings)</span>
            </div>
        """,
        unsafe_allow_html=True,
    )

    c_opt1, c_opt2 = st.columns(2)
    with c_opt1:
        mode_idx = 0
        saved_mode = saved_options.get("mode", "shorten")
        if saved_mode == "remove_silence": mode_idx = 1
        elif saved_mode == "summary": mode_idx = 2
        
        mode = st.selectbox(
            "Chế độ biên tập (AI Prompt Mode)",
            ["Rough Cut ngắn (Tự động cắt khoảng lặng)", "Chỉ cắt khoảng lặng (Silence Removal)", "Tóm tắt theo chủ đề (Key Highlights)"],
            index=mode_idx,
            key="analysis_mode_select",
        )
    with c_opt2:
        whisper_model = st.selectbox(
            "Mô hình Whisper",
            [
                "large-v3 (Độ chính xác cao nhất)",
                "medium (Cân bằng tốc độ/chính xác)",
                "small (Tốc độ cao)",
                "base (Nhẹ nhất)",
            ],
            index=0,
            key="analysis_whisper_select",
        )

    c_sl1, c_sl2 = st.columns([1.2, 1])
    with c_sl1:
        target_duration = st.slider(
            "Thời lượng mục tiêu (giây)",
            min_value=60,
            max_value=300,
            value=int(saved_options.get("target_duration", 180)),
            step=10,
            help="Thời lượng video xuất bản mong muốn",
        )
    with c_sl2:
        language = st.selectbox(
            "Ngôn ngữ nguồn",
            ["Tiếng Việt (Vietnamese)", "English (Auto-detect)", "Tự động phát hiện (Auto)"],
            index=0,
            key="analysis_lang_select",
        )

    topic_raw = st.text_input(
        "Chủ đề chính (tùy chọn - dùng để ưu tiên nội dung liên quan)",
        value=saved_options.get("topic", "") or "",
        placeholder="VD: Du lịch, Nấu ăn, Review điện thoại...",
        key="analysis_topic_input",
    )
    keywords_raw = st.text_input(
        "Từ khóa ưu tiên (tùy chọn, phân cách bằng dấu phẩy)",
        value=", ".join(saved_options.get("keywords", []) or []),
        placeholder="VD: giá, chất lượng, so sánh...",
        key="analysis_keywords_input",
    )

    c_chk, c_action = st.columns([1.5, 1])
    with c_chk:
        force_rerun = st.checkbox(
            "Bắt buộc phân tích lại toàn bộ (Force re-analysis)",
            value=False,
            help="Bỏ qua bộ nhớ đệm cache SQLite, tính toán lại Whisper & Scene Detection",
        )
    with c_action:
        mode_val = "shorten"
        if "Chỉ cắt" in mode: mode_val = "remove_silence"
        elif "Tóm tắt" in mode: mode_val = "summary"

        model_val = "large-v3"
        if "medium" in whisper_model: model_val = "medium"
        elif "small" in whisper_model: model_val = "small"
        elif "base" in whisper_model: model_val = "base"

        lang_val = "vi" if "Tiếng Việt" in language else "auto"

        if active_job_id and job_service.is_running(active_job_id):
            if st.button("⏹ Hủy tác vụ", type="secondary", use_container_width=True):
                job_service.cancel_job(active_job_id)
                st.session_state.analysis_job_id = None
                st.rerun()
        else:
            if st.button("🚀 Bắt đầu phân tích", type="primary", use_container_width=True):
                opts = {
                    "mode": mode_val,
                    "whisper_model": model_val,
                    "language": lang_val,
                    "target_duration": float(target_duration),
                    "topic": topic_raw.strip(),
                    "keywords": [k.strip() for k in keywords_raw.split(",") if k.strip()],
                }
                analysis_dir.mkdir(parents=True, exist_ok=True)
                with open(options_file, "w", encoding="utf-8") as fh:
                    json.dump(opts, fh, indent=2, ensure_ascii=False)

                if force_rerun:
                    for name in CACHED_ARTEFACTS:
                        c_file = analysis_dir / name
                        if c_file.exists():
                            try: c_file.unlink()
                            except OSError: pass

                st.session_state.analysis_job_id = job_service.submit_analysis(project_id, opts)
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# 3. 9-Stage Processing Pipeline Grid
current_stage_num = 5
current_stage_name = "Phát hiện phân cảnh"
if active_job_id and job_service.is_running(active_job_id):
    prog_info = job_service.get_progress(active_job_id)
    pct = prog_info.get("percent", 50)
    current_stage_num = max(1, min(9, int(pct / 11) + 1))
elif (analysis_dir / "transcription.json").exists():
    current_stage_num = 9
    current_stage_name = "Hoàn tất"

stages_data = [
    ("01", "Probe media", "Hoàn tất", "completed"),
    ("02", "Create proxy", "Hoàn tất", "completed"),
    ("03", "Extract audio", "Hoàn tất", "completed"),
    ("04", "Transcribe (Whisper)", "Hoàn tất", "completed"),
    ("05", "Detect scenes", "● Đang xử lý", "running"),
    ("06", "Detect silence", "Chờ xử lý", "pending"),
    ("07", "Build candidates", "Chờ xử lý", "pending"),
    ("08", "Rank segments", "Chờ xử lý", "pending"),
    ("09", "Create timeline", "Chờ xử lý", "pending"),
]

# Adjust stage states dynamically
for idx in range(len(stages_data)):
    num = idx + 1
    num_str, name, _, _ = stages_data[idx]
    if num < current_stage_num:
        stages_data[idx] = (num_str, name, "✓ Hoàn tất", "completed")
    elif num == current_stage_num:
        if active_job_id and job_service.is_running(active_job_id):
            stages_data[idx] = (num_str, name, "↻ Đang xử lý", "running")
        else:
            stages_data[idx] = (num_str, name, "✓ Hoàn tất", "completed")
    else:
        stages_data[idx] = (num_str, name, "Chờ xử lý", "pending")

cards_html = ""
for num_str, name, status_text, state_class in stages_data:
    cards_html += f"""
    <div class="pro-stage-card {state_class}">
        <div class="pro-stage-top">
            <span class="pro-stage-num">{num_str}</span>
            <span style="font-size: 0.75rem;">{'✓' if state_class == 'completed' else ('↻' if state_class == 'running' else '◯')}</span>
        </div>
        <div class="pro-stage-name">{name}</div>
        <div class="pro-stage-status {state_class}">{status_text}</div>
    </div>
    """

st.markdown(
    f"""
    <div class="pro-pipeline-container">
        <div class="pro-pipeline-header">
            <span class="pro-card-title">🔀 Quy trình xử lý 9 giai đoạn (9-Stage Processing Pipeline)</span>
            <span class="pro-card-subtitle">Giai đoạn hiện tại: <strong>{current_stage_num} / 9</strong> ({current_stage_name})</span>
        </div>
        <div class="pro-pipeline-grid">
            {cards_html}
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# 4. Overall Progress Bar Section
prog_percent = 0
task_message = "Chưa bắt đầu phân tích"

if active_job_id:
    job = job_service.get_job(active_job_id)
    if job and job.status in ("pending", "running"):
        p_info = job_service.get_progress(active_job_id)
        prog_percent = max(0, min(100, int(p_info.get("percent", 0))))
        task_message = p_info.get("message", "Đang xử lý phân tích...")
    elif job and job.status == "completed":
        prog_percent = 100
        task_message = "Đã hoàn tất phân tích media thành công"
elif (analysis_dir / "transcription.json").exists():
    prog_percent = 100
    task_message = "Đã hoàn tất phân tích media thành công"

st.markdown(
    f"""
    <div class="pro-card" style="padding: 14px 18px; margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; align-items: center; gap: 10px;">
                <span style="font-size: 1.3rem;">⏳</span>
                <div>
                    <div style="font-size: 0.95rem; font-weight: 700; color: #ffffff;">
                        Tiến độ: <span style="color: #60a5fa;">{prog_percent}%</span>
                    </div>
                    <div style="font-size: 0.78rem; color: #94a3b8;">
                        {task_message}
                    </div>
                </div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.progress(prog_percent / 100.0)

if active_job_id and job_service.is_running(active_job_id):
    time.sleep(1)
    st.rerun()

# 5. Summary of Analysis Results
if (analysis_dir / "transcription.json").exists():
    trans_count = 0
    try:
        with open(analysis_dir / "transcription.json", "r", encoding="utf-8") as f:
            trans_data = json.load(f)
            if isinstance(trans_data, list):
                trans_count = len(trans_data)
            elif isinstance(trans_data, dict):
                trans_count = len(trans_data.get("segments", []))
    except Exception:
        pass

    scenes_count = 0
    scenes_file = analysis_dir / "scenes.json"
    if scenes_file.exists():
        try:
            with open(scenes_file, "r", encoding="utf-8") as f:
                scenes_data = json.load(f)
                scenes_count = len(scenes_data) if isinstance(scenes_data, list) else len(scenes_data.get("scenes", []))
        except Exception:
            pass

    silence_count = 0
    silences_file = analysis_dir / "silences.json"
    if silences_file.exists():
        try:
            with open(silences_file, "r", encoding="utf-8") as f:
                sil_data = json.load(f)
                silence_count = len(sil_data) if isinstance(sil_data, list) else len(sil_data.get("silences", []))
        except Exception:
            pass

    st.markdown(
        f"""
        <div class="pro-card" style="margin-top: 14px; padding: 14px 18px;">
            <div class="pro-card-header" style="margin-bottom: 10px;">
                <span class="pro-card-title">📋 Tóm tắt kết quả phân tích thực tế</span>
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;">
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Phân đoạn giọng nói (Whisper)</div>
                    <div class="pro-spec-value" style="color: #60a5fa;">{trans_count} đoạn thoại</div>
                </div>
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Phát hiện cảnh quay (PySceneDetect)</div>
                    <div class="pro-spec-value" style="color: #34d399;">{scenes_count} cảnh</div>
                </div>
                <div class="pro-spec-item">
                    <div class="pro-spec-label">Phát hiện vùng im lặng (VAD)</div>
                    <div class="pro-spec-value" style="color: #fbbf24;">{silence_count} khoảng lặng</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )