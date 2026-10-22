"""
SFX (Sound Effects) Manager for Video Production Pipeline

Provides SFX event management with automatic triggering based on video content
to support the UNIVERSAL_VIDEO_ALGORITHM audio requirements.

This addresses Bottleneck #3 from the UNIVERSAL_VIDEO_ALGORITHM deployment.

Features:
- SFX event triggering based on content keywords
- Automatic SFX mapping to video events
- SFX density management (8-10 per minute)
- FFmpeg-based SFX mixing

Usage:
    from pipeline.sfx_manager import SFXManager
    
    manager = SFXManager()
    manager.add_event("money", 120.5)  # Add SFX at 120.5 seconds
    manager.add_event("explosion", 240.0)
    
    # Generate FFmpeg mix commands
    mix_commands = manager.generate_mix_commands(voice_track="voice.aac", bgm_track="bgm.aac")
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

# Default SFX directory
DEFAULT_SFX_DIR = Path("assets/music/sfx")

# Target SFX density (from UNIVERSAL_VIDEO_ALGORITHM)
TARGET_SFX_DENSITY = 8.5  # events per minute
MIN_SFX_DENSITY = 6.0
MAX_SFX_DENSITY = 12.0

# Default SFX volume
DEFAULT_SFX_VOLUME_DB = -15.0

# SFX type to file mapping
SFX_TYPE_TO_FILE = {
    "ka_ching": "ka_ching.mp3",
    "muffled_boom": "muffled_boom.mp3",
    "cartoon_whoosh": "cartoon_whoosh.mp3",
    "crayon_scribble": "crayon_scribble.mp3",
    "subtle_pop_plop": "subtle_pop_plop.mp3",
    "dramatic_silence": "silence_0.5s.mp3",
    "ticking_clock": "ticking_clock.mp3",
    "triumphant_chime": "triumphant_chime.mp3",
    "rocket_launch": "rocket_launch.mp3",
    "explosion": "explosion.mp3",
    "money": "ka_ching.mp3",
    "transition": "cartoon_whoosh.mp3",
    "text_appear": "crayon_scribble.mp3",
    "icon_pop": "subtle_pop_plop.mp3",
    "success": "triumphant_chime.mp3",
    "crisis": "ticking_clock.mp3",
}

# Content keyword to SFX type mapping
CONTENT_TRIGGERS = {
    # Money/finance
    "money": "ka_ching",
    "dollar": "ka_ching",
    "billion": "ka_ching",
    "million": "ka_ching",
    "rich": "ka_ching",
    "wealth": "ka_ching",
    "profit": "ka_ching",
    "revenue": "ka_ching",
    "valuation": "ka_ching",
    "invest": "ka_ching",
    "stock": "ka_ching",
    
    # Explosions/rocket
    "rocket": "rocket_launch",
    "launch": "rocket_launch",
    "explode": "muffled_boom",
    "explosion": "muffled_boom",
    "blast": "muffled_boom",
    "boom": "muffled_boom",
    "fail": "muffled_boom",
    "crisis": "ticking_clock",
    
    # Transitions
    "transition": "cartoon_whoosh",
    "change": "cartoon_whoosh",
    "switch": "cartoon_whoosh",
    "move": "cartoon_whoosh",
    "pan": "cartoon_whoosh",
    "slide": "cartoon_whoosh",
    
    # Text/visuals
    "text": "crayon_scribble",
    "write": "crayon_scribble",
    "draw": "crayon_scribble",
    "chart": "crayon_scribble",
    "graph": "crayon_scribble",
    "diagram": "crayon_scribble",
    "hand-drawn": "crayon_scribble",
    
    # Icons/pop-ups
    "icon": "subtle_pop_plop",
    "pop": "subtle_pop_plop",
    "appear": "subtle_pop_plop",
    "head": "subtle_pop_plop",
    "caricature": "subtle_pop_plop",
    "avatar": "subtle_pop_plop",
    
    # Dramatic moments
    "dramatic": "dramatic_silence",
    "punchline": "dramatic_silence",
    "twist": "dramatic_silence",
    "reveal": "dramatic_silence",
    "surprise": "dramatic_silence",
    
    # Success
    "success": "triumphant_chime",
    "win": "triumphant_chime",
    "victory": "triumphant_chime",
    "achieve": "triumphant_chime",
    "succeed": "triumphant_chime",
}


# =============================================================================
# ENUMS
# =============================================================================

class SFXPriority(Enum):
    """Priority level for SFX events."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SFXCategory(Enum):
    """Category of SFX."""
    MONEY = "money"
    EXPLOSION = "explosion"
    TRANSITION = "transition"
    TEXT = "text"
    ICON = "icon"
    DRAMATIC = "dramatic"
    SUCCESS = "success"
    AMBIENT = "ambient"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SFXEvent:
    """
    A single SFX event at a specific timestamp.
    
    Attributes:
        id: Unique identifier for the event
        sfx_type: Type of SFX (e.g., "ka_ching", "muffled_boom")
        timestamp: Time in seconds when SFX should play
        duration: Duration of the SFX in seconds
        volume_db: Volume in dB (negative = quieter)
        priority: Priority level
        category: SFX category
        trigger_source: What triggered this SFX (e.g., "money keyword", "shot transition")
    """
    id: str
    sfx_type: str
    timestamp: float
    duration: float = 0.5
    volume_db: float = DEFAULT_SFX_VOLUME_DB
    priority: SFXPriority = SFXPriority.MEDIUM
    category: SFXCategory = SFXCategory.AMBIENT
    trigger_source: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "sfx_type": self.sfx_type,
            "timestamp": self.timestamp,
            "duration": self.duration,
            "volume_db": self.volume_db,
            "priority": self.priority.value,
            "category": self.category.value,
            "trigger_source": self.trigger_source
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SFXEvent":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            sfx_type=data["sfx_type"],
            timestamp=data["timestamp"],
            duration=data.get("duration", 0.5),
            volume_db=data.get("volume_db", DEFAULT_SFX_VOLUME_DB),
            priority=SFXPriority(data.get("priority", "medium")),
            category=SFXCategory(data.get("category", "ambient")),
            trigger_source=data.get("trigger_source", "")
        )


