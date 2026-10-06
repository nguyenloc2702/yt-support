"""Script review page: generate → review → edit → approve gate → timeline."""

import streamlit as st

from core.exceptions import AppError
from core.logging_config import get_logger
from services.script_service import ScriptService

logger = get_logger(__name__)

st.set_page_config(page_title="Script Review", page_icon="📝", layout="wide")
st.title("📝 Script Review (AI Rewrite)")

service = ScriptService()

# ---- project selection ----
from services.project_service import ProjectService

projects = ProjectService().list_projects()
if not projects:
    st.info("Chưa có project nào. Hãy tạo project ở trang Projects.")
    st.stop()

projects = [p for p in projects if p.status not in ("new",)]
if not projects:
    st.info("Chưa có project nào đã phân tích. Chạy phân tích trước.")
    st.stop()

project = st.selectbox(
    "Chọn project",
    projects,
    format_func=lambda p: f"{p.name} ({p.id[:8]})",
)
project_id = project.id
script = service.get_script(project_id)

# ---- status badge ----
STATUS_BADGE = {
    "draft": "🟡 DRAFT — chờ review",
    "edited": "🟠 EDITED — đã chỉnh, chờ duyệt lại",
    "approved": "🟢 APPROVED — đã duyệt, có thể tạo timeline",
    "rejected": "🔴 REJECTED — đã từ chối",
}

# ---- generate ----
st.subheader("1. Tạo script bằng AI")

# Duration controls (always visible so user can regenerate with a new target)
g1, g2, g3 = st.columns([1, 1, 1.4])
with g1:
    target_preset = st.selectbox(
        "Thời lượng mục tiêu",
        options=[30, 60, 90, 120, 180],
        index=1,
        format_func=lambda s: f"{s} giây",
    )
with g2:
    tolerance = st.number_input(
        "Dung sai (giây)", min_value=1.0, max_value=10.0, value=2.0, step=0.5,
    )
with g3:
    tone = st.text_input(
        "Tông giọng mong muốn",
        value="bựa bựa, hài hước, bắt trend",
        help="Mô tả giọng văn review mong muốn.",
    )

if script is None:
    if st.button("✨ Generate script", type="primary"):
        try:
            with st.spinner("AI đang viết script... (có thể mất 30-60s)"):
                script = service.generate(
                    project_id,
                    tone=tone,
                    target_duration_seconds=float(target_preset),
                    duration_tolerance_seconds=float(tolerance),
                )
            st.success("Đã tạo script! Hãy review bên dưới.")
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
        except Exception as exc:
            logger.exception("Script generation failed")
            st.error(f"Lỗi không xác định: {exc}")
else:
    st.info(f"Trạng thái script: **{STATUS_BADGE.get(script.get('status'), script.get('status'))}**")

    # Duration check panel
    check = service.duration_check(project_id)
    if check:
        within = check["within_tolerance"]
        icon = "✅" if within else "⚠️"
        st.markdown(
            f"""{icon} **Kiểm tra thời lượng**
- Yêu cầu: **{check['target_duration_seconds']:.0f}s** (dung sai ±{script.get('duration_tolerance_seconds', 2):.0f}s)
- Ước tính lời đọc: **{check['estimated_duration_seconds']:.1f}s**
- Sai số: **{check['delta_seconds']:+.1f}s** — {'ĐẠT' if within else 'VƯỢT dung sai'}
- Số chữ: **{sum(len(str(b.get('text') or '').split()) for b in script.get('beats', []))}**"""
        )
        if not within:
            st.warning("Script đang lệch target. Hãy chỉnh sửa các beat ở tab 'Chỉnh sửa' hoặc Generate lại.")

    # Regenerate with new duration
    with st.expander("🔄 Generate lại với thời lượng mới"):
        if st.button("✨ Generate lại (xóa script hiện tại)", key="regen_btn"):
            try:
                with st.spinner("AI đang viết lại script... (có thể mất 30-60s)"):
                    script = service.generate(
                        project_id,
                        tone=tone,
                        target_duration_seconds=float(target_preset),
                        duration_tolerance_seconds=float(tolerance),
                    )
                st.success("Đã tạo lại script!")
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.exception("Script regeneration failed")
                st.error(f"Lỗi không xác định: {exc}")

if script is None:
    st.stop()

beats = script.get("beats", [])

# ---- review: compare view ----
st.subheader("2. Review script")

