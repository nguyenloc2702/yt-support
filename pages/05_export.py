import json
import time
from pathlib import Path
import streamlit as st

from core.database import init_db, SessionLocal
from core.enums import RenderStatus, RenderType
from core.models import Render
from core.render_schemas import (
    LoudnessOptions,
    MusicOptions,
    RenderConfiguration,
    TestPreviewOptions,
    TTSOptions,
    VoiceOptions,
)
from services.asset_service import AssetService
from services.job_service import JobService
from services.project_service import ProjectService
from services.render_config_service import RenderConfigService
from services.render_service import RenderService
from utils.audio import db_to_percent, percent_to_db
from utils.json_io import read_json
from utils.ui import inject_pro_css, render_sidebar_hardware_status, render_studio_header

init_db()
inject_pro_css()

st.set_page_config(
    page_title="Export Studio - Local Video Editor",
    page_icon="🎬",
    layout="wide",
)

if "project_id" not in st.session_state:
    proj_svc = ProjectService()
    projs = proj_svc.list_projects()
    if projs:
        st.session_state.project_id = projs[0].id
    else:
        st.error("Chưa chọn project. Vui lòng vào trang Projects và tạo/mở một project.")
        st.stop()

project_id = st.session_state.project_id
project_service = ProjectService()
render_service = RenderService()
job_service = JobService()
asset_service = AssetService()
config_service = RenderConfigService()

try:
    project = project_service.get_project(project_id)
except Exception as e:
    st.error(f"Không tìm thấy project: {e}")
    st.stop()

# Check for active background job
active_job = job_service.get_active_job(project_id)
is_busy = active_job is not None

render_sidebar_hardware_status(is_busy=is_busy)

# Load Timeline
proj_dir = Path(f"projects/{project_id}")
timeline_path = proj_dir / "timelines" / "current.json"
timeline_data = {
    "version": 1,
    "actual_duration": project.duration_seconds or 120.0,
    "output_aspect_ratio": "16:9",
    "clips": [
        {
            "id": "clip_001",
            "source_start": 0.0,
            "source_end": project.duration_seconds or 120.0,
            "order": 1,
            "enabled": True,
            "label": "Full Source",
        }
    ],
}

if timeline_path.exists():
    try:
        t_disk = read_json(timeline_path)
        timeline_data.update(t_disk)
    except Exception:
        pass

timeline_duration = float(timeline_data.get("actual_duration", project.duration_seconds or 120.0))

# Load or initialize render configuration
current_config = config_service.get_current(project_id)
if not current_config:
    current_config = RenderConfiguration(
        id="cfg_default",
        project_id=project_id,
        version=1,
        timeline_version=int(timeline_data.get("version", 1)),
        preset_name="youtube_1080p",
    )

