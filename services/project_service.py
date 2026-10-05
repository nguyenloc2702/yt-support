from pathlib import Path
from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.models import Project, RightsRecord
from core.schemas import ProjectCreate, ProjectOut
from core.enums import ProjectStatus, RightsType
from core.exceptions import ProjectNotFoundError, InvalidMediaError, DependencyNotFoundError
from pipeline.youtube import download_video, check_ytdlp, validate_public_url
from config.settings import settings
import uuid
import shutil
from typing import Optional, BinaryIO, Callable

class ProjectService:
    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)
        self.max_upload_bytes = settings.max_upload_gb * 1024 * 1024 * 1024
        self.allowed_extensions = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

    def _get_project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def _validate_file(self, filename: str, file_size: int) -> None:
        ext = Path(filename).suffix.lower()
        if ext not in self.allowed_extensions:
            raise InvalidMediaError(f"Unsupported file extension: {ext}. Allowed: {', '.join(self.allowed_extensions)}")
        if file_size > self.max_upload_bytes:
            raise InvalidMediaError(f"File size exceeds maximum of {settings.max_upload_gb} GB")

    # Stream the upload to disk in chunks so a 10 GB source file never
    # has to fit in RAM (uploaded_file.read() on a large file would spike
    # the worker process memory by the full file size).
    _UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MiB

    def _save_source_file(self, project_dir: Path, uploaded_file: BinaryIO, original_filename: str) -> tuple[str, str]:
        source_dir = project_dir / "source"
        source_dir.mkdir(exist_ok=True)
        # Use a safe internal name
        safe_name = "input.mp4"
        target_path = source_dir / safe_name
        # Write to a temp name first so a dropped connection cannot leave a
        # truncated input.mp4 that later passes extension checks but fails decode.
        partial_path = source_dir / "input.partial.mp4"
        try:
            with open(partial_path, "wb") as f:
                while True:
                    chunk = uploaded_file.read(self._UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    f.write(chunk)
            partial_path.replace(target_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise
        # Return relative path and original filename
        return safe_name, str(target_path.relative_to(project_dir))

    def create_project(self, data: ProjectCreate, file: Optional[BinaryIO] = None, filename: Optional[str] = None) -> ProjectOut:
        if not data.confirmed:
            raise ValueError("Rights must be confirmed")

        # Validate file if provided
        if file and filename:
            file.seek(0, 2)  # seek to end
            size = file.tell()
            file.seek(0)
            self._validate_file(filename, size)

        # Generate ID
        project_id = str(uuid.uuid4())[:8]
        # Create project directory
        proj_dir = self._get_project_dir(project_id)
        proj_dir.mkdir(parents=True, exist_ok=True)
        # Create subdirs
        (proj_dir / "source").mkdir(exist_ok=True)
        (proj_dir / "license").mkdir(exist_ok=True)
        (proj_dir / "proxy").mkdir(exist_ok=True)
        (proj_dir / "audio").mkdir(exist_ok=True)
        (proj_dir / "analysis").mkdir(exist_ok=True)
        (proj_dir / "timelines").mkdir(exist_ok=True)
        (proj_dir / "subtitles").mkdir(exist_ok=True)
        (proj_dir / "previews").mkdir(exist_ok=True)
        (proj_dir / "exports").mkdir(exist_ok=True)
        (proj_dir / "temp").mkdir(exist_ok=True)
        (proj_dir / "logs").mkdir(exist_ok=True)

        with SessionLocal() as db:
            project = Project(
                id=project_id,
                name=data.name,
                description=data.description,
                status=ProjectStatus.CREATED.value,
            )
            db.add(project)
            rights = RightsRecord(
                project_id=project_id,
                rights_type=data.rights_type.value,
                confirmed=data.confirmed,
                source_url=data.source_url,
                notes=data.notes,
            )
            db.add(rights)
            db.commit()
            db.refresh(project)

            # Save source file if provided
            if file and filename:
                source_filename, source_path = self._save_source_file(proj_dir, file, filename)
                project.source_filename = filename  # Keep original for display
                project.source_path = source_path
                project.status = ProjectStatus.SOURCE_READY.value
                db.commit()
                db.refresh(project)

            return self._project_to_out(project)

    def create_project_from_url(
        self,
        data: ProjectCreate,
        url: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancellation_token=None,
    ) -> ProjectOut:
        """
        Create a project whose source video is downloaded from a URL via yt-dlp.
        Equivalent CLI:
            yt-dlp -f "bestvideo[height<=2160]+bestaudio/best[height<=2160]"
                   --merge-output-format mp4 "URL"
        """
        if not data.confirmed:
            raise ValueError("Rights must be confirmed")

        # SSRF guard: block URLs resolving to loopback/private/reserved IPs.
        validate_public_url(url)

        if not check_ytdlp():
            raise DependencyNotFoundError(
                "yt-dlp not found. Install it with: pip install yt-dlp"
            )

        project_id = str(uuid.uuid4())[:8]
        proj_dir = self._get_project_dir(project_id)
        proj_dir.mkdir(parents=True, exist_ok=True)
        for sub in (
            "source", "license", "proxy", "audio", "analysis",
            "timelines", "subtitles", "previews", "exports", "temp", "logs",
        ):
            (proj_dir / sub).mkdir(exist_ok=True)

        with SessionLocal() as db:
            project = Project(
                id=project_id,
                name=data.name,
                description=data.description,
                status=ProjectStatus.CREATED.value,
            )
            db.add(project)
            rights = RightsRecord(
                project_id=project_id,
                rights_type=data.rights_type.value,
                confirmed=data.confirmed,
                source_url=url,
                notes=data.notes,
            )
            db.add(rights)
            db.commit()
            db.refresh(project)

        source_dir = proj_dir / "source"
        downloaded = download_video(
            url=url,
            output_dir=source_dir,
            progress_callback=progress_callback,
            cancellation_token=cancellation_token,
            max_height=getattr(settings, "max_download_height", 2160),
        )

        # Normalise the internal filename to source/input.mp4
        target = source_dir / "input.mp4"
        if downloaded != target:
            if target.exists():
                target.unlink()
            downloaded.replace(target)

        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == project_id).first()
            project.source_filename = target.name
            project.source_path = str(target.relative_to(proj_dir))
            project.status = ProjectStatus.SOURCE_READY.value
            db.commit()
            db.refresh(project)
            return self._project_to_out(project)

    def get_project(self, project_id: str) -> ProjectOut:
        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise ProjectNotFoundError(f"Project {project_id} not found")
            return self._project_to_out(project)

    def list_projects(self) -> list[ProjectOut]:
        with SessionLocal() as db:
            projects = db.query(Project).order_by(Project.created_at.desc()).all()
            return [self._project_to_out(p) for p in projects]

    def delete_project(self, project_id: str) -> None:
        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise ProjectNotFoundError(f"Project {project_id} not found")
            db.query(RightsRecord).filter(RightsRecord.project_id == project_id).delete()
            db.delete(project)
            db.commit()
        proj_dir = self._get_project_dir(project_id)
        if proj_dir.exists():
            shutil.rmtree(proj_dir)

    def update_status(self, project_id: str, status: ProjectStatus) -> None:
        with SessionLocal() as db:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                raise ProjectNotFoundError(f"Project {project_id} not found")
            project.status = status.value
            db.commit()

    def _project_to_out(self, project: Project) -> ProjectOut:
        return ProjectOut(
            id=project.id,
            name=project.name,
            description=project.description,
            status=ProjectStatus(project.status),
            source_filename=project.source_filename,
            duration_seconds=project.duration_seconds,
            width=project.width,
            height=project.height,
            fps=project.fps,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )