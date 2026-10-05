"""Settings page: LLM provider configuration (API key, base URL, model)."""

import streamlit as st

from core.logging_config import get_logger
from pipeline.llm_client import LLMResponseError, list_models
from services.settings_service import (
    DEFAULT_LLM_BASE_URL,
    LLM_BASE_URL_KEY,
    LLM_KEY,
    LLM_MODEL_KEY,
    get_llm_api_key,
    get_setting,
    mask_secret,
    set_setting,
)

logger = get_logger(__name__)

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Settings")
st.caption(
    "Cấu hình nhà cung cấp AI (OpenAI-compatible, ví dụ xKiro). "
    "API key được mã hóa trước khi lưu vào database."
)

st.subheader("🔑 Kết nối LLM")

current_key = None
try:
    current_key = get_llm_api_key()
except ValueError:
    st.error(
        "API key đã lưu không giải mã được (master key thay đổi?). "
        "Vui lòng nhập lại API key."
    )

if current_key:
    st.info(f"API key hiện tại: `{mask_secret(current_key)}`")
else:
    st.warning("Chưa có API key. Các tính năng AI (viết lại script) sẽ không hoạt động.")

# --- Model selection (outside form: needs live fetch from provider) ---
st.markdown("###### 🤖 Chọn model AI")

_prefill = get_setting(LLM_MODEL_KEY) or "gemini-2.0-flash"
if "model_options" not in st.session_state:
    st.session_state["model_options"] = [_prefill]
if "model_fetch_error" not in st.session_state:
    st.session_state["model_fetch_error"] = None

# Button to fetch the model list from the provider using current key/url
_fetch_key = None
try:
    _fetch_key = get_llm_api_key()
except ValueError:
    pass

if st.button(
    "🔄 Tải danh sách models từ provider",
    use_container_width=True,
    disabled=not _fetch_key,
    help="Gọi endpoint /v1/models của provider để lấy danh sách model khả dụng.",
):
    try:
        with st.spinner("Đang tải danh sách models..."):
            _models = list_models()
        if _models:
            st.session_state["model_options"] = _models
            st.session_state["model_fetch_error"] = None
            if _prefill not in _models:
                _models.insert(0, _prefill)
            st.success(f"Đã tải {len(_models)} models từ provider.")
        else:
            st.session_state["model_fetch_error"] = "Provider trả về danh sách rỗng."
    except LLMResponseError as exc:
        st.session_state["model_fetch_error"] = str(exc)
        logger.warning("Model list fetch failed: %s", exc)

if st.session_state["model_fetch_error"]:
    st.error(f"Không tải được danh sách models: {st.session_state['model_fetch_error']}")

with st.form("llm_settings_form"):
    new_key = st.text_input(
        "API Key",
        type="password",
        help="Dán API key mới để thay thế. Bỏ trống nếu giữ key hiện tại.",
    )
    base_url = st.text_input(
        "Base URL",
        value=get_setting(LLM_BASE_URL_KEY) or DEFAULT_LLM_BASE_URL,
        help="OpenAI-compatible endpoint. Mặc định: https://api.xkiro.com/v1",
    )

    model_choice = st.selectbox(
        "Model (chọn từ danh sách)",
        options=st.session_state["model_options"],
        index=0,
        help="Bấm 'Tải danh sách models' phía trên để cập nhật danh sách từ provider.",
    )
    use_manual_model = st.checkbox(
        "Nhập model thủ công",
        help="Tick nếu model bạn muốn không có trong danh sách.",
    )
    model = st.text_input(
        "Model (thủ công)",
        value=_prefill,
        disabled=not use_manual_model,
        help="Tên model dùng cho chat completion.",
    ) if use_manual_model else model_choice

    col_save, col_test = st.columns(2)
    with col_save:
        save_btn = st.form_submit_button("💾 Lưu cài đặt", use_container_width=True)
    with col_test:
        test_btn = st.form_submit_button("🔌 Kiểm tra kết nối", use_container_width=True)


def _test_connection(key: str, url: str, mdl: str) -> None:
    """Attempt a live request to the provider and report the outcome."""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=url, timeout=30)
        models = sorted(m.id for m in client.models.list())
        st.success(f"✅ Kết nối thành công! Provider có {len(models)} models.")
        if models:
            st.caption("Một số models khả dụng: " + ", ".join(models[:10]))
        if mdl not in models and models:
            st.warning(f"Model '{mdl}' không có trong danh sách models của provider.")
    except ImportError:
        st.error("Chưa cài package 'openai'. Chạy: pip install openai")
    except Exception as exc:
        st.error(f"❌ Kết nối thất bại: {exc}")
        logger.warning("LLM connection test failed: %s", exc)


if save_btn:
    if new_key:
        set_setting(LLM_KEY, new_key)
        st.success(f"Đã lưu API key ({mask_secret(new_key)}).")
        logger.info("LLM API key updated via Settings UI")
    set_setting(LLM_BASE_URL_KEY, base_url.strip() or DEFAULT_LLM_BASE_URL)
    set_setting(LLM_MODEL_KEY, model.strip())
    st.success("Đã lưu Base URL và Model.")
    st.rerun()

if test_btn:
    key_for_test = new_key or current_key
    if not key_for_test:
        st.error("Chưa có API key để kiểm tra. Hãy nhập API key trước.")
    else:
        with st.spinner("Đang kiểm tra kết nối tới provider..."):
            _test_connection(key_for_test, base_url.strip(), model.strip())

st.divider()
st.subheader("🔒 Bảo mật")
st.markdown(
    "- API key được mã hóa **Fernet** trước khi lưu vào SQLite; master key nằm ở "
    "`DATA_DIR/.secret_key` (tự sinh lần đầu chạy).\n"
    "- Fallback thứ tự đọc key: **DB → biến môi trường `XKIRO_API_KEY` → `OPENAI_API_KEY`**.\n"
    "- Key chỉ hiển thị dạng che (`sk-...ab12`), không bao giờ hiển thị full."
)
