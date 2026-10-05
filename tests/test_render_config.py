import pytest
from core.render_schemas import MusicOptions, RenderConfiguration, VoiceOptions
from services.render_config_service import RenderConfigService


def test_save_new_version_increments(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", tmp_path)
    service = RenderConfigService()
    project_id = "test_p1"

    # Default v1
    cfg1 = service.get_current(project_id)
    assert cfg1 is not None
    assert cfg1.version == 1

    # Save v2
    cfg2_input = RenderConfiguration(
        id="c2",
        project_id=project_id,
        version=1,
        voice=VoiceOptions(enabled=True, gain_db=2.5),
    )
    saved_v2 = service.save_new_version(project_id, cfg2_input)
    assert saved_v2.version == 2
    assert saved_v2.voice.gain_db == 2.5

    # Check that v1 still exists and was not overwritten
    old_v1 = service.get_version(project_id, 1)
    assert old_v1 is not None
    assert old_v1.version == 1

    # Check current points to v2
    current = service.get_current(project_id)
    assert current.version == 2


def test_validate_render_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.settings.projects_dir", tmp_path)
    service = RenderConfigService()
    project_id = "test_p2"

    # Pydantic schema validation catches negative fade
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MusicOptions(enabled=True, fade_in_seconds=-1.0)

    # Service validation catches enabled music without asset_id or missing asset
    invalid_config = RenderConfiguration(
        id="c_err",
        project_id=project_id,
        music=MusicOptions(enabled=True, asset_id=None),
    )

    errors = service.validate_render_configuration(
        project_id=project_id,
        configuration=invalid_config,
        timeline_duration=10.0,
    )
    assert any("no asset was selected" in err for err in errors)
