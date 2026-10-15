"""Content analysis for video analysis pipeline.

This module provides functions to analyze video content by combining:
- Shot boundaries
- Transcript segments
- Audio events

Into content blocks that are the primary unit for human review.
"""

from typing import List, Optional
from dataclasses import dataclass

from core.analysis_schemas import (
    ContentBlock,
    ShotAnalysis,
    TranscriptSegment,
    AudioEvent,
    AudioEventType,
)


@dataclass
class ContentAnalysisResult:
    """Result of content analysis."""
    content_blocks: List[ContentBlock]
    unassigned_shots: List[ShotAnalysis]
    unassigned_segments: List[TranscriptSegment]
    unassigned_events: List[AudioEvent]


def _find_overlapping_shots(
    start_ms: int,
    end_ms: int,
    shots: List[ShotAnalysis],
) -> List[str]:
    """Find shot IDs that overlap with a time range.
    
    Args:
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        shots: List of ShotAnalysis objects
        
    Returns:
        List of shot IDs that overlap
    """
    overlapping = []
    for shot in shots:
        if shot.start_ms < end_ms and shot.end_ms > start_ms:
            overlapping.append(shot.shot_id)
    return overlapping


def _find_overlapping_segments(
    start_ms: int,
    end_ms: int,
    segments: List[TranscriptSegment],
) -> List[str]:
    """Find segment IDs that overlap with a time range.
    
    Args:
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        segments: List of TranscriptSegment objects
        
    Returns:
        List of segment IDs that overlap
    """
    overlapping = []
    for seg in segments:
        if seg.start_ms < end_ms and seg.end_ms > start_ms:
            overlapping.append(seg.id)
    return overlapping


def _find_overlapping_events(
    start_ms: int,
    end_ms: int,
    events: List[AudioEvent],
) -> List[str]:
    """Find event IDs that overlap with a time range.
    
    Args:
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        events: List of AudioEvent objects
        
    Returns:
        List of event IDs that overlap
    """
    overlapping = []
    for event in events:
        if event.start_ms < end_ms and event.end_ms > start_ms:
            overlapping.append(event.event_id)
    return overlapping


def _merge_overlapping_ranges(
    ranges: List[tuple],
    min_gap_ms: int = 500,
) -> List[tuple]:
    """Merge overlapping or adjacent time ranges.
    
    Args:
        ranges: List of (start_ms, end_ms) tuples
        min_gap_ms: Maximum gap to merge (in milliseconds)
        
    Returns:
        List of merged (start_ms, end_ms) tuples
    """
    if not ranges:
        return []
    
    # Sort by start time
    sorted_ranges = sorted(ranges, key=lambda x: x[0])
    merged = [sorted_ranges[0]]
    
    for current_start, current_end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        
        # Check if ranges overlap or are close enough to merge
        if current_start <= last_end + min_gap_ms:
            # Merge them
            new_start = min(last_start, current_start)
            new_end = max(last_end, current_end)
            merged[-1] = (new_start, new_end)
        else:
            merged.append((current_start, current_end))
    
    return merged


def _get_speech_ranges(
    segments: List[TranscriptSegment],
    events: List[AudioEvent],
) -> List[tuple]:
    """Get time ranges where speech occurs.
    
    Args:
        segments: List of TranscriptSegment objects
        events: List of AudioEvent objects
        
    Returns:
        List of (start_ms, end_ms) tuples for speech ranges
    """
    ranges = []
    
    # Add ranges from speech segments
    for seg in segments:
        ranges.append((seg.start_ms, seg.end_ms))
    
    # Add ranges from speech events
    for event in events:
        if event.type == AudioEventType.SPEECH:
            ranges.append((event.start_ms, event.end_ms))
    
    return ranges


