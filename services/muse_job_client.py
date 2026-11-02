"""Client and payload helpers for the Muse Job Inbox API.

The client is intentionally small and transport-focused. It does not execute
video work locally: Muse owns the ``make_video`` orchestration after the VPS
creates and submits a job.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
from typing import Any, Mapping

import requests

logger = logging.getLogger("muse_job_client")

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_PRIORITY = 3


class MuseJobError(RuntimeError):
    """Base error for Muse Job Inbox failures."""


class MuseJobTransportError(MuseJobError):
    """Raised when the API cannot be reached or returns an invalid response."""


class MuseJobApiError(MuseJobError):
    """Raised for a valid API response with an HTTP error status."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(f"Muse API {status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MuseJobConfig:
    """Runtime configuration loaded from environment variables."""

    base_url: str
    token: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "MuseJobConfig":
        base_url = (
            os.environ.get("JOB_INBOX_INTERNAL_URL")
            or os.environ.get("MUSE_JOBS_BASE_URL", "")
        ).strip().rstrip("/")
        token = os.environ.get("CLAUDE_JOBS_TOKEN", "").strip()
        timeout_raw = os.environ.get("MUSE_JOBS_TIMEOUT_SECONDS", "20")
        try:
            timeout = max(1.0, float(timeout_raw))
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS
        return cls(base_url=base_url, token=token, timeout_seconds=timeout)

    def validate(self) -> None:
        if not self.base_url:
            raise MuseJobTransportError(
                "JOB_INBOX_INTERNAL_URL or MUSE_JOBS_BASE_URL is not configured"
            )
        if not self.token:
            raise MuseJobTransportError("CLAUDE_JOBS_TOKEN is not configured")
        if not self.base_url.startswith(("https://", "http://")):
            raise MuseJobTransportError("MUSE_JOBS_BASE_URL must include http:// or https://")
        if self.base_url.startswith("http://") and not _is_private_http_host(self.base_url):
            raise MuseJobTransportError("Public Muse Job Inbox URL must use HTTPS")


def _is_private_http_host(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("100.")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _read_script_text(script: str | Path) -> str:
    if isinstance(script, Path):
        return script.read_text(encoding="utf-8")
    candidate = Path(script)
    if "\n" not in script and candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return script


def normalize_script(script: str | Path) -> str:
    """Normalize a script while preserving meaningful line structure."""
    text = _read_script_text(script).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    normalized: list[str] = []
    blank_pending = False
    for line in lines:
        if line.strip():
            if blank_pending and normalized:
                normalized.append("")
            normalized.append(line)
            blank_pending = False
        elif normalized:
            blank_pending = True
    return "\n".join(normalized).strip()


def _infer_scene_count(script: str) -> int | None:
    matches = re.findall(r"\[CẢNH\s+(\d+)\]", script, flags=re.IGNORECASE)
    return len(set(matches)) or None


def _stable_job_id(factory_job_id: str, script: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", _clean_text(factory_job_id)).strip("-")
    digest = hashlib.sha256(script.encode("utf-8")).hexdigest()[:10]
    return f"video-{safe or 'job'}-{digest}"


def build_make_video_job(
    *,
    factory_job_id: str,
    script: str | Path,
    target_duration_sec: float,
    language: str = "vi",
    title: str = "",
    visual_style: str = "cinematic documentary, coherent characters, natural lighting",
    aspect_ratio: str = "16:9",
    voice: str = "vi-VN-NamMinhNeural",
    music_mood: str = "subtle documentary",
    priority: int = DEFAULT_PRIORITY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    source: str = "video-web-ui",
    note: str | None = None,
) -> dict[str, Any]:
    """Build an idempotent ``make_video`` request from an approved script."""
    script_text = normalize_script(script)
    if not script_text:
        raise ValueError("make_video requires a non-empty script")
    duration = float(target_duration_sec)
    if duration <= 0:
        raise ValueError("target_duration_sec must be greater than zero")
    if not 1 <= int(priority) <= 5:
        raise ValueError("priority must be between 1 and 5")
    if int(max_attempts) < 1:
        raise ValueError("max_attempts must be at least 1")

    scene_count = _infer_scene_count(script_text)
    payload: dict[str, Any] = {
        "story": title or "Approved video script",
        "script_source": "approved_script",
        "script": script_text,
        "target_duration_sec": duration,
        "language": _clean_text(language) or "vi",
        "visual_style": _clean_text(visual_style),
        "aspect_ratio": _clean_text(aspect_ratio) or "16:9",
        "voice": _clean_text(voice),
        "music_mood": _clean_text(music_mood),
        "source_metadata": {
            "factory_job_id": _clean_text(factory_job_id),
            "title": _clean_text(title),
            "scene_count_hint": scene_count,
            "script_sha256": hashlib.sha256(script_text.encode("utf-8")).hexdigest(),
        },
        "delivery_requirements": {
            "return_script": True,
            "return_scenes": True,
            "return_qc": True,
            "video_format": "mp4",
            "minimum_resolution": "1920x1080",
        },
    }
    payload["source_metadata"] = {
        key: value for key, value in payload["source_metadata"].items() if value not in (None, "")
    }
    job: dict[str, Any] = {
        "job_id": _stable_job_id(factory_job_id, script_text),
        "type": "make_video",
        "priority": int(priority),
        "max_attempts": int(max_attempts),
        "source": _clean_text(source) or "video-web-ui",
        "payload": payload,
    }
    if note:
        job["note"] = _clean_text(note)
    return job


class MuseJobClient:
    """Authenticated, idempotency-preserving HTTP client for Job Inbox."""

    def __init__(self, config: MuseJobConfig | None = None, session: requests.Session | None = None):
        self.config = config or MuseJobConfig.from_env()
        self.session = session or requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        self.config.validate()
        url = f"{self.config.base_url}/{path.lstrip('/')}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {self.config.token}")
        headers.setdefault("Accept", "application/json")
        if "json" in kwargs:
            headers.setdefault("Content-Type", "application/json")
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=kwargs.pop("timeout", self.config.timeout_seconds),
                **kwargs,
            )
        except requests.RequestException as exc:
            raise MuseJobTransportError(f"Muse API request failed: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise MuseJobTransportError(
                f"Muse API returned non-JSON response ({response.status_code})"
            ) from exc
        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, Mapping) else {}
            raise MuseJobApiError(
                response.status_code,
                str(error.get("code", "http_error")),
                str(error.get("message", "Muse API request failed")),
            )
        return data

    def create_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        required = ("job_id", "type", "payload")
        missing = [key for key in required if not job.get(key)]
        if missing:
            raise ValueError(f"Missing Muse job fields: {', '.join(missing)}")
        return self._request("POST", "/jobs", json=dict(job))

    def get_pending(self, limit: int = 10) -> dict[str, Any]:
        return self._request("GET", "/jobs/pending", params={"limit": max(1, min(int(limit), 100))})

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/jobs/{job_id}")

    def claim(self, job_id: str, worker: str = "claude-muse", lease_sec: int = 900) -> dict[str, Any]:
        return self._request("POST", f"/jobs/{job_id}/claim", json={"worker": worker, "lease_sec": lease_sec})

    def heartbeat(self, job_id: str, worker: str = "claude-muse", lease_sec: int = 900, progress_note: str = "") -> dict[str, Any]:
        body = {"worker": worker, "lease_sec": lease_sec}
        if progress_note:
            body["progress_note"] = progress_note
        return self._request("POST", f"/jobs/{job_id}/heartbeat", json=body)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/jobs/{job_id}/cancel", json={})

    def result(self, job_id: str) -> dict[str, Any]:
        """Fetch the complete job record, including Muse's result when done."""
        return self.get_job(job_id)


def submit_make_video(
    *,
    client: MuseJobClient,
    factory_job_id: str,
    script: str | Path,
    target_duration_sec: float,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build and submit a make_video job; safe to retry with the same inputs."""
    job = build_make_video_job(
        factory_job_id=factory_job_id,
        script=script,
        target_duration_sec=target_duration_sec,
        **kwargs,
    )
    return client.create_job(job)
