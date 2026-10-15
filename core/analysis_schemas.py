"""Pydantic schemas for block-based video analysis workflow.

This module defines the data structures for the human-in-the-loop video analysis
pipeline. All timecode values use integer milliseconds for precision.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# =============================================================================
# Base Types
# =============================================================================

class TimeRangeMs(BaseModel):
    """A time range with millisecond precision."""
    start_ms: int = Field(..., ge=0, description="Start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="End time in milliseconds")
    
    @model_validator(mode='after')
    def end_after_start(self) -> 'TimeRangeMs':
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) must be > start_ms ({self.start_ms})")
        return self
    
    @property
    def duration_ms(self) -> int:
        """Computed duration in milliseconds."""
        return self.end_ms - self.start_ms


# =============================================================================
# Media Information
# =============================================================================

class MediaInfo(BaseModel):
    """Metadata about the source video/audio file."""
    duration_ms: int = Field(..., gt=0, description="Total duration in milliseconds")
    fps_num: int = Field(..., gt=0, description="Frame rate numerator (e.g., 30000)")
    fps_den: int = Field(..., gt=0, description="Frame rate denominator (e.g., 1001)")
    width: int = Field(..., gt=0, description="Video width in pixels")
    height: int = Field(..., gt=0, description="Video height in pixels")
    audio_sample_rate: int = Field(default=0, ge=0, description="Audio sample rate in Hz (0 if no audio)")
    audio_channels: int = Field(default=0, ge=0, description="Number of audio channels (0 if no audio)")
    audio_codec: str | None = Field(default=None, description="Audio codec name")
    video_codec: str | None = Field(default=None, description="Video codec name")
    
    @property
    def fps(self) -> float:
        """Computed frames per second."""
        return self.fps_num / self.fps_den
    
    @property
    def duration_seconds(self) -> float:
        """Duration in seconds."""
        return self.duration_ms / 1000.0


# =============================================================================
# Shot Analysis
# =============================================================================

class ShotAnalysis(BaseModel):
    """Represents a detected shot (scene) in the video."""
    shot_id: str = Field(..., description="Unique identifier for this shot")
    start_ms: int = Field(..., ge=0, description="Shot start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="Shot end time in milliseconds")
    frame_path: str | None = Field(default=None, description="Path to representative frame")
    frame_index: int | None = Field(default=None, ge=0, description="Frame index in video")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Detection confidence")
    description: str = Field(default="", description="AI-generated description of the shot")
    
    @model_validator(mode='after')
    def validate_time_range(self) -> 'ShotAnalysis':
        if self.end_ms <= self.start_ms:
            raise ValueError("Shot end_ms must be > start_ms")
        return self
    
    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# =============================================================================
# Audio Analysis
# =============================================================================

class Speaker(BaseModel):
    """Represents a detected speaker in the audio."""
    speaker_id: str = Field(..., description="Unique identifier for this speaker")
    label: str | None = Field(default=None, description="Human-readable label")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Detection confidence")


class TranscriptWord(BaseModel):
    """A single word with timing information."""
    text: str = Field(..., description="The word text")
    start_ms: int = Field(..., ge=0, description="Word start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="Word end time in milliseconds")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Word confidence")
    
    @model_validator(mode='after')
    def validate_word_time(self) -> 'TranscriptWord':
        if self.end_ms <= self.start_ms:
            raise ValueError("Word end_ms must be > start_ms")
        return self
    
    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class TranscriptSegment(BaseModel):
    """A segment of transcribed speech."""
    id: str = Field(..., description="Unique segment identifier")
    start_ms: int = Field(..., ge=0, description="Segment start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="Segment end time in milliseconds")
    text: str = Field(..., description="Transcribed text")
    speaker_id: str | None = Field(default=None, description="Reference to speaker_id")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Segment confidence")
    words: list[TranscriptWord] = Field(default_factory=list, description="Individual words with timing")
    
    @model_validator(mode='after')
    def validate_segment_time(self) -> 'TranscriptSegment':
        if self.end_ms <= self.start_ms:
            raise ValueError("Segment end_ms must be > start_ms")
        return self
    
    @model_validator(mode='after')
    def validate_words_in_range(self) -> 'TranscriptSegment':
        for word in self.words:
            if word.start_ms < self.start_ms or word.end_ms > self.end_ms:
                raise ValueError(f"Word {word.text} timing outside segment range")
        return self
    
    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class AudioEventType(str, Enum):
    SPEECH = "speech"
    SILENCE = "silence"
    MUSIC = "music"
    NOISE = "noise"
    LAUGHTER = "laughter"
    OTHER = "other"


class AudioEvent(BaseModel):
    """An event detected in the audio track."""
    model_config = {"populate_by_name": True}
    
    event_id: str = Field(..., description="Unique event identifier")
    start_ms: int = Field(..., ge=0, description="Event start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="Event end time in milliseconds")
    type: AudioEventType = Field(default=AudioEventType.SPEECH, description="Type of audio event")
    transcript_segment_ids: list[str] = Field(default_factory=list, description="Related transcript segments")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Event detection confidence")
    
    @model_validator(mode='after')
    def validate_event_time(self) -> 'AudioEvent':
        if self.end_ms <= self.start_ms:
            raise ValueError("Audio event end_ms must be > start_ms")
        return self
    
    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# =============================================================================
# Content Blocks
# =============================================================================

class AudioDeliveryStatus(str, Enum):
    MISSING = "missing"
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    APPROVED = "approved"


class AudioFitMode(str, Enum):
    """How to fit audio to the target time range."""
    PRESERVE_SPEED = "preserve_speed"  # Keep original speed, warn if mismatch
    TRIM_EDGE_SILENCE = "trim_edge_silence"  # Trim leading/trailing silence
    FIT_95_105_PERCENT = "fit_95_105_percent"  # Speed up/slow down within 5-105%
    MANUAL_TRIM = "manual_trim"  # User manually trimmed


class AudioDelivery(BaseModel):
    """Information about uploaded audio for a content block."""
    file_path: str = Field(..., description="Path to the uploaded audio file")
    actual_duration_ms: int = Field(..., gt=0, description="Actual audio duration in milliseconds")
    expected_start_ms: int = Field(..., ge=0, description="Expected start time in output")
    expected_end_ms: int = Field(..., gt=0, description="Expected end time in output")
    fit_mode: str = Field(
        default=AudioFitMode.PRESERVE_SPEED.value,
        description="How to fit audio to time range",
    )
    overlap_ms: int = Field(default=0, ge=0, description="Overlap with next block in ms (0 if none)")
    gap_ms: int = Field(default=0, description="Gap before next block in ms (0 if none)")
    overflow_ms: int = Field(default=0, ge=0, description="How much longer the audio is than its window")
    warnings: list[str] = Field(default_factory=list, description="Non-fitting warnings")
    content_hash: str | None = Field(default=None, description="SHA-256 of the audio file")
    sample_rate: int = Field(default=0, ge=0, description="Audio sample rate")
    channels: int = Field(default=0, ge=0, description="Number of audio channels")
    codec: str | None = Field(default=None, description="Audio codec name")
    status: AudioDeliveryStatus = Field(default=AudioDeliveryStatus.UPLOADED, description="Delivery status")
    approved: bool = Field(default=False, description="Whether audio has been approved")
    
    @property
    def expected_duration_ms(self) -> int:
        return self.expected_end_ms - self.expected_start_ms

    @model_validator(mode='after')
    def validate_fit_mode(self) -> 'AudioDelivery':
        allowed = {m.value for m in AudioFitMode}
        if self.fit_mode not in allowed:
            raise ValueError(
                f"Invalid fit_mode '{self.fit_mode}'. Allowed: {sorted(allowed)}"
            )
        return self

    @model_validator(mode='after')
    def validate_time_range(self) -> 'AudioDelivery':
        if self.expected_end_ms <= self.expected_start_ms:
            raise ValueError("expected_end_ms must be > expected_start_ms")
        return self


class ContentBlock(BaseModel):
    """A content block combining video shots and audio transcript.
    
    This is the primary unit for human review and script editing.
    """
    block_id: str = Field(..., description="Unique block identifier")
    start_ms: int = Field(..., ge=0, description="Block start time in milliseconds")
    end_ms: int = Field(..., gt=0, description="Block end time in milliseconds")
    source_start_ms: int = Field(..., ge=0, description="Start in source media (ms)")
    source_end_ms: int = Field(..., gt=0, description="End in source media (ms)")
    
    # References to source data
    shot_ids: list[str] = Field(default_factory=list, description="List of shot IDs in this block")
    transcript_segment_ids: list[str] = Field(default_factory=list, description="List of transcript segment IDs")
    audio_event_ids: list[str] = Field(default_factory=list, description="List of audio event IDs")
    
    # Content
    description: str = Field(default="", description="AI-generated description of visual content")
    transcript: str = Field(default="", description="Combined transcript text from audio")
    speaker_ids: list[str] = Field(default_factory=list, description="List of speaker IDs")
    
    # Script workflow
    script_draft: str = Field(default="", description="AI-generated script draft")
    script_final: str | None = Field(default=None, description="Human-approved final script")
    
    # Quality and status
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall block confidence")
    human_verified: bool = Field(default=False, description="Whether human has verified this block")
    review_note: str = Field(default="", description="Human review notes")
    
    # Audio delivery
    audio_delivery: AudioDelivery | None = Field(default=None, description="Uploaded audio information")
    
    # Validation
    @model_validator(mode='after')
    def validate_block_time(self) -> 'ContentBlock':
        if self.end_ms <= self.start_ms:
            raise ValueError("Block end_ms must be > start_ms")
        return self
    
    @model_validator(mode='after')
    def validate_source_time(self) -> 'ContentBlock':
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("source_end_ms must be > source_start_ms")
        return self
    
    @model_validator(mode='after')
    def validate_source_range_matches_output(self) -> 'ContentBlock':
        expected_duration = self.end_ms - self.start_ms
        source_duration = self.source_end_ms - self.source_start_ms
        if expected_duration != source_duration:
            raise ValueError(f"Output duration ({expected_duration}ms) != source duration ({source_duration}ms)")
        return self
    
    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


# =============================================================================
# Review Status
# =============================================================================

class ReviewStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewInfo(BaseModel):
    """Review status for the analysis document."""
    status: ReviewStatus = Field(default=ReviewStatus.DRAFT, description="Overall review status")
    verified_block_ids: list[str] = Field(default_factory=list, description="List of human-verified block IDs")
    approved_at: datetime | None = Field(default=None, description="When analysis was approved")
    approved_by: str | None = Field(default=None, description="Who approved the analysis")


# =============================================================================
# Main Analysis Document
# =============================================================================

class AnalysisDocument(BaseModel):
    """Complete analysis document for a video.
    
    This is the central data structure for the human-in-the-loop workflow.
    All timecode values use integer milliseconds for precision.
    """
    schema_version: int = Field(default=1, ge=1, description="Schema version number")
    version: int = Field(default=1, ge=1, description="Sequential document version (increments on each review save)")
    analysis_id: str = Field(..., description="Unique analysis identifier")
    project_id: str = Field(..., description="Reference to project")
    
    # Media metadata
    media: MediaInfo = Field(..., description="Source media information")
    
    # Analysis data
    speakers: list[Speaker] = Field(default_factory=list, description="Detected speakers")
    shots: list[ShotAnalysis] = Field(default_factory=list, description="Detected shots")
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list, description="Transcript segments")
    audio_events: list[AudioEvent] = Field(default_factory=list, description="Audio events")
    content_blocks: list[ContentBlock] = Field(default_factory=list, description="Content blocks for review")
    
    # Review status
    review: ReviewInfo = Field(default_factory=ReviewInfo, description="Review status and metadata")
    
    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")
    
    # Validation: block_id must be unique
    @model_validator(mode='after')
    def validate_unique_block_ids(self) -> 'AnalysisDocument':
        """Ensure content block IDs are unique."""
        seen: set = set()
        for block in self.content_blocks:
            if block.block_id in seen:
                raise ValueError(f"Duplicate block_id: {block.block_id}")
            seen.add(block.block_id)
        return self

    # Validation: ensure no overlapping narration blocks
    @model_validator(mode='after')
    def validate_no_overlapping_narration_blocks(self) -> 'AnalysisDocument':
        """Ensure that content blocks with narration do not overlap."""
        narration_blocks = [
            b for b in self.content_blocks 
            if b.script_final is not None and b.script_final.strip()
        ]
        
        for i, block_a in enumerate(narration_blocks):
            for block_b in narration_blocks[i+1:]:
                # Check if blocks overlap
                if block_a.start_ms < block_b.end_ms and block_b.start_ms < block_a.end_ms:
                    raise ValueError(
                        f"Narration blocks {block_a.block_id} ({block_a.start_ms}-{block_a.end_ms}ms) "
                        f"and {block_b.block_id} ({block_b.start_ms}-{block_b.end_ms}ms) overlap"
                    )
        return self
    
    # Validation: ensure all blocks are within media duration
    @model_validator(mode='after')
    def validate_blocks_within_media(self) -> 'AnalysisDocument':
        """Ensure all content blocks are within media duration."""
        media_duration = self.media.duration_ms
        for block in self.content_blocks:
            if block.end_ms > media_duration:
                raise ValueError(
                    f"Block {block.block_id} ends at {block.end_ms}ms, "
                    f"but media duration is {media_duration}ms"
                )
        return self
    
    # Validation: ensure all shots are within media duration
    @model_validator(mode='after')
    def validate_shots_within_media(self) -> 'AnalysisDocument':
        """Ensure all shots are within media duration."""
        media_duration = self.media.duration_ms
        for shot in self.shots:
            if shot.end_ms > media_duration:
                raise ValueError(
                    f"Shot {shot.shot_id} ends at {shot.end_ms}ms, "
                    f"but media duration is {media_duration}ms"
                )
        return self
    
    # Validation: ensure transcript segments are within media duration
    @model_validator(mode='after')
    def validate_transcripts_within_media(self) -> 'AnalysisDocument':
        """Ensure all transcript segments are within media duration."""
        media_duration = self.media.duration_ms
        for seg in self.transcript_segments:
            if seg.end_ms > media_duration:
                raise ValueError(
                    f"Transcript segment {seg.id} ends at {seg.end_ms}ms, "
                    f"but media duration is {media_duration}ms"
                )
        return self
    
    # Helper methods
    def get_block(self, block_id: str) -> ContentBlock | None:
        """Get a content block by ID."""
        for block in self.content_blocks:
            if block.block_id == block_id:
                return block
        return None
    
    def get_verified_blocks(self) -> list[ContentBlock]:
        """Get all human-verified blocks."""
        return [b for b in self.content_blocks if b.human_verified]
    
    def get_blocks_with_final_script(self) -> list[ContentBlock]:
        """Get all blocks with final script."""
        return [b for b in self.content_blocks if b.script_final is not None and b.script_final.strip()]
    
    def get_blocks_needing_audio(self) -> list[ContentBlock]:
        """Get all blocks that need audio upload."""
        return [
            b for b in self.content_blocks 
            if (b.script_final is not None and b.script_final.strip() and 
                (b.audio_delivery is None or not b.audio_delivery.approved))
        ]
    
    def has_overlap(self) -> bool:
        """Check if any narration blocks overlap."""
        narration_blocks = [
            b for b in self.content_blocks 
            if b.script_final is not None and b.script_final.strip()
        ]
        for i, block_a in enumerate(narration_blocks):
            for block_b in narration_blocks[i+1:]:
                if block_a.start_ms < block_b.end_ms and block_b.start_ms < block_a.end_ms:
                    return True
        return False
    
    def is_ready_for_render(self) -> bool:
        """Check if analysis is ready for rendering."""
        # Must have at least one block with final script
        if not self.get_blocks_with_final_script():
            return False
        
        # Must not have overlaps
        if self.has_overlap():
            return False
        
        # All blocks with final script must have approved audio
        for block in self.get_blocks_with_final_script():
            if block.audio_delivery is None or not block.audio_delivery.approved:
                return False
        
        return True


# =============================================================================
# Delivery Report
# =============================================================================

class DeliveryReport(BaseModel):
    """Report on audio delivery status for a project."""
    project_id: str = Field(..., description="Project identifier")
    analysis_id: str = Field(..., description="Analysis identifier")
    total_blocks: int = Field(..., ge=0, description="Total number of blocks")
    blocks_with_script: int = Field(..., ge=0, description="Blocks with final script")
    blocks_with_audio: int = Field(..., ge=0, description="Blocks with uploaded audio")
    blocks_approved: int = Field(..., ge=0, description="Blocks with approved audio")
    has_overlaps: bool = Field(default=False, description="Whether any blocks overlap")
    has_gaps: bool = Field(default=False, description="Whether there are gaps between blocks")
    total_duration_ms: int = Field(..., ge=0, description="Total duration of all blocks")
    warnings: list[str] = Field(default_factory=list, description="Warning messages")
    errors: list[str] = Field(default_factory=list, description="Error messages")
    ready_for_render: bool = Field(default=False, description="Whether ready for rendering")
