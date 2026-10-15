"""Media analysis review page: run block-based analysis → review/edit blocks → verify gate."""

import time
from datetime import UTC
from pathlib import Path

import streamlit as st

from core.database import init_db
from services.job_service import JobService
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
    page_title="Media Analysis Review - Local Video Editor",
    page_icon="🔬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Project resolution (same pattern as other pages)
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
job_service = JobService()

try:
    project = project_service.get_project(project_id)
except Exception as exc:
    st.error(f"Không tìm thấy project: {exc}")
    st.stop()

media_service = MediaAnalysisService(projects_dir=Path("projects"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_tc(ms: int) -> str:
    """Format milliseconds as HH:MM:SS.mmm."""
    total_s = ms / 1000.0
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    s = int(total_s % 60)
    frac = int(round((total_s - int(total_s)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d}.{frac:03d}"


def _load_document():
    """Load persisted AnalysisDocument for this project, or None."""
    try:
        return media_service.load_existing_document(project_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sidebar hardware status
# ---------------------------------------------------------------------------
is_busy = bool(st.session_state.get("block_analysis_job_id"))
render_sidebar_hardware_status(
    whisper_status="Đang phân tích" if is_busy else "Sẵn sàng",
    is_busy=is_busy,
)

# Studio header
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

doc = _load_document()

tab_run, tab_review, tab_stats = st.tabs(
    ["▶️ Chạy phân tích", "✍️ Duyệt khối nội dung", "📈 Thống kê"]
)

# ===========================================================================
# TAB 1 – Run analysis
# ===========================================================================
with tab_run:
    st.subheader("Phân tích theo khối (Block-Based Analysis)")
    st.caption(
        "Pipeline trích xuất metadata → phát hiện cảnh → phiên âm & sự kiện audio → "
        "gom thành các khối nội dung để người dùng duyệt từng phần."
    )

    with st.form("block_analysis_options"):
        c1, c2 = st.columns(2)
        with c1:
            language = st.selectbox(
                "Ngôn ngữ",
                ["auto", "vi", "en"],
                index=0,
                help="'auto' để Whisper tự nhận diện.",
            )
            whisper_model = st.selectbox(
                "Mô hình Whisper",
                ["base", "small", "medium", "large-v3"],
                index=1,
            )
        with c2:
            min_scene_length = st.number_input(
                "Thời lượng tối thiểu mỗi cảnh (giây)",
                min_value=0.1, max_value=10.0, value=0.5, step=0.1,
            )
            min_block_duration_ms = st.number_input(
                "Thời lượng tối thiểu mỗi khối (ms)",
                min_value=200, max_value=10000, value=1000, step=100,
            )
        use_cached = st.checkbox(
            "Dùng cache nếu có kết quả cũ",
            value=True,
            help="Bỏ chọn để ép chạy lại toàn bộ pipeline.",
        )
        submitted = st.form_submit_button("🚀 Bắt đầu phân tích", type="primary")

    if submitted:
        source_path = Path("projects") / project_id / "source" / "input.mp4"
        if not source_path.exists():
            st.error(f"Không tìm thấy file nguồn: {source_path}")
        else:
            progress_bar = st.progress(0, text="Khởi động...")
            status_placeholder = st.empty()
            log_lines: list[str] = []

            def _on_progress(pct: int, msg: str) -> None:
                progress_bar.progress(min(max(pct, 0), 100), text=msg)
                log_lines.append(f"[{pct:>3}%] {msg}")
                status_placeholder.code("\n".join(log_lines[-15:]) or "(đang chờ...)")

            t0 = time.time()
            try:
                doc = media_service.run_full_analysis(
                    project_id,
                    source_path=source_path,
                    progress_callback=_on_progress,
                    options={
                        "language": None if language == "auto" else language,
                        "whisper_model": whisper_model,
                        "min_scene_length": float(min_scene_length),
                        "min_block_duration_ms": int(min_block_duration_ms),
                        "use_cached": bool(use_cached),
                    },
                )
                elapsed = time.time() - t0
                progress_bar.progress(100, text=f"✅ Hoàn tất trong {elapsed:.1f}s")
                st.success(
                    f"Đã tạo {len(doc.content_blocks)} khối từ {len(doc.shots)} cảnh "
                    f"và {len(doc.transcript_segments)} đoạn phiên âm."
                )
                st.balloons()
                time.sleep(0.8)
                st.rerun()
            except Exception as exc:
                progress_bar.empty()
                st.error(f"Phân tích thất bại: {exc}")
                st.exception(exc)

    # Show existing document summary if present
    if doc is not None and not submitted:
        st.divider()
        st.markdown("**Kết quả đã lưu:**")
        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Khối nội dung", len(doc.content_blocks))
        sm2.metric("Cảnh", len(doc.shots))
        sm3.metric("Đoạn thoại", len(doc.transcript_segments))
        sm4.metric("Sự kiện audio", len(doc.audio_events))
        st.caption(
            f"Schema v{doc.schema_version} • Phân tích ID `{doc.analysis_id[:8]}…` • "
            f"Tạo lúc {doc.created_at.strftime('%Y-%m-%d %H:%M')}"
        )

# ===========================================================================
# TAB 2 – Review / edit blocks
# ===========================================================================
with tab_review:
    if doc is None:
        st.info("Chưa có kết quả phân tích. Hãy sang tab **▶️ Chạy phân tích** trước.")
        st.stop()

    if not doc.content_blocks:
        st.warning("Tài liệu không chứa khối nào. Kiểm tra lại tham số hoặc video nguồn.")
        st.stop()

    # Global controls row
    ctrl_c1, ctrl_c2, ctrl_c3 = st.columns([1, 1, 3])
    with ctrl_c1:
        filter_mode = st.radio(
            "Lọc",
            ["Tất cả", "Chờ duyệt", "Đã duyệt"],
            horizontal=False,
            label_visibility="collapsed",
        )
    with ctrl_c2:
        if st.button("💾 Lưu toàn bộ thay đổi", use_container_width=True):
            media_service.save_document(doc)
            st.toast("Đã lưu tài liệu phân tích.", icon="✅")
            st.rerun()
    with ctrl_c3:
        verified_count = sum(1 for b in doc.content_blocks if b.human_verified)
        st.metric("Tiến độ duyệt", f"{verified_count}/{len(doc.content_blocks)}")

    # Filter list
    if filter_mode == "Chờ duyệt":
        visible_blocks = [b for b in doc.content_blocks if not b.human_verified]
    elif filter_mode == "Đã duyệt":
        visible_blocks = [b for b in doc.content_blocks if b.human_verified]
    else:
        visible_blocks = list(doc.content_blocks)

    if not visible_blocks:
        st.info(f"Không có khối nào thuộc bộ lọc '{filter_mode}'.")
        st.stop()

    # Render each block as an expander card
    for idx, block in enumerate(visible_blocks):
        badge = "✅" if block.human_verified else "⏳"
        dur_s = block.duration_ms / 1000.0
        head = (
            f"{badge} Khối #{idx + 1} · {_ms_to_tc(block.start_ms)} → "
            f"{_ms_to_tc(block.end_ms)} ({dur_s:.1f}s)"
        )
        if block.confidence > 0:
            head += f" · tin cậy {block.confidence:.0%}"

        expanded = (not block.human_verified) and idx < 3
        with st.expander(head, expanded=expanded):
            inner_l, inner_r = st.columns([1.1, 1.0], gap="medium")

            # ---- Left column: source references --------------------------
            with inner_l:
                st.markdown("**🎞️ Hình ảnh (shots)**")
                if block.shot_ids:
                    shot_map = {s.shot_id: s for s in doc.shots}
                    for sid in block.shot_ids:
                        sh = shot_map.get(sid)
                        if sh is None:
                            continue
                        desc = sh.description.strip() if sh.description else ""
                        line = (
                            f"- `{sid}` [{_ms_to_tc(sh.start_ms)}→{_ms_to_tc(sh.end_ms)}]"
                        )
                        if desc:
                            line += f" — {desc}"
                        st.markdown(line)
                else:
                    st.caption("(không có shot gán cho khối này)")

                st.markdown("**🔊 Sự kiện audio**")
                if block.audio_event_ids:
                    ev_map = {e.event_id: e for e in doc.audio_events}
                    for eid in block.audio_event_ids:
                        ev = ev_map.get(eid)
                        if ev is None:
                            continue
                        etype = getattr(ev.type, "value", str(ev.type))
                        st.markdown(
                            f"- `{eid}` **{etype}** "
                            f"[{_ms_to_tc(ev.start_ms)}→{_ms_to_tc(ev.end_ms)}]"
                        )
                else:
                    st.caption("(không có sự kiện)")

                st.markdown("**🗣️ Diễn giả liên quan**")
                if block.speaker_ids:
                    sp_map = {s.speaker_id: s for s in doc.speakers}
                    for spid in block.speaker_ids:
                        sp = sp_map.get(spid)
                        label = (sp.label if sp and sp.label else spid) if sp else spid
                        st.markdown(f"- {label}")
                else:
                    st.caption("(chưa xác định)")

            # ---- Right column: editable transcript / script --------------
            with inner_r:
                st.markdown("**📝 Transcript gốc**")
                st.text_area(
                    "transcript_src_" + block.block_id,
                    value=block.transcript,
                    height=120,
                    disabled=True,
                    label_visibility="collapsed",
                )

                st.markdown("**✒️ Script cuối cùng (biên tập được)**")
                edited_script = st.text_area(
                    "script_edit_" + block.block_id,
                    value=block.script_final if block.script_final is not None else block.script_draft,
                    height=140,
                    placeholder="Nhập script sẽ dùng cho TTS/render...",
                    key=f"ta_script_{block.block_id}",
                    label_visibility="collapsed",
                )

                note = st.text_input(
                    "Ghi chú duyệt",
                    value=block.review_note,
                    key=f"ti_note_{block.block_id}",
                    placeholder="Tuỳ chọn – ghi chú cho người khác xem sau",
                )

                btn_row = st.columns([1, 1, 1, 1])
                with btn_row[0]:
                    if st.button("💾 Lưu", key=f"btn_save_{block.block_id}", use_container_width=True):
                        try:
                            media_service.update_block_script(
                                project_id,
                                block.block_id,
                                script_final=edited_script,
                                review_note=note,
                            )
                            st.toast(f"Đã cập nhật {block.block_id}.", icon="✅")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi khi lưu: {exc}")
                with btn_row[1]:
                    if st.button("↺ Reset về transcript", key=f"btn_reset_{block.block_id}", use_container_width=True):
                        try:
                            media_service.update_block_script(
                                project_id,
                                block.block_id,
                                script_final=block.transcript,
                                review_note="",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi reset: {exc}")
                with btn_row[2]:
                    del_label = "🗑 Xoá" if not block.human_verified else "↩ Bỏ duyệt"
                    if st.button(del_label, key=f"btn_del_{block.block_id}", use_container_width=True):
                        try:
                            if block.human_verified:
                                # Unverify by clearing final script but keeping block
                                media_service.update_block_script(
                                    project_id, block.block_id,
                                    script_final="", review_note=note,
                                )
                                # Force unverify flag via direct mutation + save
                                cur = media_service.load_existing_document(project_id)
                                tgt = cur.get_block(block.block_id)
                                if tgt is not None:
                                    tgt.human_verified = False
                                    if block.block_id in cur.review.verified_block_ids:
                                        cur.review.verified_block_ids.remove(block.block_id)
                                    media_service.save_document(cur)
                                st.rerun()
                            else:
                                media_service.delete_block(project_id, block.block_id)
                                st.toast(f"Đã xoá {block.block_id}.", icon="🗑")
                                st.rerun()
                        except Exception as exc:
                            st.error(f"Lỗi: {exc}")
                with btn_row[3]:
                    pass  # reserved slot

                # Explicit move buttons
                mv_pair = st.columns(2)
                order_ids = [b.block_id for b in doc.content_blocks]
                pos = order_ids.index(block.block_id)
                with mv_pair[0]:
                    if st.button("⬆ Di chuyển lên", key=f"mv_up_{block.block_id}",
                                 disabled=pos == 0, use_container_width=True):
                        swapped = list(order_ids)
                        swapped[pos - 1], swapped[pos] = swapped[pos], swapped[pos - 1]
                        media_service.reorder_blocks(project_id, swapped)
                        st.rerun()
                with mv_pair[1]:
                    if st.button("Di chuyển xuống ⬇", key=f"mv_dn_{block.block_id}",
                                 disabled=pos >= len(order_ids) - 1, use_container_width=True):
                        swapped = list(order_ids)
                        swapped[pos + 1], swapped[pos] = swapped[pos], swapped[pos + 1]
                        media_service.reorder_blocks(project_id, swapped)
                        st.rerun()

    # Bottom approval bar
    st.divider()
    all_verified = all(b.human_verified for b in doc.content_blocks)
    ready = doc.is_ready_for_render()
    bar_cols = st.columns([2, 1, 1])
    with bar_cols[0]:
        if all_verified:
            st.success("Tất cả khối đã được duyệt ✅")
        else:
            remaining = sum(1 for b in doc.content_blocks if not b.human_verified)
            st.warning(f"Còn {remaining} khối chưa duyệt.")
    with bar_cols[1]:
        if st.button("✔ Xác nhận tổng thể", type="primary", disabled=not all_verified, use_container_width=True):
            from datetime import datetime

            from core.analysis_schemas import ReviewStatus
            doc.review.status = ReviewStatus.APPROVED
            doc.review.approved_at = datetime.now(UTC)
            doc.review.approved_by = "ui-review-page"
            media_service.save_document(doc)
            st.toast("Đã phê duyệt toàn bộ phân tích.", icon="🎉")
            st.rerun()
    with bar_cols[2]:
        if st.button("❌ Từ chối & yêu cầu làm lại", disabled=all_verified, use_container_width=True):
            from core.analysis_schemas import ReviewStatus
            doc.review.status = ReviewStatus.REJECTED
            media_service.save_document(doc)
            st.toast("Đã đánh dấu cần làm lại.", icon="♻")
            st.rerun()

# ===========================================================================
# TAB 3 – Statistics
# ===========================================================================
with tab_stats:
    if doc is None:
        st.info("Chưa có dữ liệu thống kê.")
        st.stop()

    st.subheader("Tổng quan phương tiện")
    mc = st.columns(4)
    mc[0].metric("Thời lượng", f"{doc.media.duration_seconds:.1f}s")
    mc[1].metric("Độ phân giải", f"{doc.media.width}×{doc.media.height}")
    mc[2].metric("FPS", f"{doc.media.fps:.2f}")
    mc[3].metric("Audio", f"{doc.media.audio_sample_rate}Hz / {doc.media.audio_channels}ch")

    st.subheader("Phân bố khối")
    durations = [b.duration_ms / 1000.0 for b in doc.content_blocks]
    if durations:
        sc = st.columns(3)
        sc[0].metric("Số khối", len(durations))
        sc[1].metric("TB thời lượng", f"{sum(durations)/len(durations):.1f}s")
        sc[2].metric("Min / Max", f"{min(durations):.1f}s / {max(durations):.1f}s")

        # Confidence distribution
        confidences = [b.confidence for b in doc.content_blocks if b.confidence > 0]
        if confidences:
            st.bar_chart({"confidence": confidences})

    st.subheader("Trạng thái duyệt")
    rc = st.columns(3)
    rc[0].metric("Đã duyệt", sum(1 for b in doc.content_blocks if b.human_verified))
    rc[1].metric("Có script cuối", len(doc.get_blocks_with_final_script()))
    rc[2].metric("Cần audio", len(doc.get_blocks_needing_audio()))

    st.divider()
    ready = doc.is_ready_for_render()
    st.markdown(f"**Sẵn sàng render?** {'✅ Có' if ready else '⛔ Chưa'}")
    if not ready:
        reasons = []
        if not doc.get_blocks_with_final_script():
            reasons.append("Chưa có khối nào với script cuối.")
        if doc.has_overlap():
            reasons.append("Các khối narration chồng lấn nhau.")
        missing_audio = doc.get_blocks_needing_audio()
        if missing_audio:
            reasons.append(f"{len(missing_audio)} khối thiếu audio đã duyệt.")
        for r in reasons:
            st.markdown(f"- {r}")