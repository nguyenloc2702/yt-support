"""
BGM (Background Music) Manager for Video Production Pipeline

Provides multi-track BGM management with dynamic mood shifts to support
the UNIVERSAL_VIDEO_ALGORITHM audio requirements.

This addresses Bottleneck #3 from the UNIVERSAL_VIDEO_ALGORITHM deployment.

Features:
- Multi-track BGM support with per-beat mood configuration
- Dynamic mood shifts synchronized with video beats
- Volume ducking under voiceover (-18 to -22 dB)
- BGM track mixing and transitions

Usage:
    from pipeline.bgm_manager import BGMManager
    
    manager = BGMManager()
    manager.add_track("hook", "mysterious_pizzicato.mp3", 0, 30, mood="mysterious")
    manager.add_track("setup", "nostalgic_acoustic.mp3", 30, 120, mood="nostalgic")
    
    # Generate FFmpeg mix commands
    mix_commands = manager.generate_mix_commands(voice_track="voice.aac")
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.exceptions import AnalysisError

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Default BGM directory
DEFAULT_BGM_DIR = Path("assets/music/bgm")

# Default volume levels (dB)
DEFAULT_BGM_VOLUME = -20.0  # -18 to -22 dB under voice
VOICE_VOLUME = 0.0  # Full volume

# Mood to BGM file mapping (default assets)
MOOD_TO_FILE = {
    "mysterious": "mysterious_pizzicato.mp3",
    "mysterious_pizzicato": "mysterious_pizzicato.mp3",
    "nostalgic": "nostalgic_acoustic.mp3",
    "nostalgic_acoustic": "nostalgic_acoustic.mp3",
    "energetic": "energetic_bassline.mp3",
    "energetic_bassline": "energetic_bassline.mp3",
    "ominous": "ominous_cello.mp3",
    "ominous_cello": "ominous_cello.mp3",
    "triumphant": "triumphant_synth.mp3",
    "triumphant_synth": "triumphant_synth.mp3",
    "celebratory": "celebratory_groove.mp3",
    "celebratory_groove": "celebratory_groove.mp3",
    "reflective": "reflective_guitar.mp3",
    "reflective_guitar": "reflective_guitar.mp3",
    "ticking_clock": "ticking_clock.mp3",
    "playful": "playful_sneaky.mp3",
    "playful_sneaky": "playful_sneaky.mp3",
}

# Beat role to mood mapping (from UNIVERSAL_VIDEO_ALGORITHM)
BEAT_ROLE_TO_MOOD = {
    "hook": "mysterious_pizzicato",
    "setup": "nostalgic_acoustic",
    "rising": "energetic_bassline",
    "crisis": "ominous_cello",
    "breakthrough": "triumphant_synth",
    "payoff": "celebratory_groove",
    "moral": "reflective_guitar",
}


# =============================================================================
# ENUMS
# =============================================================================

class BGMFadeType(Enum):
    """Type of fade for BGM transitions."""
    NONE = "none"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"
    CROSSFADE = "crossfade"


class BGMLoopMode(Enum):
    """Loop mode for BGM tracks."""
    NONE = "none"
    LOOP = "loop"
    STREAM_LOOP = "stream_loop"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BGMTrack:
    """
    A single BGM track with timing and mood information.
    
    Attributes:
        id: Unique identifier for the track
        path: Path to the BGM audio file
        mood: Mood descriptor (e.g., "mysterious", "energetic")
        start_time: Start time in seconds
        end_time: End time in seconds
        volume_db: Volume in dB (negative = quieter)
        fade_in: Fade in duration in seconds
        fade_out: Fade out duration in seconds
        loop: Whether to loop this track
        beat_role: The beat role this track serves
    """
    id: str
    path: Path
    mood: str
    start_time: float
    end_time: float
    volume_db: float = DEFAULT_BGM_VOLUME
    fade_in: float = 0.5
    fade_out: float = 0.5
    loop: bool = False
    beat_role: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "path": str(self.path),
            "mood": self.mood,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "volume_db": self.volume_db,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "loop": self.loop,
            "beat_role": self.beat_role
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BGMTrack":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            path=Path(data["path"]),
            mood=data["mood"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            volume_db=data.get("volume_db", DEFAULT_BGM_VOLUME),
            fade_in=data.get("fade_in", 0.5),
            fade_out=data.get("fade_out", 0.5),
            loop=data.get("loop", False),
            beat_role=data.get("beat_role", "")
        )


@dataclass
class BGMTransition:
    """
    A transition between two BGM tracks.
    
    Attributes:
        from_track_id: ID of the track transitioning from
        to_track_id: ID of the track transitioning to
        transition_time: Time in seconds when transition occurs
        transition_type: Type of transition (fade, crossfade, etc.)
        duration: Duration of the transition in seconds
    """
    from_track_id: str
    to_track_id: str
    transition_time: float
    transition_type: BGMFadeType = BGMFadeType.CROSSFADE
    duration: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_track_id": self.from_track_id,
            "to_track_id": self.to_track_id,
            "transition_time": self.transition_time,
            "transition_type": self.transition_type.value,
            "duration": self.duration
        }


@dataclass
class BGMMoodShift:
    """
    A mood shift event in the BGM timeline.
    
    Attributes:
        time: Time in seconds when mood shift occurs
        from_mood: Mood before the shift
        to_mood: Mood after the shift
        intensity: Intensity of the shift (0-1)
        swells: Volume swells during the shift
    """
    time: float
    from_mood: str
    to_mood: str
    intensity: float = 0.5
    swells_db: float = 6.0  # +6 dB during transitions
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": self.time,
            "from_mood": self.from_mood,
            "to_mood": self.to_mood,
            "intensity": self.intensity,
            "swells_db": self.swells_db
        }


@dataclass
class BGMMixConfig:
    """
    Configuration for mixing BGM tracks.
    
    Attributes:
        output_path: Path to the output mixed audio file
        sample_rate: Audio sample rate in Hz
        bitrate: Audio bitrate in kbps
        voice_ducking_db: How much to duck BGM under voice
    """
    output_path: Path
    sample_rate: int = 44100
    bitrate: str = "192k"
    voice_ducking_db: float = DEFAULT_BGM_VOLUME


# =============================================================================
# BGM MANAGER
# =============================================================================

class BGMManagerError(AnalysisError):
    """Raised when BGM management fails."""
    pass


class BGMManager:
    """
    Manages multi-track BGM with dynamic mood shifts for video production.
    
    This manager handles:
    - Adding and managing multiple BGM tracks
    - Mood shifts synchronized with video beats
    - Volume ducking under voiceover
    - FFmpeg command generation for mixing
    """
    
    def __init__(self, bgm_dir: Optional[Path] = None):
        """
        Initialize the BGM manager.
        
        Args:
            bgm_dir: Directory containing BGM audio files.
                    Defaults to DEFAULT_BGM_DIR.
        """
        self.bgm_dir = Path(bgm_dir) if bgm_dir else DEFAULT_BGM_DIR
        self.bgm_dir.mkdir(parents=True, exist_ok=True)
        
        # Track registry
        self.tracks: Dict[str, BGMTrack] = {}
        
        # Transition registry
        self.transitions: List[BGMTransition] = []
        
        # Mood shift registry
        self.mood_shifts: List[BGMMoodShift] = []
        
        logger.info(f"BGMManager initialized at {self.bgm_dir}")
    
    def add_track(
        self,
        track_id: str,
        path: Optional[Path] = None,
        mood: str = "",
        start_time: float = 0.0,
        end_time: float = 0.0,
        volume_db: float = DEFAULT_BGM_VOLUME,
        fade_in: float = 0.5,
        fade_out: float = 0.5,
        loop: bool = False,
        beat_role: str = ""
    ) -> str:
        """
        Add a BGM track to the manager.
        
        Args:
            track_id: Unique identifier for the track
            path: Path to the BGM audio file. If None, will try to find
                  a file matching the mood in bgm_dir.
            mood: Mood descriptor for the track
            start_time: Start time in seconds
            end_time: End time in seconds
            volume_db: Volume in dB
            fade_in: Fade in duration in seconds
            fade_out: Fade out duration in seconds
            loop: Whether to loop this track
            beat_role: The beat role this track serves
        
        Returns:
            The track ID
        """
        # If path is not provided, try to find a matching file
        if path is None:
            path = self._find_bgm_file(mood)
            if path is None:
                # Use a default file
                path = self.bgm_dir / MOOD_TO_FILE.get(mood, "default.mp3")
        else:
            path = Path(path)
            if not path.is_absolute() and not str(path).startswith(str(self.bgm_dir)):
                path = self.bgm_dir / path
        
        path = path.resolve()
        
        track = BGMTrack(
            id=track_id,
            path=path,
            mood=mood,
            start_time=start_time,
            end_time=end_time,
            volume_db=volume_db,
            fade_in=fade_in,
            fade_out=fade_out,
            loop=loop,
            beat_role=beat_role
        )
        
        self.tracks[track_id] = track
        logger.info(f"Added BGM track {track_id}: {mood} ({start_time}s-{end_time}s)")
        
        return track_id
    
    def _find_bgm_file(self, mood: str) -> Optional[Path]:
        """Find a BGM file matching the mood."""
        # Try exact match
        for ext in ["*.mp3", "*.wav", "*.aac", "*.ogg"]:
            for file_path in self.bgm_dir.glob(ext):
                if mood.lower() in file_path.stem.lower():
                    return file_path
        
        # Try mood mapping
        if mood in MOOD_TO_FILE:
            target_file = MOOD_TO_FILE[mood]
            for ext in [".mp3", ".wav", ".aac", ".ogg"]:
                file_path = self.bgm_dir / target_file
                if file_path.exists():
                    return file_path
        
        return None
    
    def get_track(self, track_id: str) -> Optional[BGMTrack]:
        """Get a track by ID."""
        return self.tracks.get(track_id)
    
    def remove_track(self, track_id: str) -> bool:
        """Remove a track by ID."""
        if track_id in self.tracks:
            del self.tracks[track_id]
            logger.info(f"Removed BGM track {track_id}")
            return True
        return False
    
    def add_mood_shift(
        self,
        time: float,
        from_mood: str,
        to_mood: str,
        intensity: float = 0.5,
        swells_db: float = 6.0
    ):
        """
        Add a mood shift event.
        
        Args:
            time: Time in seconds when mood shift occurs
            from_mood: Mood before the shift
            to_mood: Mood after the shift
            intensity: Intensity of the shift (0-1)
            swells_db: Volume swells during the shift
        """
        shift = BGMMoodShift(
            time=time,
            from_mood=from_mood,
            to_mood=to_mood,
            intensity=intensity,
            swells_db=swells_db
        )
        self.mood_shifts.append(shift)
        logger.info(f"Added mood shift at {time}s: {from_mood} -> {to_mood}")
    
    def add_transition(
        self,
        from_track_id: str,
        to_track_id: str,
        transition_time: float,
        transition_type: BGMFadeType = BGMFadeType.CROSSFADE,
        duration: float = 1.0
    ):
        """
        Add a transition between two tracks.
        
        Args:
            from_track_id: ID of the track transitioning from
            to_track_id: ID of the track transitioning to
            transition_time: Time in seconds when transition occurs
            transition_type: Type of transition
            duration: Duration of the transition in seconds
        """
        transition = BGMTransition(
            from_track_id=from_track_id,
            to_track_id=to_track_id,
            transition_time=transition_time,
            transition_type=transition_type,
            duration=duration
        )
        self.transitions.append(transition)
        logger.info(f"Added transition at {transition_time}s: {from_track_id} -> {to_track_id}")
    
    def get_active_tracks_at_time(self, time: float) -> List[BGMTrack]:
        """Get all tracks that are active at a specific time."""
        return [
            track for track in self.tracks.values()
            if track.start_time <= time <= track.end_time
        ]
    
    def get_mood_at_time(self, time: float) -> str:
        """Get the mood at a specific time based on mood shifts."""
        if not self.mood_shifts:
            # Return the mood of the active track
            active_tracks = self.get_active_tracks_at_time(time)
            if active_tracks:
                return active_tracks[0].mood
            return ""
        
        # Find the most recent mood shift before this time
        for shift in sorted(self.mood_shifts, key=lambda s: s.time, reverse=True):
            if shift.time <= time:
                return shift.to_mood
        
        # No shifts before this time, return first track's mood
        if self.tracks:
            return next(iter(self.tracks.values())).mood
        return ""
    
    def generate_mix_commands(
        self,
        voice_track: Optional[Path] = None,
        output_path: Optional[Path] = None,
        config: Optional[BGMMixConfig] = None
    ) -> List[List[str]]:
        """
        Generate FFmpeg commands to mix BGM tracks with voiceover.
        
        Args:
            voice_track: Path to the voiceover audio file
            output_path: Path to the output mixed audio file
            config: Mix configuration
        
        Returns:
            List of FFmpeg command lists (each as list of strings)
        """
        if not self.tracks:
            logger.warning("No BGM tracks to mix")
            return []
        
        # Sort tracks by start time
        sorted_tracks = sorted(self.tracks.values(), key=lambda t: t.start_time)
        
        # Default config
        if config is None:
            config = BGMMixConfig(
                output_path=output_path or Path("output_mixed_audio.aac")
            )
        
        # Build FFmpeg complex filter for mixing
        commands = []
        
        # Create input arguments
        inputs = []
        if voice_track:
            inputs.append("-i")
            inputs.append(str(voice_track))
        
        # Add BGM inputs
        bgm_indices = {}
        for i, track in enumerate(sorted_tracks):
            bgm_index = len(inputs) // 2
            bgm_indices[track.id] = bgm_index
            inputs.append("-stream_loop")
            inputs.append("-1" if track.loop else "0")
            inputs.append("-i")
            inputs.append(str(track.path))
        
        # Build filter complex
        filter_parts = []
        
        # Voice track processing (if provided)
        if voice_track:
            voice_label = "[voice]"
            filter_parts.append(f"[0:a]volume={VOICE_VOLUME}dB{voice_label}")
        
        # BGM track processing
        bgm_labels = []
        for i, track in enumerate(sorted_tracks):
            bgm_index = i + 1  # FFmpeg indices start at 0 for first input
            if voice_track:
                bgm_index += 1  # Account for voice track
            
            bgm_label = f"[bgm{i}]"
            
            # Volume adjustment
            volume_filter = f"volume={track.volume_db}dB"
            
            # Fade in/out
            fade_filter = ""
            if track.fade_in > 0:
                fade_filter += f"fade=t=in:st=0:d={track.fade_in}"
            if track.fade_out > 0 and track.end_time > track.start_time:
                duration = track.end_time - track.start_time
                if fade_filter:
                    fade_filter += ","
                fade_filter += f"fade=t=out:st={duration - track.fade_out}:d={track.fade_out}"
            
            # Trim to duration
            trim_filter = f"atrim=start={track.start_time}:end={track.end_time}"
            
            # Combine filters
            filters = [volume_filter]
            if fade_filter:
                filters.append(fade_filter)
            filters.append(trim_filter)
            
            filter_str = ",".join(filters)
            filter_parts.append(f"[{bgm_index}:a]{filter_str}{bgm_label}")
            bgm_labels.append(bgm_label)
        
        # Mix all tracks
        if voice_track and bgm_labels:
            all_labels = ["[voice]"] + bgm_labels
            mix_filter = f"{''.join(all_labels)}amix=inputs={len(all_labels)}:duration=longest[aout]"
            filter_parts.append(mix_filter)
        elif bgm_labels:
            mix_filter = f"{''.join(bgm_labels)}amix=inputs={len(bgm_labels)}:duration=longest[aout]"
            filter_parts.append(mix_filter)
        
        # Build the command
        if filter_parts:
            cmd = [
                "ffmpeg",
                "-y",  # Overwrite output
            ] + inputs + [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[aout]",
                "-c:a",
                "aac",
                "-b:a",
                config.bitrate,
                "-ar",
                str(config.sample_rate),
                str(config.output_path)
            ]
            commands.append(cmd)
        
        return commands
    
    def mix_audio(
        self,
        voice_track: Optional[Path] = None,
        output_path: Optional[Path] = None,
        config: Optional[BGMMixConfig] = None
    ) -> Path:
        """
        Mix BGM tracks with voiceover using FFmpeg.
        
        Args:
            voice_track: Path to the voiceover audio file
            output_path: Path to the output mixed audio file
            config: Mix configuration
        
        Returns:
            Path to the output mixed audio file
        
        Raises:
            BGMManagerError: If mixing fails
        """
        if not self.tracks:
            raise BGMManagerError("No BGM tracks to mix")
        
        if config is None:
            config = BGMMixConfig(
                output_path=output_path or Path("output_mixed_audio.aac")
            )
        
        commands = self.generate_mix_commands(voice_track, config.output_path, config)
        
        for cmd in commands:
            logger.info(f"Running: {' '.join(cmd)}")
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if result.returncode != 0:
                    logger.error(f"FFmpeg error: {result.stderr}")
                    raise BGMManagerError(f"FFmpeg mixing failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                raise BGMManagerError("FFmpeg mixing timed out")
        
        if config.output_path.exists():
            logger.info(f"Mixed audio saved to: {config.output_path}")
            return config.output_path
        
        raise BGMManagerError("Output file not created")
    
    def create_from_beat_plan(
        self,
        beat_plan: List[Dict[str, Any]],
        total_duration: float
    ) -> List[str]:
        """
        Create BGM tracks from a beat plan.
        
        Args:
            beat_plan: List of beat dictionaries with start_time, end_time, and beat_role
            total_duration: Total video duration in seconds
        
        Returns:
            List of created track IDs
        """
        track_ids = []
        
        for beat in beat_plan:
            beat_role = beat.get("beat_role", "")
            mood = BEAT_ROLE_TO_MOOD.get(beat_role, "")
            
            track_id = f"beat_{beat_role}_{len(track_ids)}"
            
            self.add_track(
                track_id=track_id,
                mood=mood,
                start_time=beat.get("start_time", 0.0),
                end_time=beat.get("end_time", total_duration),
                beat_role=beat_role
            )
            
            track_ids.append(track_id)
        
        return track_ids
    
    def create_from_algorithm_result(
        self,
        sentences: List[Dict[str, Any]],
        total_duration: float
    ) -> List[str]:
        """
        Create BGM tracks from algorithm result sentences.
        
        Args:
            sentences: List of sentence dictionaries with beat_role
            total_duration: Total video duration in seconds
        
        Returns:
            List of created track IDs
        """
        # Group sentences by beat role
        beat_groups = {}
        for sentence in sentences:
            beat_role = sentence.get("beat_role", "setup")
            if beat_role not in beat_groups:
                beat_groups[beat_role] = []
            beat_groups[beat_role].append(sentence)
        
        # Create tracks for each beat role
        track_ids = []
        current_time = 0.0
        
        for beat_role in BEAT_ROLE_TO_MOOD.keys():
            if beat_role in beat_groups:
                group_sentences = beat_groups[beat_role]
                start_time = group_sentences[0].get("start", current_time)
                end_time = group_sentences[-1].get("end", current_time + 60)
                
                mood = BEAT_ROLE_TO_MOOD[beat_role]
                track_id = f"beat_{beat_role}"
                
                self.add_track(
                    track_id=track_id,
                    mood=mood,
                    start_time=start_time,
                    end_time=end_time,
                    beat_role=beat_role
                )
                
                track_ids.append(track_id)
                current_time = end_time
        
        return track_ids
    
    def get_mood_shift_schedule(self) -> List[Dict[str, Any]]:
        """
        Get the mood shift schedule for external use.
        
        Returns:
            List of mood shift dictionaries with time, mood, and volume info
        """
        schedule = []
        
        # Sort tracks by start time
        sorted_tracks = sorted(self.tracks.values(), key=lambda t: t.start_time)
        
        for track in sorted_tracks:
            schedule.append({
                "start_time": track.start_time,
                "end_time": track.end_time,
                "mood": track.mood,
                "volume_db": track.volume_db,
                "beat_role": track.beat_role
            })
        
        return schedule
    
    def export_config(self, output_path: Path) -> Path:
        """
        Export BGM configuration to a JSON file.
        
        Args:
            output_path: Path to save the configuration
        
        Returns:
            Path to the saved configuration file
        """
        config = {
            "tracks": {tid: track.to_dict() for tid, track in self.tracks.items()},
            "transitions": [t.to_dict() for t in self.transitions],
            "mood_shifts": [s.to_dict() for s in self.mood_shifts]
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported BGM config to: {output_path}")
        return output_path
    
    def import_config(self, input_path: Path) -> int:
        """
        Import BGM configuration from a JSON file.
        
        Args:
            input_path: Path to the configuration file
        
        Returns:
            Number of tracks imported
        """
        with open(input_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        self.tracks.clear()
        self.transitions.clear()
        self.mood_shifts.clear()
        
        for tid, track_data in config.get("tracks", {}).items():
            track = BGMTrack.from_dict(track_data)
            self.tracks[tid] = track
        
        for trans_data in config.get("transitions", []):
            trans = BGMTransition(
                from_track_id=trans_data["from_track_id"],
                to_track_id=trans_data["to_track_id"],
                transition_time=trans_data["transition_time"],
                transition_type=BGMFadeType(trans_data["transition_type"]),
                duration=trans_data["duration"]
            )
            self.transitions.append(trans)
        
        for shift_data in config.get("mood_shifts", []):
            shift = BGMMoodShift(
                time=shift_data["time"],
                from_mood=shift_data["from_mood"],
                to_mood=shift_data["to_mood"],
                intensity=shift_data.get("intensity", 0.5),
                swells_db=shift_data.get("swells_db", 6.0)
            )
            self.mood_shifts.append(shift)
        
        logger.info(f"Imported {len(self.tracks)} BGM tracks from: {input_path}")
        return len(self.tracks)
    
    def clear(self):
        """Clear all tracks, transitions, and mood shifts."""
        self.tracks.clear()
        self.transitions.clear()
        self.mood_shifts.clear()
        logger.info("Cleared all BGM data")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def create_bgm_from_audio_analysis(
    audio_analysis: Dict[str, Any],
    bgm_dir: Optional[Path] = None
) -> BGMManager:
    """
    Create a BGMManager from audio analysis data.
    
    Args:
        audio_analysis: Audio analysis dictionary with dynamic_mood_shifts_by_beat
        bgm_dir: Directory for BGM files
    
    Returns:
        Configured BGMManager
    """
    manager = BGMManager(bgm_dir)
    
    # Extract mood shifts from analysis
    mood_shifts = audio_analysis.get("bgm_spec", {}).get("dynamic_mood_shifts_by_beat", [])
    
    for shift in mood_shifts:
        beat = shift.get("beat", "")
        mood = shift.get("track_mood", "")
        
        # Parse beat timing from beat string (e.g., "1. Hook (0 - 32s)")
        import re
        time_match = re.search(r"(\d+)\s*-\s*(\d+)s", beat)
        if time_match:
            start_time = float(time_match.group(1))
            end_time = float(time_match.group(2))
            
            # Extract beat role from beat string
            beat_role = beat.split(".")[1].split(" (")[0].strip().lower()
            
            manager.add_track(
                track_id=f"beat_{beat_role}",
                mood=mood,
                start_time=start_time,
                end_time=end_time,
                beat_role=beat_role
            )
    
    return manager


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """Command-line interface for BGM management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="BGM Manager for Video Production")
    parser.add_argument("--list", action="store_true", help="List all BGM tracks")
    parser.add_argument("--add", type=str, nargs=4, metavar=("ID", "MOOD", "START", "END"),
                        help="Add a BGM track (ID, mood, start_time, end_time)")
    parser.add_argument("--remove", type=str, help="Remove a BGM track (ID)")
    parser.add_argument("--export", type=str, help="Export config to file")
    parser.add_argument("--import-config", type=str, dest="import_config",
                        help="Import config from file")
    parser.add_argument("--bgm-dir", type=str, default=None, help="BGM directory")
    
    args = parser.parse_args()
    
    manager = BGMManager(bgm_dir=args.bgm_dir)
    
    if args.list:
        print("BGM Tracks:")
        for tid, track in manager.tracks.items():
            print(f"  {tid}: {track.mood} ({track.start_time}s-{track.end_time}s)")
    
    elif args.add:
        track_id, mood, start_time, end_time = args.add
        manager.add_track(
            track_id=track_id,
            mood=mood,
            start_time=float(start_time),
            end_time=float(end_time)
        )
        print(f"Added track {track_id}")
    
    elif args.remove:
        success = manager.remove_track(args.remove)
        print(f"Removed: {success}")
    
    elif args.export:
        output_path = Path(args.export)
        manager.export_config(output_path)
        print(f"Exported to: {output_path}")
    
    elif args.import_config:
        input_path = Path(args.import_config)
        count = manager.import_config(input_path)
        print(f"Imported {count} tracks")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
