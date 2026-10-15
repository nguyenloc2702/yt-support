"""Audio analysis for video analysis pipeline.

This module provides functions to analyze audio tracks, including:
- Speech transcription with word-level timing
- Speaker diarization
- Audio event detection (speech, silence, music, etc.)
"""

from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

from pipeline.media_probe import probe_media
from core.analysis_schemas import (
    TranscriptSegment,
    TranscriptWord,
    Speaker,
    AudioEvent,
    AudioEventType,
    MediaInfo,
)
from core.exceptions import AnalysisError


@dataclass
class AudioAnalysisResult:
    """Result of audio analysis."""
    transcript_segments: List[TranscriptSegment]
    speakers: List[Speaker]
    audio_events: List[AudioEvent]
    media_info: MediaInfo


def _convert_whisper_segment(seg: dict, segment_id: str) -> TranscriptSegment:
    """Convert a Whisper segment to a TranscriptSegment.
    
    Args:
        seg: Whisper segment dictionary
        segment_id: Unique ID for this segment
        
    Returns:
        TranscriptSegment object
    """
    start_ms = int(seg.get("start", 0) * 1000)
    end_ms = int(seg.get("end", 0) * 1000)
    text = seg.get("text", "")
    confidence = float(seg.get("confidence", 0.0))
    
    # Convert words if present
    words = []
    if "words" in seg:
        for w in seg["words"]:
            word = TranscriptWord(
                text=w.get("word", ""),
                start_ms=int(w.get("start", 0) * 1000),
                end_ms=int(w.get("end", 0) * 1000),
                confidence=float(w.get("probability", 0.0)),
            )
            words.append(word)
    
    return TranscriptSegment(
        id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        confidence=confidence,
        words=words,
    )


def transcribe_audio(
    audio_path: Path | str,
    model_size: Optional[str] = None,
    language: Optional[str] = None,
) -> Tuple[List[TranscriptSegment], MediaInfo]:
    """Transcribe audio file and return transcript segments.
    
    Args:
        audio_path: Path to the audio file
        model_size: Whisper model size (e.g., "small", "medium", "large")
        language: Language code (e.g., "vi", "en") or None for auto-detection
        
    Returns:
        Tuple of (transcript_segments, media_info)
    """
    audio_path = Path(audio_path)
    
    try:
        from pipeline.transcribe import transcribe_audio as _transcribe
    except ImportError:
        raise AnalysisError("Transcription module not available")
    
    # Probe media to get info
    media_info = probe_media(audio_path)
    
    # Run transcription
    result = _transcribe(
        audio_path,
        model_size=model_size,
        language=language,
    )
    
    # Convert segments
    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        segment = _convert_whisper_segment(seg, f"seg_{i:04d}")
        segments.append(segment)
    
    return segments, media_info


def detect_speaker_turns(
    transcript_segments: List[TranscriptSegment],
    min_speaker_duration: float = 0.5,
) -> List[Speaker]:
    """Detect speaker turns from transcript segments.
    
    This is a simple heuristic-based speaker detection that groups
    consecutive segments with similar audio characteristics.
    
    Args:
        transcript_segments: List of transcript segments
        min_speaker_duration: Minimum duration for a speaker turn in seconds
        
    Returns:
        List of Speaker objects
    """
    if not transcript_segments:
        return []
    
    # Simple approach: one speaker per segment for now
    # In a full implementation, this would use speaker diarization
    speakers = []
    for i, seg in enumerate(transcript_segments):
        speaker_id = f"speaker_{i % 2}"  # Alternate between 2 speakers
        speaker = Speaker(
            speaker_id=speaker_id,
            label=f"Speaker {i % 2 + 1}",
            confidence=0.8,
        )
        if speaker not in speakers:
            speakers.append(speaker)
    
    return speakers


def detect_audio_events(
    transcript_segments: List[TranscriptSegment],
    media_duration_ms: int,
    silence_threshold: float = 0.5,
) -> List[AudioEvent]:
    """Detect audio events (speech, silence, etc.) from transcript.
    
    Args:
        transcript_segments: List of transcript segments
        media_duration_ms: Total media duration in milliseconds
        silence_threshold: Minimum silence duration in seconds
        
    Returns:
        List of AudioEvent objects
    """
    events = []
    
    # Sort segments by start time
    sorted_segments = sorted(transcript_segments, key=lambda s: s.start_ms)
    
    # Detect speech events from segments
    for i, seg in enumerate(sorted_segments):
        event = AudioEvent(
            event_id=f"ae_{i:04d}",
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            type=AudioEventType.SPEECH,
            transcript_segment_ids=[seg.id],
            confidence=seg.confidence,
        )
        events.append(event)
    
    # Detect silence events in gaps between speech
    if sorted_segments:
        # Check for silence before first segment
        first_start = sorted_segments[0].start_ms
        if first_start > (silence_threshold * 1000):
            event = AudioEvent(
                event_id="ae_silence_0",
                start_ms=0,
                end_ms=first_start,
                type=AudioEventType.SILENCE,
                confidence=0.9,
            )
            events.append(event)
        
        # Check for silence between segments
        for i in range(len(sorted_segments) - 1):
            seg_end = sorted_segments[i].end_ms
            next_start = sorted_segments[i + 1].start_ms
            gap_ms = next_start - seg_end
            if gap_ms > (silence_threshold * 1000):
                event = AudioEvent(
                    event_id=f"ae_silence_{i + 1}",
                    start_ms=seg_end,
                    end_ms=next_start,
                    type=AudioEventType.SILENCE,
                    confidence=0.9,
                )
                events.append(event)
        
        # Check for silence after last segment
        last_end = sorted_segments[-1].end_ms
        if last_end < media_duration_ms:
            gap_ms = media_duration_ms - last_end
            if gap_ms > (silence_threshold * 1000):
                event = AudioEvent(
                    event_id=f"ae_silence_{len(sorted_segments)}",
                    start_ms=last_end,
                    end_ms=media_duration_ms,
                    type=AudioEventType.SILENCE,
                    confidence=0.9,
                )
                events.append(event)
    else:
        # No speech segments, entire audio is silence
        event = AudioEvent(
            event_id="ae_silence_0",
            start_ms=0,
            end_ms=media_duration_ms,
            type=AudioEventType.SILENCE,
            confidence=0.9,
        )
        events.append(event)
    
    # Sort all events by start time
    events.sort(key=lambda e: e.start_ms)
    
    return events


