"""Tests for the audio_analysis module."""

import pytest
from pathlib import Path

from pipeline.audio_analysis import (
    segments_to_dict_list,
    dict_list_to_segments,
    speakers_to_dict_list,
    dict_list_to_speakers,
    events_to_dict_list,
    dict_list_to_events,
    detect_audio_events,
    detect_speaker_turns,
    _convert_whisper_segment,
)
from core.analysis_schemas import (
    TranscriptSegment,
    TranscriptWord,
    Speaker,
    AudioEvent,
    AudioEventType,
)


class TestConvertWhisperSegment:
    """Tests for Whisper segment conversion."""

    def test_convert_basic_segment(self):
        """Test converting a basic Whisper segment."""
        seg = {
            "start": 1.5,
            "end": 3.5,
            "text": "Hello world",
            "confidence": 0.95,
        }
        
        result = _convert_whisper_segment(seg, "seg_001")
        
        assert isinstance(result, TranscriptSegment)
        assert result.id == "seg_001"
        assert result.start_ms == 1500
        assert result.end_ms == 3500
        assert result.text == "Hello world"
        assert result.confidence == 0.95

    def test_convert_segment_with_words(self):
        """Test converting a segment with word-level timing."""
        seg = {
            "start": 0.0,
            "end": 2.0,
            "text": "Hello world",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 1.0, "probability": 0.98},
                {"word": "world", "start": 1.0, "end": 2.0, "probability": 0.97},
            ],
        }
        
        result = _convert_whisper_segment(seg, "seg_001")
        
        assert len(result.words) == 2
        assert result.words[0].text == "Hello"
        assert result.words[0].start_ms == 0
        assert result.words[0].end_ms == 1000
        assert result.words[1].text == "world"
        assert result.words[1].start_ms == 1000
        assert result.words[1].end_ms == 2000


class TestDetectAudioEvents:
    """Tests for audio event detection."""

    def test_detect_events_from_segments(self):
        """Test detecting audio events from transcript segments."""
        segments = [
            TranscriptSegment(
                id="seg_001",
                start_ms=0,
                end_ms=2000,
                text="Hello",
                confidence=0.95,
            ),
            TranscriptSegment(
                id="seg_002",
                start_ms=3000,
                end_ms=5000,
                text="World",
                confidence=0.90,
            ),
        ]
        
        events = detect_audio_events(segments, media_duration_ms=6000, silence_threshold=0.5)
        
        # Should have 2 speech events + 2 silence events (between and at end)
        speech_events = [e for e in events if e.type == AudioEventType.SPEECH]
        silence_events = [e for e in events if e.type == AudioEventType.SILENCE]
        
        assert len(speech_events) == 2
        assert len(silence_events) == 2
        # Silence between segments
        assert silence_events[0].start_ms == 2000
        assert silence_events[0].end_ms == 3000
        # Silence at end
        assert silence_events[1].start_ms == 5000
        assert silence_events[1].end_ms == 6000

    def test_detect_events_no_segments(self):
        """Test detecting events when there are no segments."""
        events = detect_audio_events([], media_duration_ms=5000, silence_threshold=0.5)
        
        assert len(events) == 1
        assert events[0].type == AudioEventType.SILENCE
        assert events[0].start_ms == 0
        assert events[0].end_ms == 5000


class TestDetectSpeakerTurns:
    """Tests for speaker turn detection."""

    def test_detect_speakers_from_segments(self):
        """Test detecting speakers from segments."""
        segments = [
            TranscriptSegment(id="seg_001", start_ms=0, end_ms=2000, text="Hello"),
            TranscriptSegment(id="seg_002", start_ms=2000, end_ms=4000, text="World"),
            TranscriptSegment(id="seg_003", start_ms=4000, end_ms=6000, text="Test"),
        ]
        
        speakers = detect_speaker_turns(segments)
        
        assert len(speakers) == 2  # Alternates between 2 speakers
        assert speakers[0].speaker_id == "speaker_0"
        assert speakers[1].speaker_id == "speaker_1"

    def test_detect_speakers_empty(self):
        """Test detecting speakers from empty segments."""
        speakers = detect_speaker_turns([])
        assert len(speakers) == 0


