from datetime import datetime
from pathlib import Path
from typing import BinaryIO, List, Optional
import uuid
import shutil

from config.settings import settings
from core.exceptions import InvalidMediaError
from core.render_schemas import MusicAsset
from utils.audio import probe_audio
from utils.json_io import atomic_write_json, read_json


class AssetService:
    def __init__(self):
        self.projects_dir = Path(settings.projects_dir)
        self.max_upload_bytes = settings.max_music_upload_mb * 1024 * 1024
        self.allowed_extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

    def _get_music_dir(self, project_id: str) -> Path:
        music_dir = (self.projects_dir / project_id / "assets" / "music").resolve()
        music_dir.mkdir(parents=True, exist_ok=True)
        return music_dir

    def _get_manifest_path(self, project_id: str) -> Path:
        return self._get_music_dir(project_id) / "manifest.json"

    def _load_manifest(self, project_id: str) -> dict:
        manifest_path = self._get_manifest_path(project_id)
        if not manifest_path.exists():
            return {"version": 1, "assets": []}
        try:
            return read_json(manifest_path)
        except Exception:
            return {"version": 1, "assets": []}

    def _save_manifest(self, project_id: str, manifest: dict) -> None:
        manifest_path = self._get_manifest_path(project_id)
        atomic_write_json(manifest_path, manifest)

    def upload_music(
        self,
        project_id: str,
        uploaded_file: BinaryIO,
        original_filename: str,
        rights_type: str = "owned",
        rights_confirmed: bool = False,
        source_url: Optional[str] = None,
        rights_notes: Optional[str] = None,
    ) -> MusicAsset:
        """
        Upload and register a background music asset for the project.
        - Validates extension and file size.
        - Probes audio stream with FFprobe.
        - Enforces rights confirmation gate.
        - Generates safe internal filename and writes manifest atomically.
        """
        if not rights_confirmed:
            raise ValueError("Rights must be explicitly confirmed before using music asset.")

        ext = Path(original_filename).suffix.lower()
        if ext not in self.allowed_extensions:
            raise InvalidMediaError(
                f"Unsupported music extension '{ext}'. Allowed: {', '.join(sorted(self.allowed_extensions))}"
            )

        music_dir = self._get_music_dir(project_id)
        asset_id = f"music_{uuid.uuid4().hex[:8]}"
        stored_filename = f"{asset_id}{ext}"
        temp_path = music_dir / f"upload_temp_{asset_id}{ext}"

        try:
            uploaded_file.seek(0, 2)
            size = uploaded_file.tell()
            uploaded_file.seek(0)

            if size > self.max_upload_bytes:
                raise InvalidMediaError(
                    f"Music file size ({size / (1024*1024):.1f} MB) exceeds maximum allowed {settings.max_music_upload_mb} MB"
                )

            with open(temp_path, "wb") as f:
                shutil.copyfileobj(uploaded_file, f)

            # Validate via FFprobe
            probe_info = probe_audio(temp_path)

            target_path = music_dir / stored_filename
            temp_path.replace(target_path)

            relative_path = f"assets/music/{stored_filename}"

            asset = MusicAsset(
                id=asset_id,
                project_id=project_id,
                stored_filename=stored_filename,
                original_filename=original_filename,
                relative_path=relative_path,
                duration_seconds=probe_info["duration_seconds"],
                codec_name=probe_info.get("codec_name"),
                sample_rate=probe_info.get("sample_rate"),
                channels=probe_info.get("channels"),
                rights_type=rights_type,
                rights_confirmed=rights_confirmed,
                source_url=source_url,
                rights_notes=rights_notes,
                created_at=datetime.utcnow(),
            )

            manifest = self._load_manifest(project_id)
            assets_list = manifest.get("assets", [])
            assets_list.append(asset.model_dump(mode="json"))
            manifest["assets"] = assets_list
            self._save_manifest(project_id, manifest)

            return asset
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def list_music_assets(self, project_id: str) -> List[MusicAsset]:
        """List all registered music assets for a project."""
        manifest = self._load_manifest(project_id)
        results = []
        for raw in manifest.get("assets", []):
            try:
                results.append(MusicAsset(**raw))
            except Exception:
                continue
        return results

    def get_music_asset(self, project_id: str, asset_id: str) -> Optional[MusicAsset]:
        """Retrieve a single music asset by its ID."""
        for asset in self.list_music_assets(project_id):
            if asset.id == asset_id:
                return asset
        return None

    def delete_music_asset(self, project_id: str, asset_id: str) -> None:
        """Delete a music asset and remove it from the manifest."""
        asset = self.get_music_asset(project_id, asset_id)
        if not asset:
            return

        music_dir = self._get_music_dir(project_id)
        asset_path = (music_dir / asset.stored_filename).resolve()
        if asset_path.is_relative_to(music_dir) and asset_path.exists():
            asset_path.unlink()

        manifest = self._load_manifest(project_id)
        manifest["assets"] = [a for a in manifest.get("assets", []) if a.get("id") != asset_id]
        self._save_manifest(project_id, manifest)

    def resolve_music_path(self, project_id: str, asset_id: str) -> Path:
        """
        Safely resolve the absolute Path of a music asset.
        Rejects path traversal or symlinks outside the music directory.
        """
        asset = self.get_music_asset(project_id, asset_id)
        if not asset:
            raise InvalidMediaError(f"Music asset '{asset_id}' not found in project '{project_id}'")

        music_dir = self._get_music_dir(project_id)
        asset_path = (music_dir / asset.stored_filename).resolve()

        if not asset_path.is_relative_to(music_dir):
            raise InvalidMediaError("Security alert: Unsafe music asset path")

        if not asset_path.exists():
            raise InvalidMediaError(f"Music asset file missing on disk: {asset_path}")

        return asset_path
