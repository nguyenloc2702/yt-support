"""Pydantic schemas for API boundary (Appendix D/E shapes live here too)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source: dict = Field(description="{type: youtube_url|upload|drive_url, url|file_ref}")
    target: dict = Field(description="{language, style_lock_version}")
    options: dict = Field(default_factory=dict)


class ShotImport(BaseModel):
    script_version: int = Field(ge=1)
    language: str | None = None
    text: str | None = None
    audio: dict = Field(description="{file_url|file_path, duration_sec (untrusted)}")
    shots: list[dict] = Field(min_length=1)


class BatchCreate(BaseModel):
    size: int = Field(default=4, ge=1, le=4)


class GateDecision(BaseModel):
    gate: str
    decision: str
    reason: str = ""
    version: int | None = None


class HoldRequest(BaseModel):
    reason: str
    detail: str | None = None


class WebhookRegister(BaseModel):
    url: str
    events: list[str] | None = None
    item_id: str | None = None
    secret: str | None = Field(default=None,
                               description="HMAC secret; omit to use VIDEO_WEBHOOK_SECRET")


class CharacterSheetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="protagonist")
    reference_path: str = Field(description="path under VIDEO_DATA_DIR of the ref image")


class GenerateRequest(BaseModel):
    actor: str = "api"


class TodoFromScriptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    script_text: str | None = Field(
        default=None,
        description="Full English script text to automatically segment into timed shots",
    )
    shots: list[dict] | None = Field(
        default=None,
        description="Optional structured list of shots [{index, narration, visual_prompt, shot_type, ...}]",
    )
    bgm_enabled: bool = Field(default=True, description="Mix background music with sidechain ducking")
    voice: str | None = Field(default=None, description="Edge-TTS English voice name")
    collect_references: bool = Field(
        default=True,
        description="Automatically harvest 10-20 reference images throughout the script",
    )


class TodoFromYoutubeCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    youtube_url: str = Field(min_length=5, description="YouTube URL or 11-char video ID")
    target_duration_sec: float | None = Field(
        default=None, ge=5.0, le=3600.0,
        description="Optional target duration cap in seconds",
    )
    bgm_enabled: bool = Field(default=True, description="Mix background music with sidechain ducking")
    voice: str | None = Field(default=None, description="Edge-TTS English voice name")
    transcript_segments: list[dict] | None = Field(
        default=None,
        description="Optional pre-fetched transcript segments [{start, end, text}] for offline/test mode",
    )
    collect_references: bool = Field(
        default=True,
        description="Automatically harvest 10-20 reference images throughout the script",
    )


class ShortClipSubmitJson(BaseModel):
    """G.13 — agent video ngoài submit clip cho Short job (JSON mode)."""
    seq_num: int | None = Field(default=None, ge=1, description="1-based; tự tăng nếu bỏ trống")
    file_path: str | None = Field(default=None, description="Path trong VIDEO_DATA_DIR")
    file_url: str | None = Field(default=None, description="URL allowlisted")
    covered_scene_indexes: list[int] | None = Field(
        default=None, description="Scene bị clip này phủ (gộp nhiều scene)")
    note: str | None = Field(default=None, max_length=500)


class TodoVideoSubmitJson(BaseModel):
    seq_num: int | None = Field(default=None, ge=1, description="1-based sequential order (1, 2, 3...)")
    file_path: str | None = Field(default=None, description="Path to video file under VIDEO_DATA_DIR")
    file_url: str | None = Field(default=None, description="Allowlisted URL to fetch video file")
    covered_shot_indexes: list[int] | None = Field(
        default=None,
        description="Optional metadata listing which script shot indexes this video clip covers",
    )
    note: str | None = Field(default=None, max_length=500)


class TodoImageSubmitJson(BaseModel):
    kind: str = Field(default="keyframe", description="'keyframe', 'character', 'scene', or 'reference'")
    seq_num: int | None = Field(default=None, ge=1, description="1-based image sequence number")
    shot_index: int | None = Field(default=None, ge=1, description="Target shot index for keyframe or reference")
    name: str | None = Field(default=None, max_length=120, description="Character or image label")
    base64_data: str | None = Field(default=None, description="Base64-encoded PNG/JPG/WEBP image bytes")
    file_path: str | None = Field(default=None, description="Path to image file under VIDEO_DATA_DIR")
    file_url: str | None = Field(default=None, description="Allowlisted URL to fetch image file")
    prompt_used: str | None = Field(default=None, description="Prompt or query used by the agent to generate/find the image")
    covered_shot_indexes: list[int] | None = Field(
        default=None,
        description="Shot indexes covered by this keyframe or reference image",
    )
    note: str | None = Field(default=None, max_length=500)


class TodoCollectReferencesRequest(BaseModel):
    count: int = Field(default=15, ge=5, le=25, description="Target number of reference images (10-20)")
    force: bool = Field(default=False, description="Re-harvest even if reference images already exist")


class TodoImportPackageCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    audio_file_path: str = Field(description="Path to existing EN audio file (.mp3 or .wav)")
    shots: list[dict] = Field(min_length=1, description="List of timed English shots")
    source_url: str | None = None
    bgm_enabled: bool = True
    voice: str = "en-US-AndrewMultilingualNeural"



class ConfirmAssembleRequest(BaseModel):
    burn_subtitles: bool = Field(
        default=False,
        description="Whether to burn English subtitles into the final assembled MP4",
    )
    allow_shortfall: bool = Field(
        default=False,
        description="Set to true to confirm assembly even when total video duration is shorter than EN audio",
    )
    watermark_enabled: bool = Field(
        default=True,
        description="Whether to burn a moving translucent anti-theft watermark into the final assembled MP4",
    )
    watermark_text: str | None = Field(
        default=None,
        max_length=40,
        description="Optional custom watermark text (defaults to render_spec.yaml watermark.text, e.g. @StickSto)",
    )
    allow_missing_thumbnail: bool = Field(
        default=False,
        description="Set to true to allow assembly even when no thumbnail image (kind=thumbnail) has been uploaded",
    )


class SyncGdriveRequest(BaseModel):
    purge_raw_clips: bool = Field(
        default=False,
        description="Whether to purge raw uploaded video clips after successful GDrive sync",
    )


class YouTubePublishRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    privacy_status: str = Field(default="private", pattern="^(private|unlisted|public)$")
    publish_at: str | None = Field(
        default=None,
        description="Optional ISO-8601 UTC/offset timestamp at least 10 minutes in the future; forces privacy_status='private'",
    )
    upload_captions: bool = Field(default=True)
    category_id: str = Field(default="22", max_length=16)


class YouTubeVideoPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = Field(default=None, max_length=30)
    privacy_status: str | None = Field(default=None, pattern="^(private|unlisted|public)$")


class FeedbackItemCreate(BaseModel):
    category: str = Field(
        default="pronunciation",
        description="'pronunciation'|'proper_noun'|'year_number'|'pacing'|'script_mismatch'|'audio_quality'|'bgm'|'other'",
    )
    severity: str = Field(default="warning", description="'info'|'warning'|'critical'")
    shot_index: int | None = Field(default=None, ge=1, description="1-based shot index if tied to a specific shot")
    timestamp_sec: float | None = Field(default=None, ge=0.0, description="Timestamp in audio_en.wav where issue was heard")
    original_text: str | None = Field(default=None, max_length=500, description="Original word/phrase in script/audio")
    suggested_text: str | None = Field(default=None, max_length=500, description="Agent-suggested correction")
    pronunciation_hint: str | None = Field(default=None, max_length=500, description="Phonetic or pronunciation spelling guide")
    message: str = Field(min_length=1, max_length=2000, description="Detailed explanation from the agent")
    agent_name: str = Field(default="external_video_agent", max_length=100)


class FeedbackBatchCreate(BaseModel):
    feedbacks: list[FeedbackItemCreate]


class FeedbackResolveRequest(BaseModel):
    status: str = Field(default="resolved", description="'resolved' | 'ignored' | 'open'")
    resolution_note: str | None = Field(default=None, max_length=1000)


class AudioRegenerateRequest(BaseModel):
    slow_factor: float = Field(default=0.96, ge=0.70, le=1.20, description="Speed factor (0.96 = ~4% slower)")
    extra_pause_sec: float = Field(default=0.12, ge=0.0, le=1.0, description="Extra pause between shots in seconds")
    bgm_enabled: bool = Field(default=True, description="Mix multi-track BGM bed under voice track")
    bgm_duck_db: float = Field(default=-20.0, description="BGM duck level in dB (synced with audio_config.yaml)")





class WebLoginRequest(BaseModel):
    pin: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$",
                     description="Mã PIN 6 số mở khóa web UI")


class ShotCheckUpdate(BaseModel):
    shot_index: int | None = Field(default=None, ge=1, description="1-based shot index")
    checked: bool | None = Field(default=None, description="True if shot is generated/approved for agent tracking")
    is_generated: bool | None = Field(default=None, description="Alias for checked")
    generated_duration_sec: float | None = Field(default=None, ge=0.0, description="Actual generated duration in seconds for this shot")
    note: str | None = Field(default=None, max_length=500, description="Optional note for agent/human")


class ShotChecklistBatchUpdate(BaseModel):
    updates: list[ShotCheckUpdate] = Field(default_factory=list)
    check_all_with_video: bool = Field(default=False, description="Automatically tick all shots that have an uploaded video clip")
    uncheck_missing: bool = Field(default=False, description="Automatically untick shots that have no video clip")


# --- Short Studio (Phụ lục G) ------------------------------------------------

class ShortCreate(BaseModel):
    mode: str = Field(pattern="^(news|decode)$",
                      description="news = Tin nóng 25-40s; decode = Giải mã tiền bạc 40-60s")
    lang: str = Field(default="vi", pattern="^(vi|en)$",
                      description="vi = tiếng Việt; en = mảng quốc tế (voice + prompt EN)")
    source_text: str = Field(min_length=1, max_length=6000,
                             description="Tin vắn dán vào hoặc chủ đề")
    source_note: str | None = Field(default=None, max_length=300,
                                    description="Nguồn tin ghi trên video (mode news)")
    source_link: str | None = Field(default=None, max_length=1000,
                                    description="Link bài báo — lấy ảnh gốc minh họa (G.11)")
    target_seconds: float | None = Field(default=None, ge=10, le=90)
    title: str | None = Field(default=None, max_length=200)
    voice: str | None = Field(default=None, max_length=100,
                              description="Edge-TTS voice override (mặc định theo lang)")


class ShortScriptUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    hook: str | None = Field(default=None, max_length=500)
    beats: list[str] | None = Field(default=None, max_length=4)
    closing: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=1000)
    source_note: str | None = Field(default=None, max_length=300)

