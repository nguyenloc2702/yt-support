"""Contract tests for the local Muse Job Inbox API."""

import importlib

from fastapi.testclient import TestClient


def test_job_inbox_lifecycle(tmp_path, monkeypatch):
    db_path = tmp_path / "inbox.sqlite3"
    monkeypatch.setenv("JOB_INBOX_DB_PATH", str(db_path))
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", "test-token")
    import services.job_inbox_api as module
    module = importlib.reload(module)
    client = TestClient(module.app)
    headers = {"Authorization": "Bearer test-token"}
    job = {
        "job_id": "video-test-001",
        "type": "make_video",
        "payload": {"script": "[CẢNH 001] Test"},
    }

    response = client.post("/jobs", json=job, headers=headers)
    assert response.status_code == 200
    assert response.json()["created"] is True

    duplicate = client.post("/jobs", json=job, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False

    pending = client.get("/jobs/pending", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["ok"] is True
    assert pending.json()["count"] == 1
    assert pending.json()["jobs"][0]["job_id"] == "video-test-001"

    claim = client.post(
        "/jobs/video-test-001/claim",
        json={"worker": "claude-muse", "lease_sec": 60},
        headers=headers,
    )
    assert claim.status_code == 200
    assert claim.json()["job"]["status"] == "running"

    heartbeat = client.post(
        "/jobs/video-test-001/heartbeat",
        json={"worker": "claude-muse", "lease_sec": 60, "progress_note": "rendering"},
        headers=headers,
    )
    assert heartbeat.status_code == 200

    result = client.post(
        "/jobs/video-test-001/result",
        json={"summary": "done", "outputs": {"video_url": "https://example.test/video.mp4"}},
        headers=headers,
    )
    assert result.status_code == 200
    assert result.json()["status"] == "done"
    duplicate = client.post(
        "/jobs/video-test-001/result",
        json={"summary": "done", "outputs": {"video_url": "https://example.test/video.mp4"}},
        headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_retryable_failure_returns_job_to_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_INBOX_DB_PATH", str(tmp_path / "retry.sqlite3"))
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", "test-token")
    import importlib
    import services.job_inbox_api as module
    module = importlib.reload(module)
    client = TestClient(module.app)
    headers = {"Authorization": "Bearer test-token"}
    job = {"job_id": "retry-job", "type": "write_script", "max_attempts": 2, "payload": {"idea": "test"}}
    assert client.post("/jobs", json=job, headers=headers).status_code == 200
    assert client.post("/jobs/retry-job/claim", json={"worker": "claude-muse", "lease_sec": 60}, headers=headers).status_code == 200
    failed = client.post(
        "/jobs/retry-job/fail",
        json={"error": {"code": "temporary", "message": "retry"}, "retryable": True},
        headers=headers,
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "pending"


def test_job_inbox_requires_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_INBOX_DB_PATH", str(tmp_path / "inbox.sqlite3"))
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", "test-token")
    import services.job_inbox_api as module
    module = importlib.reload(module)
    client = TestClient(module.app)
    assert client.get("/jobs/pending").status_code == 401


def test_pending_jobs_ordered_by_priority_asc_and_created_at_asc(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_INBOX_DB_PATH", str(tmp_path / "order.sqlite3"))
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", "test-token")
    import services.job_inbox_api as module
    module = importlib.reload(module)
    client = TestClient(module.app)
    headers = {"Authorization": "Bearer test-token"}

    client.post("/jobs", json={"job_id": "job-p3-1", "type": "test", "priority": 3, "payload": {"k": 1}}, headers=headers)
    client.post("/jobs", json={"job_id": "job-p1-1", "type": "test", "priority": 1, "payload": {"k": 2}}, headers=headers)
    client.post("/jobs", json={"job_id": "job-p5-1", "type": "test", "priority": 5, "payload": {"k": 3}}, headers=headers)
    client.post("/jobs", json={"job_id": "job-p1-2", "type": "test", "priority": 1, "payload": {"k": 4}}, headers=headers)

    response = client.get("/jobs/pending", headers=headers)
    assert response.status_code == 200
    job_ids = [j["job_id"] for j in response.json()["jobs"]]
    assert job_ids == ["job-p1-1", "job-p1-2", "job-p3-1", "job-p5-1"]