def create_content_blocks(
    shots: List[ShotAnalysis],
    transcript_segments: List[TranscriptSegment],
    audio_events: List[AudioEvent],
    media_duration_ms: int,
    min_block_duration_ms: int = 1000,
) -> ContentAnalysisResult:
    """Create content blocks from shots, segments, and events.
    
    A content block represents a contiguous section of the video that has
    both visual (shots) and audio (transcript) content. These blocks are
    the primary unit for human review and script editing.
    
    Args:
        shots: List of ShotAnalysis objects
        transcript_segments: List of TranscriptSegment objects
        audio_events: List of AudioEvent objects
        media_duration_ms: Total media duration in milliseconds
        min_block_duration_ms: Minimum duration for a block
        
    Returns:
        ContentAnalysisResult with created blocks and unassigned items
    """
    # Get speech ranges and merge them
    speech_ranges = _get_speech_ranges(transcript_segments, audio_events)
    merged_speech_ranges = _merge_overlapping_ranges(speech_ranges)
    
    # Create blocks from merged speech ranges
    content_blocks = []
    block_id_counter = 0
    
    for start_ms, end_ms in merged_speech_ranges:
        # Skip very short ranges
        if end_ms - start_ms < min_block_duration_ms:
            continue
        
        # Find overlapping shots, segments, and events
        shot_ids = _find_overlapping_shots(start_ms, end_ms, shots)
        segment_ids = _find_overlapping_segments(start_ms, end_ms, transcript_segments)
        event_ids = _find_overlapping_events(start_ms, end_ms, audio_events)
        
        # Get combined transcript text
        transcript_texts = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id:
                    transcript_texts.append(seg.text)
        transcript = " ".join(transcript_texts)
        
        # Get speaker IDs from segments
        speaker_ids = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id and seg.speaker_id:
                    if seg.speaker_id not in speaker_ids:
                        speaker_ids.append(seg.speaker_id)
        
        # Get combined description from shots
        descriptions = []
        for shot_id in shot_ids:
            for shot in shots:
                if shot.shot_id == shot_id and shot.description:
                    descriptions.append(shot.description)
        description = " ".join(descriptions)
        
        # Calculate confidence as average of segment confidences
        confidences = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id:
                    confidences.append(seg.confidence)
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        # Create block
        block = ContentBlock(
            block_id=f"block_{block_id_counter:04d}",
            start_ms=start_ms,
            end_ms=end_ms,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
            shot_ids=shot_ids,
            transcript_segment_ids=segment_ids,
            audio_event_ids=event_ids,
            description=description,
            transcript=transcript,
            speaker_ids=speaker_ids,
            script_draft=transcript,  # Initial script draft is the transcript
            confidence=confidence,
        )
        content_blocks.append(block)
        block_id_counter += 1
    
    # Find unassigned items
    assigned_shot_ids = set()
    for block in content_blocks:
        assigned_shot_ids.update(block.shot_ids)
    unassigned_shots = [s for s in shots if s.shot_id not in assigned_shot_ids]
    
    assigned_segment_ids = set()
    for block in content_blocks:
        assigned_segment_ids.update(block.transcript_segment_ids)
    unassigned_segments = [s for s in transcript_segments if s.id not in assigned_segment_ids]
    
    assigned_event_ids = set()
    for block in content_blocks:
        assigned_event_ids.update(block.audio_event_ids)
    unassigned_events = [e for e in audio_events if e.event_id not in assigned_event_ids]
    
    return ContentAnalysisResult(
        content_blocks=content_blocks,
        unassigned_shots=unassigned_shots,
        unassigned_segments=unassigned_segments,
        unassigned_events=unassigned_events,
    )


