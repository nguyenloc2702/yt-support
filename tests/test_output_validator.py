import pytest
from core.render_schemas import OutputValidationResult
from pipeline.output_validator import generate_qc_report, validate_rendered_output
from utils.json_io import read_json


def test_validate_rendered_output_nonexistent(tmp_path):
    fake_path = tmp_path / "nonexistent.mp4"
    res = validate_rendered_output(
        output_path=fake_path,
        expected_duration=10.0,
        expected_width=1920,
        expected_height=1080,
        expect_audio=True,
    )
    assert not res.valid
    assert any("does not exist" in err for err in res.errors)


def test_validate_rendered_output_empty_file(tmp_path):
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    res = validate_rendered_output(
        output_path=empty_file,
        expected_duration=10.0,
        expected_width=1920,
        expected_height=1080,
        expect_audio=True,
    )
    assert not res.valid
    assert any("empty" in err for err in res.errors)


def test_generate_qc_report(tmp_path):
    out_file = tmp_path / "video.mp4"
    out_file.write_bytes(b"test")

    val_res = OutputValidationResult(
        valid=True,
        duration_seconds=15.2,
        width=1920,
        height=1080,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
    )

    qc_path = generate_qc_report(
        output_path=out_file,
        validation_result=val_res,
        render_id="rend_123",
        project_id="proj_abc",
        timeline_version=2,
        render_config_version=3,
    )

    assert qc_path.exists()
    data = read_json(qc_path)
    assert data["render_id"] == "rend_123"
    assert data["render_config_version"] == 3
    assert data["valid"] is True
    assert data["media"]["video_codec"] == "h264"
