# ruff: noqa: N999
"""Settings page: LLM & VLM provider configuration (Presets, API key, base URL, models)."""

import streamlit as st

from core.logging_config import get_logger
from pipeline.llm_client import LLMResponseError, list_models
from services.settings_service import (
    DEFAULT_LLM_BASE_URL,
    LLM_BASE_URL_KEY,
    LLM_KEY,
    LLM_MODEL_KEY,
    LLM_PROVIDER_PRESET_KEY,
    PROVIDER_PRESETS,
    VLM_ENABLED_KEY,
    VLM_MODEL_KEY,
    get_llm_api_key,
    get_llm_base_url,
    get_llm_model,
    get_provider_preset,
    get_vlm_model,
    is_vlm_enabled,
    mask_secret,
    set_setting,
    verify_provider_connection,
)

logger = get_logger(__name__)

st.set_page_config(page_title="Settings", page_icon="⚙️", layout="wide")
st.title("⚙️ Cài đặt AI & Nhà Cung Cấp")
st.caption(
    "Cấu hình nhà cung cấp AI linh hoạt (OpenAI, Google Gemini, OpenRouter, Ollama local, Custom/xKiro). "
    "Hỗ trợ Vision-Language Model (VLM) để phân tích bối cảnh video từ keyframe. "
    "API key luôn được mã hóa Fernet an toàn trong database."
)

current_key = None
try:
    current_key = get_llm_api_key()
except ValueError:
    st.error(
        "API key đã lưu không giải mã được (master key thay đổi?). "
        "Vui lòng nhập lại API key."
    )

if current_key:
    st.info(f"🔑 API key hiện tại: `{mask_secret(current_key)}`")
else:
    st.warning("⚠️ Chưa có API key. Các tính năng AI (viết lại script, VLM captioning) sẽ không hoạt động nếu provider yêu cầu key.")

# --- Session State Initialization ---
saved_preset = get_provider_preset()
if saved_preset not in PROVIDER_PRESETS:
    saved_preset = "xkiro"

if "selected_preset" not in st.session_state:
    st.session_state["selected_preset"] = saved_preset

saved_llm_model = get_llm_model() or PROVIDER_PRESETS[saved_preset]["default_llm_model"]
saved_vlm_model = get_vlm_model() or PROVIDER_PRESETS[saved_preset]["default_vlm_model"]

if "model_options" not in st.session_state:
    preset_suggestions = PROVIDER_PRESETS[saved_preset].get("suggested_models", [])
    initial_options = list(dict.fromkeys([saved_llm_model, saved_vlm_model, *preset_suggestions]))
    st.session_state["model_options"] = [m for m in initial_options if m]

if "model_fetch_error" not in st.session_state:
    st.session_state["model_fetch_error"] = None

# --- Provider Preset Selection ---
st.subheader("🌐 Chọn Nhà Cung Cấp (Provider Preset)")

preset_keys = list(PROVIDER_PRESETS.keys())
preset_labels = [f"{PROVIDER_PRESETS[k]['name']}" for k in preset_keys]
current_idx = preset_keys.index(st.session_state["selected_preset"]) if st.session_state["selected_preset"] in preset_keys else 0

def _on_preset_change():
    selected = st.session_state["preset_selector"]
    st.session_state["selected_preset"] = selected
    preset_info = PROVIDER_PRESETS[selected]
    if selected != "custom" and preset_info.get("base_url"):
        st.session_state["input_base_url"] = preset_info["base_url"]
        if preset_info.get("suggested_models"):
            st.session_state["model_options"] = preset_info["suggested_models"]

chosen_preset = st.selectbox(
    "Preset nhà cung cấp có sẵn:",
    options=preset_keys,
    index=current_idx,
    format_func=lambda k: PROVIDER_PRESETS[k]["name"],
    key="preset_selector",
    on_change=_on_preset_change,
    help="Chọn nhà cung cấp để tự động điền endpoint Base URL và gợi ý models phù hợp.",
)

col_fetch, col_info = st.columns([1, 2])
with col_fetch:
    _fetch_key = None
    try:
        _fetch_key = get_llm_api_key()
    except ValueError:
        pass

    if st.button(
        "🔄 Tải danh sách models từ provider",
        use_container_width=True,
        disabled=not _fetch_key and chosen_preset != "ollama",
        help="Gọi endpoint /v1/models của provider để lấy danh sách model khả dụng.",
    ):
        try:
            with st.spinner("Đang tải danh sách models..."):
                _models = list_models()
            if _models:
                st.session_state["model_options"] = _models
                st.session_state["model_fetch_error"] = None
                st.success(f"Đã tải {len(_models)} models từ provider.")
            else:
                st.session_state["model_fetch_error"] = "Provider trả về danh sách rỗng."
        except LLMResponseError as exc:
            st.session_state["model_fetch_error"] = str(exc)
            logger.warning("Model list fetch failed: %s", exc)

with col_info:
    if st.session_state["model_fetch_error"]:
        st.error(f"Không tải được models: {st.session_state['model_fetch_error']}")
    else:
        st.caption(f"Đang cấu hình: **{PROVIDER_PRESETS[chosen_preset]['name']}**")

# --- Detailed Configuration Form ---
st.subheader("🛠️ Cấu hình Chi tiết Endpoint & Models")