@dataclass
class SFXTriggerRule:
    """
    A rule for automatically triggering SFX based on content.
    
    Attributes:
        pattern: Text pattern to match (regex or keyword)
        sfx_type: SFX type to trigger
        priority: Priority level
        category: SFX category
        cooldown: Minimum time between triggers in seconds
    """
    pattern: str
    sfx_type: str
    priority: SFXPriority = SFXPriority.MEDIUM
    category: SFXCategory = SFXCategory.AMBIENT
    cooldown: float = 5.0  # seconds
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern,
            "sfx_type": self.sfx_type,
            "priority": self.priority.value,
            "category": self.category.value,
            "cooldown": self.cooldown
        }


@dataclass
class SFXMixConfig:
    """
    Configuration for mixing SFX tracks.
    
    Attributes:
        output_path: Path to the output mixed audio file
        sample_rate: Audio sample rate in Hz
        bitrate: Audio bitrate in kbps
        master_volume_db: Master volume adjustment
    """
    output_path: Path
    sample_rate: int = 44100
    bitrate: str = "192k"
    master_volume_db: float = 0.0


# =============================================================================
# SFX MANAGER
# =============================================================================

class SFXManagerError(AnalysisError):
    """Raised when SFX management fails."""
    pass


class SFXManager:
    """
    Manages SFX events for video production with automatic triggering.
    
    This manager handles:
    - Adding SFX events at specific timestamps
    - Automatic SFX triggering based on content keywords
    - SFX density management
    - FFmpeg command generation for mixing
    """
    
    def __init__(self, sfx_dir: Optional[Path] = None):
        """
        Initialize the SFX manager.
        
        Args:
            sfx_dir: Directory containing SFX audio files.
                    Defaults to DEFAULT_SFX_DIR.
        """
        self.sfx_dir = Path(sfx_dir) if sfx_dir else DEFAULT_SFX_DIR
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        
        # Event registry
        self.events: Dict[str, SFXEvent] = {}
        
        # Trigger rules
        self.trigger_rules: List[SFXTriggerRule] = []
        
        # Last trigger times for cooldown
        self._last_trigger_times: Dict[str, float] = {}
        
        # Initialize default trigger rules
        self._initialize_default_rules()
        
        logger.info(f"SFXManager initialized at {self.sfx_dir}")
    
    def _initialize_default_rules(self):
        """Initialize default SFX trigger rules."""
        for keyword, sfx_type in CONTENT_TRIGGERS.items():
            category = self._infer_category(sfx_type)
            self.trigger_rules.append(SFXTriggerRule(
                pattern=keyword,
                sfx_type=sfx_type,
                priority=SFXPriority.MEDIUM,
                category=category,
                cooldown=10.0
            ))
    
    def _infer_category(self, sfx_type: str) -> SFXCategory:
        """Infer SFX category from type."""
        if sfx_type in ["ka_ching"]:
            return SFXCategory.MONEY
        elif sfx_type in ["muffled_boom", "rocket_launch", "explosion"]:
            return SFXCategory.EXPLOSION
        elif sfx_type in ["cartoon_whoosh"]:
            return SFXCategory.TRANSITION
        elif sfx_type in ["crayon_scribble"]:
            return SFXCategory.TEXT
        elif sfx_type in ["subtle_pop_plop"]:
            return SFXCategory.ICON
        elif sfx_type in ["dramatic_silence"]:
            return SFXCategory.DRAMATIC
        elif sfx_type in ["triumphant_chime"]:
            return SFXCategory.SUCCESS
        else:
            return SFXCategory.AMBIENT
    
    def add_event(
        self,
        sfx_type: str,
        timestamp: float,
        duration: float = 0.5,
        volume_db: float = DEFAULT_SFX_VOLUME_DB,
        priority: SFXPriority = SFXPriority.MEDIUM,
        category: Optional[SFXCategory] = None,
        trigger_source: str = ""
    ) -> str:
        """
        Add an SFX event at a specific timestamp.
        
        Args:
            sfx_type: Type of SFX
            timestamp: Time in seconds when SFX should play
            duration: Duration of the SFX in seconds
            volume_db: Volume in dB
            priority: Priority level
            category: SFX category (auto-inferred if not provided)
            trigger_source: What triggered this SFX
        
        Returns:
            The event ID
        """
        event_id = f"sfx_{sfx_type}_{timestamp:.2f}"
        
        if category is None:
            category = self._infer_category(sfx_type)
        
        event = SFXEvent(
            id=event_id,
            sfx_type=sfx_type,
            timestamp=timestamp,
            duration=duration,
            volume_db=volume_db,
            priority=priority,
            category=category,
            trigger_source=trigger_source
        )
        
        self.events[event_id] = event
        logger.info(f"Added SFX event {event_id}: {sfx_type} at {timestamp}s")
        
        return event_id
    
    def add_trigger_rule(
        self,
        pattern: str,
        sfx_type: str,
        priority: SFXPriority = SFXPriority.MEDIUM,
        category: Optional[SFXCategory] = None,
        cooldown: float = 5.0
    ):
        """
        Add a custom SFX trigger rule.
        
        Args:
            pattern: Text pattern to match
            sfx_type: SFX type to trigger
            priority: Priority level
            category: SFX category
            cooldown: Minimum time between triggers in seconds
        """
        if category is None:
            category = self._infer_category(sfx_type)
        
        rule = SFXTriggerRule(
            pattern=pattern,
            sfx_type=sfx_type,
            priority=priority,
            category=category,
            cooldown=cooldown
        )
        self.trigger_rules.append(rule)
        logger.info(f"Added SFX trigger rule: {pattern} -> {sfx_type}")
    
    def get_event(self, event_id: str) -> Optional[SFXEvent]:
        """Get an SFX event by ID."""
        return self.events.get(event_id)
    
    def remove_event(self, event_id: str) -> bool:
        """Remove an SFX event by ID."""
        if event_id in self.events:
            del self.events[event_id]
            logger.info(f"Removed SFX event {event_id}")
            return True
        return False
    
    def find_sfx_file(self, sfx_type: str) -> Optional[Path]:
        """
        Find an SFX file matching the type.
        
        Args:
            sfx_type: SFX type to find
        
        Returns:
            Path to the SFX file, or None if not found
        """
        # Try exact match
        for ext in ["*.mp3", "*.wav", "*.aac", "*.ogg"]:
            for file_path in self.sfx_dir.glob(ext):
                if sfx_type.lower() in file_path.stem.lower():
                    return file_path
        
        # Try type mapping
        if sfx_type in SFX_TYPE_TO_FILE:
            target_file = SFX_TYPE_TO_FILE[sfx_type]
            for ext in [".mp3", ".wav", ".aac", ".ogg"]:
                file_path = self.sfx_dir / target_file
                if file_path.exists():
                    return file_path
        
        return None
    
    def trigger_from_text(self, text: str, timestamp: float) -> List[str]:
        """
        Trigger SFX events based on text content.
        
        Args:
            text: Text to analyze for SFX triggers
            timestamp: Timestamp to associate with triggered SFX
        
        Returns:
            List of created event IDs
        """
        import re
        
        created_events = []
        text_lower = text.lower()
        
        for rule in self.trigger_rules:
            # Check cooldown
            if rule.pattern in self._last_trigger_times:
                last_time = self._last_trigger_times[rule.pattern]
                if timestamp - last_time < rule.cooldown:
                    continue
            
            # Match pattern
            if re.search(rule.pattern, text_lower, re.IGNORECASE):
                event_id = self.add_event(
                    sfx_type=rule.sfx_type,
                    timestamp=timestamp,
                    priority=rule.priority,
                    category=rule.category,
                    trigger_source=f"text:{rule.pattern}"
                )
                created_events.append(event_id)
                self._last_trigger_times[rule.pattern] = timestamp
        
        return created_events
    
    def trigger_from_shot(self, shot_id: int, timestamp: float, shot_type: str = "") -> List[str]:
        """
        Trigger SFX events based on shot type.
        
        Args:
            shot_id: Shot ID
            timestamp: Timestamp of the shot
            shot_type: Type of shot (e.g., "transition", "money", "explosion")
        
        Returns:
            List of created event IDs
        """
        created_events = []
        
        # Transition SFX for every N shots
        if shot_id % 3 == 0:
            event_id = self.add_event(
                sfx_type="cartoon_whoosh",
                timestamp=timestamp,
                category=SFXCategory.TRANSITION,
                trigger_source=f"shot_transition:{shot_id}"
            )
            created_events.append(event_id)
        
        # Shot type-specific SFX
        if shot_type in CONTENT_TRIGGERS:
            sfx_type = CONTENT_TRIGGERS[shot_type]
            event_id = self.add_event(
                sfx_type=sfx_type,
                timestamp=timestamp,
                category=self._infer_category(sfx_type),
                trigger_source=f"shot_type:{shot_type}"
            )
            created_events.append(event_id)
        
        return created_events
    
    def trigger_from_metaphor(self, metaphor: str, timestamp: float) -> List[str]:
        """
        Trigger SFX events based on metaphor content.
        
        Args:
            metaphor: Metaphor text
            timestamp: Timestamp of the metaphor
        
        Returns:
            List of created event IDs
        """
        # Metaphors often involve visual concepts, add subtle SFX
        return self.trigger_from_text(metaphor, timestamp)
    
    def trigger_from_humor(self, humor_text: str, timestamp: float) -> List[str]:
        """
        Trigger SFX events based on humor content.
        
        Args:
            humor_text: Humor text
            timestamp: Timestamp of the humor
        
        Returns:
            List of created event IDs
        """
        # Humor often benefits from punchline SFX
        event_id = self.add_event(
            sfx_type="subtle_pop_plop",
            timestamp=timestamp,
            category=SFXCategory.ICON,
            trigger_source="humor_punchline"
        )
        return [event_id]
    
    def get_events_in_range(self, start_time: float, end_time: float) -> List[SFXEvent]:
        """Get all SFX events in a time range."""
        return [
            event for event in self.events.values()
            if start_time <= event.timestamp <= end_time
        ]
    
    def get_events_per_minute(self) -> float:
        """
        Calculate SFX density (events per minute).
        
        Returns:
            SFX events per minute
        """
        if not self.events:
            return 0.0
        
        last_time = max(e.timestamp for e in self.events.values())
        return len(self.events) / (last_time / 60) if last_time > 0 else 0.0
    
    def adjust_density(self, target_density: float = TARGET_SFX_DENSITY) -> int:
        """
        Adjust SFX density to meet target by adding or removing events.
        
        Args:
            target_density: Target SFX events per minute
        
        Returns:
            Number of events added or removed
        """
        current_density = self.get_events_per_minute()
        if current_density == 0:
            return 0
        
        # Calculate how many events we need
        last_time = max(e.timestamp for e in self.events.values())
        target_count = int(target_density * (last_time / 60))
        current_count = len(self.events)
        
        if current_count == target_count:
            return 0
        
        # Simple adjustment: add or remove events
        # In practice, this would be more sophisticated
        changes = 0
        
        if current_count < target_count:
            # Add events at regular intervals
            interval = last_time / target_count
            for i in range(target_count - current_count):
                timestamp = (i + 1) * interval
                event_id = self.add_event(
                    sfx_type="subtle_pop_plop",
                    timestamp=timestamp,
                    trigger_source="density_adjustment"
                )
                changes += 1
        else:
            # Remove lowest priority events
            sorted_events = sorted(
                self.events.values(),
                key=lambda e: (e.priority.value, e.timestamp),
                reverse=True
            )
            for event in sorted_events[:current_count - target_count]:
                self.remove_event(event.id)
                changes -= 1
        
        return changes
    
    def generate_mix_commands(
        self,
        voice_track: Optional[Path] = None,
        bgm_track: Optional[Path] = None,
        output_path: Optional[Path] = None,
        config: Optional[SFXMixConfig] = None
    ) -> List[List[str]]:
        """
        Generate FFmpeg commands to mix SFX with voice and BGM.
        
        Args:
            voice_track: Path to the voiceover audio file
            bgm_track: Path to the BGM audio file
            output_path: Path to the output mixed audio file
            config: Mix configuration
        
        Returns:
            List of FFmpeg command lists
        """
        if not self.events:
            logger.warning("No SFX events to mix")
            return []
        
        # Default config
        if config is None:
            config = SFXMixConfig(
                output_path=output_path or Path("output_with_sfx.aac")
            )
        
        # Sort events by timestamp
        sorted_events = sorted(self.events.values(), key=lambda e: e.timestamp)
        
        # Build commands
        commands = []
        
        # For a large number of SFX events, we need to mix them in batches
        # FFmpeg has a limit on the number of inputs
        batch_size = 10  # Mix 10 SFX at a time
        
        for i in range(0, len(sorted_events), batch_size):
            batch = sorted_events[i:i + batch_size]
            
            # Build input list
            inputs = []
            
            # Add voice track
            if voice_track:
                inputs.append("-i")
                inputs.append(str(voice_track))
            
            # Add BGM track
            if bgm_track:
                inputs.append("-i")
                inputs.append(str(bgm_track))
            
            # Add SFX inputs
            sfx_indices = {}
            for j, event in enumerate(batch):
                sfx_index = len(inputs) // 2
                sfx_file = self.find_sfx_file(event.sfx_type)
                if sfx_file:
                    sfx_indices[event.id] = sfx_index
                    inputs.append("-i")
                    inputs.append(str(sfx_file))
            
            if not sfx_indices:
                continue
            
            # Build filter complex
            filter_parts = []
            
            # Process voice
            if voice_track:
                filter_parts.append("[0:a]volume=0dB[voice]")
                voice_label = "[voice]"
            else:
                voice_label = ""
            
            # Process BGM
            if bgm_track:
                bgm_index = 1 if voice_track else 0
                filter_parts.append(f"[{bgm_index}:a]volume=-20dB[bgm]")
                bgm_label = "[bgm]"
            else:
                bgm_label = ""
            
            # Process SFX
            sfx_labels = []
            for j, event in enumerate(batch):
                sfx_index = (2 if voice_track else 0) + (1 if bgm_track else 0) + j
                sfx_label = f"[sfx{j}]"
                
                # Volume and timing
                filters = [
                    f"volume={event.volume_db}dB",
                    f"adelay={event.timestamp * 1000}|{event.timestamp * 1000}",
                    f"atrim=start=0:end={event.duration}"
                ]
                
                filter_parts.append(f"[{sfx_index}:a]{','.join(filters)}{sfx_label}")
                sfx_labels.append(sfx_label)
            
            # Mix all together
            all_labels = []
            if voice_track:
                all_labels.append("[voice]")
            if bgm_track:
                all_labels.append("[bgm]")
            all_labels.extend(sfx_labels)
            
            if all_labels:
                mix_filter = f"{''.join(all_labels)}amix=inputs={len(all_labels)}:duration=longest[aout]"
                filter_parts.append(mix_filter)
            
            # Build command
            if filter_parts:
                cmd = [
                    "ffmpeg",
                    "-y",
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
        bgm_track: Optional[Path] = None,
        output_path: Optional[Path] = None,
        config: Optional[SFXMixConfig] = None
    ) -> Path:
        """
        Mix SFX with voice and BGM using FFmpeg.
        
        Args:
            voice_track: Path to the voiceover audio file
            bgm_track: Path to the BGM audio file
            output_path: Path to the output mixed audio file
            config: Mix configuration
        
        Returns:
            Path to the output mixed audio file
        
        Raises:
            SFXManagerError: If mixing fails
        """
        if not self.events:
            raise SFXManagerError("No SFX events to mix")
        
        if config is None:
            config = SFXMixConfig(
                output_path=output_path or Path("output_with_sfx.aac")
            )
        
        commands = self.generate_mix_commands(voice_track, bgm_track, config.output_path, config)
        
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
                    raise SFXManagerError(f"FFmpeg mixing failed: {result.stderr}")
            except subprocess.TimeoutExpired:
                raise SFXManagerError("FFmpeg mixing timed out")
        
        if config.output_path.exists():
            logger.info(f"Mixed audio with SFX saved to: {config.output_path}")
            return config.output_path
        
        raise SFXManagerError("Output file not created")
    
    def create_from_algorithm_result(
        self,
        sentences: List[Dict[str, Any]],
        shots: List[Dict[str, Any]],
        metaphors: List[Dict[str, Any]],
        humor_lines: List[Dict[str, Any]]
    ) -> int:
        """
        Create SFX events from algorithm result data.
        
        Args:
            sentences: List of sentence dictionaries
            shots: List of shot dictionaries
            metaphors: List of metaphor dictionaries
            humor_lines: List of humor line dictionaries
        
        Returns:
            Number of SFX events created
        """
        count = 0
        
        # Trigger SFX from sentences
        for sentence in sentences:
            text = sentence.get("text", "")
            timestamp = sentence.get("start", 0.0)
            
            events = self.trigger_from_text(text, timestamp)
            count += len(events)
        
        # Trigger SFX from shots
        for i, shot in enumerate(shots):
            timestamp = shot.get("start", 0.0)
            shot_type = shot.get("scene_description", "").lower()
            
            events = self.trigger_from_shot(i + 1, timestamp, shot_type)
            count += len(events)
        
        # Trigger SFX from metaphors
        for metaphor in metaphors:
            text = metaphor.get("text", "")
            timestamp = metaphor.get("timestamp", 0.0)
            
            events = self.trigger_from_metaphor(text, timestamp)
            count += len(events)
        
        # Trigger SFX from humor lines
        for humor in humor_lines:
            text = humor.get("text", "")
            timestamp = humor.get("timestamp", 0.0)
            
            events = self.trigger_from_humor(text, timestamp)
            count += len(events)
        
        # Adjust density to target
        self.adjust_density()
        
        return count
    
    def export_config(self, output_path: Path) -> Path:
        """
        Export SFX configuration to a JSON file.
        
        Args:
            output_path: Path to save the configuration
        
        Returns:
            Path to the saved configuration file
        """
        config = {
            "events": {eid: event.to_dict() for eid, event in self.events.items()},
            "trigger_rules": [rule.to_dict() for rule in self.trigger_rules]
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported SFX config to: {output_path}")
        return output_path
    
    def import_config(self, input_path: Path) -> int:
        """
        Import SFX configuration from a JSON file.
        
        Args:
            input_path: Path to the configuration file
        
        Returns:
            Number of events imported
        """
        with open(input_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        self.events.clear()
        
        for eid, event_data in config.get("events", {}).items():
            event = SFXEvent.from_dict(event_data)
            self.events[eid] = event
        
        self.trigger_rules.clear()
        for rule_data in config.get("trigger_rules", []):
            rule = SFXTriggerRule(
                pattern=rule_data["pattern"],
                sfx_type=rule_data["sfx_type"],
                priority=SFXPriority(rule_data.get("priority", "medium")),
                category=SFXCategory(rule_data.get("category", "ambient")),
                cooldown=rule_data.get("cooldown", 5.0)
            )
            self.trigger_rules.append(rule)
        
        logger.info(f"Imported {len(self.events)} SFX events from: {input_path}")
        return len(self.events)
    
    def clear(self):
        """Clear all events and reset trigger cooldowns."""
        self.events.clear()
        self._last_trigger_times.clear()
        logger.info("Cleared all SFX data")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def create_sfx_from_audio_analysis(
    audio_analysis: Dict[str, Any],
    sfx_dir: Optional[Path] = None
) -> SFXManager:
    """
    Create an SFXManager from audio analysis data.
    
    Args:
        audio_analysis: Audio analysis dictionary with sfx_spec
        sfx_dir: Directory for SFX files
    
    Returns:
        Configured SFXManager
    """
    manager = SFXManager(sfx_dir)
    
    # Extract SFX event triggers from analysis
    sfx_spec = audio_analysis.get("sfx_spec", {})
    event_triggers = sfx_spec.get("event_triggers", {})
    average_density = sfx_spec.get("average_density_per_minute", TARGET_SFX_DENSITY)
    
    # Add trigger rules for each event type
    for event_type, description in event_triggers.items():
        manager.add_trigger_rule(
            pattern=event_type,
            sfx_type=event_type,
            cooldown=60.0 / average_density  # Based on density
        )
    
    return manager


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """Command-line interface for SFX management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="SFX Manager for Video Production")
    parser.add_argument("--list", action="store_true", help="List all SFX events")
    parser.add_argument("--add", type=str, nargs=2, metavar=("TYPE", "TIMESTAMP"),
                        help="Add an SFX event (type, timestamp)")
    parser.add_argument("--remove", type=str, help="Remove an SFX event (ID)")
    parser.add_argument("--density", action="store_true", help="Show SFX density")
    parser.add_argument("--export", type=str, help="Export config to file")
    parser.add_argument("--import-config", type=str, dest="import_config",
                        help="Import config from file")
    parser.add_argument("--sfx-dir", type=str, default=None, help="SFX directory")
    
    args = parser.parse_args()
    
    manager = SFXManager(sfx_dir=args.sfx_dir)
    
    if args.list:
        print("SFX Events:")
        for eid, event in manager.events.items():
            print(f"  {eid}: {event.sfx_type} at {event.timestamp}s")
    
    elif args.add:
        sfx_type, timestamp = args.add
        event_id = manager.add_event(
            sfx_type=sfx_type,
            timestamp=float(timestamp)
        )
        print(f"Added event {event_id}")
    
    elif args.remove:
        success = manager.remove_event(args.remove)
        print(f"Removed: {success}")
    
    elif args.density:
        density = manager.get_events_per_minute()
        print(f"SFX Density: {density:.1f} events/minute")
    
    elif args.export:
        output_path = Path(args.export)
        manager.export_config(output_path)
        print(f"Exported to: {output_path}")
    
    elif args.import_config:
        input_path = Path(args.import_config)
        count = manager.import_config(input_path)
        print(f"Imported {count} events")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
