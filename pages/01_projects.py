from pathlib import Path
import streamlit as st

from core.database import init_db
from services.project_service import ProjectService
from core.schemas import ProjectCreate
from core.enums import RightsType
from core.exceptions import InvalidMediaError, DependencyNotFoundError
from config.settings import settings
from utils.ui import inject_pro_css, render_sidebar_hardware_status, render_studio_header

st.set_page_config(
    page_title="Projects - Local Video Editor",
    page_icon="📁",
    layout="wide",
)

init_db()
inject_pro_css()
service = ProjectService()

# Sidebar
render_sidebar_hardware_status()

# Studio Header
render_studio_header(
    project_name="Quản lý Dự án (Project Manager)",
    resolution="Pro Studio v2.4",
    codec="Local Storage",
    timecode="--:--:--:--",
)

st.markdown(
    """
    <div class="pro-card">
        <div class="pro-card-header">
            <span class="pro-card-title">➕ Tạo Dự Án Mới (Create Project)</span>
            <span class="pro-card-subtitle">MP4, MOV, MKV, WebM hoặc Tải trực tiếp qua URL</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

mode = st.radio(
    "Nguồn video đầu vào",
    ["Tải tệp từ máy tính (Local File)", "Tải từ YouTube / URL"],
    horizontal=True,
    key="create_mode",
)

if mode == "Tải tệp từ máy tính (Local File)":
    with st.form("create_project_form"):
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            name = st.text_input("Tên dự án (Project Name)*", placeholder="VD: Tech Review - Vlog 42")
            uploaded_file = st.file_uploader(
                "Chọn tệp Video nguồn", type=["mp4", "mov", "mkv", "webm", "m4v"]
            )
            rights_type = st.selectbox("Loại bản quyền nội dung (Rights Type)", [rt.value for rt in RightsType])
        
        with col_f2:
            description = st.text_area("Mô tả dự án", placeholder="Mô tả ngắn gọn về nội dung video...")
            notes = st.text_input("Ghi chú bổ sung (Tùy chọn)")
            source_url = st.text_input("Link nguồn gốc (Tùy chọn)")
        
        confirmed = st.checkbox("Tôi xác nhận có đầy đủ quyền sở hữu hoặc sử dụng hợp pháp nội dung video này", value=True)
        
        col_btn, _ = st.columns([2, 4])
        with col_btn:
            submitted = st.form_submit_button("🚀 Tạo Dự Án & Bắt Đầu Phân Tích", use_container_width=True, type="primary")

        if submitted:
            if not name:
                st.error("Vui lòng nhập tên dự án.")
            elif not confirmed:
                st.error("Bạn phải xác nhận quyền sử dụng nội dung.")
            elif not uploaded_file:
                st.error("Vui lòng tải lên một tệp video.")
            else:
                try:
                    data = ProjectCreate(
                        name=name,
                        description=description,
                        rights_type=RightsType(rights_type),
                        confirmed=confirmed,
                        source_url=source_url or None,
                        notes=notes or None,
                    )
                    with st.spinner("Đang khởi tạo dự án và lưu trữ tệp nguồn..."):
                        project = service.create_project(data, uploaded_file, uploaded_file.name)
                    st.success(f"Dự án '{project.name}' đã được tạo thành công (ID: {project.id})")
                    st.session_state.project_id = project.id
                    st.switch_page("pages/02_analysis.py")
                except InvalidMediaError as e:
                    st.error(f"Tệp không hợp lệ: {str(e)}")
                except Exception as e:
                    st.error(f"Lỗi: {str(e)}")
else:
    max_height = getattr(settings, "max_download_height", 2160)
    st.caption(f"Tải video chất lượng cao nhất (lên tới {max_height}p) tự động qua yt-dlp.")
    with st.form("create_project_url_form"):
        col_u1, col_u2 = st.columns(2)
        with col_u1:
            name = st.text_input("Tên dự án (Project Name)*", placeholder="VD: Youtube Review Clip")
            url = st.text_input("Video URL (YouTube, Vimeo, ...)*", placeholder="https://www.youtube.com/watch?v=...")
            rights_type = st.selectbox("Loại bản quyền nội dung", [rt.value for rt in RightsType])
        with col_u2:
            description = st.text_area("Mô tả dự án", placeholder="Mô tả nội dung...")
            notes = st.text_input("Ghi chú bổ sung")
        
        confirmed = st.checkbox("Tôi xác nhận có đầy đủ quyền sở hữu hoặc sử dụng hợp pháp nội dung video này", value=True)
        submitted_url = st.form_submit_button("⚡ Tải Video & Tạo Dự Án", type="primary")

        if submitted_url:
            if not name:
                st.error("Vui lòng nhập tên dự án.")
            elif not confirmed:
                st.error("Bạn phải xác nhận quyền sử dụng nội dung.")
            elif not url:
                st.error("Vui lòng nhập đường dẫn URL video.")
            else:
                try:
                    data = ProjectCreate(
                        name=name,
                        description=description,
                        rights_type=RightsType(rights_type),
                        confirmed=confirmed,
                        source_url=url,
                        notes=notes or None,
                    )
                    progress = st.progress(0, text="Đang kết nối và tải video...")

                    def _cb(pct: int, msg: str) -> None:
                        progress.progress(min(max(pct, 0), 100), text=msg)

                    with st.spinner("Đang tải video từ URL..."):
                        project = service.create_project_from_url(
                            data, url, progress_callback=_cb
                        )
                    progress.progress(100, text="Tải xong!")
                    st.success(f"Dự án '{project.name}' tạo hoàn tất.")
                    st.session_state.project_id = project.id
                    st.switch_page("pages/02_analysis.py")
                except DependencyNotFoundError as e:
                    st.error(str(e))
                    st.code("pip install yt-dlp", language="bash")
                except InvalidMediaError as e:
                    st.error(f"Lỗi tải video: {str(e)}")
                except Exception as e:
                    st.error(f"Lỗi: {str(e)}")

st.markdown("<br>", unsafe_allow_html=True)

# Project List Section
st.markdown(
    """
    <div class="pro-card-header">
        <span class="pro-card-title">📋 Danh Sách Dự Án Đã Tạo</span>
        <span class="pro-card-subtitle">Chọn dự án để vào Studio</span>
    </div>
    """,
    unsafe_allow_html=True,
)

projects = service.list_projects()
if not projects:
    st.info("Chưa có dự án nào. Tạo một dự án mới ở trên để bắt đầu làm việc.")
else:
    for p in projects:
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        with c1:
            st.markdown(f"### 🎬 {p.name}")
            if p.source_filename:
                st.caption(f"📁 Tệp nguồn: `{p.source_filename}`")
            if p.duration_seconds:
                st.caption(f"⏱ Thời lượng: `{p.duration_seconds:.2f}s` | Độ phân giải: `{p.width}x{p.height}`")
        with c2:
            status_tag = "rgba(16, 185, 129, 0.2)" if p.status.value == "completed" else "rgba(59, 130, 246, 0.2)"
            status_text_color = "#34d399" if p.status.value == "completed" else "#60a5fa"
            st.markdown(
                f"""
                <div style="margin-top: 10px;">
                    <span style="background: {status_tag}; color: {status_text_color}; padding: 4px 10px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">
                        {p.status.value.upper()}
                    </span>
                    <div style="font-size: 0.78rem; color: #94a3b8; margin-top: 6px;">
                        Tạo lúc: {p.created_at.strftime('%Y-%m-%d %H:%M')}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
            if st.button("Mở Studio", key=f"open_proj_{p.id}", use_container_width=True, type="primary"):
                st.session_state.project_id = p.id
                st.switch_page("pages/02_analysis.py")
        with c4:
            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
            if st.button("Xóa", key=f"del_proj_{p.id}", use_container_width=True):
                service.delete_project(p.id)
                st.rerun()
        st.markdown("<hr style='border-color: #1e293b; margin: 10px 0;'>", unsafe_allow_html=True)