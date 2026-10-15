"""Analysis JSON I/O page: export/import AnalysisDocument (Phase 11)."""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from core.database import init_db
from services.media_analysis_service import MediaAnalysisService
from services.project_service import ProjectService
from utils.ui import (
    inject_pro_css,
    render_sidebar_hardware_status,
    render_studio_header,
)

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Analysis JSON I/O - Local Video Editor",
    page_icon="💾",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------
if "project_id" not in st.session_state:
    _ps = ProjectService()
    _projs = _ps.list_projects()
    if _projs:
        st.session_state.project_id = _projs[0].id
    else:
        st.error("Chưa chọn project. Vui lòng vào trang Projects và tạo/mở một project.")
        st.stop()

project_id = st.session_state.project_id
project_service = ProjectService()

try:
    project = project_service.get_project(project_id)
except Exception as exc:
    st.error(f"Không tìm thấy project: {exc}")
    st.stop()

media_service = MediaAnalysisService(projects_dir=Path("projects"))

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
render_sidebar_hardware_status(whisper_status="Sẵn sàng", is_busy=False)
render_studio_header(
    project_name=f"{project.name}",
    resolution="—" if not project.width else f"{project.width}x{project.height}",
    codec=project.__dict__.get("video_codec") or "—",
    timecode="00:00:00:00",
)

st.title("💾 Xuất / Nhập tài liệu phân tích (JSON)")
st.caption(
    "Xuất toàn bộ AnalysisDocument ra tệp JSON để sao lưu hoặc chuyển giữa các máy, "
    "và nhập lại với kiểm định schema chặt (Pydantic)."
)

doc = media_service.load_existing_document(project_id)

# ===========================================================================
# Export
# ===========================================================================
st.subheader("📤 Xuất JSON")
if doc is None:
    st.info("Chưa có tài liệu phân tích cho project này.")
else:
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("⬇ Tạo tệp JSON", type="primary", use_container_width=True):
            try:
                json_str = media_service.export_document_json(project_id)
                exports_dir = Path("projects") / project_id / "exports"
                exports_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = exports_dir / f"analysis_document_{stamp}.json"
                out_path.write_text(json_str, encoding="utf-8")
                st.session_state["last_export_path"] = str(out_path)
                st.session_state["last_export_json"] = json_str
                st.toast(f"Đã xuất: {out_path.name}", icon="💾")
            except Exception as exc:
                st.error(f"Lỗi xuất: {exc}")

    if "last_export_path" in st.session_state:
        st.success(f"Đã lưu tại: `{st.session_state['last_export_path']}`")
        st.download_button(
            "⬇ Tải xuống tệp JSON",
            data=st.session_state.get("last_export_json", ""),
            file_name=Path(st.session_state["last_export_path"]).name,
            mime="application/json",
        )
    # Preview
    with st.expander("Xem trước JSON (500 ký tự đầu)", expanded=False):
        preview = media_service.export_document_json(project_id)
        st.code(preview[:500] + ("…" if len(preview) > 500 else ""), language="json")

st.divider()

# ===========================================================================
# Import
# ===========================================================================
st.subheader("📥 Nhập JSON")
st.warning(
    "Nhập sẽ **ghi đè** tài liệu phân tích hiện tại của project "
    "(bao gồm script đã duyệt và audio delivery). Hãy xuất bản sao lưu trước."
)

uploaded = st.file_uploader("Chọn tệp analysis_document*.json", type=["json"])
import_mode = st.radio(
    "Chế độ nhập",
    ["Xem trước (dry-run)", "Ghi đè thật"],
    horizontal=True,
)

if uploaded is not None:
    raw = uploaded.getvalue().decode("utf-8", errors="replace")
    with st.expander("Nội dung tệp (500 ký tự đầu)", expanded=False):
        st.code(raw[:500] + ("…" if len(raw) > 500 else ""), language="json")

    if import_mode == "Xem trước (dry-run)":
        if st.button("🔬 Kiểm định schema (không lưu)", type="primary"):
            try:
                data = json.loads(raw)
                # Validate only — construct without persisting
                from core.analysis_schemas import AnalysisDocument as AD
                candidate = AD(**data)
                st.success(
                    f"Schema hợp lệ ✅ — {len(candidate.content_blocks)} khối, "
                    f"analysis_id `{candidate.analysis_id[:8]}…`"
                )
            except json.JSONDecodeError as exc:
                st.error(f"JSON hỏng: {exc}")
            except Exception as exc:
                st.error(f"Schema không hợp lệ: {exc}")
    else:
        confirm = st.checkbox("Tôi hiểu rằng tài liệu hiện tại sẽ bị ghi đè.")
        if st.button("📥 Nhập & Ghi đè", type="primary", disabled=not confirm):
            try:
                saved = media_service.import_document_json(project_id, raw)
                st.success(
                    f"Đã nhập tài liệu: {len(saved.content_blocks)} khối "
                    f"(project_id đã được ép thành `{project_id}`)."
                )
                st.toast("Nhập thành công.", icon="✅")
            except Exception as exc:
                st.error(f"Lỗi nhập: {exc}")

st.divider()
st.caption(
    "Schema: `core.analysis_schemas.AnalysisDocument` • Xuất/Nhập qua "
    "`MediaAnalysisService.export_document_json` / `import_document_json`."
)
