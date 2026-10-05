"""Tests for JobService job lifecycle: create, progress, cancel, resumable."""

import pytest

from core.enums import JobStatus, JobType
from services.job_service import JobService


@pytest.fixture(autouse=True)
def clean_class_state():
    """Reset in-memory class state between tests."""
    JobService._progress.clear()
    JobService._tokens.clear()
    JobService._interrupted.clear()
    yield
    JobService._progress.clear()
    JobService._tokens.clear()
    JobService._interrupted.clear()


def _create_job(svc, project_id="p1", job_type=JobType.ANALYSIS):
    job_id = svc._create_job(project_id, job_type, payload={"opt": 1})
    return job_id


def test_create_and_get_job(isolated_db):
    svc = JobService()
    job_id = _create_job(svc)
    job = svc.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.PENDING.value
    assert job.job_type == JobType.ANALYSIS.value
    assert svc.is_running(job_id) is True


def test_get_active_job_live(isolated_db):
    svc = JobService()
    job_id = _create_job(svc)
    active = svc.get_active_job("p1")
    assert active is not None
    assert active.id == job_id


def test_stale_job_marked_interrupted(isolated_db):
    """Job rows that say running but have no live worker → interrupted."""
    svc = JobService()
    job_id = _create_job(svc)
    # Simulate dead worker: remove in-memory state
    del JobService._progress[job_id]
    del JobService._tokens[job_id]

    active = svc.get_active_job("p1")
    assert active is None
    job = svc.get_job(job_id)
    assert job.status == JobStatus.CANCELLED.value
    assert job.current_step == "interrupted"

    # And it's discoverable as resumable
    resumable = svc.find_resumable_job("p1")
    assert resumable is not None
    assert resumable["job_id"] == job_id


def test_find_resumable_returns_none_when_no_jobs(isolated_db):
    svc = JobService()
    assert svc.find_resumable_job("empty_project") is None


def test_cancel_job_sets_token(isolated_db):
    svc = JobService()
    job_id = _create_job(svc)
    token = JobService._tokens[job_id]
    assert not token.is_set()
    svc.cancel_job(job_id)
    assert token.is_set()


def test_get_progress_defaults(isolated_db):
    svc = JobService()
    assert svc.get_progress("nope") == {"percent": 0, "message": ""}
