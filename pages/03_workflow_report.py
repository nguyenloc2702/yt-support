# ruff: noqa: N999
"""Output timeline report and simple script-fit review."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from config.settings import settings
from core.database import init_db
from pipeline.script_matcher import match_script_to_report
from utils.ui import inject_pro_css, render_sidebar_hardware_status

st.set_page_config(page_title="Timeline Report", page_icon="🧭", layout="wide")
init_db()
inject_pro_css()
render_sidebar_hardware_status()

project_id = st.session_state.get("project_id")
if not project_id:
    st.warning("Chưa chọn project. Hãy bắt đầu từ Video Workflow.")
    st.stop()

project_dir = Path(settings.projects_dir) / project_id
report_path = project_dir / "analysis" / "timeline_report.json"
timeline_path = project_dir / "timelines" / "current.json"
source_path = project_dir / "source" / "input.mp4"

try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    st.error("Chưa có báo cáo timeline. Hãy chạy lại bước Tổng hợp & phân tích.")
    st.stop()

st.title("🧭 Báo cáo video đã tổng hợp")
st.caption("Các mốc bên dưới là timestamp của video đầu ra, không phải timestamp nguồn.")

if source_path.exists():
    st.video(str(source_path))

rows = report.get("rows") or []
metric_cols = st.columns(4)
metric_cols[0].metric("Số đoạn mô tả", len(rows))
metric_cols[1].metric("Thời lượng", f"{float(report.get('output_duration_seconds') or 0):.1f}s")
metric_cols[2].metric("Có speech", sum(1 for row in rows if row.get("has_speech")))
metric_cols[3].metric("Không rõ audio", sum(1 for row in rows if row.get("audio_state") == "noise_or_music_or_unknown"))

st.subheader("Mô tả theo mốc thời gian")
if not rows:
    st.info("Báo cáo chưa có đoạn nào.")
else:
    for row in rows:
        start = float(row.get("output_start") or 0)
        end = float(row.get("output_end") or 0)
        with st.expander(f"{start:07.2f}s → {end:07.2f}s", expanded=False):
            st.write(f"**Hình ảnh:** {row.get('visual_description') or 'Chưa có mô tả.'}")
            st.write(f"**Transcript:** {row.get('transcript') or '(không có thoại nhận diện được)'}")
            st.write(f"**Audio:** `{row.get('audio_state', 'unknown')}`")
            st.write(f"**Nguồn:** {float(row.get('source_start') or 0):.2f}s → {float(row.get('source_end') or 0):.2f}s")
            st.caption(f"Confidence: {row.get('confidence', 'unknown')}")

st.divider()
st.subheader("4. Dán script chuẩn để đối chiếu")
script_text = st.text_area(
    "Script chuẩn",
    height=220,
    placeholder="Dán script chính thức vào đây. Hệ thống chỉ đánh giá khớp/miss, không tự thay thế script của bạn.",
)

if st.button("🔎 Đối chiếu script", type="primary", disabled=not script_text.strip()):
    match_report = match_script_to_report(script_text, report)
    match_path = project_dir / "analysis" / "script_match_report.json"
    match_path.write_text(
        json.dumps(match_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    st.session_state["workflow_match_report"] = match_report

match_report = st.session_state.get("workflow_match_report")
if isinstance(match_report, dict):
    summary = match_report.get("summary") or {}
    st.subheader("Kết quả đối chiếu")
    mc = st.columns(4)
    mc[0].metric("Tổng đoạn", summary.get("total", 0))
    mc[1].metric("Khớp", summary.get("matched", 0))
    mc[2].metric("Khớp một phần", summary.get("partial", 0))
    mc[3].metric("Miss", summary.get("miss", 0))

    for issue in match_report.get("issues") or []:
        severity = str(issue.get("severity") or "medium").upper()
        title = (
            f"{severity} • {float(issue.get('start') or 0):.2f}s → "
            f"{float(issue.get('end') or 0):.2f}s • {issue.get('status', 'review')}"
        )
        if severity == "HIGH":
            st.error(title)
        elif severity == "MEDIUM":
            st.warning(title)
        else:
            st.info(title)
        st.caption(issue.get("message", ""))
        st.write(f"Script: {issue.get('script_text', '')}")
        st.write(f"Video/audio: {issue.get('evidence', '')}")

if st.button("➡️ Sang Preview / Xuất", use_container_width=True):
    st.switch_page("pages/07_export.py")
