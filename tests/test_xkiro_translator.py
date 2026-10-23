"""Unit tests for the xKiro batch translator (services.xkiro_translator)."""

import json

import pytest

import services.xkiro_translator as xt
from services.xkiro_translator import BATCH_SIZE, TranslationError, translate_batch


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "translation_cache.json"
    monkeypatch.setattr(xt, "_CACHE_PATH", cache_file)
    # Avoid touching the real settings DB for the model name
    monkeypatch.setattr(
        "services.settings_service.get_xkiro_generation_model",
        lambda: "test-model",
    )
    return cache_file


def test_empty_items_no_gateway_call(monkeypatch):
    calls = {"n": 0}

    def fake_chat_json(*a, **kw):
        calls["n"] += 1
        return {"translations": []}

    monkeypatch.setattr(xt, "chat_json", fake_chat_json)
    assert translate_batch([], "en", "vi") == []
    assert calls["n"] == 0


def test_batching_chunks_by_batch_size(monkeypatch, isolated_cache):
    calls = []

    def fake_chat_json(system_prompt, user_prompt, **kw):
        calls.append(user_prompt)
        # Count numbered lines in the prompt to know the chunk size
        n_lines = sum(1 for ln in user_prompt.splitlines() if ln[:2].rstrip(".").isdigit())
        return {"translations": [f"dịch {i}" for i in range(n_lines)]}

    monkeypatch.setattr(xt, "chat_json", fake_chat_json)

    items = [(str(i), f"line {i}") for i in range(BATCH_SIZE * 2 + 5)]
    out = translate_batch(items, "en", "vi")

    assert len(out) == len(items)
    assert len(calls) == 3  # 20 + 20 + 5
    assert out[0] == "dịch 0"
    assert out[20] == "dịch 0"  # second chunk restarts numbering
    assert out[-1] == "dịch 4"  # last chunk has 5 items


def test_cache_hit_skips_gateway(monkeypatch, isolated_cache):
    calls = []

    def fake_chat_json(system_prompt, user_prompt, **kw):
        calls.append(user_prompt)
        n_lines = sum(1 for ln in user_prompt.splitlines() if ln[:2].rstrip(".").isdigit())
        return {"translations": [f"dịch {i}" for i in range(n_lines)]}

    monkeypatch.setattr(xt, "chat_json", fake_chat_json)

    first = translate_batch([("a", "hello world")], "en", "vi")
    assert first == ["dịch 0"]
    assert isolated_cache.exists()

    # Second identical call must be served entirely from the persistent cache
    second = translate_batch([("a", "hello world")], "en", "vi")
    assert second == ["dịch 0"]
    assert len(calls) == 1


def test_count_mismatch_raises(monkeypatch, isolated_cache):
    monkeypatch.setattr(xt, "chat_json", lambda *a, **kw: {"translations": ["only one"]})
    with pytest.raises(TranslationError, match="returned"):
        translate_batch([("a", "one"), ("b", "two")], "en", "vi")


def test_empty_translation_line_raises(monkeypatch, isolated_cache):
    monkeypatch.setattr(xt, "chat_json", lambda *a, **kw: {"translations": ["", "ok"]})
    with pytest.raises(TranslationError, match="empty line"):
        translate_batch([("a", "one"), ("b", "two")], "en", "vi")


def test_glossary_pinned_in_prompt(monkeypatch, isolated_cache):
    captured = {}

    def fake_chat_json(system_prompt, user_prompt, **kw):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return {"translations": ["ngân hàng trung ương"]}

    monkeypatch.setattr(xt, "chat_json", fake_chat_json)
    translate_batch([("a", "The central bank raised rates")], "en", "vi")

    assert "THUẬT NGỮ BẮT BUỘC" in captured["user"]
    assert '"central bank" => "ngân hàng trung ương"' in captured["user"]


def test_translations_persisted_atomically(monkeypatch, isolated_cache):
    monkeypatch.setattr(xt, "chat_json", lambda *a, **kw: {"translations": ["xin chào"]})
    translate_batch([("a", "hello")], "en", "vi")

    data = json.loads(isolated_cache.read_text(encoding="utf-8"))
    assert len(data) == 1
    # Key is sha256(source|target|text) truncated to 32 chars
    key = xt._cache_key("hello", "en", "vi")
    assert data[key] == "xin chào"
    assert not isolated_cache.with_suffix(".tmp").exists()