def create_blocks_from_scenes(
    scenes: List[dict],
    transcript_segments: List[TranscriptSegment],
    audio_events: List[AudioEvent],
) -> List[ContentBlock]:
    """Create content blocks directly from scene boundaries.
    
    This is a simpler approach that creates one block per scene,
    assigning transcript segments and audio events that fall within
    each scene's time range.
    
    Args:
        scenes: List of scene dictionaries with 'start' and 'end' in seconds
        transcript_segments: List of TranscriptSegment objects
        audio_events: List of AudioEvent objects
        
    Returns:
        List of ContentBlock objects
    """
    content_blocks = []
    
    for i, scene in enumerate(scenes):
        start_ms = int(scene["start"] * 1000)
        end_ms = int(scene["end"] * 1000)
        scene_id = scene.get("scene_id", i)
        
        # Find overlapping segments and events
        segment_ids = _find_overlapping_segments(start_ms, end_ms, transcript_segments)
        event_ids = _find_overlapping_events(start_ms, end_ms, audio_events)
        
        # Get combined transcript text
        transcript_texts = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id:
                    transcript_texts.append(seg.text)
        transcript = " ".join(transcript_texts)
        
        # Get speaker IDs from segments
        speaker_ids = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id and seg.speaker_id:
                    if seg.speaker_id not in speaker_ids:
                        speaker_ids.append(seg.speaker_id)
        
        # Calculate confidence
        confidences = []
        for seg_id in segment_ids:
            for seg in transcript_segments:
                if seg.id == seg_id:
                    confidences.append(seg.confidence)
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        
        block = ContentBlock(
            block_id=f"block_{scene_id:04d}",
            start_ms=start_ms,
            end_ms=end_ms,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
            shot_ids=[f"shot_{scene_id:04d}"],  # Will be updated with actual shot IDs
            transcript_segment_ids=segment_ids,
            audio_event_ids=event_ids,
            description="",
            transcript=transcript,
            speaker_ids=speaker_ids,
            script_draft=transcript,
            confidence=confidence,
        )
        content_blocks.append(block)
    
    return content_blocks


def blocks_to_dict_list(blocks: List[ContentBlock]) -> List[dict]:
    """Convert ContentBlock objects to dictionary list.
    
    Args:
        blocks: List of ContentBlock objects
        
    Returns:
        List of dictionaries
    """
    result = []
    for block in blocks:
        d = {
            "block_id": block.block_id,
            "start_ms": block.start_ms,
            "end_ms": block.end_ms,
            "source_start_ms": block.source_start_ms,
            "source_end_ms": block.source_end_ms,
            "shot_ids": block.shot_ids,
            "transcript_segment_ids": block.transcript_segment_ids,
            "audio_event_ids": block.audio_event_ids,
            "description": block.description,
            "transcript": block.transcript,
            "speaker_ids": block.speaker_ids,
            "script_draft": block.script_draft,
            "script_final": block.script_final,
            "confidence": block.confidence,
            "human_verified": block.human_verified,
            "review_note": block.review_note,
            "audio_delivery": None,
        }
        result.append(d)
    return result


def dict_list_to_blocks(data: List[dict]) -> List[ContentBlock]:
    """Convert dictionary list to ContentBlock objects.
    
    Args:
        data: List of block dictionaries
        
    Returns:
        List of ContentBlock objects
    """
    blocks = []
    for d in data:
        block = ContentBlock(
            block_id=d["block_id"],
            start_ms=d["start_ms"],
            end_ms=d["end_ms"],
            source_start_ms=d["source_start_ms"],
            source_end_ms=d["source_end_ms"],
            shot_ids=d.get("shot_ids", []),
            transcript_segment_ids=d.get("transcript_segment_ids", []),
            audio_event_ids=d.get("audio_event_ids", []),
            description=d.get("description", ""),
            transcript=d.get("transcript", ""),
            speaker_ids=d.get("speaker_ids", []),
            script_draft=d.get("script_draft", ""),
            script_final=d.get("script_final"),
            confidence=d.get("confidence", 0.0),
            human_verified=d.get("human_verified", False),
            review_note=d.get("review_note", ""),
            audio_delivery=None,  # Will be set separately
        )
        blocks.append(block)
    return blocks