tab_original, tab_edit = st.tabs(["👀 Xem script", "✏️ Chỉnh sửa"])

with tab_original:
    st.markdown(f"### {script.get('title', '')}")
    st.caption(f"Tông giọng: {script.get('tone', '')}")
    seg_text = {}
    try:
        from services.script_service import ScriptService as _S
        transcript = _S._load_transcript(_S._project_dir(project_id))
        seg_text = {
            s["id"]: s.get("text", "") for s in transcript.get("segments", [])
            if s.get("id") is not None
        }
    except AppError as exc:
        st.warning(f"Không tải được transcript để hiển thị nguồn gốc: {exc}")
    except Exception:
        logger.exception("Failed to load transcript for display")

    for beat in beats:
        with st.expander(f"Beat {beat['id'] + 1} · {beat.get('emotion', 'neutral')}"):
            st.markdown(f"**Voiceover:** {beat.get('text', '')}")
            st.markdown("**Nguồn gốc (transcript):**")
            for sid in beat.get("source_segment_ids", []):
                st.caption(f"[{sid}] {seg_text.get(sid, '(không tìm thấy)')}")

with tab_edit:
    st.caption("Sửa nội dung beat. Lưu sẽ chuyển trạng thái về EDITED (phải duyệt lại).")
    new_title = st.text_input("Tiêu đề", value=script.get("title", ""), key="edit_title")
    edited_beats = []
    for beat in beats:
        with st.container(border=True):
            col_no, col_text, col_del = st.columns([0.08, 0.82, 0.10])
            with col_no:
                st.markdown(f"**{beat['id'] + 1}**")
            with col_text:
                new_text = st.text_area(
                    "Nội dung",
                    value=beat.get("text", ""),
                    key=f"beat_text_{beat['id']}",
                    label_visibility="collapsed",
                )
            with col_del:
                keep = st.checkbox("Giữ", value=True, key=f"beat_keep_{beat['id']}")
            edited_beats.append(
                {**beat, "text": new_text, "_keep": keep}
            )
    if st.button("💾 Lưu chỉnh sửa", key="save_edits"):
        kept = [
            {k: v for k, v in b.items() if not k.startswith("_")}
            for b in edited_beats if b["_keep"]
        ]
        # Re-number beat ids sequentially so beat_mapper stays consistent.
        for new_id, b in enumerate(kept):
            b["id"] = new_id
        try:
            service.save_edits(project_id, kept, title=new_title or None)
            st.success("Đã lưu. Trạng thái: EDITED — hãy duyệt lại.")
            st.rerun()
        except AppError as exc:
            st.error(str(exc))

# ---- confirm gate ----
st.subheader("3. Duyệt script (Confirm gate)")
c1, c2, c3 = st.columns(3)
with c1:
    if st.button("✅ Approve & tạo timeline", type="primary", use_container_width=True):
        try:
            service.approve(project_id)
            with st.spinner("Đang map beats sang clips..."):
                timeline = service.build_script_timeline(project_id)
            # save timeline via render_config_service pattern: write to timelines/
            from config.settings import settings as _settings
            from pathlib import Path as _Path
            from utils.json_io import atomic_write_json
            tl_dir = _Path(_settings.projects_dir) / project_id / "timelines"
            atomic_write_json(tl_dir / "current.json", timeline)
            st.success(
                f"✅ Đã duyệt & tạo timeline: {len(timeline['clips'])} clips, "
                f"{timeline['actual_duration']:.1f}s"
            )
        except AppError as exc:
            st.error(str(exc))
with c2:
    if st.button("✏️ Approve chỉ trạng thái", use_container_width=True,
                 help="Duyệt mà chưa tạo timeline ngay"):
        try:
            service.approve(project_id)
            st.success("Đã duyệt script.")
            st.rerun()
        except AppError as exc:
            st.error(str(exc))
with c3:
    if st.button("❌ Reject (yêu cầu tạo lại)", use_container_width=True):
        try:
            service.reject(project_id)
            st.warning("Đã reject. Bạn có thể generate lại ở bước 1.")
            st.rerun()
        except AppError as exc:
            st.error(str(exc))

if service.get_status(project_id) != "approved":
    st.warning(
        "🔒 **Gate:** script phải được Approve trước khi tạo timeline / render. "
        "Đây là bước bắt buộc để bạn kiểm soát nội dung cuối cùng."
    )
