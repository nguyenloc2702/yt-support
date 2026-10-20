# ruff: noqa: N999
"""Unified five-step video workflow entry point.

The 5-step user journey:
1. Clone a permitted YouTube source.
2. Enter the desired output duration & run synthesis.
3. Review detailed timestamped visual, transcript, and audio descriptions.
4. Paste standard script & receive timestamped audio/content miss feedback.
5. Preview (with optional low-volume background music) & export final video.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import streamlit as st

from config.settings import settings
from core.database import init_db
from core.enums import JobType, RightsType
from core.exceptions import (
    DependencyNotFoundError,
    InvalidMediaError,
    ProjectNotFoundError,
)
from core.schemas import ProjectCreate
from pipeline.script_matcher import match_script_to_report
from pipeline.timeline_report import build_timeline_report
from services.asset_service import AssetService
from services.job_service import JobService
from services.project_service import ProjectService
from services.render_config_service import RenderConfigService
from services.settings_service import (
    PROVIDER_PRESETS,
    get_provider_preset,
    get_vlm_model,
    is_vlm_enabled,
)
from utils.ui import inject_pro_css, render_sidebar_hardware_status

st.set_page_config(
    page_title="Video Workflow - 5 Bước",
    page_icon="🎬",
    layout="wide",
)

init_db()
inject_pro_css()
render_sidebar_hardware_status()

project_service = ProjectService()
job_service = JobService()
render_config_service = RenderConfigService()
asset_service = AssetService()

st.title("🎬 Video Workflow — 5 Bước Đơn Giản")
st.caption("Clone video → Nhập thời lượng → Xem mô tả timestamp → Đối chiếu script → Preview & Xuất")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_dir(proj_id: str) -> Path:
    return Path(settings.projects_dir) / proj_id


def _load_json_file(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Resolve active project
# ---------------------------------------------------------------------------
project_id = st.session_state.get("project_id")
project = None
if project_id:
    try:
        project = project_service.get_project(project_id)
    except ProjectNotFoundError:
        st.session_state.pop("project_id", None)
        project_id = None

proj_dir = _project_dir(project_id) if project_id else None

# Check active jobs
active_analysis_job = (
    job_service.get_active_job(project_id, JobType.ANALYSIS.value)
    if project_id else None
)
active_render_job = (
    job_service.get_active_job(project_id, JobType.PREVIEW_RENDER.value)
    or job_service.get_active_job(project_id, JobType.FINAL_RENDER.value)
    if project_id else None
)

# ---------------------------------------------------------------------------
# Step completion checks
# ---------------------------------------------------------------------------
source_ready = bool(project and project.source_filename)
timeline_path = proj_dir / "timelines" / "current.json" if proj_dir else None
timeline_ready = bool(timeline_path and timeline_path.exists())
report_path = proj_dir / "analysis" / "timeline_report.json" if proj_dir else None
report_ready = bool(report_path and report_path.exists())
match_path = proj_dir / "analysis" / "script_match_report.json" if proj_dir else None
script_matched = bool(match_path and match_path.exists())
previews_dir = proj_dir / "previews" if proj_dir else None
finals_dir = proj_dir / "renders" if proj_dir else None
has_preview = bool(previews_dir and list(previews_dir.glob("*.mp4")))
has_final = bool(finals_dir and list(finals_dir.glob("*.mp4")))
render_ready = has_preview or has_final

step_states = [
    source_ready,
    timeline_ready,
    timeline_ready,
    script_matched,
    render_ready,
]
step_labels = [
    "1. Clone video",
    "2. Thời lượng",
    "3. Mô tả timestamp",
    "4. Đối chiếu script",
    "5. Preview / Xuất",
]

# Stepper header
cols = st.columns(5)
for index, (col, label) in enumerate(zip(cols, step_labels)):
    with col:
        if step_states[index]:
            st.success(f"✅ {label}")
        elif index == 0 or all(step_states[:index]):
            st.info(f"● {label}")
        else:
            st.caption(f"○ {label}")

st.divider()

# ---------------------------------------------------------------------------
# Bước 1: Clone video YouTube
# ---------------------------------------------------------------------------
if not source_ready:
    st.subheader("Bước 1: Clone video từ YouTube")
    st.caption("Dán đường dẫn YouTube công khai mà bạn có quyền sử dụng để tạo dự án.")

    with st.form("simple_clone_form"):
        url = st.text_input(
            "URL YouTube",
            placeholder="https://www.youtube.com/watch?v=...",
        )
        name = st.text_input("Tên dự án", placeholder="Video tổng hợp của tôi")
        rights_type = st.selectbox(
            "Quyền sử dụng",
            [item.value for item in RightsType],
            format_func=lambda val: {
                "owned": "Nội dung do tôi sở hữu",
                "licensed": "Tôi có giấy phép sử dụng",
                "public_domain": "Public domain (Công cộng)",
                "creative_commons": "Creative Commons phù hợp",
            }.get(val, val),
        )
        confirmed = st.checkbox(
            "Tôi xác nhận có quyền sử dụng và xử lý video này.",
            value=False,
        )
        clone_clicked = st.form_submit_button(
            "⬇️ Clone video",
            type="primary",
            use_container_width=True,
        )

    if clone_clicked:
        if not url.strip():
            st.error("Vui lòng nhập URL YouTube.")
        elif not name.strip():
            st.error("Vui lòng nhập tên dự án.")
        elif not confirmed:
            st.error("Bạn cần xác nhận quyền sử dụng trước khi clone video.")
        else:
            try:
                data = ProjectCreate(
                    name=name.strip(),
                    description="",
                    rights_type=RightsType(rights_type),
                    confirmed=True,
                    source_url=url.strip(),
                )
                progress_bar = st.progress(0, text="Đang clone video...")

                def _progress_cb(percent: int, message: str) -> None:
                    progress_bar.progress(
                        min(max(int(percent), 0), 100),
                        text=message or "Đang tải video...",
                    )

                new_project = project_service.create_project_from_url(
                    data,
                    url.strip(),
                    progress_callback=_progress_cb,
                )
                st.session_state.project_id = new_project.id
                progress_bar.progress(100, text="Clone video hoàn tất.")
                st.success("Đã clone video thành công!")
                st.rerun()
            except DependencyNotFoundError as exc:
                st.error(str(exc))
            except InvalidMediaError as exc:
                st.error(f"Không thể clone video: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Clone video thất bại: {exc}")

else:
    # Top info bar
    c_info1, c_info2 = st.columns([4, 1])
    with c_info1:
        st.markdown(
            f"Dự án: **{project.name}** • Nguồn: `{project.source_filename}` • "
            f"Thời lượng gốc: **{project.duration_seconds or 0:.1f}s**"
        )
    with c_info2:
        if st.button("🔄 Chọn dự án khác", use_container_width=True):
            st.session_state.pop("project_id", None)
            st.rerun()

    st.markdown("---")

    # -----------------------------------------------------------------------
    # Bước 2: Thời lượng & Tổng hợp
    # -----------------------------------------------------------------------
    st.subheader("Bước 2: Nhập thời lượng mong muốn & Tổng hợp")
    opts_path = proj_dir / "analysis" / "options.json"
    saved_opts = _load_json_file(opts_path) or {}
    default_dur = int(saved_opts.get("target_duration") or 60)
    max_dur = max(60, int(project.duration_seconds or 3600))
    default_dur = min(max(default_dur, 10), max_dur)

    col_dur, col_action = st.columns([2, 3])
    with col_dur:
        target_duration = st.number_input(
            "Thời lượng mong muốn (giây)",
            min_value=10,
            max_value=max_dur,
            value=default_dur,
            step=10,
            help="Thời lượng video tổng hợp sau khi cắt gọn.",
        )
        use_vlm_toggle = st.checkbox(
            "👁️ Bật phân tích bối cảnh AI (VLM)",
            value=is_vlm_enabled(),
            help="Tự động trích xuất keyframe và phân tích bối cảnh chân thực bằng AI thị giác.",
        )
        preset_info = PROVIDER_PRESETS.get(get_provider_preset(), {})
        st.caption(
            f"Provider: **{preset_info.get('name', 'Custom')}** • Model VLM: `{get_vlm_model()}`"
        )

    with col_action:
        st.write("")
        st.write("")
        synth_disabled = bool(active_analysis_job and job_service.is_running(active_analysis_job.id))
        if st.button(
            "🚀 Tổng hợp & phân tích video",
            type="primary",
            use_container_width=True,
            disabled=synth_disabled,
        ):
            options = {
                "mode": "summary",
                "whisper_model": getattr(settings, "whisper_model", "small"),
                "language": "auto",
                "target_duration": float(target_duration),
                "topic": "",
                "keywords": [],
                "use_semantic": True,
                "use_vlm": use_vlm_toggle,
                "vlm_model": get_vlm_model(),
            }
            _save_json_file(opts_path, options)
            job_service.submit_analysis(project_id, options)
            st.rerun()

    # Active analysis progress
    if active_analysis_job and job_service.is_running(active_analysis_job.id):
        p_info = job_service.get_progress(active_analysis_job.id)
        percent = int(p_info.get("percent", 0))
        st.progress(percent / 100, text=f"Đang tổng hợp... {p_info.get('message', '')} ({percent}%)")
        if st.button("⏹ Hủy tổng hợp", use_container_width=True):
            job_service.cancel_job(active_analysis_job.id)
            st.rerun()
        time.sleep(1)
        st.rerun()
    elif active_analysis_job and active_analysis_job.status == "failed":
        st.error(f"Tổng hợp thất bại: {active_analysis_job.error_message or 'Lỗi không xác định'}")

    # -----------------------------------------------------------------------
    # Bước 3: Xem mô tả chi tiết theo timestamp
    # -----------------------------------------------------------------------
    if timeline_ready:
        st.markdown("---")
        st.subheader("Bước 3: Xem mô tả chi tiết theo timestamp")
        timeline_data = _load_json_file(timeline_path) or {}

        # Auto generate timeline report if not yet generated
        report_data = _load_json_file(report_path) if report_path else None
        if not report_data and timeline_data:
            trans_path = proj_dir / "analysis" / "transcription.json"
            scenes_path = proj_dir / "analysis" / "scenes.json"
            silences_path = proj_dir / "analysis" / "silences.json"
            shots_path = proj_dir / "analysis" / "shots.json"
            transcript = _load_json_file(trans_path) or []
            scenes = _load_json_file(scenes_path) or []
            silences = _load_json_file(silences_path) or []
            shots = _load_json_file(shots_path) or []
            report_data = build_timeline_report(
                timeline=timeline_data,
                transcript=transcript if isinstance(transcript, list) else [],
                scenes=scenes if isinstance(scenes, list) else [],
                silences=silences if isinstance(silences, list) else [],
                shots=shots if isinstance(shots, list) else [],
            )
            _save_json_file(report_path, report_data)

        if report_data:
            rows = report_data.get("rows") or []
            vlm_count = sum(1 for r in rows if r.get("has_vlm"))

            col_vlm_info, col_vlm_btn = st.columns([3, 1])
            with col_vlm_info:
                if vlm_count:
                    st.caption(f"✨ Đã làm giàu mô tả VLM cho **{vlm_count}/{len(rows)}** mốc thời gian.")
                else:
                    st.caption("ℹ️ Chưa có mô tả VLM cho timeline này. Bấm nút bên cạnh để phân tích hình ảnh.")
            with col_vlm_btn:
                if st.button("✨ Làm giàu mô tả VLM", use_container_width=True, help="Trích xuất keyframes và gọi VLM để làm giàu mô tả bối cảnh"):
                    with st.spinner("Đang trích xuất keyframes và phân tích VLM..."):
                        try:
                            from pipeline.keyframes import KeyframeExtractor
                            from pipeline.vlm_captioning import (
                                caption_all_shots_parallel,
                            )

                            proxy_p = proj_dir / "proxy" / "proxy_720p.mp4"
                            src_p = proj_dir / "source" / project.source_filename
                            shot_video = proxy_p if proxy_p.exists() else src_p

                            kf_extractor = KeyframeExtractor()
                            kf_dir = proj_dir / "keyframes"
                            scenes_p = proj_dir / "analysis" / "scenes.json"
                            scenes_list = _load_json_file(scenes_p) or []

                            raw_shots = kf_extractor.extract_for_shots(
                                video_path=shot_video,
                                shots=scenes_list,
                                out_dir=kf_dir,
                            )
                            captioned = caption_all_shots_parallel(
                                shots=raw_shots,
                                model=get_vlm_model(),
                                max_workers=3,
                            )
                            shots_p = proj_dir / "analysis" / "shots.json"
                            _save_json_file(shots_p, captioned)

                            trans_p = proj_dir / "analysis" / "transcription.json"
                            silences_p = proj_dir / "analysis" / "silences.json"
                            tr_list = _load_json_file(trans_p) or []
                            sil_list = _load_json_file(silences_p) or []

                            report_data = build_timeline_report(
                                timeline=timeline_data,
                                transcript=tr_list if isinstance(tr_list, list) else [],
                                scenes=scenes_list if isinstance(scenes_list, list) else [],
                                silences=sil_list if isinstance(sil_list, list) else [],
                                shots=captioned,
                            )
                            _save_json_file(report_path, report_data)
                            st.success("Đã hoàn tất làm giàu mô tả bằng VLM!")
                            st.rerun()
                        except Exception as vlm_ui_exc:  # noqa: BLE001
                            st.error(f"Lỗi phân tích VLM: {vlm_ui_exc}")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tổng số đoạn", len(rows))
            m2.metric("Thời lượng tổng hợp", f"{float(report_data.get('output_duration_seconds') or 0):.1f}s")
            m3.metric("Đoạn có thoại", sum(1 for r in rows if r.get("has_speech")))
            m4.metric("Đoạn có VLM", vlm_count)

            with st.expander("📋 Bảng mô tả chi tiết từng mốc timestamp", expanded=True):
                for row in rows:
                    out_s = float(row.get("output_start") or 0)
                    out_e = float(row.get("output_end") or 0)
                    src_s = float(row.get("source_start") or 0)
                    src_e = float(row.get("source_end") or 0)
                    aud_state = row.get("audio_state", "unknown")
                    conf = row.get("confidence", "low")
                    vlm_tag = " `[👁️ VLM AI]`" if row.get("has_vlm") else ""

                    aud_badge = {
                        "speech_detected": "🗣 Thoại",
                        "silence": "🔇 Im lặng",
                    }.get(aud_state, "🎵 Âm nền/Nhạc")

                    st.markdown(
                        f"**⏱ {out_s:06.2f}s → {out_e:06.2f}s** `[{aud_badge}]`{vlm_tag} "
                        f"*(Nguồn: {src_s:.1f}s - {src_e:.1f}s • Độ tin cậy: {conf})*"
                    )
                    st.write(f"• **Hình ảnh / Hành động:** {row.get('visual_description') or 'Chưa có mô tả'}")
                    if row.get("transcript"):
                        st.write(f"• **Thoại:** \"{row.get('transcript')}\"")
                    st.divider()

        # -------------------------------------------------------------------
        # Bước 4: Dán script chuẩn & Đối chiếu
        # -------------------------------------------------------------------
        st.markdown("---")
        st.subheader("Bước 4: Dán script chuẩn & Nhận feedback mốc thời gian")
        st.caption("Dán kịch bản thuyết minh chuẩn của bạn. Hệ thống sẽ đối chiếu với timeline video và cảnh báo nếu miss audio/thoại.")

        saved_match = _load_json_file(match_path) if match_path else None
        prev_script = ""
        if saved_match and saved_match.get("issues"):
            prev_script = "\n\n".join(i.get("script_text", "") for i in saved_match["issues"])

        script_text = st.text_area(
            "Nội dung script chuẩn",
            value=st.session_state.get("workflow_script_text", prev_script),
            height=180,
            placeholder="Dán script lời thoại hoặc thuyết minh tại đây...",
        )
        st.session_state["workflow_script_text"] = script_text

        if st.button("🔎 Đối chiếu script với video", type="primary", disabled=not script_text.strip()) and report_data:
            match_result = match_script_to_report(script_text, report_data)
            _save_json_file(match_path, match_result)
            st.session_state["workflow_match_result"] = match_result
            st.success("Đã hoàn thành đối chiếu script!")
            st.rerun()

        match_result = st.session_state.get("workflow_match_result") or saved_match
        if match_result and match_result.get("issues"):
            summary = match_result.get("summary") or {}
            c_tot, c_mat, c_par, c_mis = st.columns(4)
            c_tot.metric("Tổng câu/đoạn script", summary.get("total", 0))
            c_mat.metric("Khớp tốt (Matched)", summary.get("matched", 0))
            c_par.metric("Khớp một phần (Partial)", summary.get("partial", 0))
            c_mis.metric("Miss audio / Lệch (Miss)", summary.get("miss", 0))

            with st.expander("🔍 Chi tiết feedback đối chiếu theo mốc thời gian", expanded=True):
                for issue in match_result.get("issues", []):
                    severity = str(issue.get("severity") or "low").upper()
                    status = issue.get("status")
                    start = float(issue.get("start") or 0)
                    end = float(issue.get("end") or 0)

                    if severity == "HIGH":
                        st.error(f"🔴 **HIGH • {start:.1f}s → {end:.1f}s • {issue.get('message', '')}**")
                    elif severity == "MEDIUM":
                        st.warning(f"🟡 **MEDIUM • {start:.1f}s → {end:.1f}s • {issue.get('message', '')}**")
                    else:
                        st.success(f"🟢 **OK • {start:.1f}s → {end:.1f}s • {issue.get('message', '')}**")

                    st.markdown(f"**Script:** `{issue.get('script_text')}`")
                    st.caption(f"Evidence: {issue.get('evidence')}")
                    st.write("")

        # -------------------------------------------------------------------
        # Bước 5: Preview & Xuất video
        # -------------------------------------------------------------------
        st.markdown("---")
        st.subheader("Bước 5: Preview + Nhạc nền tùy chọn & Xuất video")

        render_cfg = render_config_service.get_current(project_id)

        # Background music toggle
        has_music = render_cfg.music.enabled if render_cfg else False
        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            bg_music_enabled = st.checkbox(
                "🎵 Bật nhạc nền (âm lượng nhỏ, -16.5 dB)",
                value=has_music,
                help="Tự động lồng nhạc nền nhẹ nhàng dưới giọng nói/âm thanh video.",
            )

        music_file = None
        if bg_music_enabled:
            with col_m2:
                music_file = st.file_uploader(
                    "Tải lên file nhạc nền (.mp3, .wav, .m4a)",
                    type=["mp3", "wav", "m4a", "aac"],
                    key="bg_music_uploader",
                )
                if music_file is not None:
                    try:
                        asset = asset_service.upload_music(
                            project_id=project_id,
                            uploaded_file=music_file,
                            original_filename=music_file.name,
                            rights_confirmed=True,
                        )
                        render_cfg.music.enabled = True
                        render_cfg.music.asset_id = asset.id
                        render_cfg.music.gain_db = -16.5
                        render_config_service.save_new_version(project_id, render_cfg)
                        st.success(f"Đã lưu nhạc nền: {music_file.name}")
                    except Exception as exc:  # noqa: BLE001
                        st.warning(f"Không thể xử lý file nhạc: {exc}")
        else:
            if render_cfg and render_cfg.music.enabled:
                render_cfg.music.enabled = False
                render_config_service.save_new_version(project_id, render_cfg)

        # Action buttons
        c_prev, c_final = st.columns(2)
        render_busy = bool(active_render_job and job_service.is_running(active_render_job.id))

        with c_prev:
            if st.button(
                "👁 Xem Preview (720p nhanh)",
                type="primary",
                use_container_width=True,
                disabled=render_busy,
            ):
                job_service.submit_preview_render(project_id, timeline_data)
                st.rerun()

        with c_final:
            if st.button(
                "⬇️ Xuất hoàn chỉnh (1080p sắc nét)",
                type="secondary",
                use_container_width=True,
                disabled=render_busy,
            ):
                job_service.submit_final_render(project_id, timeline_data)
                st.rerun()

        # Active render job tracking
        if active_render_job and job_service.is_running(active_render_job.id):
            p_info = job_service.get_progress(active_render_job.id)
            percent = int(p_info.get("percent", 0))
            st.progress(percent / 100, text=f"Đang render ({active_render_job.job_type}): {p_info.get('message', '')} ({percent}%)")
            if st.button("⏹ Hủy render", use_container_width=True):
                job_service.cancel_job(active_render_job.id)
                st.rerun()
            time.sleep(1)
            st.rerun()

        # Display latest preview or render
        latest_preview = sorted(previews_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime) if previews_dir and previews_dir.exists() else []
        latest_final = sorted(finals_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime) if finals_dir and finals_dir.exists() else []

        if latest_final:
            st.success(f"🎬 Video hoàn chỉnh sẵn sàng: `{latest_final[-1].name}`")
            st.video(str(latest_final[-1]))
        elif latest_preview:
            st.info(f"👁 Video Preview sẵn sàng: `{latest_preview[-1].name}`")
            st.video(str(latest_preview[-1]))

        # Advanced audio settings in expander
        with st.expander("⚙️ Cấu hình âm thanh nâng cao (Advanced Audio Settings)", expanded=False):
            st.caption("Tinh chỉnh âm lượng, ducking và chuẩn phát sóng EBU R128.")
            if render_cfg:
                v_gain = st.slider(
                    "Âm lượng thoại (Voice Gain dB)",
                    min_value=-30.0,
                    max_value=12.0,
                    value=float(render_cfg.voice.gain_db),
                    step=0.5,
                )
                m_gain = st.slider(
                    "Âm lượng nhạc nền (Music Gain dB)",
                    min_value=-40.0,
                    max_value=6.0,
                    value=float(render_cfg.music.gain_db),
                    step=0.5,
                )
                m_loop = st.checkbox("Lặp lại nhạc nền khi video dài hơn", value=render_cfg.music.loop)

                if st.button("Lưu cấu hình âm thanh", use_container_width=True):
                    render_cfg.voice.gain_db = v_gain
                    render_cfg.music.gain_db = m_gain
                    render_cfg.music.loop = m_loop
                    render_config_service.save_new_version(project_id, render_cfg)
                    st.success("Đã cập nhật cấu hình âm thanh.")

# ---------------------------------------------------------------------------
# Project switcher expander
# ---------------------------------------------------------------------------
with st.expander("📁 Danh sách dự án đã có", expanded=False):
    all_projects = project_service.list_projects()
    if not all_projects:
        st.caption("Chưa có dự án nào.")
    else:
        for p in all_projects[:10]:
            col_name, col_btn = st.columns([4, 1])
            with col_name:
                st.write(f"**{p.name}** — `{p.status.value}`")
            with col_btn:
                if st.button("Mở", key=f"open_proj_{p.id}", use_container_width=True):
                    st.session_state.project_id = p.id
                    st.rerun()
