import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from config.settings import settings
from core.database import SessionLocal
from core.enums import ProjectStatus, RenderStatus, RenderType
from core.exceptions import JobCancelledError, RenderError
from core.models import Project, Render
from core.render_schemas import RenderConfiguration, TestPreviewOptions
from pipeline.audio_mix import slice_timeline_by_output_range
from pipeline.renderer import render_video
from services.render_config_service import RenderConfigService
from utils.cleanup import cleanup_render_temp, cleanup_stale_previews
from utils.disk import ensure_free_space


class RenderService:
    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)
        self.config_service = RenderConfigService()

    def _get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def _resolve_configuration(
        self, project_id: str, render_config_version: int | None = None
    ) -> RenderConfiguration:
        if render_config_version is not None:
            config = self.config_service.get_version(project_id, render_config_version)
            if config:
                return config
        current = self.config_service.get_current(project_id)
        if current:
            return current
        raise RenderError("No render configuration found for this project")

    def _get_preset(self, preset_name: str, aspect_ratio: str = "16:9") -> dict:
        if preset_name == "preview_720p":
            if aspect_ratio == "9:16":
                width, height = 720, 1280
            elif aspect_ratio == "1:1":
                width, height = 720, 720
            else:
                width, height = 1280, 720
            return {
                "width": width,
                "height": height,
                "video_codec": "libx264",
                "preset": "veryfast",
                "crf": getattr(settings, "preview_crf", 28),
                "audio_codec": "aac",
                "audio_bitrate": "128k",
            }

        presets = {
            "youtube_1080p": {
                "width": 1920,
                "height": 1080,
                "video_codec": "libx264",
                "preset": "medium",
                "crf": getattr(settings, "final_crf", 20),
                "audio_codec": "aac",
                "audio_bitrate": "192k",
            },
            "vertical_1080p": {
                "width": 1080,
                "height": 1920,
                "video_codec": "libx264",
                "preset": "medium",
                "crf": getattr(settings, "final_crf", 20),
                "audio_codec": "aac",
                "audio_bitrate": "192k",
            },
            "square_1080p": {
                "width": 1080,
                "height": 1080,
                "video_codec": "libx264",
                "preset": "medium",
                "crf": getattr(settings, "final_crf", 20),
                "audio_codec": "aac",
                "audio_bitrate": "192k",
            },
        }
        return presets.get(preset_name, presets["youtube_1080p"])

    def render_test_preview(
        self,
        project_id: str,
        timeline: dict,
        test_options: TestPreviewOptions,
        render_config_version: int | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
        cancellation_token=None,
    ) -> Path:
        """Render a 10-30s derived slice of the timeline for rapid audio & pacing checks."""
        proj_dir = self._get_project_dir(project_id)
        source_path = proj_dir / "source" / "input.mp4"
        if not source_path.exists():
            raise RenderError("Source file not found")

        # Fail fast before a long encode when disk is nearly full.
        ensure_free_space(proj_dir, source_path.stat().st_size)

        previews_dir = proj_dir / "previews"
        previews_dir.mkdir(exist_ok=True)

        configuration = self._resolve_configuration(project_id, render_config_version)

        # Slice timeline
        derived_timeline, output_offset = slice_timeline_by_output_range(
            timeline=timeline,
            output_start=test_options.output_start_seconds,
            duration=test_options.duration_seconds,
        )

        preset = self._get_preset("preview_720p", derived_timeline.get("output_aspect_ratio", "16:9"))
        test_id = uuid.uuid4().hex[:8]
        output_path = previews_dir / f"test_preview_{test_id}.mp4"
        render_id = f"render_{test_id}"

        # Register render in DB
        with SessionLocal() as db:
            render = Render(
                id=render_id,
                project_id=project_id,
                timeline_version=timeline.get("version", 1),
                render_config_version=configuration.version,
                render_type=RenderType.TEST_PREVIEW.value,
                preset_name="test_preview_720p",
                output_path=str(output_path),
                status=RenderStatus.RENDERING.value,
                started_at=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(render)
            db.commit()

        try:
            render_video(
                source_path=source_path,
                timeline=derived_timeline,
                output_path=output_path,
                preset=preset,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
                render_configuration=configuration,
                project_id=project_id,
                render_id=render_id,
                output_timeline_offset_seconds=output_offset,
            )

            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.COMPLETED.value
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()

            # Best-effort housekeeping: scratch dirs and old previews.
            try:
                cleanup_render_temp(proj_dir)
                cleanup_stale_previews(proj_dir)
            except Exception:  # noqa: BLE001
                pass

            return output_path

        except JobCancelledError:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.CANCELLED.value if hasattr(RenderStatus, "CANCELLED") else "cancelled"
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise
        except Exception as exc:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.FAILED.value
                    r.error_message = str(exc)
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise

    @staticmethod
    def enforce_render_gates(
        project_id: str,
        timeline: dict,
        audio_source: str = "original",
        projects_dir: Path | None = None,
    ) -> dict:
        """Phase D gates: content approval + audio delivery approval.

        Runs before any render call (preview, test and final share the same
        gates). Returns the timeline unchanged when all gates pass; raises
        RenderError otherwise.
        """
        from pipeline.audio_timeline import check_render_readiness
        from services.media_analysis_service import MediaAnalysisService

        # Gate 1: block-based timelines must pass content readiness
        if timeline.get("analysis_id"):
            doc = None
            if projects_dir is not None:
                try:
                    doc = MediaAnalysisService(projects_dir=projects_dir).load_existing_document(project_id)
                except Exception:
                    doc = None
            if doc is not None:
                check = check_render_readiness(doc)
                if not check["ready"]:
                    raise RenderError(
                        "Content gate failed: " + "; ".join(check["reasons"])
                    )

        # Gate 2: uploaded_blocks source requires all overlays approved and no overlap
        if audio_source == "uploaded_blocks":
            overlays = timeline.get("audio_overlays") or []
            if not overlays:
                raise RenderError(
                    "Audio gate failed: audio_source='uploaded_blocks' nhưng không có "
                    "audio_overlays đã duyệt."
                )
            sorted_ov = sorted(overlays, key=lambda o: float(o["output_start"]))
            for a, b in zip(sorted_ov, sorted_ov[1:]):
                if float(b["output_start"]) < float(a["output_end"]) - 1e-6:
                    raise RenderError(
                        f"Audio gate failed: overlap between {a.get('block_id')} and "
                        f"{b.get('block_id')} — không cho phép render."
                    )
        return timeline

    def render_preview(
        self,
        project_id: str,
        timeline: dict,
        render_config_version: int | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
        cancellation_token=None,
        subtitle_mode: str = "none",
        audio_source: str = "original",
    ) -> Path:
        """Render a full preview (720p) of the timeline with audio post-processing."""
        self.enforce_render_gates(project_id, timeline, audio_source, self._get_project_dir(project_id).parent.parent)
        proj_dir = self._get_project_dir(project_id)
        source_path = proj_dir / "source" / "input.mp4"
        if not source_path.exists():
            raise RenderError("Source file not found")

        previews_dir = proj_dir / "previews"
        previews_dir.mkdir(exist_ok=True)

        configuration = self._resolve_configuration(project_id, render_config_version)
        preset = self._get_preset("preview_720p", timeline.get("output_aspect_ratio", "16:9"))

        render_id = f"render_{uuid.uuid4().hex[:8]}"
        output_path = previews_dir / f"preview_v{configuration.version}.mp4"

        with SessionLocal() as db:
            render = Render(
                id=render_id,
                project_id=project_id,
                timeline_version=timeline.get("version", 1),
                render_config_version=configuration.version,
                render_type=RenderType.PREVIEW.value,
                preset_name="preview_720p",
                output_path=str(output_path),
                status=RenderStatus.RENDERING.value,
                started_at=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(render)
            db.commit()

        try:
            render_video(
                source_path=source_path,
                timeline=timeline,
                output_path=output_path,
                preset=preset,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
                render_configuration=configuration,
                project_id=project_id,
                render_id=render_id,
                subtitle_mode=subtitle_mode,
                audio_source=audio_source,
            )

            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.COMPLETED.value
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()

            with SessionLocal() as db:
                p = db.query(Project).filter(Project.id == project_id).first()
                if p:
                    p.status = ProjectStatus.PREVIEW_READY.value
                    db.commit()

            return output_path

        except JobCancelledError:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.CANCELLED.value if hasattr(RenderStatus, "CANCELLED") else "cancelled"
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise
        except Exception as exc:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.FAILED.value
                    r.error_message = str(exc)
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise

    def render_final(
        self,
        project_id: str,
        timeline: dict,
        preset_name: str = "youtube_1080p",
        render_config_version: int | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
        cancellation_token=None,
        audio_source: str = "original",
    ) -> Path:
        """Render final delivery video (1080p) with complete post-processing."""
        self.enforce_render_gates(project_id, timeline, audio_source, self._get_project_dir(project_id).parent.parent)
        proj_dir = self._get_project_dir(project_id)
        source_path = proj_dir / "source" / "input.mp4"
        if not source_path.exists():
            raise RenderError("Source file not found")

        exports_dir = proj_dir / "exports"
        exports_dir.mkdir(exist_ok=True)

        configuration = self._resolve_configuration(project_id, render_config_version)
        preset = self._get_preset(preset_name, timeline.get("output_aspect_ratio", "16:9"))

        render_id = f"render_{uuid.uuid4().hex[:8]}"
        output_path = exports_dir / f"final_v{configuration.version}.mp4"

        with SessionLocal() as db:
            render = Render(
                id=render_id,
                project_id=project_id,
                timeline_version=timeline.get("version", 1),
                render_config_version=configuration.version,
                render_type=RenderType.FINAL.value,
                preset_name=preset_name,
                output_path=str(output_path),
                status=RenderStatus.RENDERING.value,
                started_at=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(render)
            db.commit()

        try:
            render_video(
                source_path=source_path,
                timeline=timeline,
                output_path=output_path,
                preset=preset,
                progress_callback=progress_callback,
                cancellation_token=cancellation_token,
                render_configuration=configuration,
                project_id=project_id,
                render_id=render_id,
                audio_source=audio_source,
            )

            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.COMPLETED.value
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()

            with SessionLocal() as db:
                p = db.query(Project).filter(Project.id == project_id).first()
                if p:
                    p.status = ProjectStatus.COMPLETED.value
                    db.commit()

            return output_path

        except JobCancelledError:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.CANCELLED.value if hasattr(RenderStatus, "CANCELLED") else "cancelled"
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise
        except Exception as exc:
            with SessionLocal() as db:
                r = db.query(Render).filter(Render.id == render_id).first()
                if r:
                    r.status = RenderStatus.FAILED.value
                    r.error_message = str(exc)
                    r.finished_at = datetime.now(UTC).replace(tzinfo=None)
                    db.commit()
            raise