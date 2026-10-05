import streamlit as st
from pathlib import Path
from config.settings import settings
from core.database import init_db
from core.logging_config import get_logger
from services.project_service import ProjectService
from utils.ui import (
    inject_pro_css,
    render_sidebar_hardware_status,
    render_studio_header,
)
import subprocess

logger = get_logger(__name__)

def check_binary(name: str) -> bool:
    try:
        subprocess.run([name, "-version"], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def startup_check():
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.projects_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.log_dir).mkdir(parents=True, exist_ok=True)

    if not check_binary(settings.ffmpeg_path):
        st.warning(f"FFmpeg not found at {settings.ffmpeg_path}. Some features may not work.")
    if not check_binary(settings.ffprobe_path):
        st.warning(f"FFprobe not found at {settings.ffprobe_path}. Some features may not work.")

    init_db()
    logger.info("Startup checks complete")

def main():
    st.set_page_config(
        page_title="Local Video Editor v2.4 Pro Studio",
        page_icon="🎬",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    startup_check()
    inject_pro_css()

    # Sidebar Header
    st.sidebar.markdown(
        """
        <div class="pro-sidebar-brand">
            <div class="brand-name"><span class="brand-logo">🎬</span> Video Studio</div>
            <div class="brand-sub">AI Rough-Cut Editor · v2.4</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.sidebar.button("➕ Tạo dự án mới", use_container_width=True, type="primary"):
        st.switch_page("pages/01_projects.py")

    # Sidebar Footer
    render_sidebar_hardware_status(whisper_status="Sẵn sàng", is_busy=False)

    # Main Top Header
    render_studio_header(
        project_name="Studio Dashboard",
        resolution="",
        codec="Local Offline Pipeline",
        timecode="",
    )

    # Welcome
    st.markdown(
        """
        ### 🎬 AI Rough-Cut Video Studio
        Biên tập video tự động cục bộ — Whisper, PySceneDetect, Audio Ducking & EBU R128.
        Chọn một công cụ bên thanh bên trái để bắt đầu.
        """
    )
    st.divider()

    # Recent Projects quick access
    service = ProjectService()
    projects = service.list_projects()
    
    st.subheader("📁 Dự án gần đây")

    if not projects:
        st.info("Chưa có dự án nào. Bấm '+ Tạo dự án mới' ở Sidebar để bắt đầu.")
    else:
        for p in projects[:5]:
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            with c1:
                st.markdown(f"**🎬 {p.name}**")
                if p.source_filename:
                    st.caption(f"Nguồn: `{p.source_filename}`")
            with c2:
                status_color = "#10b981" if p.status.value == "completed" else "#3b82f6"
                st.markdown(f"Trạng thái: <span style='color:{status_color}; font-weight:600;'>{p.status.value}</span>", unsafe_allow_html=True)
                st.caption(f"Ngày tạo: {p.created_at.strftime('%Y-%m-%d %H:%M')}")
            with c3:
                if st.button("Mở Studio", key=f"dash_open_{p.id}", use_container_width=True):
                    st.session_state.project_id = p.id
                    st.switch_page("pages/02_analysis.py")
            with c4:
                if st.button("Xóa", key=f"dash_del_{p.id}", use_container_width=True):
                    service.delete_project(p.id)
                    st.rerun()

if __name__ == "__main__":
    main()