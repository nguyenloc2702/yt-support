"""Tests for Muse Job Inbox client and make_video payload normalization."""

from pathlib import Path

import pytest
import requests

from services.muse_job_client import (
    MuseJobApiError,
    MuseJobClient,
    MuseJobConfig,
    build_make_video_job,
)


def test_build_make_video_job_is_deterministic_and_normalizes_script(tmp_path: Path):
    script_path = tmp_path / "script.txt"
    script_path.write_text("[CẢNH 001]  Hello  \r\n\r\n[CẢNH 002] World\r\n", encoding="utf-8")

    first = build_make_video_job(
        factory_job_id="remake_001",
        script=script_path,
        target_duration_sec=60,
        title="Money documentary",
    )
    second = build_make_video_job(
        factory_job_id="remake_001",
        script=script_path,
        target_duration_sec=60,
        title="Money documentary",
    )

    assert first["job_id"] == second["job_id"]
    assert first["type"] == "make_video"
    assert first["payload"]["script"] == "[CẢNH 001]  Hello\n\n[CẢNH 002] World"
    assert first["payload"]["source_metadata"]["scene_count_hint"] == 2
    assert first["payload"]["delivery_requirements"]["return_qc"] is True


def test_build_make_video_job_rejects_invalid_input():
    with pytest.raises(ValueError, match="non-empty script"):
        build_make_video_job(factory_job_id="x", script=" ", target_duration_sec=10)
    with pytest.raises(ValueError, match="greater than zero"):
        build_make_video_job(factory_job_id="x", script="script", target_duration_sec=0)
    with pytest.raises(ValueError, match="priority"):
        build_make_video_job(factory_job_id="x", script="script", target_duration_sec=10, priority=6)


def test_config_requires_https_for_public_url(monkeypatch):
    monkeypatch.setenv("MUSE_JOBS_BASE_URL", "http://example.com/api/claude-jobs")
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", "x" * 32)
    with pytest.raises(RuntimeError, match="HTTPS"):
        MuseJobConfig.from_env().validate()


def test_client_adds_auth_and_returns_json():
    class FakeResponse:
        status_code = 201

        def json(self):
            return {"ok": True, "job_id": "job-1", "status": "pending"}

    class FakeSession:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return FakeResponse()

    session = FakeSession()
    client = MuseJobClient(
        MuseJobConfig("https://muse.example/api/claude-jobs", "secret-token"),
        session=session,
    )
    result = client.create_job({"job_id": "job-1", "type": "make_video", "payload": {"script": "x"}})

    assert result["status"] == "pending"
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer secret-token"
    assert session.calls[0][2]["json"]["type"] == "make_video"


def test_client_maps_api_errors():
    class FakeResponse:
        status_code = 409

        def json(self):
            return {"ok": False, "error": {"code": "job_already_claimed", "message": "busy"}}

    class FakeSession:
        def request(self, *args, **kwargs):
            return FakeResponse()

    client = MuseJobClient(
        MuseJobConfig("https://muse.example/api/claude-jobs", "secret-token"),
        session=FakeSession(),
    )
    with pytest.raises(MuseJobApiError) as exc_info:
        client.get_job("job-1")
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "job_already_claimed"