with st.form("llm_settings_form"):
    new_key = st.text_input(
        "API Key",
        type="password",
        help="Dán API key mới để thay thế. Bỏ trống nếu giữ key hiện tại. Với Ollama có thể điền 'ollama'.",
    )

    default_base_url = get_llm_base_url() if chosen_preset == saved_preset else PROVIDER_PRESETS[chosen_preset].get("base_url", DEFAULT_LLM_BASE_URL)
    base_url = st.text_input(
        "Base URL (OpenAI-compatible)",
        value=default_base_url or DEFAULT_LLM_BASE_URL,
        help="Endpoint API tuân theo chuẩn OpenAI /v1. Ví dụ: https://api.openai.com/v1 hoặc http://localhost:11434/v1",
    )

    col_llm, col_vlm = st.columns(2)

    with col_llm:
        st.markdown("##### 📝 Text Model (Script / Viết lại)")
        avail_models = st.session_state.get("model_options", [saved_llm_model])
        if saved_llm_model not in avail_models and saved_llm_model:
            avail_models = [saved_llm_model, *avail_models]

        llm_choice = st.selectbox(
            "Model Text",
            options=avail_models,
            index=0,
            help="Model dùng để tổng hợp nội dung, viết lại script.",
        )
        use_manual_llm = st.checkbox("Nhập model Text thủ công", key="manual_llm_chk")
        model = st.text_input(
            "Tên Model Text (thủ công)",
            value=saved_llm_model,
            disabled=not use_manual_llm,
        ) if use_manual_llm else llm_choice

    with col_vlm:
        st.markdown("##### 👁️ VLM Model (AI Thị giác / Keyframe)")
        vlm_enabled = st.checkbox(
            "Kích hoạt VLM phân tích hình ảnh video",
            value=is_vlm_enabled(),
            help="Khi bật, hệ thống sẽ gửi keyframe tiêu biểu đến VLM để mô tả hành động và bối cảnh chân thực.",
        )
        vlm_choice = st.selectbox(
            "Model VLM",
            options=avail_models,
            index=avail_models.index(saved_vlm_model) if saved_vlm_model in avail_models else 0,
            help="Model có hỗ trợ thị giác (Multimodal vision: gpt-4o, gemini-2.0-flash, qwen2-vl,...).",
        )
        use_manual_vlm = st.checkbox("Nhập model VLM thủ công", key="manual_vlm_chk")
        vlm_model = st.text_input(
            "Tên Model VLM (thủ công)",
            value=saved_vlm_model,
            disabled=not use_manual_vlm,
        ) if use_manual_vlm else vlm_choice

    st.markdown("---")
    col_save, col_test = st.columns(2)
    with col_save:
        save_btn = st.form_submit_button("💾 Lưu tất cả cài đặt", use_container_width=True)
    with col_test:
        test_btn = st.form_submit_button("⚡ Kiểm tra kết nối & Vision", use_container_width=True)

if save_btn:
    if new_key:
        set_setting(LLM_KEY, new_key.strip())
        st.success(f"Đã lưu API key mới ({mask_secret(new_key)}).")
        logger.info("LLM API key updated via Settings UI")
    set_setting(LLM_PROVIDER_PRESET_KEY, chosen_preset)
    set_setting(LLM_BASE_URL_KEY, base_url.strip() or DEFAULT_LLM_BASE_URL)
    set_setting(LLM_MODEL_KEY, model.strip())
    set_setting(VLM_MODEL_KEY, vlm_model.strip())
    set_setting(VLM_ENABLED_KEY, "true" if vlm_enabled else "false")
    st.success("✅ Đã lưu toàn bộ cấu hình Provider, Model và VLM thành công!")
    st.rerun()

if test_btn:
    key_for_test = new_key.strip() if new_key else current_key
    target_model = vlm_model.strip() if vlm_enabled and vlm_model else model.strip()

    with st.spinner(f"Đang kiểm tra kết nối tới {base_url.strip()} với model '{target_model}'..."):
        res = verify_provider_connection(
            base_url=base_url.strip(),
            api_key=key_for_test,
            model_name=target_model,
            test_vision=vlm_enabled,
            timeout=20.0,
        )

    if res.get("success"):
        st.success(f"✅ {res['message']} (Độ trễ: {res.get('latency_ms', 0):.0f}ms)")
        if vlm_enabled:
            if res.get("vision_ok"):
                st.info("👁️ **Vision Check Thành Công:** Model phản hồi tốt với dữ liệu hình ảnh keyframe.")
            else:
                st.warning("⚠️ Model kết nối được nhưng không phản hồi kết quả Vision test. Kiểm tra xem model có hỗ trợ Vision không.")
        if res.get("available_models"):
            st.caption(f"Provider có {res['total_models']} models khả dụng: " + ", ".join(res["available_models"][:8]))
    else:
        st.error(f"❌ {res.get('message')}")

st.divider()
st.subheader("🔒 Bảo mật & Hoạt động Ngoại tuyến (Offline Fallback)")
st.markdown(
    "- **Bảo mật:** API key được mã hóa chuẩn **Fernet** trong SQLite. Master key được lưu tại file `.secret_key` độc lập.\n"
    "- **Tự do Provider:** Bạn có thể kết nối bất kỳ server OpenAI-compatible nào (Ollama nội bộ, vLLM, xKiro, OpenRouter, Gemini).\n"
    "- **Graceful Fallback:** Nếu chưa nhập API key hoặc provider bị lỗi mạng, pipeline video tự động dùng mô tả tất định (metadata camera/âm thanh), tuyệt đối không làm gãy pipeline biên tập."
)
