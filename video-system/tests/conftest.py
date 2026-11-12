"""Shared fixtures. Env MUST be set before importing backend.app.main."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
import struct
import wave

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_TMP = tempfile.mkdtemp(prefix="video_system_test_")
os.environ["VIDEO_DATA_DIR"] = _TMP
os.environ["VIDEO_API_KEYS"] = "test-admin:admin,test-ingest:ingest,test-read:read"
os.environ["VIDEO_ALLOWED_HOSTS"] = "localhost,127.0.0.1,storage.test"
os.environ["VIDEO_WEBHOOK_SECRET"] = "whsec-test"
os.environ.setdefault("VIDEO_OFFLINE_SEARCH", "1")
# Stock connector phải OFF trong test: .env thật có key Pixabay → pop TRƯỚC
# import (phòng config đọc sớm) và SAU import (config._load_dotenv_env set lại
# lúc import) để produce test không bắn call mạng thật.
os.environ.pop("VIDEO_PIXABAY_API_KEY", None)
os.environ.pop("VIDEO_PEXELS_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import providers  # noqa: E402
from backend.app.main import app, batches, items, shots  # noqa: E402
from backend.app.main import store as _app_store  # noqa: E402

os.environ.pop("VIDEO_PIXABAY_API_KEY", None)   # .env loader đã set lại lúc import
os.environ.pop("VIDEO_PEXELS_API_KEY", None)


@pytest.fixture()
def store_fixture():
    """Store toàn cục của app (dùng cho test gọi service trực tiếp)."""
    return _app_store


class FakeProbe:
    """Configurable probe — defaults satisfy render_spec."""

    def __init__(self, video=None, image=None, audio=None):
        self.video = video or {"width": 1280, "height": 720, "fps": 24.0,
                               "codec": "h264", "pix_fmt": "yuv420p",
                               "duration": 8.0, "has_audio": True}
        self.image = image or {"width": 1280, "height": 720}
        self.audio = audio or {"duration": 32.0}

    def probe_video(self, path):
        return dict(self.video)

    def probe_audio(self, path):
        return dict(self.audio)

    def probe_image(self, path):
        return dict(self.image)


class FakeOcr:
    def __init__(self, text_by_call=None):
        self.text_by_call = list(text_by_call or [])
        self.calls = []

    def ocr(self, path, at_seconds=0.0):
        self.calls.append(path)
        return self.text_by_call.pop(0) if self.text_by_call else ""


class FakeGeneration:
    engine_name = "veo"
    animate_calls = []

    def animate(self, keyframe_path, prompt, duration):
        self.animate_calls.append((keyframe_path, prompt, duration))
        # write a real file so _produce can hash it (manifest binding test)
        import tempfile
        p = Path(tempfile.mkdtemp(prefix="fakegen_")) / "video.mp4"
        p.write_bytes(b"FAKE_MP4_BYTES" * 100)
        return str(p)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def admin():
    return {"Authorization": "Bearer test-admin"}


@pytest.fixture()
def ingest():
    return {"Authorization": "Bearer test-ingest"}


@pytest.fixture()
def read_key():
    return {"Authorization": "Bearer test-read"}


@pytest.fixture()
def adapters():
    """Wire fake adapters; restore clean registry after the test."""
    saved = (providers.registry.probe, providers.registry.ocr,
             providers.registry.generation)
    probe, ocr, gen = FakeProbe(), FakeOcr(), FakeGeneration()
    providers.registry.probe = probe
    providers.registry.ocr = ocr
    providers.registry.generation = gen
    yield {"probe": probe, "ocr": ocr, "gen": gen}
    providers.registry.probe, providers.registry.ocr, providers.registry.generation = saved


@pytest.fixture()
def wav_file(tmp_path):
    """Writes under VIDEO_DATA_DIR/uploads — the import sandbox only accepts
    files there (same rule real clients must follow)."""
    def make(seconds=32.0, name="audio.wav"):
        from backend.app.config import data_dir
        p = data_dir() / "uploads" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * int(8000 * seconds))
        return p
    return make


def import_payload(version=1, duration=32.0, count=4, seg=8.0, **over):
    shots_list = []
    for i in range(1, count + 1):
        s = {"index": i, "shot_type": "scene",
             "narration": f"line {i}",
             "audio_start_sec": round((i - 1) * seg, 3),
             "audio_end_sec": round(i * seg, 3),
             "visual_prompt_clean": f"prompt {i}"}
        s.update(over)
        # default: a declared keyframe hash (keyframe-first is mandatory since
        # the audit fixes; tests that need "no keyframe" override this to None)
        s.setdefault("keyframe_sha256", "k" * 64)
        shots_list.append(s)
    return {"script_version": version, "language": "en",
            "text": "test script",
            "audio": {"file_path": None, "duration_sec": duration},
            "shots": shots_list}


@pytest.fixture()
def import_payload_factory():
    return import_payload


def make_item(client, admin, title="test item") -> str:
    r = client.post("/api/v1/items", headers=admin, json={
        "title": title, "source": {"type": "upload"},
        "target": {"language": "en", "style_lock_version": "v3"}})
    assert r.status_code == 201, r.text
    return r.json()["item_id"]


@pytest.fixture()
def make_item_fn(client, admin):
    return lambda title="test item": make_item(client, admin, title)


# --- Short Studio fakes (Phụ lục G) ------------------------------------------

def default_short_script():
    """Kịch bản mode 'news' đủ ngân sách 80–110 từ (tổng 107 từ)."""
    hook = "Chỉ sau một đêm duy nhất, tổng cộng 3 tỷ đồng đã biến mất khỏi tài khoản của hàng loạt khách hàng"
    beats = [
        "Sáng hôm qua, hàng loạt người dùng liên tục gọi điện báo giao dịch lạ dù chưa từng chuyển tiền cho ai",
        "Ngân hàng sau đó xác nhận rằng đây là nhóm đối tượng giả mạo nhân viên chăm sóc khách hàng để đánh cắp mã OTP",
        "Số tiền trung bình mỗi nạn nhân mất khoảng 50 triệu đồng, có người mất tới 200 triệu",
    ]
    closing = "Theo bạn, trong trường hợp này ngân hàng hay khách hàng mới là bên phải chịu trách nhiệm cuối cùng?"
    texts = [hook] + beats + [closing]
    wc = sum(len(t.split()) for t in texts)
    return {
        "title": "3 tỷ đồng biến mất sau một đêm",
        "hook": hook, "beats": beats, "closing": closing,
        "hashtags": {"tiktok": ["#tinhot", "#tientebac", "#antoannganhang"],
                     "facebook": ["#tinhot", "#canhbao"],
                     "youtube": ["#shorts", "#tinngan"]},
        "description": "Cảnh giác với giả mạo CSKH ngân hàng — câu chuyện 3 tỷ đồng.",
        "source_note": "Nguồn: VnExpress",
        "word_count": wc,
    }


class FakeLlm:
    def __init__(self, script=None):
        self.script = script if script is not None else default_short_script()
        self.calls = []

    def chat_json(self, system, user, schema=None):
        import json as _json
        self.calls.append({"system": system, "user": user, "schema": schema})
        return _json.loads(_json.dumps(self.script))  # deep copy mỗi lần gọi


class FakeImageGen:
    def __init__(self):
        self.calls = []

    def generate_image(self, prompt, out_path, size="1080x1920"):
        self.calls.append({"prompt": prompt, "out_path": out_path, "size": size})
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"FAKE_PNG_BYTES" * 50)
        return {"path": str(p), "model": "fake-image", "size_used": size}


@pytest.fixture()
def short_adapters():
    """Wire LLM + image-gen fakes; restore registry sau test (pattern `adapters`)."""
    saved = (providers.registry.llm, providers.registry.image_gen)
    llm, img = FakeLlm(), FakeImageGen()
    providers.registry.llm = llm
    providers.registry.image_gen = img
    yield {"llm": llm, "img": img}
    providers.registry.llm, providers.registry.image_gen = saved


@pytest.fixture()
def short_adapters_removed():
    """Bắt buộc fail-closed: LLM/image-gen không cấu hình."""
    saved = (providers.registry.llm, providers.registry.image_gen)
    providers.registry.llm = None
    providers.registry.image_gen = None
    yield
    providers.registry.llm, providers.registry.image_gen = saved


def make_fake_bank_image(name="bank_01.png"):
    """Ảnh template dọc thật (Pillow) cho bank fast-mode trong test."""
    from PIL import Image
    from backend.app.config import data_dir
    d = data_dir() / "shorts_bank"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    Image.new("RGB", (108, 192), (28, 25, 23)).save(p, format="PNG")
    return p
