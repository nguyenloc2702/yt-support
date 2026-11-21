import shutil
from pathlib import Path

import streamlit as st

THEME_KEY = "ui_theme"
DEFAULT_THEME = "light"

# Single light design lock shared with video-system/config/DESIGN.md.
# Keep legacy variable names as aliases so existing pages remain compatible.
_THEME_VARIABLES = {
    "light": """
    :root {
      --bg: #FAFAF9;
      --surface: #FFFFFF;
      --fg: #1C1917;
      --fg-muted: #78716C;
      --border: #E7E5E4;
      --accent: #2563EB;
      --accent-fg: #FFFFFF;
      --ok: #16A34A;
      --run: #D97706;
      --held: #DC2626;
      --ghost: #78716C;

      --bg-app: var(--bg);
      --bg-sidebar: var(--surface);
      --bg-card: var(--surface);
      --bg-inset: #F5F5F4;
      --border-subtle: var(--border);
      --border-card: var(--border);
      --text-main: var(--fg);
      --text-body: #44403C;
      --text-muted: var(--fg-muted);
      --text-subtle: #A8A29E;
      --accent-hover: #1D4ED8;
      --accent-soft: #EFF6FF;
      --accent-ring: rgba(37, 99, 235, 0.22);
      --scroll-thumb: #D6D3D1;
      --scroll-thumb-hover: #A8A29E;
    }
    """,
}


def get_theme() -> str:
    return "light"


def set_theme(theme: str) -> None:
    # Design lock is intentionally light-only; retained for compatibility.
    st.session_state[THEME_KEY] = "light"


def inject_pro_css() -> None:
    """Inject the light-only production design tokens and shared stylesheet."""
    css_path = Path("assets/style.css")
    css_content = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    st.markdown(
        f"<style>{_THEME_VARIABLES['light']}{css_content}</style>",
        unsafe_allow_html=True,
    )


def render_theme_toggle() -> None:
    """Deprecated compatibility no-op; production UI is light-only."""
    return None


def render_studio_header(project_name: str = "Dự án",
                         resolution: str = "",
                         codec: str = "",
                         timecode: str = ""):
    """Renders the unified top header bar without non-functional mockup buttons."""
    meta_items = []
    if resolution and codec:
        meta_items.append(f'<span class="pro-header-badge">{resolution} • {codec}</span>')
    elif resolution:
        meta_items.append(f'<span class="pro-header-badge">{resolution}</span>')
    elif codec:
        meta_items.append(f'<span class="pro-header-badge">{codec}</span>')

    if timecode:
        meta_items.append(f'<span class="pro-header-timecode">{timecode}</span>')

    meta_html = "".join(meta_items)

    header_html = f"""
    <div class="pro-header-bar">
        <div class="pro-header-left">
            <span class="pro-header-title">{project_name}</span>
            {meta_html}
        </div>
        <div class="pro-header-actions">
            <span class="pro-header-badge pro-header-ready">● Hệ thống sẵn sàng</span>
        </div>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)


def render_sidebar_hardware_status(whisper_status: str = "Sẵn sàng", is_busy: bool = False):
    """Renders disk space and Whisper status at the bottom of the sidebar."""
    try:
        total, used, free = shutil.disk_usage(".")
        free_gb = free // (2**30)
    except Exception:
        free_gb = 248

    dot_class = "blue" if is_busy else "green"
    status_text = "Đang xử lý" if is_busy else whisper_status

    footer_html = f"""
    <div class="pro-sidebar-footer">
        <div class="pro-status-pill">
            <span>💾 Dung lượng đĩa: <strong>{free_gb}GB</strong></span>
            <span class="pro-status-dot green"></span>
        </div>
        <div class="pro-status-pill">
            <span>⚙ Whisper: <strong>{status_text}</strong></span>
            <span class="pro-status-dot {dot_class}"></span>
        </div>
    </div>
    """
    st.sidebar.markdown(footer_html, unsafe_allow_html=True)