class TestSegmentsSerialization:
    """Tests for segment serialization."""

    def test_segments_to_dict_list(self):
        """Test converting segments to dictionary list."""
        segments = [
            TranscriptSegment(
                id="seg_001",
                start_ms=0,
                end_ms=1000,
                text="Hello",
                confidence=0.95,
                words=[
                    TranscriptWord(text="Hello", start_ms=0, end_ms=1000, confidence=0.95),
                ],
            ),
        ]
        
        result = segments_to_dict_list(segments)
        
        assert len(result) == 1
        assert result[0]["id"] == "seg_001"
        assert result[0]["start_ms"] == 0
        assert result[0]["end_ms"] == 1000
        assert result[0]["text"] == "Hello"
        assert len(result[0]["words"]) == 1

    def test_dict_list_to_segments(self):
        """Test converting dictionary list to segments."""
        data = [
            {
                "id": "seg_001",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "Hello",
                "confidence": 0.95,
                "words": [
                    {"text": "Hello", "start_ms": 0, "end_ms": 1000, "confidence": 0.95},
                ],
            },
        ]
        
        segments = dict_list_to_segments(data)
        
        assert len(segments) == 1
        assert isinstance(segments[0], TranscriptSegment)
        assert segments[0].id == "seg_001"
        assert segments[0].start_ms == 0
        assert len(segments[0].words) == 1


class TestSpeakersSerialization:
    """Tests for speaker serialization."""

    def test_speakers_to_dict_list(self):
        """Test converting speakers to dictionary list."""
        speakers = [
            Speaker(speaker_id="speaker_0", label="Speaker 1", confidence=0.9),
            Speaker(speaker_id="speaker_1", label="Speaker 2", confidence=0.8),
        ]
        
        result = speakers_to_dict_list(speakers)
        
        assert len(result) == 2
        assert result[0]["speaker_id"] == "speaker_0"
        assert result[0]["label"] == "Speaker 1"

    def test_dict_list_to_speakers(self):
        """Test converting dictionary list to speakers."""
        data = [
            {"speaker_id": "speaker_0", "label": "Speaker 1", "confidence": 0.9},
            {"speaker_id": "speaker_1", "label": "Speaker 2", "confidence": 0.8},
        ]
        
        speakers = dict_list_to_speakers(data)
        
        assert len(speakers) == 2
        assert isinstance(speakers[0], Speaker)
        assert speakers[0].speaker_id == "speaker_0"


class TestEventsSerialization:
    """Tests for event serialization."""

    def test_events_to_dict_list(self):
        """Test converting events to dictionary list."""
        events = [
            AudioEvent(
                event_id="ae_001",
                start_ms=0,
                end_ms=1000,
                type=AudioEventType.SPEECH,
                transcript_segment_ids=["seg_001"],
                confidence=0.95,
            ),
        ]
        
        result = events_to_dict_list(events)
        
        assert len(result) == 1
        assert result[0]["event_id"] == "ae_001"
        assert result[0]["type"] == "speech"

    def test_dict_list_to_events(self):
        """Test converting dictionary list to events."""
        data = [
            {
                "event_id": "ae_001",
                "start_ms": 0,
                "end_ms": 1000,
                "type": "speech",
                "transcript_segment_ids": ["seg_001"],
                "confidence": 0.95,
            },
        ]
        
        events = dict_list_to_events(data)
        
        assert len(events) == 1
        assert isinstance(events[0], AudioEvent)
        assert events[0].event_id == "ae_001"
        assert events[0].type == AudioEventType.SPEECH


class TestRoundTrip:
    """Tests for serialization round-trip."""

    def test_segments_round_trip(self):
        """Test that segments can be serialized and deserialized."""
        original = [
            TranscriptSegment(
                id="seg_001",
                start_ms=0,
                end_ms=1000,
                text="Hello",
                confidence=0.95,
                words=[TranscriptWord(text="Hello", start_ms=0, end_ms=1000, confidence=0.95)],
            ),
        ]
        
        data = segments_to_dict_list(original)
        restored = dict_list_to_segments(data)
        
        assert len(restored) == len(original)
        assert restored[0].id == original[0].id
        assert restored[0].start_ms == original[0].start_ms
        assert restored[0].text == original[0].text

    def test_speakers_round_trip(self):
        """Test that speakers can be serialized and deserialized."""
        original = [
            Speaker(speaker_id="speaker_0", label="Speaker 1", confidence=0.9),
        ]
        
        data = speakers_to_dict_list(original)
        restored = dict_list_to_speakers(data)
        
        assert len(restored) == len(original)
        assert restored[0].speaker_id == original[0].speaker_id

    def test_events_round_trip(self):
        """Test that events can be serialized and deserialized."""
        original = [
            AudioEvent(
                event_id="ae_001",
                start_ms=0,
                end_ms=1000,
                type=AudioEventType.SPEECH,
                confidence=0.95,
            ),
        ]
        
        data = events_to_dict_list(original)
        restored = dict_list_to_events(data)
        
        assert len(restored) == len(original)
        assert restored[0].event_id == original[0].event_id
        assert restored[0].type == original[0].type
