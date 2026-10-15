"""Script review page: generate → review → edit → approve gate → timeline."""

import streamlit as st

from core.exceptions import AppError
from core.logging_config import get_logger
from services.script_service import ScriptService

logger = get_logger(__name__)

st.set_page_config(page_title="Duyệt Script", page_icon="📝", layout="wide")
st.title("📝 Duyệt & Chỉnh sửa Script (AI viết lại)")

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


def _fmt_tc(seconds: float) -> str:
    m, s = divmod(max(0.0, float(seconds)), 60.0)
    return f"{int(m):02d}:{s:04.1f}"


# Load current timeline (if any) to show each beat's output window.
tl_windows: dict[int, tuple[float, float]] = {}
tl_warnings: dict[int, list[str]] = {}
try:
    from config.settings import settings as _settings
    from pathlib import Path as _Path

    _tl_path = _Path(_settings.projects_dir) / project_id / "timelines" / "current.json"
    if _tl_path.is_file():
        import json as _json

        _tl = _json.loads(_tl_path.read_text(encoding="utf-8"))
        for _c in _tl.get("clips", []):
            bid = _c.get("beat_id")
            if bid is None:
                continue
            s, e = float(_c.get("output_start", 0)), float(_c.get("output_end", 0))
            if bid in tl_windows:
                s0, e0 = tl_windows[bid]
                tl_windows[bid] = (min(s0, s), max(e0, e))
            else:
                tl_windows[bid] = (s, e)
            for w in _tl.get("warnings", []) or []:
                if w.startswith(f"Beat {bid}:"):
                    tl_warnings.setdefault(bid, []).append(w)
except Exception:
    logger.debug("No timeline for beat windows", exc_info=True)

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
        bid = beat["id"]
        win = tl_windows.get(bid)
        head = f"Beat {bid + 1} · {beat.get('emotion', 'neutral')}"
        if win:
            head += f" · 🎬 lồng tiếng {_fmt_tc(win[0])} → {_fmt_tc(win[1])} ({win[1] - win[0]:.1f}s)"
        with st.expander(head):
            if bid in tl_warnings:
                for w in tl_warnings[bid]:
                    st.warning(f"⚠️ {w}")
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
            # Keep the timeline in sync with the edited script when possible;
            # the approve gate requires re-approval first (expected for EDITED).
            timeline_note = ""
            try:
                service.build_script_timeline(project_id)
                timeline_note = " Timeline đã được lập kế hoạch lại theo script mới."
            except AppError:
                timeline_note = " Timeline sẽ được lập lại sau khi bạn duyệt script."
            # No st.rerun() here: it would wipe these messages on rerun.
            st.success("Đã lưu. Trạng thái: EDITED — hãy duyệt lại." + timeline_note)
        except AppError as exc:
            st.error(str(exc))

# ---- listen to the script before rendering ----
with st.expander("🎧 Bước duyệt audio trước khi render (bắt buộc)", expanded=True):
    st.caption(
        "Xuất script (.txt) + audio TTS đọc toàn bộ script (MP3/WAV) kèm phụ đề SRT/karaoke ASS. "
        "Nghe kiểm tra, rồi bấm '✅ Duyệt audio' để mở khóa xuất bản video."
    )
    ex1, ex2 = st.columns(2)
    with ex1:
        if st.button("⬇️ Xuất script (.txt)", key="export_txt_btn"):
            try:
                txt_path = service.export_script_txt(project_id)
                st.success(f"Đã xuất: {txt_path}")
                st.download_button(
                    "⬇️ Tải script (.txt)",
                    data=txt_path.read_text(encoding="utf-8"),
                    file_name="script.txt",
                    mime="text/plain",
                    key="dl_script_txt",
                )
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.exception("Script txt export failed")
                st.error(f"Lỗi không xác định: {exc}")
    with ex2:
        if st.button("🎙️ Tạo audio duyệt (TTS)", key="export_audio_btn"):
            try:
                with st.spinner("Đang tổng hợp giọng đọc... (không cần duyệt script)"):
                    res = service.export_script_audio(project_id)
                st.session_state["script_audio"] = res
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.exception("Script audio export failed")
                st.error(f"Lỗi không xác định: {exc}")
    res = st.session_state.get("script_audio")
    if res:
        mp3 = res.get("mp3")
        if mp3:
            from pathlib import Path as _P

            if _P(mp3).is_file():
                st.audio(str(_P(mp3)), format="audio/mp3")
            st.caption(f"Thời lượng: {res['duration_seconds']:.1f}s")
            st.markdown(f"- WAV: `{res['wav']}`")
            st.markdown(f"- MP3: `{res['mp3']}`")
            st.markdown(f"- SRT (phụ đề theo câu): `{res['srt']}`")
            if res.get("ass_karaoke"):
                st.markdown(f"- ASS karaoke (highlight từng từ): `{res['ass_karaoke']}`")

    # Audio approval gate
    audio_ok = service.is_audio_approved(project_id)
    if audio_ok:
        st.success("✅ Audio đã được duyệt — có thể sang trang Xuất bản để render.")
    else:
        if st.button("✅ Duyệt audio này", type="primary", key="approve_audio_btn",
                     help="Chỉ bấm sau khi đã nghe thử file audio phía trên."):
            try:
                service.approve_audio(project_id)
                st.success("Đã duyệt audio. Trang Xuất bản đã được mở khóa.")
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.exception("Audio approval failed")
                st.error(f"Lỗi không xác định: {exc}")
        else:
            st.warning("🔒 Chưa duyệt audio — trang Xuất bản sẽ bị khóa render cho đến khi bạn duyệt.")

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
