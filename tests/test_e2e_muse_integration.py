"""End-to-End (E2E) integration tests for the Muse Job Inbox ecosystem.

Covers the full integration loop:
1. Local Factory & Client submission (payload normalization, idempotency)
2. FastAPI Job Inbox API & SQLite state persistence
3. Muse Worker polling with Priority ASC (1=critical .. 5=low) and FIFO tie-break
4. Atomic claiming and lease lock enforcement
5. Heartbeat reporting and lease extension
6. Result delivery, duplicate idempotency, and local result retrieval
7. Lease timeout recovery & toxic job dead-letter protection (max_attempts)
8. Transient retryable errors vs permanent failures
9. Security & conflict enforcement (tokens, double claims, ownership)
10. AutomatedFactory Mode 2 dispatch integration
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient

from pipeline.automated_factory import AutomatedFactory
from services.muse_job_client import (
    DEFAULT_PRIORITY,
    MuseJobClient,
    MuseJobConfig,
    build_make_video_job,
)


def _setup_api_client(tmp_path: Path, monkeypatch, token: str = "test-secret-token") -> tuple[TestClient, MuseJobClient, Path]:
    db_path = tmp_path / "e2e_inbox.sqlite3"
    monkeypatch.setenv("JOB_INBOX_DB_PATH", str(db_path))
    monkeypatch.setenv("CLAUDE_JOBS_TOKEN", token)
    monkeypatch.setenv("JOB_INBOX_INTERNAL_URL", "http://127.0.0.1:8787")

    import services.job_inbox_api as api_module
    api_module = importlib.reload(api_module)
    test_client = TestClient(api_module.app)

    muse_client = MuseJobClient(
        MuseJobConfig("http://127.0.0.1:8787", token),
        session=test_client,
    )
    return test_client, muse_client, db_path


def test_e2e_full_lifecycle_happy_path(tmp_path: Path, monkeypatch):
    """Scenario 1: Happy Path E2E from script creation to local result retrieval."""
    test_client, muse_client, db_path = _setup_api_client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer test-secret-token"}

    # 1. Local App builds a make_video job
    script_text = (
        "[CẢNH 001]\nĐế chế La Mã đang ở đỉnh cao quyền lực.\n\n"
        "[CẢNH 002]\nNhưng những cuộc khủng hoảng kinh tế bắt đầu nổ ra.\n\n"
        "[CẢNH 003]\nVà dấu chấm hết không thể tránh khỏi."
    )
    job_payload = build_make_video_job(
        factory_job_id="remake-rome-001",
        script=script_text,
        target_duration_sec=90.0,
        title="Sự sụp đổ của La Mã",
        priority=1,  # Urgent
        voice="vi-VN-NamMinhNeural",
        source="automated-factory",
    )
    assert job_payload["priority"] == 1
    assert job_payload["type"] == "make_video"

    # 2. Local App submits job to API
    created_resp = muse_client.create_job(job_payload)
    assert created_resp["created"] is True
    job_id = created_resp["job"]["job_id"]
    assert created_resp["job"]["status"] == "pending"

    # 3. Muse Worker polls GET /jobs/pending
    pending_resp = test_client.get("/jobs/pending?limit=10", headers=headers)
    assert pending_resp.status_code == 200
    pending_body = pending_resp.json()
    assert pending_body["ok"] is True
    assert pending_body["count"] == 1
    assert pending_body["jobs"][0]["job_id"] == job_id
    assert pending_body["jobs"][0]["status"] == "pending"

    # 4. Muse Worker claims job
    claim_resp = test_client.post(
        f"/jobs/{job_id}/claim",
        json={"worker": "claude-muse-worker-1", "lease_sec": 300},
        headers=headers,
    )
    assert claim_resp.status_code == 200
    claimed_data = claim_resp.json()["job"]
    assert claimed_data["status"] == "running"
    assert claimed_data["worker"] == "claude-muse-worker-1"
    assert claimed_data["attempts"] == 1
    assert claimed_data["lease_until"] > time.time()

    # 5. Muse Worker sends heartbeat
    hb_resp = test_client.post(
        f"/jobs/{job_id}/heartbeat",
        json={
            "worker": "claude-muse-worker-1",
            "lease_sec": 400,
            "progress_note": "Rendered 2/3 scenes successfully",
        },
        headers=headers,
    )
    assert hb_resp.status_code == 200
    hb_data = hb_resp.json()["job"]
    assert hb_data["progress_note"] == "Rendered 2/3 scenes successfully"

    # 6. Muse Worker delivers completion result
    result_payload = {
        "worker": "claude-muse-worker-1",
        "finished_at": "2026-11-02T20:20:00Z",
        "summary": "Rome documentary video generated and quality approved.",
        "outputs": {
            "video_url": "https://vps.example.com/outputs/rome_doc.mp4",
            "duration_sec": 91.2,
            "qc": {"pass": True, "score": 9.4, "issues": []},
            "scenes": [
                {"scene": 1, "clip_url": "https://vps.example.com/scenes/001.mp4"},
                {"scene": 2, "clip_url": "https://vps.example.com/scenes/002.mp4"},
                {"scene": 3, "clip_url": "https://vps.example.com/scenes/003.mp4"},
            ],
        },
    }
    res_post = test_client.post(
        f"/jobs/{job_id}/result",
        json=result_payload,
        headers=headers,
    )
    assert res_post.status_code == 200
    res_body = res_post.json()
    assert res_body["ok"] is True
    assert res_body["status"] == "done"
    assert res_body["job_id"] == job_id
    assert res_body["duplicate"] is False

    # 7. Duplicate result idempotency check
    dup_res = test_client.post(
        f"/jobs/{job_id}/result",
        json=result_payload,
        headers=headers,
    )
    assert dup_res.status_code == 200
    dup_body = dup_res.json()
    assert dup_body["ok"] is True
    assert dup_body["status"] == "done"
    assert dup_body["duplicate"] is True

    # 8. Local App retrieves job and validates outputs
    job_final = muse_client.get_job(job_id)["job"]
    assert job_final["status"] == "done"
    assert job_final["result"]["outputs"]["video_url"] == "https://vps.example.com/outputs/rome_doc.mp4"
    assert job_final["result"]["outputs"]["qc"]["pass"] is True


def test_e2e_priority_sorting_and_fifo_tiebreak(tmp_path: Path, monkeypatch):
    """Scenario 2: E2E Priority Queue order (1 highest .. 5 lowest) with FIFO tie-break."""
    test_client, _, _ = _setup_api_client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer test-secret-token"}

    # Post jobs with deliberate priority order
    jobs = [
        {"job_id": "job-p3-default", "type": "render", "priority": 3, "payload": {"n": "p3"}},
        {"job_id": "job-p1-first", "type": "render", "priority": 1, "payload": {"n": "p1-1"}},
        {"job_id": "job-p5-lowest", "type": "render", "priority": 5, "payload": {"n": "p5"}},
        {"job_id": "job-p2-high", "type": "render", "priority": 2, "payload": {"n": "p2"}},
        {"job_id": "job-p1-second", "type": "render", "priority": 1, "payload": {"n": "p1-2"}},
    ]
    for j in jobs:
        resp = test_client.post("/jobs", json=j, headers=headers)
        assert resp.status_code == 200

    pending = test_client.get("/jobs/pending?limit=10", headers=headers).json()
    assert pending["count"] == 5
    retrieved_ids = [item["job_id"] for item in pending["jobs"]]

    # Expectation:
    # 1. P1 first (FIFO: job-p1-first then job-p1-second)
    # 2. P2
    # 3. P3
    # 4. P5
    expected_order = [
        "job-p1-first",
        "job-p1-second",
        "job-p2-high",
        "job-p3-default",
        "job-p5-lowest",
    ]
    assert retrieved_ids == expected_order


def test_e2e_lease_expiry_and_dead_letter_protection(tmp_path: Path, monkeypatch):
    """Scenario 3: Lease timeout recovers to pending until max_attempts reached, then fails."""
    test_client, _, db_path = _setup_api_client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer test-secret-token"}

    # Create job with max_attempts = 2
    job = {
        "job_id": "toxic-job-001",
        "type": "make_video",
        "max_attempts": 2,
        "payload": {"script": "flaky render"},
    }
    test_client.post("/jobs", json=job, headers=headers)

    # Attempt 1: Worker claims job
    claim1 = test_client.post(
        "/jobs/toxic-job-001/claim",
        json={"worker": "worker-alpha", "lease_sec": 60},
        headers=headers,
    )
    assert claim1.status_code == 200
    assert claim1.json()["job"]["attempts"] == 1

    # Simulate crash: Fast-forward lease expiry in SQLite
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE muse_jobs SET lease_until = ? WHERE job_id = ?", (time.time() - 10, "toxic-job-001"))

    # Next pending polling detects expired lease -> attempts (1) < max_attempts (2) -> recovers to pending!
    poll1 = test_client.get("/jobs/pending", headers=headers).json()
    assert poll1["count"] == 1
    assert poll1["jobs"][0]["job_id"] == "toxic-job-001"
    assert poll1["jobs"][0]["status"] == "pending"

    # Attempt 2: Worker claims job again
    claim2 = test_client.post(
        "/jobs/toxic-job-001/claim",
        json={"worker": "worker-beta", "lease_sec": 60},
        headers=headers,
    )
    assert claim2.status_code == 200
    assert claim2.json()["job"]["attempts"] == 2

    # Simulate crash again: Fast-forward lease expiry
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE muse_jobs SET lease_until = ? WHERE job_id = ?", (time.time() - 10, "toxic-job-001"))

    # Next pending polling detects expired lease -> attempts (2) >= max_attempts (2) -> fails with lease_expired!
    poll2 = test_client.get("/jobs/pending", headers=headers).json()
    assert poll2["count"] == 0  # No longer pending

    # Inspect job status: must be failed permanently
    job_resp = test_client.get("/jobs/toxic-job-001", headers=headers).json()["job"]
    assert job_resp["status"] == "failed"
    assert job_resp["error"]["code"] == "lease_expired"


def test_e2e_retryable_error_vs_terminal_failure(tmp_path: Path, monkeypatch):
    """Scenario 4: Retryable error returns job to pending; non-retryable halts immediately."""
    test_client, _, _ = _setup_api_client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer test-secret-token"}

    job = {
        "job_id": "retryable-job-001",
        "type": "make_video",
        "max_attempts": 3,
        "payload": {"script": "temporary glitch"},
    }
    test_client.post("/jobs", json=job, headers=headers)

    # Claim attempt 1
    test_client.post("/jobs/retryable-job-001/claim", json={"worker": "w1", "lease_sec": 60}, headers=headers)

    # Worker reports transient failure (retryable=True)
    fail1 = test_client.post(
        "/jobs/retryable-job-001/fail",
        json={"error": {"code": "upstream_503", "message": "Service unavailable"}, "retryable": True},
        headers=headers,
    )
    assert fail1.status_code == 200
    assert fail1.json()["status"] == "pending"

    # Job is available in pending again
    pending = test_client.get("/jobs/pending", headers=headers).json()
    assert pending["count"] == 1
    assert pending["jobs"][0]["job_id"] == "retryable-job-001"

    # Claim attempt 2
    test_client.post("/jobs/retryable-job-001/claim", json={"worker": "w2", "lease_sec": 60}, headers=headers)

    # Worker reports permanent fatal failure (retryable=False)
    fail2 = test_client.post(
        "/jobs/retryable-job-001/fail",
        json={"error": {"code": "invalid_script", "message": "Content rejected"}, "retryable": False},
        headers=headers,
    )
    assert fail2.status_code == 200
    assert fail2.json()["status"] == "failed"

    # Job is no longer pending
    pending_after = test_client.get("/jobs/pending", headers=headers).json()
    assert pending_after["count"] == 0


def test_e2e_concurrency_and_security_gates(tmp_path: Path, monkeypatch):
    """Scenario 5: Concurrency conflicts and security authentication enforcement."""
    test_client, _, _ = _setup_api_client(tmp_path, monkeypatch)
    auth_header = {"Authorization": "Bearer test-secret-token"}

    # 1. Reject unauthenticated access
    assert test_client.get("/jobs/pending").status_code == 401
    assert test_client.post("/jobs", json={}).status_code == 401

    # 2. Reject wrong bearer token
    assert test_client.get("/jobs/pending", headers={"Authorization": "Bearer wrong"}).status_code == 401

    # 3. Create job
    test_client.post(
        "/jobs",
        json={"job_id": "conflict-test", "type": "test", "payload": {"foo": "bar"}},
        headers=auth_header,
    )

    # 4. Conflict on same job_id with different payload
    conflict = test_client.post(
        "/jobs",
        json={"job_id": "conflict-test", "type": "test", "payload": {"foo": "DIFFERENT"}},
        headers=auth_header,
    )
    assert conflict.status_code == 409

    # 5. Worker A claims job
    claim_a = test_client.post(
        "/jobs/conflict-test/claim",
        json={"worker": "worker-A", "lease_sec": 60},
        headers=auth_header,
    )
    assert claim_a.status_code == 200

    # 6. Worker B tries to claim the same job while running -> 409 Conflict
    claim_b = test_client.post(
        "/jobs/conflict-test/claim",
        json={"worker": "worker-B", "lease_sec": 60},
        headers=auth_header,
    )
    assert claim_b.status_code == 409

    # 7. Worker B tries to heartbeat Worker A's job -> 409 Conflict
    hb_unowned = test_client.post(
        "/jobs/conflict-test/heartbeat",
        json={"worker": "worker-B", "lease_sec": 60},
        headers=auth_header,
    )
    assert hb_unowned.status_code == 409


def test_e2e_automated_factory_dispatch_mode2(tmp_path: Path, monkeypatch):
    """Scenario 6: AutomatedFactory Mode 2 dispatch with MUSE_VIDEO_ENABLED=true."""
    test_client, _, _ = _setup_api_client(tmp_path, monkeypatch)
    factory_db = tmp_path / "factory_queue.sqlite3"
    out_dir = tmp_path / "factory_out"
    factory = AutomatedFactory(output_dir=out_dir, db_path=factory_db)

    # Prepare project folder and script
    job_id = factory.jq.create_job(
        mode="remake",
        url="https://youtube.com/watch?v=apollo11",
    )
    project_dir = out_dir / job_id
    project_dir.mkdir(parents=True)
    script_file = project_dir / f"kich_ban_VI_{job_id}.txt"
    script_content = (
        "[CẢNH 001]\nGiới thiệu câu chuyện ly kỳ về khám phá vũ trụ.\n\n"
        "[CẢNH 002]\nCon tàu Apollo 11 tiếp cận bề mặt Mặt Trăng.\n\n"
        "[CẢNH 003]\nMột bước chân nhỏ cho con người, một bước tiến lớn cho nhân loại."
    )
    script_file.write_text(script_content, encoding="utf-8")

    # Seed job in local factory SQLite
    factory.jq.update_job_status(job_id, "APPROVED", script_file=str(script_file))
    with factory.jq._get_conn() as conn:
        conn.execute(
            "UPDATE factory_jobs SET project_dir = ?, source_title = ? WHERE id = ?",
            (str(project_dir), "Apollo documentary", job_id),
        )
        conn.commit()

    # Enable Muse video dispatch
    monkeypatch.setenv("MUSE_VIDEO_ENABLED", "true")

    # Wire AutomatedFactory's MuseJobClient session to FastAPI test_client
    original_client_init = MuseJobClient.__init__

    def patched_init(self, config=None, session=None):
        original_client_init(
            self,
            config=MuseJobConfig("http://127.0.0.1:8787", "test-secret-token"),
            session=test_client,
        )

    monkeypatch.setattr(MuseJobClient, "__init__", patched_init)

    # Execute approved reup
    success = factory.execute_approved_reup(job_id)
    assert success is True

    # Check local factory state transitioned to RENDERING
    local_job = factory.jq.get_job(job_id)
    assert local_job["status"] == "RENDERING"

    # Check Job Inbox API received the exact job
    headers = {"Authorization": "Bearer test-secret-token"}
    pending = test_client.get("/jobs/pending", headers=headers).json()
    assert pending["ok"] is True
    assert pending["count"] == 1
    inbox_job = pending["jobs"][0]
    assert inbox_job["type"] == "make_video"
    assert inbox_job["priority"] == DEFAULT_PRIORITY
    assert "Apollo 11" in inbox_job["payload"]["script"]
    assert inbox_job["payload"]["source_metadata"]["factory_job_id"] == job_id
