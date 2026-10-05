from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field


class MusicAsset(BaseModel):
    id: str
    project_id: str
    stored_filename: str
    original_filename: str
    relative_path: str
    duration_seconds: float = Field(gt=0)
    codec_name: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    rights_type: str = "owned"
    rights_confirmed: bool = False
    source_url: Optional[str] = None
    license_file_path: Optional[str] = None
    rights_notes: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VoiceOptions(BaseModel):
    enabled: bool = True
    gain_db: float = Field(default=0.0, ge=-60.0, le=12.0)


class MusicOptions(BaseModel):
    enabled: bool = False
    asset_id: Optional[str] = None
    gain_db: float = Field(default=-16.5, ge=-60.0, le=6.0)
    loop: bool = True
    source_offset_seconds: float = Field(default=0.0, ge=0.0)
    timeline_start_seconds: float = Field(default=0.0, ge=0.0)
    fade_in_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    fade_out_seconds: float = Field(default=3.0, ge=0.0, le=30.0)
    short_music_mode: Literal["loop", "stop"] = "loop"


class LoudnessOptions(BaseModel):
    enabled: bool = True
    target_lufs: float = Field(default=-16.0, ge=-24.0, le=-9.0)
    loudness_range: float = Field(default=11.0, ge=1.0, le=20.0)
    true_peak_db: float = Field(default=-1.5, ge=-6.0, le=0.0)


class RenderConfiguration(BaseModel):
    id: str
    project_id: str
    version: int = Field(default=1, ge=1)
    timeline_version: int = Field(default=1, ge=1)
    preset_name: str = "youtube_1080p"
    voice: VoiceOptions = Field(default_factory=VoiceOptions)
    music: MusicOptions = Field(default_factory=MusicOptions)
    loudness: LoudnessOptions = Field(default_factory=LoudnessOptions)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TestPreviewOptions(BaseModel):
    output_start_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(default=20.0, ge=10.0, le=30.0)


class AudioMixRequest(BaseModel):
    joined_video_path: str
    output_path: str
    output_duration_seconds: float = Field(gt=0)
    output_timeline_offset_seconds: float = Field(default=0.0, ge=0.0)
    source_has_audio: bool = True
    voice: VoiceOptions = Field(default_factory=VoiceOptions)
    music: MusicOptions = Field(default_factory=MusicOptions)
    loudness: LoudnessOptions = Field(default_factory=LoudnessOptions)


class OutputValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    has_video: bool = False
    has_audio: bool = False
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
