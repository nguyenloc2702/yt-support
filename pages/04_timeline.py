import json
from pathlib import Path
import streamlit as st

from core.database import init_db
from services.project_service import ProjectService
from utils.ui import inject_pro_css, render_sidebar_hardware_status, render_studio_header

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Timeline Studio - Local Video Editor",
    page_icon="🎬",
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

timeline_path = Path(f"projects/{project_id}/timelines/current.json")

timeline_data = None
if timeline_path.exists():
    try:
        with open(timeline_path, "r", encoding="utf-8") as f:
            timeline_data = json.load(f)
    except Exception as e:
        st.error(f"Lỗi đọc file timeline: {e}")

version_val = timeline_data.get("version", 1.0) if timeline_data else 1.0
render_studio_header(
    project_name=f"{project.name}",
    resolution=f"{project.width}x{project.height}" if project.width else "",
    codec=f"Timeline v{version_val}",
    timecode=tc_str,
)

if not timeline_data or not timeline_data.get("clips"):
    st.info("Dự án hiện chưa có bản dựng Timeline (current.json).")
    
    analysis_dir = Path(f"projects/{project_id}/analysis")
    cand_file = analysis_dir / "candidates.json"
    ranked_file = analysis_dir / "ranked.json"

    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("⚡ Tạo Timeline từ kết quả phân tích", type="primary"):
            try:
                from pipeline.timeline_builder import build_timeline
                candidates = []
                target_file = ranked_file if ranked_file.exists() else cand_file
                if target_file.exists():
                    with open(target_file, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                        candidates = c_data if isinstance(c_data, list) else c_data.get("candidates", [])
                
                tl = build_timeline(
                    candidates=candidates,
                    mode="shorten",
                    target_duration=180.0,
                    source_duration=project.duration_seconds or 180.0,
                    preserve_source_order=True,
                )
                tl["project_id"] = project_id
                timeline_path.parent.mkdir(parents=True, exist_ok=True)
                with open(timeline_path, "w", encoding="utf-8") as f:
                    json.dump(tl, f, indent=2, ensure_ascii=False)
                st.success("Đã tạo Timeline thành công!")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi tạo timeline: {e}")
    with c2:
        if st.button("🚀 Chuyển đến trang Phân tích"):
            st.session_state.project_id = project_id
            st.switch_page("pages/02_analysis.py")
    st.stop()

# Compute actual statistics
clips = timeline_data.get("clips", [])
enabled_clips = [c for c in clips if c.get("enabled", True)]
total_output_sec = sum(max(0.0, c.get("source_end", 0.0) - c.get("source_start", 0.0)) for c in enabled_clips)
source_sec = project.duration_seconds or total_output_sec
saved_sec = max(0.0, source_sec - total_output_sec)

# Top Metrics Overview
st.markdown(
    f"""
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
        <div>
            <span style="font-size: 1.15rem; font-weight: 700; color: #ffffff;">Tổng quan bản dựng (Rough Cut)</span>
            <span style="background: #1e293b; color: #93c5fd; padding: 2px 8px; border-radius: 4px; font-size: 0.78rem; margin-left: 8px;">
                Chế độ: {timeline_data.get('mode', 'shorten')}
            </span>
        </div>
        <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.76rem; font-weight: 600; padding: 3px 10px; border-radius: 4px;">
            ✓ Sẵn sàng xuất bản
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown(
        f"""
        <div class="pro-metric-box">
            <div class="pro-metric-title">Thời lượng nguồn gốc</div>
            <div class="pro-metric-val">{int(source_sec//60):02d}:{int(source_sec%60):02d}</div>
            <div style="font-size: 0.7rem; color: #94a3b8;">{source_sec:.1f} giây</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with m2:
    st.markdown(
        f"""
        <div class="pro-metric-box">
            <div class="pro-metric-title">Thời lượng sau biên tập</div>
            <div class="pro-metric-val" style="color: #34d399;">{int(total_output_sec//60):02d}:{int(total_output_sec%60):02d}</div>
            <div class="pro-metric-sub">{total_output_sec:.1f} giây</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with m3:
    st.markdown(
        f"""
        <div class="pro-metric-box">
            <div class="pro-metric-title">Số phân đoạn clip</div>
            <div class="pro-metric-val">{len(enabled_clips)} <span style="font-size: 0.75rem; color: #94a3b8;">/ {len(clips)} clips</span></div>
            <div style="font-size: 0.7rem; color: #94a3b8;">Đang kích hoạt</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with m4:
    st.markdown(
        f"""
        <div class="pro-metric-box">
            <div class="pro-metric-title">Thời lượng rút gọn</div>
            <div class="pro-metric-val" style="color: #38bdf8;">-{saved_sec:.1f}s</div>
            <div style="font-size: 0.7rem; color: #94a3b8;">Giảm {(saved_sec/max(1, source_sec)*100):.1f}% thời gian</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)

# Data Table / Editor
st.markdown(
    """
    <div class="pro-card" style="padding: 14px; margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span class="pro-card-title">📋 Danh sách các phân đoạn trên Timeline</span>
        </div>
    """,
    unsafe_allow_html=True,
)

# Convert clips to editable format
table_data = []
for idx, c in enumerate(clips):
    start = c.get("source_start", 0.0)
    end = c.get("source_end", 0.0)
    dur = max(0.0, end - start)
    table_data.append({
        "Bật": c.get("enabled", True),
        "Thứ tự": c.get("order", idx + 1),
        "Mã Clip": c.get("id", f"clip_{idx+1}"),
        "Bắt đầu (s)": round(start, 2),
        "Kết thúc (s)": round(end, 2),
        "Thời lượng (s)": round(dur, 2),
        "Nhãn": c.get("label", "other"),
    })

edited_df = st.data_editor(
    table_data,
    disabled=["Thứ tự", "Mã Clip", "Thời lượng (s)"],
    use_container_width=True,
    key="timeline_editor_table",
)

st.markdown("</div>", unsafe_allow_html=True)

col_act_left, col_act_right = st.columns([1, 1])
with col_act_left:
    if st.button("💾 Lưu thay đổi Timeline", use_container_width=True):
        try:
            for i, row in enumerate(edited_df):
                if i < len(clips):
                    clips[i]["enabled"] = bool(row["Bật"])
                    clips[i]["source_start"] = float(row["Bắt đầu (s)"])
                    clips[i]["source_end"] = float(row["Kết thúc (s)"])
                    clips[i]["label"] = str(row["Nhãn"])
            
            # Recalculate actual_duration
            active = [c for c in clips if c.get("enabled", True)]
            timeline_data["actual_duration"] = sum(max(0.0, c["source_end"] - c["source_start"]) for c in active)
            timeline_data["clips"] = clips

            with open(timeline_path, "w", encoding="utf-8") as f:
                json.dump(timeline_data, f, indent=2, ensure_ascii=False)
            st.success("Đã cập nhật và lưu Timeline thành công!")
            st.rerun()
        except Exception as e:
            st.error(f"Lỗi khi lưu timeline: {e}")

with col_act_right:
    if st.button("🚀 Tiến hành xuất video (Export Studio)", type="primary", use_container_width=True):
        st.session_state.project_id = project_id
        st.switch_page("pages/05_export.py")