from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import math
import uuid

from config.settings import settings
from core.exceptions import RenderError
from core.render_schemas import RenderConfiguration
from services.asset_service import AssetService
from utils.json_io import atomic_write_json, read_json


class RenderConfigService:
    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)
        self.asset_service = AssetService()

    def _get_config_dir(self, project_id: str) -> Path:
        cfg_dir = (self.projects_dir / project_id / "render_configs").resolve()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        return cfg_dir

    def get_current(self, project_id: str) -> Optional[RenderConfiguration]:
        """Load current active render configuration, or create a default v1 if none exists."""
        cfg_dir = self._get_config_dir(project_id)
        current_path = cfg_dir / "current.json"
        if not current_path.exists():
            # Create default config v1
            default_config = RenderConfiguration(
                id=f"cfg_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                version=1,
                timeline_version=1,
                preset_name="youtube_1080p",
            )
            self.save_new_version(project_id, default_config)
            return default_config

        try:
            data = read_json(current_path)
            return RenderConfiguration(**data)
        except Exception:
            return None

    def list_versions(self, project_id: str) -> List[RenderConfiguration]:
        """List all saved render configuration versions in ascending order."""
        cfg_dir = self._get_config_dir(project_id)
        configs = []
        for file in sorted(cfg_dir.glob("render_config_v*.json")):
            try:
                data = read_json(file)
                configs.append(RenderConfiguration(**data))
            except Exception:
                continue
        configs.sort(key=lambda c: c.version)
        return configs

    def get_version(self, project_id: str, version: int) -> Optional[RenderConfiguration]:
        """Retrieve a specific render config version."""
        cfg_dir = self._get_config_dir(project_id)
        version_path = cfg_dir / f"render_config_v{version}.json"
        if not version_path.exists():
            return None
        try:
            data = read_json(version_path)
            return RenderConfiguration(**data)
        except Exception:
            return None

    def save_new_version(
        self,
        project_id: str,
        configuration: RenderConfiguration,
    ) -> RenderConfiguration:
        """
        Save a configuration as a new version.
        Guarantees that older versions are never overwritten.
        Updates current.json atomically.
        """
        cfg_dir = self._get_config_dir(project_id)
        existing = self.list_versions(project_id)
        max_ver = max([c.version for c in existing], default=0)
        next_ver = max_ver + 1

        new_id = f"cfg_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)

        config_dict = configuration.model_dump(mode="json")
        config_dict["id"] = new_id
        config_dict["project_id"] = project_id
        config_dict["version"] = next_ver
        config_dict["created_at"] = now.isoformat()
        config_dict["updated_at"] = now.isoformat()

        new_config = RenderConfiguration(**config_dict)

        version_path = cfg_dir / f"render_config_v{next_ver}.json"
        current_path = cfg_dir / "current.json"

        # Atomic write
        payload = new_config.model_dump(mode="json")
        atomic_write_json(version_path, payload)
        atomic_write_json(current_path, payload)

        return new_config

    def restore_version(self, project_id: str, version: int) -> RenderConfiguration:
        """Restore an older version as the active configuration by creating a new version pointing to its settings."""
        target = self.get_version(project_id, version)
        if not target:
            raise RenderError(f"Render configuration version {version} not found")
        return self.save_new_version(project_id, target)

    def validate_render_configuration(
        self,
        project_id: str,
        configuration: RenderConfiguration,
        timeline_duration: float,
    ) -> List[str]:
        """
        Validate render configuration before starting render job.
        Returns a list of error messages (empty if completely valid).
        """
        errors: List[str] = []

        if timeline_duration <= 0 or not math.isfinite(timeline_duration):
            errors.append(f"Invalid timeline duration: {timeline_duration}")

        # Check voice
        if not math.isfinite(configuration.voice.gain_db):
            errors.append("Voice gain must be a finite number")

        # Check loudness
        if not math.isfinite(configuration.loudness.target_lufs):
            errors.append("Target LUFS must be a finite number")

        # Check music
        if configuration.music.enabled:
            if not configuration.music.asset_id:
                errors.append("Music is enabled but no asset was selected")
            else:
                asset = self.asset_service.get_music_asset(project_id, configuration.music.asset_id)
                if not asset:
                    errors.append(f"Selected music asset '{configuration.music.asset_id}' does not exist")
                elif not asset.rights_confirmed:
                    errors.append("Selected music asset does not have confirmed rights")

            if not math.isfinite(configuration.music.gain_db):
                errors.append("Music gain must be a finite number")

            if configuration.music.source_offset_seconds < 0:
                errors.append("Music source offset cannot be negative")

            if configuration.music.timeline_start_seconds < 0:
                errors.append("Music timeline start cannot be negative")

            if configuration.music.fade_in_seconds < 0 or configuration.music.fade_out_seconds < 0:
                errors.append("Fade duration cannot be negative")

        return errors
