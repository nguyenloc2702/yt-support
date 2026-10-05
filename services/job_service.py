import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.database import SessionLocal
from core.models import Job, Project
from core.enums import JobStatus, JobType, ProjectStatus
from core.exceptions import JobCancelledError
from core.render_schemas import TestPreviewOptions
from config.settings import settings


class JobService:
    """
    Single-process background job runner for local use.

    - Runs at most `settings.max_concurrent_jobs` jobs (default 1).
    - Tracks live progress and cancellation tokens in memory (per process).
    - Persists job rows to SQLite so the UI can poll across Streamlit reruns.
    """

    _executor: Optional[ThreadPoolExecutor] = None
    _lock = threading.Lock()
    _progress: Dict[str, Dict[str, Any]] = {}
    _tokens: Dict[str, threading.Event] = {}
    _interrupted: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(
                max_workers=max(1, settings.max_concurrent_jobs),
                thread_name_prefix="job",
            )
        return cls._executor

    def _create_job(
        self,
        project_id: str,
        job_type: JobType,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        job_id = f"job_{uuid.uuid4().hex[:8]}"
        payload_str = json.dumps(payload, ensure_ascii=False) if payload else None
        with SessionLocal() as db:
            db.add(
                Job(
                    id=job_id,
                    project_id=project_id,
                    job_type=job_type.value,
                    status=JobStatus.PENDING.value,
                    progress=0,
                    current_step="queued",
                    payload_json=payload_str,
                )
            )
            db.commit()
        self._progress[job_id] = {"percent": 0, "message": "queued"}
        self._tokens[job_id] = threading.Event()
        return job_id

    def _set_job(self, job_id: str, **fields) -> None:
        with SessionLocal() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            db.commit()

    def _update_progress(self, job_id: str, percent: int, message: str) -> None:
        self._progress[job_id] = {"percent": percent, "message": message}
        self._set_job(job_id, progress=percent, current_step=message)

    def get_progress(self, job_id: str) -> Dict[str, Any]:
        return self._progress.get(job_id, {"percent": 0, "message": ""})

    def get_job(self, job_id: str) -> Optional[Job]:
        with SessionLocal() as db:
            return db.query(Job).filter(Job.id == job_id).first()

    def is_running(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value))

    def get_active_job(
        self, project_id: str, job_type: Optional[str] = None
    ) -> Optional[Job]:
        """
        Return the newest still-running job for a project, or None.

        Streamlit session_state is per browser session, so a short disconnect
        loses the job id even though the worker thread keeps running. The job
        row in SQLite survives, so the UI can re-attach to it here.

        Jobs whose row says pending/running but which are absent from the
        in-process `_progress` map were started by a previous server run, so
        their worker thread is gone. They are re-labelled as interrupted (not
        failed) because the artefacts already on disk stay valid and the job
        can be resumed.
        """
        with SessionLocal() as db:
            query = db.query(Job).filter(
                Job.project_id == project_id,
                Job.status.in_(
                    [JobStatus.PENDING.value, JobStatus.RUNNING.value]
                ),
            )
            if job_type is not None:
                query = query.filter(Job.job_type == job_type)
            rows = query.all()

        live = [job for job in rows if job.id in self._progress]
        if not live:
            for job in rows:
                self._mark_interrupted(job)
            return None

        live.sort(
            key=lambda job: (
                job.created_at or datetime.min,
                job.status == JobStatus.RUNNING.value,
            )
        )
        return live[-1]

    def _mark_interrupted(self, job: Job) -> None:
        """
        Re-label a job whose worker thread died with a previous server run.

        The stages already written to disk remain valid, so the job is recorded
        as cancelled (resumable) rather than failed (lost). The last known step
        is stashed in `_interrupted` so the UI can show it before the row is
        overwritten.
        """
        step = job.current_step or ""
        if step == "interrupted":
            self._interrupted.setdefault(
                job.id,
                {
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "progress": job.progress or 0,
                    "step": "đã dừng trước đó",
                },
            )
            return
        self._interrupted[job.id] = {
            "job_id": job.id,
            "job_type": job.job_type,
            "progress": job.progress or 0,
            "step": step or "đang chạy",
        }
        self._set_job(
            job.id,
            status=JobStatus.CANCELLED.value,
            current_step="interrupted",
            error_message=(
                "Bị gián đoạn do khởi động lại — có thể chạy nối tiếp từ bước "
                f"“{step or 'không rõ'}” ({job.progress or 0}%)."
            ),
            finished_at=datetime.utcnow(),
        )

    def find_resumable_job(
        self, project_id: str, job_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Return the newest job of this project that can be continued, or None.

        Covers both jobs already flagged as interrupted and rows still marked
        pending/running whose worker thread no longer exists.
        """
        with SessionLocal() as db:
            query = db.query(Job).filter(Job.project_id == project_id)
            if job_type is not None:
                query = query.filter(Job.job_type == job_type)
            rows = query.all()

        rows.sort(key=lambda job: job.created_at or datetime.min)

        found: List[Dict[str, Any]] = []
        for job in rows:
            if job.current_step == "interrupted":
                found.append(
                    self._interrupted.get(
                        job.id,
                        {
                            "job_id": job.id,
                            "job_type": job.job_type,
                            "progress": job.progress or 0,
                            "step": "đã dừng",
                        },
                    )
                )
            elif (
                job.status == JobStatus.FAILED.value
                and (job.error_message or "").startswith("Interrupted by server restart")
            ):
                # Rows written by an earlier version recorded a dead worker
                # thread as `failed`. The artefacts on disk are still valid, so
                # re-label them as resumable instead of asking the user to
                # start over.
                self._mark_interrupted(job)
                found.append(self._interrupted[job.id])
            elif (
                job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)
                and job.id not in self._progress
            ):
                self._mark_interrupted(job)
                found.append(self._interrupted[job.id])

        return found[-1] if found else None

    def cancel_job(self, job_id: str) -> None:
        token = self._tokens.get(job_id)
        if token is not None:
            token.set()

    # ---- public submit API ----

    def submit_analysis(self, project_id: str, options: Optional[Dict[str, Any]] = None) -> str:
        from services.analysis_service import AnalysisService

        job_id = self._create_job(project_id, JobType.ANALYSIS)
        token = self._tokens[job_id]

        def _run() -> None:
            self._set_job(
                job_id,
                status=JobStatus.RUNNING.value,
                started_at=datetime.utcnow(),
            )
            try:
                AnalysisService().run_analysis(
                    project_id,
                    progress_callback=lambda p, m: self._update_progress(job_id, p, m),
                    cancellation_token=token,
                    options=options or {},
                )
                self._set_job(
                    job_id,
                    status=JobStatus.COMPLETED.value,
                    progress=100,
                    current_step="done",
                    finished_at=datetime.utcnow(),
                )
            except JobCancelledError:
                self._set_job(
                    job_id,
                    status=JobStatus.CANCELLED.value,
                    current_step="cancelled",
                    finished_at=datetime.utcnow(),
                )
            except Exception as exc:  # noqa: BLE001 - surface any pipeline error
                self._set_job(
                    job_id,
                    status=JobStatus.FAILED.value,
                    error_message=str(exc),
                    finished_at=datetime.utcnow(),
                )

        self._get_executor().submit(_run)
        return job_id

    def _submit_render(
        self,
        project_id: str,
        job_type: JobType,
        runner: Callable[[Callable[[int, str], None], threading.Event], Any],
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        job_id = self._create_job(project_id, job_type, payload=payload)
        token = self._tokens[job_id]

        def _run() -> None:
            self._set_job(
                job_id,
                status=JobStatus.RUNNING.value,
                started_at=datetime.utcnow(),
            )
            try:
                runner(lambda p, m: self._update_progress(job_id, p, m), token)
                self._set_job(
                    job_id,
                    status=JobStatus.COMPLETED.value,
                    progress=100,
                    current_step="done",
                    finished_at=datetime.utcnow(),
                )
            except JobCancelledError:
                self._set_job(
                    job_id,
                    status=JobStatus.CANCELLED.value,
                    current_step="cancelled",
                    finished_at=datetime.utcnow(),
                )
            except Exception as exc:  # noqa: BLE001
                self._set_job(
                    job_id,
                    status=JobStatus.FAILED.value,
                    error_message=str(exc),
                    finished_at=datetime.utcnow(),
                )

        self._get_executor().submit(_run)
        return job_id

    def submit_test_preview_render(
        self,
        project_id: str,
        timeline: dict,
        test_options: TestPreviewOptions,
        render_config_version: Optional[int] = None,
    ) -> str:
        from services.render_service import RenderService

        service = RenderService()
        payload = {
            "project_id": project_id,
            "timeline_version": timeline.get("version", 1),
            "render_config_version": render_config_version,
            "render_type": "test_preview",
            "test_preview": test_options.model_dump(mode="json"),
        }
        return self._submit_render(
            project_id,
            JobType.TEST_PREVIEW_RENDER,
            lambda cb, token: service.render_test_preview(
                project_id,
                timeline,
                test_options=test_options,
                render_config_version=render_config_version,
                progress_callback=cb,
                cancellation_token=token,
            ),
            payload=payload,
        )

    def submit_preview_render(
        self,
        project_id: str,
        timeline: dict,
        render_config_version: Optional[int] = None,
    ) -> str:
        from services.render_service import RenderService

        service = RenderService()
        payload = {
            "project_id": project_id,
            "timeline_version": timeline.get("version", 1),
            "render_config_version": render_config_version,
            "render_type": "preview",
        }
        return self._submit_render(
            project_id,
            JobType.PREVIEW_RENDER,
            lambda cb, token: service.render_preview(
                project_id,
                timeline,
                render_config_version=render_config_version,
                progress_callback=cb,
                cancellation_token=token,
            ),
            payload=payload,
        )

    def submit_final_render(
        self,
        project_id: str,
        timeline: dict,
        preset_name: str = "youtube_1080p",
        render_config_version: Optional[int] = None,
    ) -> str:
        from services.render_service import RenderService

        service = RenderService()
        payload = {
            "project_id": project_id,
            "timeline_version": timeline.get("version", 1),
            "preset_name": preset_name,
            "render_config_version": render_config_version,
            "render_type": "final",
        }
        return self._submit_render(
            project_id,
            JobType.FINAL_RENDER,
            lambda cb, token: service.render_final(
                project_id,
                timeline,
                preset_name=preset_name,
                render_config_version=render_config_version,
                progress_callback=cb,
                cancellation_token=token,
            ),
            payload=payload,
        )