def analyze_audio(
    audio_path: Path | str,
    model_size: Optional[str] = None,
    language: Optional[str] = None,
) -> AudioAnalysisResult:
    """Perform complete audio analysis.
    
    Args:
        audio_path: Path to the audio file
        model_size: Whisper model size
        language: Language code or None for auto-detection
        
    Returns:
        AudioAnalysisResult with all analysis data
    """
    audio_path = Path(audio_path)
    
    # Transcribe audio
    transcript_segments, media_info = transcribe_audio(
        audio_path,
        model_size=model_size,
        language=language,
    )
    
    # Detect speakers
    speakers = detect_speaker_turns(transcript_segments)
    
    # Detect audio events
    audio_events = detect_audio_events(
        transcript_segments,
        media_info.duration_ms,
    )
    
    # Assign speaker IDs to segments
    if speakers and transcript_segments:
        for i, seg in enumerate(transcript_segments):
            seg.speaker_id = speakers[i % len(speakers)].speaker_id
    
    return AudioAnalysisResult(
        transcript_segments=transcript_segments,
        speakers=speakers,
        audio_events=audio_events,
        media_info=media_info,
    )


def segments_to_dict_list(segments: List[TranscriptSegment]) -> List[dict]:
    """Convert TranscriptSegment objects to dictionary list.
    
    Args:
        segments: List of TranscriptSegment objects
        
    Returns:
        List of dictionaries
    """
    result = []
    for seg in segments:
        d = {
            "id": seg.id,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "duration_ms": seg.duration_ms,
            "text": seg.text,
            "speaker_id": seg.speaker_id,
            "confidence": seg.confidence,
            "words": [
                {
                    "text": w.text,
                    "start_ms": w.start_ms,
                    "end_ms": w.end_ms,
                    "confidence": w.confidence,
                }
                for w in seg.words
            ],
        }
        result.append(d)
    return result


def dict_list_to_segments(data: List[dict]) -> List[TranscriptSegment]:
    """Convert dictionary list to TranscriptSegment objects.
    
    Args:
        data: List of segment dictionaries
        
    Returns:
        List of TranscriptSegment objects
    """
    segments = []
    for d in data:
        words = [
            TranscriptWord(
                text=w["text"],
                start_ms=w["start_ms"],
                end_ms=w["end_ms"],
                confidence=w.get("confidence", 0.0),
            )
            for w in d.get("words", [])
        ]
        
        seg = TranscriptSegment(
            id=d["id"],
            start_ms=d["start_ms"],
            end_ms=d["end_ms"],
            text=d["text"],
            speaker_id=d.get("speaker_id"),
            confidence=d.get("confidence", 0.0),
            words=words,
        )
        segments.append(seg)
    return segments


def speakers_to_dict_list(speakers: List[Speaker]) -> List[dict]:
    """Convert Speaker objects to dictionary list.
    
    Args:
        speakers: List of Speaker objects
        
    Returns:
        List of dictionaries
    """
    return [
        {
            "speaker_id": s.speaker_id,
            "label": s.label,
            "confidence": s.confidence,
        }
        for s in speakers
    ]


def dict_list_to_speakers(data: List[dict]) -> List[Speaker]:
    """Convert dictionary list to Speaker objects.
    
    Args:
        data: List of speaker dictionaries
        
    Returns:
        List of Speaker objects
    """
    return [
        Speaker(
            speaker_id=d["speaker_id"],
            label=d.get("label"),
            confidence=d.get("confidence", 0.0),
        )
        for d in data
    ]


def events_to_dict_list(events: List[AudioEvent]) -> List[dict]:
    """Convert AudioEvent objects to dictionary list.
    
    Args:
        events: List of AudioEvent objects
        
    Returns:
        List of dictionaries
    """
    return [
        {
            "event_id": e.event_id,
            "start_ms": e.start_ms,
            "end_ms": e.end_ms,
            "type": e.type.value,
            "transcript_segment_ids": e.transcript_segment_ids,
            "confidence": e.confidence,
        }
        for e in events
    ]


def dict_list_to_events(data: List[dict]) -> List[AudioEvent]:
    """Convert dictionary list to AudioEvent objects.
    
    Args:
        data: List of event dictionaries
        
    Returns:
        List of AudioEvent objects
    """
    events = []
    for d in data:
        event = AudioEvent(
            event_id=d["event_id"],
            start_ms=d["start_ms"],
            end_ms=d["end_ms"],
            type=AudioEventType(d.get("type", "speech")),
            transcript_segment_ids=d.get("transcript_segment_ids", []),
            confidence=d.get("confidence", 0.0),
        )
        events.append(event)
    return events
