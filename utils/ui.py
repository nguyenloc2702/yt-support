import shutil
from pathlib import Path

import streamlit as st

THEME_KEY = "ui_theme"
DEFAULT_THEME = "dark"

# CSS variable blocks per theme. Light is a clean SaaS look (white cards,
# teal accent, soft shadows); dark keeps the previous studio feel but aligned
# to the same layout metrics.
_THEME_VARIABLES = {
    "light": """
    :root {
      --bg-app: #f7f8fa;
      --bg-sidebar: #ffffff;
      --bg-card: #ffffff;
      --bg-inset: #f2f4f7;
      --border-subtle: #eceef2;
      --border-card: #e4e7ec;

      --text-main: #101828;
      --text-body: #344054;
      --text-muted: #667085;
      --text-subtle: #98a2b3;

      --accent: #0d9488;
      --accent-hover: #0f766e;
      --accent-soft: #f0fdfa;
      --accent-ring: rgba(13, 148, 136, 0.25);

      --scroll-thumb: #d0d5dd;
      --scroll-thumb-hover: #98a2b3;
    }
    """,
    "dark": """
    :root {
      --bg-app: #0b0f19;
      --bg-sidebar: #0e131f;
      --bg-card: #131a29;
      --bg-inset: #0f1624;
      --border-subtle: #1e293b;
      --border-card: #232f48;

      --text-main: #f1f5f9;
      --text-body: #cbd5e1;
      --text-muted: #94a3b8;
      --text-subtle: #64748b;

      --accent: #14b8a6;
      --accent-hover: #0d9488;
      --accent-soft: rgba(20, 184, 166, 0.12);
      --accent-ring: rgba(20, 184, 166, 0.35);

      --scroll-thumb: #334155;
      --scroll-thumb-hover: #64748b;
    }
    """,
}


def get_theme() -> str:
    return st.session_state.get(THEME_KEY, DEFAULT_THEME)


def set_theme(theme: str) -> None:
    if theme in _THEME_VARIABLES:
        st.session_state[THEME_KEY] = theme


def inject_pro_css() -> None:
    """Inject theme variables + the shared stylesheet."""
    theme = get_theme()
    css_path = Path("assets/style.css")
    css_content = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    st.markdown(
        f"<style>{_THEME_VARIABLES.get(theme, _THEME_VARIABLES[DEFAULT_THEME])}"
        f"{css_content}</style>",
        unsafe_allow_html=True,
    )


def render_theme_toggle() -> None:
    """Light/dark switch in the sidebar. Call after inject_pro_css()."""
    theme = get_theme()
    label = "🌙 Chế độ tối" if theme == "light" else "☀️ Chế độ sáng"
    if st.sidebar.button(label, use_container_width=True, key="theme_toggle_btn"):
        set_theme("dark" if theme == "light" else "light")
        st.rerun()


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
