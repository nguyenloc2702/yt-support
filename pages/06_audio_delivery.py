"""Audio delivery review page: upload → validate fit → approve per block."""

import time
from pathlib import Path

import streamlit as st

from core.database import init_db
from services.audio_delivery_service import AudioDeliveryService
from services.project_service import ProjectService
from utils.ui import (
    inject_pro_css,
    render_sidebar_hardware_status,
    render_studio_header,
)

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Audio Delivery - Local Video Editor",
    page_icon="🎧",
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

delivery_service = AudioDeliveryService(projects_dir=Path("projects"))

# ---------------------------------------------------------------------------
# Sidebar & Header
# ---------------------------------------------------------------------------
render_sidebar_hardware_status(whisper_status="Sẵn sàng", is_busy=False)

_total_seconds = project.duration_seconds or 0.0
_hours = int(_total_seconds // 3600)
_mins = int((_total_seconds % 3600) // 60)
_secs = int(_total_seconds % 60)
_frames = int((_total_seconds - int(_total_seconds)) * max(project.fps or 25, 1))
_timecode = f"{_hours:02d}:{_mins:02d}:{_secs:02d}:{_frames:02d}"
_res_text = f"{project.width}x{project.height}" if project.width else "—"

render_studio_header(
    project_name=f"{project.name}",
    resolution=f"{_res_text} • {(project.fps or 0):.0f}fps" if project.fps else _res_text,
    codec=project.__dict__.get("video_codec") or "—",
    timecode=_timecode,
)

# ---------------------------------------------------------------------------
# Load document & summary
# ---------------------------------------------------------------------------
try:
    doc = delivery_service.get_document(project_id)
except Exception as exc:
    st.error(f"Không tải được tài liệu phân tích: {exc}")
    st.info("Hãy chạy **Phân tích theo khối** ở tab Media Analysis trước.")
    st.stop()

summary = delivery_service.get_summary(project_id)

sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric("Tổng số khối", summary["total_blocks"])
sc2.metric("Đã tải audio", summary["delivered"])
sc3.metric("Đã duyệt audio", summary["approved"])
sc4.metric("Còn thiếu", summary["pending"])

if summary["all_ready"]:
    st.success("✅ Tất cả audio đã được duyệt. Sẵn sàng chuyển sang bước Render Timeline.")
else:
    st.warning(f"⏳ Còn {summary['pending']} khối cần tải hoặc duyệt audio.")

st.divider()

tab_upload, tab_review = st.tabs(["📤 Tải lên & Kiểm tra", "✔ Duyệt từng khối"])

# ===========================================================================
# TAB 1 – Upload & Validate
# ===========================================================================
with tab_upload:
    st.subheader("Tải file audio cho khối nội dung")
    st.caption(
        "Chọn khối chưa có audio (hoặc muốn thay thế), tải tệp .wav/.mp3, "
        "hệ thống sẽ đo thời lượng thật và so sánh với khung thời gian mong đợi."
    )

    # Blocks needing audio or already uploaded (to replace)
    candidate_blocks = [b for b in doc.content_blocks]
    if not candidate_blocks:
        st.info("Không có khối nào trong tài liệu.")
        st.stop()

    block_labels = []
    for b in candidate_blocks:
        status_tag = ""
        if b.audio_delivery is None:
            status_tag = " ⏳"
        elif not b.audio_delivery.approved:
            status_tag = " 📦"
        else:
            status_tag = " ✅"
        dur_s = b.duration_ms / 1000.0
        block_labels.append(
            f"{b.block_id}{status_tag} · {dur_s:.1f}s ({b.start_ms}→{b.end_ms}ms)"
        )

    sel_idx = st.selectbox(
        "Khối đích",
        range(len(block_labels)),
        format_func=lambda i: block_labels[i],
    )
    selected_block = candidate_blocks[sel_idx]

    uploaded_file = st.file_uploader(
        "Chọn tệp âm thanh (.wav, .mp3, .m4a)",
        type=["wav", "mp3", "m4a"],
        key=f"upl_{selected_block.block_id}",
    )

    fit_mode = st.radio(
        "Chế độ khớp thời lượng",
        ["preserve_speed", "stretch", "compress"],
        horizontal=True,
        help=(
            "preserve_speed: giữ tốc độ, cắt/kéo dài bằng silence padding; "
            "stretch/compress: thay đổi tempo để vừa khít khung."
        ),
    )

    col_btn, col_msg = st.columns([1, 3])
    with col_btn:
        submit_btn = st.button("🚀 Tải lên & Đo lường", use_container_width=True, type="primary")

    if submit_btn and uploaded_file is not None:
        # Save to disk under projects/<id>/audio_deliveries/
        audio_dir = Path("projects") / project_id / "audio_deliveries"
        audio_dir.mkdir(parents=True, exist_ok=True)
        dest_path = audio_dir / f"{selected_block.block_id}_{uploaded_file.name}"
        with open(dest_path, "wb") as fh:
            fh.write(uploaded_file.getvalue())

        # Measure duration via ffprobe
        try:
            from pipeline.tts import _probe_duration
            duration_sec = _probe_duration(dest_path)
            actual_ms = int(round(duration_sec * 1000))
        except Exception as exc:
            st.error(f"Không đo được thời lượng tệp: {exc}")
            st.stop()

        try:
            doc = delivery_service.upload_audio(
                project_id,
                selected_block.block_id,
                file_path=str(dest_path),
                actual_duration_ms=actual_ms,
                fit_mode=fit_mode,
            )
            st.toast(f"Đã tải audio cho {selected_block.block_id}.", icon="📦")
            time.sleep(0.5)
            st.rerun()
        except Exception as exc:
            st.error(f"Lỗi khi ghi nhận audio: {exc}")

    elif submit_btn and uploaded_file is None:
        st.error("Vui lòng chọn tệp âm thanh trước.")

    # Show validation result for currently selected block if it has audio
    if selected_block.audio_delivery is not None:
        st.divider()
        st.markdown(f"**Kiểm tra khớp cho `{selected_block.block_id}`:**")
        try:
            fit_result = delivery_service.validate_audio_fit(project_id, selected_block.block_id)
            fc1, fc2 = st.columns([1, 3])
            with fc1:
                st.metric("Δ lệch (ms)", f"{fit_result['delta_ms']:+d}")
            with fc2:
                if fit_result["ok"]:
                    st.success(fit_result["message"])
                else:
                    st.warning(fit_result["message"])
        except Exception as exc:
            st.error(f"Lỗi kiểm tra: {exc}")

# ===========================================================================
# TAB 2 – Review / Approve per block
# ===========================================================================
with tab_review:
    st.subheader("Duyệt audio theo khối")
    st.caption("Nghe thử, xác nhận khớp thời lượng, rồi bấm Duyệt. Có thể Thu hồi duyệt nếu cần làm lại.")

    pending_or_uploaded = [
        b for b in doc.content_blocks
        if b.audio_delivery is not None
    ]

    if not pending_or_uploaded:
        st.info("Chưa có khối nào được tải audio. Sang tab **📤 Tải lên & Kiểm tra** trước.")
        st.stop()

    for block in pending_or_uploaded:
        ad = block.audio_delivery
        badge = "✅ Đã duyệt" if ad.approved else "📦 Chờ duyệt"
        head = f"{badge} · {block.block_id} · {block.duration_ms/1000:.1f}s"
        expanded = not ad.approved

        with st.expander(head, expanded=expanded):
            left, right = st.columns([1.2, 1.0])

            with left:
                st.markdown("**Thông tin audio**")
                st.write(f"- Đường dẫn: `{ad.file_path}`")
                st.write(f"- Thời lượng thật: **{ad.actual_duration_ms} ms**")
                st.write(f"- Khung mong đợi: **{ad.expected_start_ms} → {ad.expected_end_ms} ms** "
                         f"(={ad.expected_duration_ms} ms)")
                st.write(f"- Chế độ khớp: `{ad.fit_mode}`")
                st.write(f"- Gap trước đó: {ad.gap_ms} ms | Overlap sau: {ad.overlap_ms} ms")

                # Inline playback if file exists
                p = Path(ad.file_path)
                if p.exists():
                    try:
                        st.audio(str(p), format="audio/wav")
                    except Exception:
                        st.caption("(không phát được trực tiếp — hãy mở file ngoài)")
                else:
                    st.warning("Tệp audio không tồn tại trên đĩa.")

            with right:
                st.markdown("**Hành động**")
                btn_row = st.columns(2)
                with btn_row[0]:
                    if st.button("✔ Duyệt audio", key=f"appr_{block.block_id}",
                                 disabled=ad.approved, use_container_width=True, type="primary"):
                        try:
                            delivery_service.approve_audio(project_id, block.block_id)
                            st.toast(f"Đã duyệt audio cho {block.block_id}.", icon="✅")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi duyệt: {exc}")
                with btn_row[1]:
                    if st.button("↩ Thu hồi duyệt", key=f"rev_{block.block_id}",
                                 disabled=not ad.approved, use_container_width=True):
                        try:
                            delivery_service.revoke_approval(project_id, block.block_id)
                            st.toast(f"Đã thu hồi duyệt cho {block.block_id}.", icon="♻")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi thu hồi: {exc}")

                # Quick fit check button
                if st.button("🔍 Kiểm tra lại khớp", key=f"chk_{block.block_id}", use_container_width=True):
                    try:
                        res = delivery_service.validate_audio_fit(project_id, block.block_id)
                        if res["ok"]:
                            st.success(res["message"])
                        else:
                            st.warning(f"{res['message']} (Δ={res['delta_ms']:+d}ms)")
                    except Exception as exc:
                        st.error(f"Lỗi kiểm tra: {exc}")

    # Bottom bulk action bar
    st.divider()
    all_approved = all(b.audio_delivery and b.audio_delivery.approved for b in doc.content_blocks if b.audio_delivery)
    bc1, bc2 = st.columns([2, 1])
    with bc1:
        if all_approved:
            st.success("Tất cả audio của các khối đã tải đều được duyệt ✅")
        else:
            remaining = sum(1 for b in doc.content_blocks if b.audio_delivery and not b.audio_delivery.approved)
            st.warning(f"Còn {remaining} khối đã tải nhưng chưa duyệt.")
    with bc2:
        if st.button("🎉 Xác nhận toàn bộ audio", type="primary",
                     disabled=not all_approved, use_container_width=True):
            st.toast("Đánh dấu hoàn tất giai đoạn giao audio.", icon="🎧")
            # Future: set a global flag on the document / navigate to timeline render
            st.rerun()