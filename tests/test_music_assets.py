import io
import pytest
from core.exceptions import InvalidMediaError
from services.asset_service import AssetService


def test_upload_music_rights_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", tmp_path)
    service = AssetService()

    fake_file = io.BytesIO(b"dummy audio content")
    with pytest.raises(ValueError, match="Rights must be explicitly confirmed"):
        service.upload_music(
            project_id="test_proj",
            uploaded_file=fake_file,
            original_filename="song.mp3",
            rights_confirmed=False,
        )


def test_upload_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", tmp_path)
    service = AssetService()

    fake_file = io.BytesIO(b"dummy exe content")
    with pytest.raises(InvalidMediaError, match="Unsupported music extension"):
        service.upload_music(
            project_id="test_proj",
            uploaded_file=fake_file,
            original_filename="malicious.exe",
            rights_confirmed=True,
        )


def test_resolve_music_path_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", tmp_path)
    service = AssetService()

    with pytest.raises(InvalidMediaError, match="not found"):
        service.resolve_music_path("test_proj", "nonexistent_asset_id")
