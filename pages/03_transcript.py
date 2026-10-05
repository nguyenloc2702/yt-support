import json
from pathlib import Path
import streamlit as st

from core.database import init_db
from services.project_service import ProjectService
from utils.ui import inject_pro_css, render_sidebar_hardware_status, render_studio_header

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Transcript Studio - Local Video Editor",
    page_icon="📝",
    layout="wide",
)

if "project_id" not in st.session_state:
    service = ProjectService()
    projs = service.list_projects()
    if projs:
        st.session_state.project_id = projs[0].id
    else:
        st.error("Chưa chọn project. Vui lòng vào trang Projects và tạo/mở một project.")
        st.stop()

project_id = st.session_state.project_id
service = ProjectService()

try:
    project = service.get_project(project_id)
except Exception as e:
    st.error(f"Không tìm thấy project: {e}")
    st.stop()

render_sidebar_hardware_status()

# Top Header
total_sec = project.duration_seconds or 0
mins = int(total_sec // 60)
secs = int(total_sec % 60)
tc_str = f"{mins:02d}:{secs:02d}" if total_sec else ""

render_studio_header(
    project_name=f"{project.name}",
    resolution=f"{project.width}x{project.height}" if project.width else "",
    codec="Whisper Transcript",
    timecode=tc_str,
)

analysis_dir = Path(f"projects/{project_id}/analysis")
trans_file = analysis_dir / "transcription.json"

# Load real transcription
segments = []
language = None
if trans_file.exists():
    try:
        with open(trans_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                segments = data.get("segments", [])
                language = data.get("language")
            elif isinstance(data, list):
                segments = data
    except Exception as e:
        st.error(f"Lỗi đọc file transcription: {e}")

col_left, col_right = st.columns([1, 1.4], gap="medium")

# ================= LEFT COLUMN: Video Player & Overview =================
with col_left:
    st.markdown(
        f"""
        <div class="pro-card" style="padding: 12px; margin-bottom: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="font-family: monospace; font-size: 0.78rem; color: #94a3b8; font-weight: 600;">SOURCE: {project.source_filename or 'Chưa có tên'}</span>
                <span style="background: rgba(59, 130, 246, 0.2); border: 1px solid rgba(59, 130, 246, 0.4); color: #93c5fd; font-size: 0.72rem; font-weight: 700; padding: 2px 6px; border-radius: 4px;">
                    {'PROXY SẴN SÀNG' if Path(f"projects/{project_id}/proxy/proxy_720p.mp4").exists() else 'NGUỒN GỐC'}
                </span>
            </div>
        """,
        unsafe_allow_html=True,
    )

    proxy_path = Path(f"projects/{project_id}/proxy/proxy_720p.mp4")
    source_path = Path(f"projects/{project_id}/source/input.mp4")

    if proxy_path.exists():
        st.video(str(proxy_path))
    elif source_path.exists():
        st.video(str(source_path))
    else:
        st.info("Chưa có video proxy hoặc video nguồn.")

    st.markdown("</div>", unsafe_allow_html=True)

    # Overview info card
    total_words = sum(len(s.get("text", "").split()) for s in segments)
    total_speech_time = sum(max(0, s.get("end", 0) - s.get("start", 0)) for s in segments)

    st.markdown(
        f"""
        <div class="pro-card" style="margin-bottom: 12px; padding: 14px;">
            <div class="pro-card-header" style="margin-bottom: 10px; padding-bottom: 6px;">
                <span class="pro-card-title">📊 Thông tin lời thoại tổng thể</span>
                <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.72rem; font-weight: 600; padding: 2px 6px; border-radius: 4px;">
                    Ngôn ngữ: {language or 'vi'}
                </span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px;">
                <div style="background: #161e31; padding: 8px; border-radius: 6px; border: 1px solid #1e293b;">
                    <div style="font-size: 0.7rem; color: #64748b;">Số phân đoạn</div>
                    <div style="font-size: 0.88rem; font-weight: 700; color: #60a5fa;">{len(segments)} đoạn</div>
                </div>
                <div style="background: #161e31; padding: 8px; border-radius: 6px; border: 1px solid #1e293b;">
                    <div style="font-size: 0.7rem; color: #64748b;">Tổng số từ</div>
                    <div style="font-size: 0.88rem; font-weight: 700; color: #e2e8f0;">{total_words:,} từ</div>
                </div>
                <div style="background: #161e31; padding: 8px; border-radius: 6px; border: 1px solid #1e293b;">
                    <div style="font-size: 0.7rem; color: #64748b;">Thời lượng tiếng nói</div>
                    <div style="font-size: 0.88rem; font-weight: 700; color: #34d399; font-family: monospace;">{total_speech_time:.1f} giây</div>
                </div>
                <div style="background: #161e31; padding: 8px; border-radius: 6px; border: 1px solid #1e293b;">
                    <div style="font-size: 0.7rem; color: #64748b;">Tỷ lệ phủ âm thanh</div>
                    <div style="font-size: 0.88rem; font-weight: 700; color: #fbbf24;">
                        {(total_speech_time / max(1, total_sec) * 100):.1f}%
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("⏩ Chuyển sang biên tập Dòng thời gian (Timeline)", type="primary", use_container_width=True):
        st.session_state.project_id = project_id
        st.switch_page("pages/04_timeline.py")

# ================= RIGHT COLUMN: Transcript Cards =================
with col_right:
    if not segments:
        st.info("Dự án chưa có biên bản phân tích lời thoại (Whisper). Vui lòng thực hiện phân tích trước.")
        if st.button("🚀 Đến trang Phân tích ngay"):
            st.session_state.project_id = project_id
            st.switch_page("pages/02_analysis.py")
    else:
        search_q = st.text_input(
            "Tìm kiếm trong lời thoại",
            placeholder="🔍 Nhập từ khóa cần tìm trong câu thoại...",
            label_visibility="collapsed",
        )

        filtered_segments = []
        for s in segments:
            text = s.get("text", "")
            if not search_q or search_q.lower() in text.lower():
                filtered_segments.append(s)

        st.caption(f"Hiển thị {len(filtered_segments)} / {len(segments)} phân đoạn")

        # Scrollable container
        card_container = st.container(height=550)
        with card_container:
            for idx, s in enumerate(filtered_segments[:100]):  # Limit to 100 for render speed
                start_sec = s.get("start", 0.0)
                end_sec = s.get("end", 0.0)
                dur = max(0.0, end_sec - start_sec)
                text = s.get("text", "").strip()
                speaker = s.get("speaker", f"Người nói")

                # Format time
                s_min, s_s = int(start_sec // 60), int(start_sec % 60)
                e_min, e_s = int(end_sec // 60), int(end_sec % 60)
                tc_display = f"{s_min:02d}:{s_s:02d} - {e_min:02d}:{e_s:02d}"

                # Highlight search keyword
                highlighted_text = text
                if search_q and search_q.lower() in text.lower():
                    # Simple case-insensitive highlight
                    import re
                    pattern = re.compile(re.escape(search_q), re.IGNORECASE)
                    highlighted_text = pattern.sub(
                        lambda m: f'<span style="background: rgba(245, 158, 11, 0.35); color: #fef08a; padding: 1px 4px; border-radius: 3px; font-weight: bold;">{m.group(0)}</span>',
                        text,
                    )

                st.markdown(
                    f"""
                    <div class="pro-transcript-card" style="margin-bottom: 8px; padding: 10px 14px;">
                        <div class="pro-transcript-header" style="margin-bottom: 6px;">
                            <div class="pro-transcript-meta">
                                <span class="pro-transcript-time" style="font-family: monospace; font-size: 0.74rem;">{tc_display}</span>
                                <span style="font-size: 0.76rem; color: #94a3b8;">{speaker}</span>
                                <span style="font-size: 0.72rem; color: #64748b;">({dur:.1f}s)</span>
                            </div>
                            <span style="font-size: 0.72rem; color: #64748b;">#{idx + 1}</span>
                        </div>
                        <div style="font-size: 0.86rem; color: #e2e8f0; line-height: 1.5;">
                            {highlighted_text}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            if len(filtered_segments) > 100:
                st.info(f"Đã giới hạn hiển thị 100 phân đoạn đầu tiên trên tổng số {len(filtered_segments)} phân đoạn.")