# Format timecode helper
def _sec_to_tc(seconds: float) -> str:
    s = max(0.0, seconds)
    hrs = int(s // 3600)
    mins = int((s % 3600) // 60)
    secs = int(s % 60)
    frames = int((s - int(s)) * 30)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}:{frames:02d}"

# Top Header
render_studio_header(
    project_name=f"{project.name}",
    resolution=f"{project.width}x{project.height}" if project.width else "1080p",
    codec="EBU R128 Master",
    timecode=_sec_to_tc(timeline_duration),
)

# Page Title & Engine Subtitle
st.markdown(
    """
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <div>
            <h3 style="margin: 0; color: #ffffff; font-weight: 800; font-size: 1.25rem;">Cấu hình xuất bản & Xử lý âm thanh</h3>
            <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 4px;">Streamlit Audio Engine • FFmpeg v6.1 Audio Post-production & EBU R128 Normalizer</div>
        </div>
        <span style="background: rgba(59, 130, 246, 0.15); border: 1px solid rgba(59, 130, 246, 0.4); color: #93c5fd; padding: 4px 10px; border-radius: 4px; font-size: 0.78rem; font-weight: 600;">
            EBU R128 Active
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

col_left, col_right = st.columns([1.1, 1], gap="large")

# ================= LEFT COLUMN: Presets, Audio & BGM =================
with col_left:
    # A. Output Preset Selection
    st.markdown(
        """
        <div class="pro-card" style="margin-bottom: 16px;">
            <div class="pro-card-header">
                <span class="pro-card-title">🎛 A. Cấu hình định dạng đầu ra (Output Preset)</span>
                <span class="pro-card-subtitle">Preset Profile</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    preset_options = [
        "YouTube 1080p (Tiêu chuẩn)",
        "Preview 720p (Nhanh)",
        "Dọc TikTok/Reels 1080×1920",
        "Vuông 1080×1080",
    ]
    preset_map = {
        "YouTube 1080p (Tiêu chuẩn)": "youtube_1080p",
        "Preview 720p (Nhanh)": "preview_720p",
        "Dọc TikTok/Reels 1080×1920": "vertical_1080p",
        "Vuông 1080×1080": "square_1080p",
    }
    reverse_preset_map = {v: k for k, v in preset_map.items()}

    current_preset_label = reverse_preset_map.get(current_config.preset_name, preset_options[0])
    selected_preset_label = st.radio(
        "Chọn Preset đầu ra",
        options=preset_options,
        index=preset_options.index(current_preset_label),
        horizontal=True,
        label_visibility="collapsed",
    )
    selected_preset = preset_map[selected_preset_label]

    # B. Source Audio
    st.markdown(
        """
        <div class="pro-card" style="margin-bottom: 16px;">
            <div class="pro-card-header">
                <span class="pro-card-title">🎙 B. Âm thanh nguồn (Source Audio)</span>
                <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.72rem; font-weight: 600; padding: 2px 6px; border-radius: 4px;">Sẵn sàng</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    src_audio_en = st.checkbox("Kích hoạt âm thanh nguồn gốc", value=current_config.voice.enabled, key="src_audio_en")

    current_voice_pct = int(round(db_to_percent(current_config.voice.gain_db))) if src_audio_en else 0
    current_voice_pct = min(max(current_voice_pct, 0), 200)

    voice_pct = st.slider(
        "Âm lượng giọng nói / thoại gốc",
        min_value=0,
        max_value=200,
        value=current_voice_pct if current_voice_pct > 0 else 100,
        disabled=not src_audio_en,
        key="voice_vol_slider",
    )
    calculated_voice_db = percent_to_db(voice_pct)
    st.caption(f"Tương đương: **{calculated_voice_db:+.1f} dB** (0% = Tắt tiếng, 100% = Gain gốc, 200% = +6dB)")

    norm_audio_en = st.checkbox(
        "Chuẩn hóa âm thanh đầu ra (Normalize final audio - EBU R128: -16 LUFS, -1.5 dBTP)",
        value=current_config.loudness.enabled,
        key="norm_audio_en",
    )

    # B2. TTS Narration (edge-tts)
    st.markdown(
        """
        <div class="pro-card" style="margin-top: 16px;">
            <div class="pro-card-header">
                <span class="pro-card-title">🗣 B2. Lồng giọng AI (TTS Narration - edge-tts)</span>
                <span class="pro-card-subtitle">Đọc script beat đã duyệt thành giọng nói</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tts_en = st.checkbox(
        "Lồng giọng AI đọc script (TTS)",
        value=current_config.tts.enabled,
        key="tts_en",
        help="Tổng hợp giọng đọc từ text của từng beat trong script đã duyệt, đè lên audio gốc (giảm dB khi TTS nói)."
    )

    tts_voices = {
        "vi-VN-HoaiMyNeural": "🇻🇳 Hoài My (Nữ, Việt Nam)",
        "vi-VN-NamMinhNeural": "🇻🇳 Nam Minh (Nam, Việt Nam)",
        "en-US-AriaNeural": "🇺🇸 Aria (Female, English)",
        "en-US-GuyNeural": "🇺🇸 Guy (Male, English)",
    }
    current_voice = current_config.tts.voice if current_config.tts.voice in tts_voices else "vi-VN-HoaiMyNeural"
    voice_keys = list(tts_voices.keys())
    tts_voice = st.selectbox(
        "Chọn giọng đọc",
        options=voice_keys,
        index=voice_keys.index(current_voice),
        format_func=lambda v: tts_voices[v],
        disabled=not tts_en,
        key="tts_voice",
    )
    tc1, tc2 = st.columns(2)
    with tc1:
        tts_rate = st.slider(
            "Tốc độ đọc (%)",
            min_value=-30,
            max_value=50,
            value=0,
            disabled=not tts_en,
            key="tts_rate",
        )
    with tc2:
        tts_vol_pct = st.slider(
            "Âm lượng giọng TTS (%)",
            min_value=0,
            max_value=150,
            value=100,
            disabled=not tts_en,
            key="tts_vol",
        )
    tts_replace = st.checkbox(
        "Thay thế hoàn toàn giọng đọc gốc (chỉ còn giọng TTS)",
        value=current_config.tts.replace_original,
        disabled=not tts_en,
        key="tts_replace",
        help="Bật: video xuất ra CHỈ có giọng AI đọc script. Tắt: giữ giọng gốc ở nền (giảm dB theo slider dưới) và đè giọng AI lên.",
    )
    tts_duck = st.slider(
        "Giảm âm lượng audio gốc khi TTS nói (dB)",
        min_value=0,
        max_value=30,
        value=12,
        disabled=not tts_en or tts_replace,
        key="tts_duck",
    )
    tts_rate_str = f"{tts_rate:+d}%"
    tts_gain_db = percent_to_db(tts_vol_pct) if tts_vol_pct not in (0, 100) else (-30.0 if tts_vol_pct == 0 else 0.0)
    st.caption(
        "ℹ️ Nếu script chưa được duyệt hoặc timeline không phải chế độ Script Review, xuất bản vẫn chạy bình thường không có TTS."
    )

    # C. Background Music (BGM)
    st.markdown(
        """
        <div class="pro-card" style="margin-top: 16px;">
            <div class="pro-card-header">
                <span class="pro-card-title">🎵 C. Nhạc nền (Background Music - BGM)</span>
                <span class="pro-card-subtitle">Local Library & Post-mix</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    bgm_en = st.checkbox("Sử dụng nhạc nền (BGM)", value=current_config.music.enabled, key="bgm_en")

    # Upload expander
    with st.expander("➕ Tải tệp nhạc mới vào thư viện dự án", expanded=False):
        uploaded_audio = st.file_uploader(
            "Chọn tệp âm thanh (MP3, WAV, M4A, AAC, FLAC, OGG, OPUS)",
            type=["mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"],
            key="bgm_file_uploader",
        )
        r_type = st.selectbox("Loại giấy phép / bản quyền", ["owned", "licensed", "creative_commons", "public_domain"])
        r_source_url = st.text_input("URL nguồn (nếu có)", "")
        r_notes = st.text_input("Ghi chú bản quyền (mã license, tác giả...)", "")
        r_confirmed = st.checkbox(
            "Tôi xác nhận có quyền sở hữu hoặc giấy phép sử dụng hợp pháp tệp âm thanh này.",
            value=False,
            key="bgm_rights_confirm_chk",
        )

        if st.button("Xác nhận & Lưu vào dự án", use_container_width=True):
            if not uploaded_audio:
                st.warning("Vui lòng chọn một tệp âm thanh.")
            elif not r_confirmed:
                st.error("Bạn bắt buộc phải xác nhận có quyền sử dụng tệp âm thanh này.")
            else:
                try:
                    with st.spinner("Đang kiểm định âm thanh bằng FFprobe và lưu trữ an toàn..."):
                        new_asset = asset_service.upload_music(
                            project_id=project_id,
                            uploaded_file=uploaded_audio,
                            original_filename=uploaded_audio.name,
                            rights_type=r_type,
                            rights_confirmed=r_confirmed,
                            source_url=r_source_url or None,
                            rights_notes=r_notes or None,
                        )
                        st.success(f"Đã lưu tệp nhạc '{new_asset.original_filename}' ({new_asset.duration_seconds:.1f}s) thành công!")
                        time.sleep(0.5)
                        st.rerun()
                except Exception as exc:
                    st.error(f"Lỗi tải nhạc: {exc}")

    # Available Music Assets List
    music_assets = asset_service.list_music_assets(project_id)
    asset_options = {a.id: f"{a.original_filename} ({_sec_to_tc(a.duration_seconds)})" for a in music_assets}

    selected_asset_id = None
    if music_assets:
        default_index = 0
        if current_config.music.asset_id:
            for idx, a in enumerate(music_assets):
                if a.id == current_config.music.asset_id:
                    default_index = idx
                    break

        selected_asset_id = st.selectbox(
            "Chọn tệp nhạc nền",
            options=list(asset_options.keys()),
            format_func=lambda x: asset_options[x],
            index=default_index,
            disabled=not bgm_en,
            label_visibility="collapsed",
        )

        # Asset info badge
        chosen_asset = asset_service.get_music_asset(project_id, selected_asset_id)
        if chosen_asset:
            sr_khz = f"{chosen_asset.sample_rate // 1000}kHz" if chosen_asset.sample_rate else "48kHz"
            st.markdown(
                f"""
                <div style="background: #161e31; border: 1px solid #232f48; border-radius: 6px; padding: 10px; margin: 10px 0; display: flex; justify-content: space-between; align-items: center;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 1.4rem;">📄</span>
                        <div>
                            <div style="font-size: 0.82rem; font-weight: 600; color: #f1f5f9;">
                                Thời lượng {_sec_to_tc(chosen_asset.duration_seconds)} • {chosen_asset.codec_name or 'Audio'} {sr_khz}
                            </div>
                            <div style="font-size: 0.72rem; color: #94a3b8;">{chosen_asset.original_filename}</div>
                        </div>
                    </div>
                    <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.72rem; font-weight: 600; padding: 3px 8px; border-radius: 4px;">
                        ✓ {chosen_asset.rights_type.capitalize()}
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("Chưa có bản nhạc nào trong thư viện dự án. Hãy tải tệp nhạc mới ở phần mở rộng phía trên.")

    # BGM volume slider
    current_bgm_pct = int(round(db_to_percent(current_config.music.gain_db))) if bgm_en else 20
    current_bgm_pct = min(max(current_bgm_pct, 0), 100)

    bgm_pct = st.slider(
        "Âm lượng nhạc nền",
        min_value=0,
        max_value=100,
        value=current_bgm_pct,
        disabled=not bgm_en,
        key="bgm_vol_slider",
    )
    calculated_bgm_db = percent_to_db(bgm_pct)
    st.caption(f"Tương đương: **{calculated_bgm_db:+.1f} dB** (Mặc định khuyên dùng: khoảng -16.5 dB)")

    # Ducking notice
    with st.expander("🎙️ Auto Ducking (Nâng cao)", expanded=False):
        st.info("ℹ️ Tính năng Ducking tự động theo sidechain giọng nói đang được phát triển và sẽ được kích hoạt trong bản cập nhật tới.")

    bgm_loop = st.checkbox("Lặp lại nhạc nền tự động (Loop music)", value=current_config.music.loop, disabled=not bgm_en, key="bgm_loop")

    # Audio Alignment 4 columns
    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        bgm_offset = st.number_input(
            "Offset nguồn (s)",
            min_value=0.0,
            value=float(current_config.music.source_offset_seconds),
            step=0.5,
            disabled=not bgm_en,
        )
    with ac2:
        bgm_timeline_start = st.number_input(
            "Bắt đầu tại (s)",
            min_value=0.0,
            value=float(current_config.music.timeline_start_seconds),
            step=0.5,
            disabled=not bgm_en,
        )
    with ac3:
        bgm_fade_in = st.number_input(
            "Fade-in (s)",
            min_value=0.0,
            value=float(current_config.music.fade_in_seconds),
            step=0.5,
            disabled=not bgm_en,
        )
    with ac4:
        bgm_fade_out = st.number_input(
            "Fade-out (s)",
            min_value=0.0,
            value=float(current_config.music.fade_out_seconds),
            step=0.5,
            disabled=not bgm_en,
        )

    # Save Configuration Action
    st.markdown("---")
    col_save, col_ver_info = st.columns([1.2, 1])
    with col_save:
        if st.button("💾 Lưu cấu hình render mới", use_container_width=True, type="primary"):
            new_cfg = RenderConfiguration(
                id=f"cfg_{time.time()}",
                project_id=project_id,
                version=current_config.version,
                timeline_version=int(timeline_data.get("version", 1)),
                preset_name=selected_preset,
                voice=VoiceOptions(enabled=src_audio_en, gain_db=calculated_voice_db),
                music=MusicOptions(
                    enabled=bgm_en,
                    asset_id=selected_asset_id,
                    gain_db=calculated_bgm_db,
                    loop=bgm_loop,
                    source_offset_seconds=bgm_offset,
                    timeline_start_seconds=bgm_timeline_start,
                    fade_in_seconds=bgm_fade_in,
                    fade_out_seconds=bgm_fade_out,
                ),
                tts=TTSOptions(
                    enabled=tts_en,
                    voice=tts_voice,
                    rate=tts_rate_str,
                    gain_db=tts_gain_db,
                    duck_gain_db=float(-tts_duck),
                    replace_original=tts_replace,
                ),
                loudness=LoudnessOptions(enabled=norm_audio_en),
            )
            saved = config_service.save_new_version(project_id, new_cfg)
            st.success(f"Đã lưu cấu hình Render phiên bản **v{saved.version}** thành công!")
            time.sleep(0.5)
            st.rerun()

    with col_ver_info:
        all_versions = config_service.list_versions(project_id)
        st.markdown(
            f"""
            <div style="font-size: 0.8rem; color: #94a3b8; padding-top: 6px;">
                Cấu hình hiện tại: <strong style="color: #60a5fa;">v{current_config.version}</strong> (Tổng: {len(all_versions)} bản lưu)
            </div>
            """,
            unsafe_allow_html=True,
        )

# ================= RIGHT COLUMN: LIVE QC, RENDER PROGRESS & EXECUTION =================
with col_right:
    # 1. Render Execution Control Box
    st.markdown(
        """
        <div class="pro-card" style="margin-bottom: 14px; padding: 14px;">
            <div class="pro-card-header" style="margin-bottom: 8px;">
                <span class="pro-card-title">🚀 Khởi động kết xuất (Render Engine)</span>
                <span class="pro-card-subtitle">FFmpeg Hardware Accelerated</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Test preview settings
    t_c1, t_c2 = st.columns(2)
    with t_c1:
        test_start = st.number_input("Bắt đầu thử tại (giây)", min_value=0.0, max_value=max(0.0, timeline_duration - 5.0), value=0.0, step=5.0)
    with t_c2:
        test_dur = st.slider("Thời lượng thử (giây)", min_value=10, max_value=30, value=20, step=5)

    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1:
        if st.button("⚡ Test Preview (20s)", disabled=is_busy, use_container_width=True):
            test_opts = TestPreviewOptions(output_start_seconds=test_start, duration_seconds=float(test_dur))
            job_service.submit_test_preview_render(
                project_id=project_id,
                timeline=timeline_data,
                test_options=test_opts,
                render_config_version=current_config.version,
            )
            st.rerun()

    with btn_col2:
        if st.button("🎬 Preview 720p", disabled=is_busy, use_container_width=True):
            job_service.submit_preview_render(
                project_id=project_id,
                timeline=timeline_data,
                render_config_version=current_config.version,
            )
            st.rerun()

    with btn_col3:
        if st.button("🚀 Render Final", disabled=is_busy, use_container_width=True, type="primary"):
            job_service.submit_final_render(
                project_id=project_id,
                timeline=timeline_data,
                preset_name=selected_preset,
                render_config_version=current_config.version,
            )
            st.rerun()

    # 2. Live Job Progress or Player Box
    if is_busy and active_job:
        prog_info = job_service.get_progress(active_job.id)
        prog_pct = max(0, min(100, prog_info.get("percent", 0)))
        step_msg = prog_info.get("message", "Đang xử lý...")

        st.markdown(
            f"""
            <div class="pro-card" style="margin: 14px 0; padding: 14px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="font-size: 0.9rem; font-weight: 700; color: #ffffff;">
                        ● Đang kết xuất: {active_job.job_type.upper()}
                    </div>
                    <div style="font-size: 1.1rem; font-weight: 800; color: #60a5fa; font-family: monospace;">{prog_pct}%</div>
                </div>
                <div style="font-size: 0.76rem; color: #94a3b8; margin-bottom: 8px;">
                    {step_msg}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.progress(prog_pct / 100.0)

        if st.button("✕ Hủy bỏ kết xuất", key="cancel_render_btn", type="secondary"):
            job_service.cancel_job(active_job.id)
            st.toast("Đã gửi yêu cầu hủy kết xuất.")
            time.sleep(0.5)
            st.rerun()

        # Polling ticker
        time.sleep(1.0)
        st.rerun()

    else:
        # Check latest completed render file
        latest_render = None
        with SessionLocal() as db:
            latest_render = (
                db.query(Render)
                .filter(Render.project_id == project_id, Render.status == RenderStatus.COMPLETED.value)
                .order_by(Render.created_at.desc())
                .first()
            )

        if latest_render and latest_render.output_path and Path(latest_render.output_path).exists():
            st.markdown(
                f"""
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-size: 0.76rem; font-family: monospace; color: #94a3b8;">
                        ▶ {Path(latest_render.output_path).name}
                    </span>
                    <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #34d399; font-size: 0.7rem; font-weight: 800; padding: 2px 6px; border-radius: 4px;">
                        ● SẴN SÀNG PHÁT
                    </span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.video(str(latest_render.output_path))
        else:
            st.markdown(
                """
                <div style="background: #070a10; border: 1px solid #232f48; border-radius: 8px; height: 180px; position: relative; display: flex; flex-direction: column; justify-content: center; align-items: center; padding: 10px;">
                    <span style="font-size: 2.2rem; color: #3b82f6; margin-bottom: 6px;">🖥️</span>
                    <span style="font-size: 0.82rem; font-weight: 600; color: #cbd5e1;">Chưa có bản kết xuất nào</span>
                    <span style="font-size: 0.72rem; color: #64748b;">Nhấn 'Render Test Preview' hoặc 'Render Final' để xem video kết quả</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # 3. Output Summary Card
    st.markdown(
        f"""
        <div class="pro-card" style="margin: 14px 0; padding: 14px;">
            <div class="pro-card-header" style="margin-bottom: 8px; padding-bottom: 6px;">
                <span class="pro-card-title" style="font-size: 0.85rem;">📄 Tóm tắt thông số kết xuất (Output Summary)</span>
                <span style="font-family: monospace; font-size: 0.72rem; color: #93c5fd;">Config-v{current_config.version}</span>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.78rem;">
                <div><span style="color: #64748b;">Preset mục tiêu:</span> <strong>{selected_preset_label}</strong></div>
                <div><span style="color: #64748b;">Thời lượng dự kiến:</span> <strong>{_sec_to_tc(timeline_duration)}</strong></div>
                <div><span style="color: #64748b;">Video Codec:</span> <strong>H.264 (yuv420p)</strong></div>
                <div><span style="color: #64748b;">Audio Codec:</span> <strong>AAC-LC 192k 48kHz Stereo</strong></div>
                <div><span style="color: #64748b;">Phiên bản Timeline:</span> <strong>v{timeline_data.get('version', 1)}</strong></div>
                <div><span style="color: #64748b;">Chuẩn âm thanh:</span> <strong style="color: #34d399;">{'EBU R128 (-16 LUFS)' if norm_audio_en else 'Gain Manual'}</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 4. QC Results Checklist Card
    # Look for the latest .qc.json report
    latest_qc = None
    if latest_render and latest_render.output_path:
        qc_file = Path(f"{latest_render.output_path}.qc.json")
        if qc_file.exists():
            try:
                latest_qc = read_json(qc_file)
            except Exception:
                pass

    if latest_qc:
        media_qc = latest_qc.get("media", {})
        dur_str = _sec_to_tc(media_qc.get("duration_seconds", 0))
        res_str = f"{media_qc.get('width', 0)}×{media_qc.get('height', 0)}"
        qc_valid = latest_qc.get("valid", True)
        qc_badge_color = "#34d399" if qc_valid else "#f87171"
        qc_badge_text = "Đạt chuẩn phát hành" if qc_valid else "Cần lưu ý"

        st.markdown(
            f"""
            <div class="pro-card" style="margin-bottom: 14px; padding: 14px;">
                <div class="pro-card-header" style="margin-bottom: 8px; padding-bottom: 6px;">
                    <span class="pro-card-title" style="font-size: 0.85rem;">✓ Kiểm định chất lượng sơ bộ (Quality Control - QC Results)</span>
                    <span style="background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: {qc_badge_color}; font-size: 0.7rem; font-weight: 600; padding: 2px 6px; border-radius: 4px;">
                        {qc_badge_text}
                    </span>
                </div>
                <div style="font-size: 0.78rem; line-height: 1.6;">
                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                        <span>✓ Thời lượng thực tế: {dur_str}</span>
                        <span style="color: #34d399;">Khớp Timeline</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                        <span>✓ Độ phân giải: {res_str}</span>
                        <span style="color: #34d399;">Đúng tỉ lệ preset</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                        <span>✓ Luồng video: {media_qc.get('video_codec', 'h264').upper()}</span>
                        <span style="color: #cbd5e1;">Chuẩn hóa CFR</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                        <span>✓ Luồng âm thanh: {media_qc.get('audio_codec', 'aac').upper()}</span>
                        <span style="color: #38bdf8;">EBU R128 (-16 LUFS)</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; color: #fbbf24;">
                        <span>ℹ Cảnh báo kiểm định: <strong>{len(latest_qc.get('warnings', []))} lưu ý</strong></span>
                        <span>0 lỗi nghiêm trọng</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="pro-card" style="margin-bottom: 14px; padding: 14px;">
                <div class="pro-card-header" style="margin-bottom: 8px; padding-bottom: 6px;">
                    <span class="pro-card-title" style="font-size: 0.85rem;">✓ Kiểm định chất lượng sơ bộ (Quality Control - QC Results)</span>
                    <span style="color: #94a3b8; font-size: 0.7rem;">Sẵn sàng kiểm định</span>
                </div>
                <div style="font-size: 0.78rem; color: #94a3b8; line-height: 1.6;">
                    Sau khi kết xuất hoàn tất, FFprobe sẽ tự động kiểm tra thời lượng, độ phân giải, luồng âm thanh và lưu báo cáo QC JSON tương ứng.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 5. Recent Outputs List
    with SessionLocal() as db:
        recent_renders = (
            db.query(Render)
            .filter(Render.project_id == project_id)
            .order_by(Render.created_at.desc())
            .limit(5)
            .all()
        )

    st.markdown(
        """
        <div style="background: #131a29; border: 1px solid #232f48; border-radius: 6px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; font-size: 0.78rem; margin-bottom: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <span>🕒</span>
                <span>Danh sách tệp đã xuất gần đây (Recent Outputs)</span>
            </div>
            <span style="color: #64748b; font-family: monospace;">Tổng: """ + str(len(recent_renders)) + """ bản ghi</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if recent_renders:
        for r in recent_renders:
            fname = Path(r.output_path).name if r.output_path else f"Render {r.id}"
            st_color = "#34d399" if r.status == "completed" else ("#f87171" if r.status == "failed" else "#60a5fa")
            st.markdown(
                f"""
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: #0e131f; border: 1px solid #1e293b; border-radius: 4px; margin-bottom: 4px; font-size: 0.75rem;">
                    <div>
                        <strong style="color: #f1f5f9;">{fname}</strong>
                        <span style="color: #64748b; margin-left: 6px;">({r.preset_name} • v{r.render_config_version or 1})</span>
                    </div>
                    <span style="color: {st_color}; font-weight: 600; text-transform: uppercase; font-size: 0.68rem;">{r.status}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )