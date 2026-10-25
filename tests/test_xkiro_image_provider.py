"""Unit tests for the xKiro async image generation provider."""

from types import SimpleNamespace

import pytest


@pytest.fixture
def provider(monkeypatch):
    from services import xkiro_image_provider as mod

    monkeypatch.setattr(mod, "_api_key", lambda: "sk-test-image-key")
    monkeypatch.setattr(mod, "_api_base", lambda: "https://api.test/v1")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def _resp(status=200, body=None, content=b"", text=""):
    return SimpleNamespace(
        status_code=status,
        json=lambda: body if body is not None else {},
        content=content,
        text=text,
    )


def _poll_then_download(polls, download):
    state = {"calls": 0}

    def fake_get(url, headers=None, timeout=None):
        if url.startswith("https://cdn.test/"):
            return download
        state["calls"] += 1
        return polls[min(state["calls"], len(polls)) - 1]

    return fake_get


def test_edit_image_posts_multipart_and_saves_result(tmp_path, monkeypatch, provider):
    src = tmp_path / "shot_007.jpg"
    src.write_bytes(b"\xff\xd8\xff" + b"x" * 2000)
    posts = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        posts.append({"url": url, "data": data, "files": files})
        return _resp(202, {"id": "job-edit", "status": "processing"})

    download = _resp(200, content=b"\xff\xd8\xff" + b"y" * 2000)
    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        _poll_then_download(
            [
                _resp(200, {"status": "processing"}),
                _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/e.jpg"}]}),
            ],
            download,
        ),
    )

    out = tmp_path / "shot_007_pose1.jpg"
    used = provider.edit_xkiro_image("change only the arm pose", src, out)

    assert used == provider.DEFAULT_IMAGE_MODEL
    assert out.read_bytes().startswith(b"\xff\xd8\xff")
    assert posts[0]["url"].endswith("/images/edits")
    assert posts[0]["data"]["prompt"] == "change only the arm pose"
    # The gateway rejects a multipart "n" field with a 400
    assert "n" not in posts[0]["data"]
    assert "image" in posts[0]["files"]


def test_edit_image_falls_back_to_free_model_without_credit(tmp_path, monkeypatch, provider):
    src = tmp_path / "shot_007.jpg"
    src.write_bytes(b"\xff\xd8\xff" + b"x" * 2000)
    models = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        models.append(data["model"])
        if data["model"] == provider.DEFAULT_IMAGE_MODEL:
            return _resp(402, {"error": {"message": "Insufficient wallet balance"}}, text="Insufficient wallet balance")
        return _resp(202, {"id": "job-free"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"\xff\xd8\xff" + b"z" * 2000)
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/f.jpg"}]})
        ),
    )

    out = tmp_path / "pose.jpg"
    used = provider.edit_xkiro_image("pose", src, out)

    assert used == provider.FREE_IMAGE_MODEL
    assert models == [provider.DEFAULT_IMAGE_MODEL, provider.FREE_IMAGE_MODEL]
    assert out.exists()


def test_generate_image_polls_job_and_saves_download(tmp_path, monkeypatch, provider):
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append(json)
        return _resp(202, {"id": "job-1", "status": "processing"})

    polls = [
        _resp(200, {"id": "job-1", "status": "processing"}),
        _resp(200, {"id": "job-1", "status": "succeeded", "data": [{"url": "https://cdn.test/a.png"}]}),
    ]
    download = _resp(200, content=b"\x89PNG" + b"x" * 2000)

    def fake_get(url, headers=None, timeout=None):
        if url.startswith("https://cdn.test/"):
            return download
        return polls.pop(0)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)

    out = tmp_path / "char" / "reference.png"
    used = provider.generate_xkiro_image("crayon portrait", out)

    assert used == provider.DEFAULT_IMAGE_MODEL
    assert out.read_bytes().startswith(b"\x89PNG")
    assert posts[0]["model"] == provider.DEFAULT_IMAGE_MODEL
    assert posts[0]["prompt"] == "crayon portrait"
    assert not list(out.parent.glob(".*.tmp"))


def test_generate_image_failed_job_raises_without_file(tmp_path, monkeypatch, provider):
    monkeypatch.setattr("requests.post", lambda *a, **k: _resp(202, {"id": "job-bad"}))
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: _resp(200, {"id": "job-bad", "status": "failed", "error": "content policy"}),
    )

    out = tmp_path / "reference.png"
    with pytest.raises(provider.ImageGenerationError, match="failed"):
        provider.generate_xkiro_image("x", out, fallback_model=provider.DEFAULT_IMAGE_MODEL)
    assert not out.exists()


def test_generate_image_retries_when_rate_limited(tmp_path, monkeypatch, provider):
    sleeps = []
    monkeypatch.setattr(provider.time, "sleep", lambda s: sleeps.append(s))
    calls = {"post": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] += 1
        if calls["post"] == 1:
            return _resp(429, {"error": {"message": "2 image requests in progress"}})
        return _resp(202, {"id": "job-rl"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"img" * 1000)
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/x.png"}]})
        ),
    )

    out = tmp_path / "reference.png"
    provider.generate_xkiro_image("x", out, fallback_model=provider.DEFAULT_IMAGE_MODEL)

    assert calls["post"] == 2
    assert provider.RATE_LIMIT_WAIT_SECONDS in sleeps
    assert out.exists()


def test_generate_image_falls_back_to_free_model(tmp_path, monkeypatch, provider):
    models_hit = []

    def fake_post(url, json=None, headers=None, timeout=None):
        models_hit.append(json["model"])
        if json["model"] == provider.DEFAULT_IMAGE_MODEL:
            return _resp(503, text="model does not exist or is not routable")
        return _resp(202, {"id": "job-free"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"img" * 1000)
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/x.png"}]})
        ),
    )

    out = tmp_path / "reference.png"
    used = provider.generate_xkiro_image("x", out)

    assert used == provider.FREE_IMAGE_MODEL
    assert models_hit == [provider.DEFAULT_IMAGE_MODEL, provider.FREE_IMAGE_MODEL]
    assert out.exists()


def test_generate_image_rejects_tiny_payload(tmp_path, monkeypatch, provider):
    monkeypatch.setattr("requests.post", lambda *a, **k: _resp(202, {"id": "job-tiny"}))
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"x")
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/x.png"}]})
        ),
    )

    out = tmp_path / "reference.png"
    with pytest.raises(provider.ImageGenerationError, match="too small"):
        provider.generate_xkiro_image(
            "x", out, fallback_model=provider.DEFAULT_IMAGE_MODEL
        )
    assert not out.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def _succeed_on_second_request(monkeypatch, provider, posts):
    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append(json["size"])
        return _resp(202, {"id": "job-wide"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"img" * 1000)
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/x.png"}]})
        ),
    )


def test_landscape_size_is_forwarded(tmp_path, monkeypatch, provider):
    posts = []
    _succeed_on_second_request(monkeypatch, provider, posts)

    out = tmp_path / "shot.jpg"
    provider.generate_xkiro_image("wide crayon scene", out, size="1792x1024")

    assert posts == ["1792x1024"]
    assert out.exists()


def test_unsupported_size_retries_square(tmp_path, monkeypatch, provider):
    sizes = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sizes.append(json["size"])
        if json["size"] == "1792x1024":
            return _resp(400, {"error": {"message": "invalid size"}}, text="invalid size")
        return _resp(202, {"id": "job-square"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(
        "requests.get",
        lambda url, **k: (
            _resp(200, content=b"img" * 1000)
            if url.startswith("https://cdn.test/")
            else _resp(200, {"status": "succeeded", "data": [{"url": "https://cdn.test/x.png"}]})
        ),
    )

    out = tmp_path / "shot.jpg"
    used = provider.generate_xkiro_image("wide crayon scene", out, size="1792x1024")

    assert used == provider.DEFAULT_IMAGE_MODEL
    assert sizes == ["1792x1024", "1024x1024"]
    assert out.exists()
