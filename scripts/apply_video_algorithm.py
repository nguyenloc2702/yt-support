"""
Video Algorithm Applier Agent

Applies the UNIVERSAL_VIDEO_ALGORITHM to generate production-ready assets:
- Algorithm-compliant scripts (hook paradox, open loops, 54/46 pacing)
- Shot plans with 4.5-5s duration and <=6 words per shot
- Character reference management
- Multi-track BGM and SFX planning

Usage:
    python scripts/apply_video_algorithm.py --topic "History of Nvidia" --output-dir data/test_video
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Force UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("video_algorithm_agent")

from pipeline.llm_client import get_llm_client, chat_json_multimodal


# =============================================================================
# ALGORITHM CONSTANTS (from UNIVERSAL_VIDEO_ALGORITHM.md)
# =============================================================================

# Story Metrics
MIN_OPEN_LOOPS = 4
MAX_HOOK_DURATION = 30.0  # seconds
TARGET_SHORT_SENTENCE_PCT = 0.54  # 54%
TARGET_WPM = 184.8
MIN_WPM = 160
MAX_WPM = 200
DRAMATIC_PAUSES_PER_MINUTE = 10

# Visual Metrics
TARGET_SHOT_DURATION_MEAN = 4.75  # 4.5-5s
MIN_SHOT_DURATION = 4.0
MAX_SHOT_DURATION = 6.0
MAX_WORDS_PER_SHOT = 6

# Audio Metrics
TARGET_LUFS = -18.0
BGM_DUCKING_DB = -20.0  # -18 to -22 dB under voice
SFX_DENSITY_PER_MINUTE = 8.5


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Hook:
    """Video hook with paradox and question."""
    paradox: str
    question: str
    duration: float  # seconds
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "paradox": self.paradox,
            "question": self.question,
            "duration": self.duration
        }


@dataclass
class OpenLoop:
    """An open loop (question) that creates curiosity."""
    id: int
    opened_at: float  # timestamp in seconds
    closed_at: float  # timestamp in seconds
    question: str
    resolution: str
    loop_type: str  # "large", "medium", "small"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "question": self.question,
            "resolution": self.resolution,
            "loop_type": self.loop_type
        }


@dataclass
class Sentence:
    """A sentence in the script with algorithm metadata."""
    text: str
    start: float  # timestamp
    end: float  # timestamp
    word_count: int
    is_short: bool  # <=6 words
    is_punchline: bool
    is_question: bool
    dramatic_pause_before: float  # seconds
    beat_role: str  # hook, setup, rising, crisis, breakthrough, payoff, moral
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "word_count": self.word_count,
            "is_short": self.is_short,
            "is_punchline": self.is_punchline,
            "is_question": self.is_question,
            "dramatic_pause_before": self.dramatic_pause_before,
            "beat_role": self.beat_role
        }


@dataclass
class Shot:
    """A video shot with algorithm-compliant constraints."""
    id: int
    start: float
    end: float
    duration: float
    text: str
    word_count: int
    scene_description: str
    visual_prompt: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "text": self.text,
            "word_count": self.word_count,
            "scene_description": self.scene_description,
            "visual_prompt": self.visual_prompt
        }


@dataclass
class Metaphor:
    """A metaphor used in the script."""
    text: str
    explanation: str
    timestamp: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "explanation": self.explanation,
            "timestamp": self.timestamp
        }


@dataclass
class HumorLine:
    """A humor line in the script."""
    text: str
    timestamp: float
    humor_type: str  # pun, irony, absurd, callback
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "timestamp": self.timestamp,
            "humor_type": self.humor_type
        }


@dataclass
class Character:
    """A character in the video."""
    id: str
    name: str
    description: str
    reference_image_path: Optional[str] = None
    first_appearance: float = 0.0
    shots: List[int] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "reference_image_path": self.reference_image_path,
            "first_appearance": self.first_appearance,
            "shots": self.shots
        }


@dataclass
class BGMTrack:
    """A BGM track with mood and timing."""
    id: str
    path: str
    mood: str
    start_time: float
    end_time: float
    volume_db: float = -20.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "mood": self.mood,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "volume_db": self.volume_db
        }


@dataclass
class SFXEvent:
    """An SFX event at a specific timestamp."""
    id: str
    sfx_type: str
    timestamp: float
    duration: float = 0.5
    volume_db: float = -15.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sfx_type": self.sfx_type,
            "timestamp": self.timestamp,
            "duration": self.duration,
            "volume_db": self.volume_db
        }


@dataclass
class VideoAlgorithmResult:
    """Complete result from applying the algorithm."""
    topic: str
    hook: Hook
    open_loops: List[OpenLoop]
    sentences: List[Sentence]
    shots: List[Shot]
    metaphors: List[Metaphor]
    humor_lines: List[HumorLine]
    characters: List[Character]
    bgm_tracks: List[BGMTrack]
    sfx_events: List[SFXEvent]
    
    # Validation metrics
    validation: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "hook": self.hook.to_dict(),
            "open_loops": [loop.to_dict() for loop in self.open_loops],
            "sentences": [s.to_dict() for s in self.sentences],
            "shots": [shot.to_dict() for shot in self.shots],
            "metaphors": [m.to_dict() for m in self.metaphors],
            "humor_lines": [h.to_dict() for h in self.humor_lines],
            "characters": [c.to_dict() for c in self.characters],
            "bgm_tracks": [t.to_dict() for t in self.bgm_tracks],
            "sfx_events": [e.to_dict() for e in self.sfx_events],
            "validation": self.validation
        }
    
    def save(self, output_dir: Path):
        """Save the result to a JSON file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.topic.replace(' ', '_')}_algorithm_result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved algorithm result to: {output_path}")
        canonical_path = output_dir / "algorithm_result.json"
        with open(canonical_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved canonical algorithm result to: {canonical_path}")
        return output_path


# =============================================================================
# ALGORITHM AGENT
# =============================================================================

class VideoAlgorithmAgent:
    """
    Applies the UNIVERSAL_VIDEO_ALGORITHM to generate production-ready assets.
    """
    
    # Beat roles in order
    BEAT_ROLES = ["hook", "setup", "rising", "crisis", "breakthrough", "payoff", "moral"]
    
    # SFX event triggers mapping
    SFX_TRIGGERS = {
        "money": "ka_ching",
        "explosion": "muffled_boom", 
        "transition": "cartoon_whoosh",
        "text_appear": "crayon_scribble",
        "icon_pop": "subtle_pop_plop",
        "dramatic_pause": "dramatic_silence",
        "rocket": "muffled_boom",
        "crisis": "ticking_clock",
        "success": "triumphant_chime"
    }
    
    # BGM moods for different beats
    BGM_MOODS = {
        "hook": "mysterious_pizzicato",
        "setup": "nostalgic_acoustic",
        "rising": "energetic_bassline",
        "crisis": "ominous_cello",
        "breakthrough": "triumphant_synth",
        "payoff": "celebratory_groove",
        "moral": "reflective_guitar"
    }
    
    def __init__(self, output_dir: Path, topic: str):
        self.output_dir = Path(output_dir)
        self.topic = topic
        self.result: Optional[VideoAlgorithmResult] = None
        
        # Create directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.output_dir / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.characters_dir = self.assets_dir / "characters"
        self.characters_dir.mkdir(parents=True, exist_ok=True)
        
    def apply(self) -> VideoAlgorithmResult:
        """
        Apply the complete UNIVERSAL_VIDEO_ALGORITHM.
        
        Returns:
            VideoAlgorithmResult with all algorithm components
        """
        logger.info(f"Applying UNIVERSAL_VIDEO_ALGORITHM to topic: {self.topic}")
        
        # Step 1: Generate hook
        hook = self._generate_hook()
        logger.info(f"Generated hook: {hook.paradox[:50]}...")
        
        # Step 2: Generate open loops
        open_loops = self._generate_open_loops(hook)
        logger.info(f"Generated {len(open_loops)} open loops")
        
        # Step 3: Generate script with 54/46 pacing
        sentences = self._generate_script(open_loops, hook)
        logger.info(f"Generated {len(sentences)} sentences")
        
        # Step 4: Plan shots (4.5-5s duration, <=6 words)
        shots = self._plan_shots(sentences)
        logger.info(f"Planned {len(shots)} shots with mean duration {self._calculate_mean_duration(shots):.2f}s")
        
        # Step 5: Generate metaphors
        metaphors = self._generate_metaphors(sentences)
        logger.info(f"Generated {len(metaphors)} metaphors")
        
        # Step 6: Generate humor lines
        humor_lines = self._generate_humor(sentences)
        logger.info(f"Generated {len(humor_lines)} humor lines")
        
        # Step 7: Detect characters
        characters = self._detect_characters(sentences)
        logger.info(f"Detected {len(characters)} characters")
        
        # Step 8: Plan BGM tracks
        bgm_tracks = self._plan_bgm_tracks(shots)
        logger.info(f"Planned {len(bgm_tracks)} BGM tracks")
        
        # Step 9: Plan SFX events
        sfx_events = self._plan_sfx_events(sentences, shots)
        logger.info(f"Planned {len(sfx_events)} SFX events (density: {self._calculate_sfx_density(sfx_events):.1f}/min)")
        
        # Validate results
        validation = self._validate_results(
            hook, open_loops, sentences, shots, metaphors, humor_lines, sfx_events
        )
        
        # Create result
        self.result = VideoAlgorithmResult(
            topic=self.topic,
            hook=hook,
            open_loops=open_loops,
            sentences=sentences,
            shots=shots,
            metaphors=metaphors,
            humor_lines=humor_lines,
            characters=characters,
            bgm_tracks=bgm_tracks,
            sfx_events=sfx_events,
            validation=validation
        )
        
        return self.result
    
    def _generate_hook(self) -> Hook:
        """
        Step 1: Generate hook with paradox and question.
        
        Formula: [Số liệu khủng] + [So sánh bất ngờ] + [Câu hỏi treo]
        """
        system_prompt = """You are a master storyteller creating engaging video hooks.
Your task is to create a compelling hook for a video about the given topic.

REQUIREMENTS:
1. Use the formula: [Shocking statistic] + [Unexpected comparison] + [Suspended question]
2. Hook must be answerable within the video
3. Hook must create a question in the viewer's mind
4. Hook must include specific numbers (not vague)
5. Duration should be <= 30 seconds when spoken
6. Respond in the same language as the topic

EXAMPLES:
Topic: "History of Nvidia"
Output: {"paradox": "Năm 2024, Nvidia có giá trị gấp 10 lần GDP của Việt Nam.", "question": "Làm sao một công ty chỉ bán card màn hình lại có thể mạnh như vậy?", "duration": 18.5}

Topic: "How Bitcoin Works"
Output: {"paradox": "In 2021, a single Bitcoin was worth more than a kilogram of gold.", "question": "How can digital money that doesn't exist physically be worth so much?", "duration": 22.0}"""

        user_prompt = f"Topic: {self.topic}"
        
        json_schema = {
            "type": "object",
            "properties": {
                "paradox": {"type": "string", "description": "Shocking statistic + unexpected comparison"},
                "question": {"type": "string", "description": "Suspended question that creates curiosity"},
                "duration": {"type": "number", "description": "Estimated duration in seconds (<= 30)"}
            },
            "required": ["paradox", "question", "duration"]
        }
        
        try:
            result = chat_json_multimodal(
                system_prompt=system_prompt,
                user_text=user_prompt,
                json_schema=json_schema,
                temperature=0.7,
                max_tokens=512
            )
            return Hook(
                paradox=result.get("paradox", ""),
                question=result.get("question", ""),
                duration=float(result.get("duration", 20.0))
            )
        except Exception as e:
            logger.warning(f"LLM hook generation failed, using fallback: {e}")
            return self._generate_fallback_hook()
    
    def _generate_fallback_hook(self) -> Hook:
        """Generate a fallback hook if LLM fails."""
        # Simple fallback based on topic
        return Hook(
            paradox=f"Did you know that {self.topic} has a surprising history?",
            question=f"What makes {self.topic} so fascinating?",
            duration=15.0
        )
    
    def _generate_open_loops(self, hook: Hook) -> List[OpenLoop]:
        """
        Step 2: Generate nested open loops.
        
        Rules:
        - Loop 0 (large): Opens at hook, closes at conclusion
        - Loops 1-4 (medium): Each 2-4 minutes, nested
        - Optional small loops within medium loops
        """
        system_prompt = """You are a master storyteller creating nested open loops for video storytelling.

REQUIREMENTS:
1. Create 4-5 main open loops (questions) that drive the narrative
2. Loop 0 must open at the beginning (hook) and close at the end (conclusion)
3. Each subsequent loop must OPEN before the previous loop CLOSES (nested)
4. Each loop must have a clear question and resolution
5. Loops should be 2-4 minutes apart
6. Include loop_type: "large", "medium", or "small"
7. Respond in the same language as the topic

EXAMPLE:
{
  "open_loops": [
    {"id": 0, "opened_at": 0, "closed_at": 1150, "question": "How did Elon Musk become the first trillionaire?", "resolution": "Through relentless innovation and risk-taking across multiple industries.", "loop_type": "large"},
    {"id": 1, "opened_at": 120, "closed_at": 300, "question": "Would Zip2 be crushed by media giants?", "resolution": "No, it was acquired for $307 million.", "loop_type": "medium"},
    {"id": 2, "opened_at": 240, "closed_at": 450, "question": "Could X.com survive the PayPal war?", "resolution": "Yes, it became PayPal and was acquired by eBay.", "loop_type": "medium"}
  ]
}

Topic: History of Nvidia"""

        user_prompt = f"Topic: {self.topic}\nHook question: {hook.question}"
        
        json_schema = {
            "type": "object",
            "properties": {
                "open_loops": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "opened_at": {"type": "number", "description": "Timestamp in seconds"},
                            "closed_at": {"type": "number", "description": "Timestamp in seconds"},
                            "question": {"type": "string"},
                            "resolution": {"type": "string"},
                            "loop_type": {"type": "string", "enum": ["large", "medium", "small"]}
                        },
                        "required": ["id", "opened_at", "closed_at", "question", "resolution", "loop_type"]
                    }
                }
            },
            "required": ["open_loops"]
        }
        
        try:
            result = chat_json_multimodal(
                system_prompt=system_prompt,
                user_text=user_prompt,
                json_schema=json_schema,
                temperature=0.7,
                max_tokens=2048
            )
            loops_data = result.get("open_loops", [])
            return [
                OpenLoop(
                    id=loop["id"],
                    opened_at=float(loop["opened_at"]),
                    closed_at=float(loop["closed_at"]),
                    question=loop["question"],
                    resolution=loop["resolution"],
                    loop_type=loop["loop_type"]
                )
                for loop in loops_data
            ]
        except Exception as e:
            logger.warning(f"LLM open loop generation failed, using fallback: {e}")
            return self._generate_fallback_open_loops(hook)
    
    def _generate_fallback_open_loops(self, hook: Hook) -> List[OpenLoop]:
        """Generate fallback open loops."""
        # Create 4 basic loops
        total_duration = 600.0  # 10 minutes default
        loops = []
        
        # Large loop (entire video)
        loops.append(OpenLoop(
            id=0,
            opened_at=0.0,
            closed_at=total_duration,
            question=hook.question,
            resolution=f"The answer to {hook.question}",
            loop_type="large"
        ))
        
        # 3 medium loops
        for i in range(1, 4):
            start = i * 150.0
            end = start + 120.0
            loops.append(OpenLoop(
                id=i,
                opened_at=start,
                closed_at=end,
                question=f"What happens in part {i}?",
                resolution=f"Part {i} resolution",
                loop_type="medium"
            ))
        
        return loops
    
    def _generate_script(self, open_loops: List[OpenLoop], hook: Hook) -> List[Sentence]:
        """
        Step 3: Generate script with 54/46 pacing rule.
        
        Rules:
        - 54% short sentences (<=6 words)
        - 46% long sentences (7-15 words)
        - Every 3-5 sentences must have 1 short punchline
        """
        # Calculate total duration from loops
        total_duration = max(loop.closed_at for loop in open_loops)
        
        # Estimate sentence count based on WPM
        estimated_words = int(TARGET_WPM * (total_duration / 60))
        estimated_sentences = estimated_words // 8  # average 8 words per sentence
        
        system_prompt = f"""You are a master scriptwriter creating engaging video narration.

TOPIC: {self.topic}
HOOK: {hook.paradox} {hook.question}

REQUIREMENTS:
1. Create approximately {estimated_sentences} sentences for a {total_duration/60:.1f} minute video
2. Follow the 54/46 rule: 54% short sentences (<=6 words), 46% long sentences (7-15 words)
3. Every 3-5 sentences must include 1 short punchline sentence
4. Include the hook at the beginning
5. Include questions to open loops and resolutions to close them
6. Use conversational, engaging language
7. Include specific numbers and facts
8. Respond in the same language as the topic

OPEN LOOPS TO INCLUDE:
{json.dumps([{'id': l.id, 'question': l.question, 'resolution': l.resolution, 'opened_at': l.opened_at, 'closed_at': l.closed_at} for l in open_loops], ensure_ascii=False)}

EXAMPLE OUTPUT FORMAT:
{{
  "sentences": [
    {{"text": "Năm 2024, Nvidia có giá trị gấp 10 lần GDP của Việt Nam.", "word_count": 8, "is_short": false, "is_punchline": false, "is_question": false, "beat_role": "hook"}},
    {{"text": "Làm sao?", "word_count": 2, "is_short": true, "is_punchline": true, "is_question": true, "beat_role": "hook"}},
    {{"text": "Câu chuyện bắt đầu từ một sinh viên ở Stanford.", "word_count": 7, "is_short": false, "is_punchline": false, "is_question": false, "beat_role": "setup"}}
  ]
}}"""

        user_prompt = "Generate the script sentences."
        
        json_schema = {
            "type": "object",
            "properties": {
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "word_count": {"type": "integer"},
                            "is_short": {"type": "boolean"},
                            "is_punchline": {"type": "boolean"},
                            "is_question": {"type": "boolean"},
                            "beat_role": {"type": "string", "enum": ["hook", "setup", "rising", "crisis", "breakthrough", "payoff", "moral"]}
                        },
                        "required": ["text", "word_count", "is_short", "is_punchline", "is_question", "beat_role"]
                    }
                }
            },
            "required": ["sentences"]
        }
        
        try:
            result = chat_json_multimodal(
                system_prompt=system_prompt,
                user_text=user_prompt,
                json_schema=json_schema,
                temperature=0.7,
                max_tokens=4096
            )
            sentences_data = result.get("sentences", [])
            
            sentences = []
            current_time = 0.0
            for i, s in enumerate(sentences_data):
                # Estimate duration based on word count and WPM
                word_duration = (s["word_count"] / TARGET_WPM) * 60
                
                # Add dramatic pause before punchlines
                dramatic_pause = 0.6 if s.get("is_punchline") else 0.0
                
                sentences.append(Sentence(
                    text=s["text"],
                    start=current_time,
                    end=current_time + word_duration + dramatic_pause,
                    word_count=s["word_count"],
                    is_short=s.get("is_short", False),
                    is_punchline=s.get("is_punchline", False),
                    is_question=s.get("is_question", False),
                    dramatic_pause_before=dramatic_pause,
                    beat_role=s.get("beat_role", "setup")
                ))
                
                current_time += word_duration + dramatic_pause
            
            return sentences
            
        except Exception as e:
            logger.warning(f"LLM script generation failed, using fallback: {e}")
            return self._generate_fallback_script(open_loops, hook)
    
    def _generate_fallback_script(self, open_loops: List[OpenLoop], hook: Hook) -> List[Sentence]:
        """Generate fallback script."""
        sentences = []
        current_time = 0.0
        
        # Add hook
        hook_words = hook.paradox.split() + hook.question.split()
        sentences.append(Sentence(
            text=f"{hook.paradox} {hook.question}",
            start=0.0,
            end=15.0,
            word_count=len(hook_words),
            is_short=False,
            is_punchline=True,
            is_question=True,
            dramatic_pause_before=0.0,
            beat_role="hook"
        ))
        current_time = 15.0
        
        # Generate sample sentences
        sample_texts = [
            "Let me tell you the story.",
            "It all began with a simple idea.",
            "But there was a problem.",
            "The solution was unexpected.",
            "And that changed everything."
        ]
        
        for i, text in enumerate(sample_texts):
            words = text.split()
            word_duration = (len(words) / TARGET_WPM) * 60
            
            sentences.append(Sentence(
                text=text,
                start=current_time,
                end=current_time + word_duration,
                word_count=len(words),
                is_short=len(words) <= 6,
                is_punchline=i % 3 == 0,
                is_question=False,
                dramatic_pause_before=0.6 if i % 3 == 0 else 0.0,
                beat_role=self.BEAT_ROLES[(i + 1) % len(self.BEAT_ROLES)]
            ))
            current_time += word_duration + (0.6 if i % 3 == 0 else 0.0)
        
        return sentences
    
    def _plan_shots(self, sentences: List[Sentence]) -> List[Shot]:
        """
        Step 4: Plan shots with 4.5-5s duration and <=6 words.
        
        Algorithm:
        - Group sentences into shots
        - Each shot must have <=6 words
        - Target duration: 4.5-5s
        - If a sentence has >6 words, split it
        """
        shots = []
        shot_id = 1
        current_start = 0.0
        current_words = []
        current_word_count = 0
        
        for sentence in sentences:
            # If adding this sentence would exceed MAX_WORDS_PER_SHOT,
            # finalize current shot and start a new one
            if current_word_count + sentence.word_count > MAX_WORDS_PER_SHOT:
                # Finalize current shot
                if current_words:
                    shot_text = " ".join(current_words)
                    shot_duration = sentence.start - current_start
                    
                    # Ensure minimum duration
                    if shot_duration < MIN_SHOT_DURATION:
                        shot_duration = MIN_SHOT_DURATION
                    
                    # Generate visual description
                    scene_desc, visual_prompt = self._generate_visual_prompt(shot_text, shot_id)
                    
                    shots.append(Shot(
                        id=shot_id,
                        start=current_start,
                        end=current_start + shot_duration,
                        duration=shot_duration,
                        text=shot_text,
                        word_count=current_word_count,
                        scene_description=scene_desc,
                        visual_prompt=visual_prompt
                    ))
                    shot_id += 1
                
                # Start new shot
                current_start = sentence.start
                current_words = [sentence.text]
                current_word_count = sentence.word_count
            else:
                current_words.append(sentence.text)
                current_word_count += sentence.word_count
        
        # Add the last shot
        if current_words:
            shot_text = " ".join(current_words)
            shot_duration = sentences[-1].end - current_start
            
            if shot_duration < MIN_SHOT_DURATION:
                shot_duration = MIN_SHOT_DURATION
            
            scene_desc, visual_prompt = self._generate_visual_prompt(shot_text, shot_id)
            
            shots.append(Shot(
                id=shot_id,
                start=current_start,
                end=current_start + shot_duration,
                duration=shot_duration,
                text=shot_text,
                word_count=current_word_count,
                scene_description=scene_desc,
                visual_prompt=visual_prompt
            ))
        
        return shots
    
    def _generate_visual_prompt(self, text: str, shot_id: int) -> Tuple[str, str]:
        """Generate visual description and prompt for a shot."""
        # Simple keyword-based visual generation
        text_lower = text.lower()
        
        if any(word in text_lower for word in ["money", "dollar", "rich", "billion"]):
            desc = "Financial scene with money/currency visuals"
            prompt = "Cinematic 4K shot of money, financial charts, or wealth symbols. Professional documentary style, silent, no audio."
        elif any(word in text_lower for word in ["computer", "tech", "software", "code"]):
            desc = "Technology/computer scene"
            prompt = "Cinematic 4K shot of computers, code on screens, or tech environments. Professional documentary style, silent, no audio."
        elif any(word in text_lower for word in ["person", "people", "man", "woman"]):
            desc = "Person/character scene"
            prompt = "Cinematic 4K portrait shot of a person speaking or working. Professional documentary style, silent, no audio."
        elif any(word in text_lower for word in ["history", "past", "old", "vintage"]):
            desc = "Historical/vintage scene"
            prompt = "Cinematic 4K vintage/retro shot with historical atmosphere. Professional documentary style, silent, no audio."
        else:
            desc = "General scene"
            prompt = "Cinematic 4K shot matching the text content. Professional documentary style, silent, no audio."
        
        return desc, prompt
    
    def _generate_metaphors(self, sentences: List[Sentence]) -> List[Metaphor]:
        """
        Step 5: Generate metaphors (1 per 3-5 minutes).
        """
        total_duration = sentences[-1].end if sentences else 0
        target_metaphors = max(1, int(total_duration / 180))  # 1 per 3 minutes
        
        system_prompt = f"""You are a creative writer generating metaphors for video storytelling.

TOPIC: {self.topic}
TOTAL DURATION: {total_duration/60:.1f} minutes
TARGET: {target_metaphors} metaphors

REQUIREMENTS:
1. Create {target_metaphors} metaphors that explain complex concepts simply
2. Each metaphor should have: text, explanation, and approximate timestamp
3. Metaphors should be spaced throughout the video
4. Use the 3-layer metaphor approach: concept -> familiar object -> action with conflict
5. Respond in the same language as the topic

EXAMPLE:
{{
  "metaphors": [
    {{"text": "Bitcoin is like digital gold.", "explanation": "Just as gold has value due to scarcity and trust, Bitcoin derives value from its limited supply and decentralized nature.", "timestamp": 120.0}},
    {{"text": "A blockchain is like a public ledger.", "explanation": "Everyone can see all transactions, but no one can change past entries without consensus.", "timestamp": 300.0}}
  ]
}}"""

        user_prompt = "Generate metaphors for the video."
        
        json_schema = {
            "type": "object",
            "properties": {
                "metaphors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "explanation": {"type": "string"},
                            "timestamp": {"type": "number"}
                        },
                        "required": ["text", "explanation", "timestamp"]
                    }
                }
            },
            "required": ["metaphors"]
        }
        
        try:
            result = chat_json_multimodal(
                system_prompt=system_prompt,
                user_text=user_prompt,
                json_schema=json_schema,
                temperature=0.8,
                max_tokens=1024
            )
            metaphors_data = result.get("metaphors", [])
            return [
                Metaphor(
                    text=m["text"],
                    explanation=m["explanation"],
                    timestamp=float(m["timestamp"])
                )
                for m in metaphors_data
            ]
        except Exception as e:
            logger.warning(f"LLM metaphor generation failed, using fallback: {e}")
            return []
    
    def _generate_humor(self, sentences: List[Sentence]) -> List[HumorLine]:
        """
        Step 6: Generate humor lines (4+ for video).
        """
        target_humor = 4
        
        system_prompt = f"""You are a funny writer creating humor for educational videos.

TOPIC: {self.topic}
TARGET: {target_humor} humor lines

REQUIREMENTS:
1. Create {target_humor} funny lines for the video
2. Each humor line should have: text, timestamp, and humor_type
3. Humor types: pun, irony, absurd, callback
4. Space humor throughout the video
5. Keep humor appropriate and relevant to the topic
6. Respond in the same language as the topic

EXAMPLE:
{{
  "humor_lines": [
    {{"text": "And then the computer said '404: Brain not found'.", "timestamp": 150.0, "humor_type": "pun"}},
    {{"text": "Turns out, the 'cloud' is just someone else's computer.", "timestamp": 420.0, "humor_type": "irony"}}
  ]
}}"""

        user_prompt = "Generate humor lines for the video."
        
        json_schema = {
            "type": "object",
            "properties": {
                "humor_lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "timestamp": {"type": "number"},
                            "humor_type": {"type": "string", "enum": ["pun", "irony", "absurd", "callback"]}
                        },
                        "required": ["text", "timestamp", "humor_type"]
                    }
                }
            },
            "required": ["humor_lines"]
        }
        
        try:
            result = chat_json_multimodal(
                system_prompt=system_prompt,
                user_text=user_prompt,
                json_schema=json_schema,
                temperature=0.9,
                max_tokens=1024
            )
            humor_data = result.get("humor_lines", [])
            return [
                HumorLine(
                    text=h["text"],
                    timestamp=float(h["timestamp"]),
                    humor_type=h["humor_type"]
                )
                for h in humor_data
            ]
        except Exception as e:
            logger.warning(f"LLM humor generation failed, using fallback: {e}")
            return []
    
    def _detect_characters(self, sentences: List[Sentence]) -> List[Character]:
        """
        Step 7: Detect characters in the script.
        """
        # Simple character detection from capitalized names
        import re
        
        characters = []
        char_id = 1
        
        # Common name patterns
        name_pattern = re.compile(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b")
        
        for sentence in sentences:
            matches = name_pattern.findall(sentence.text)
            for name in matches:
                # Check if character already exists
                existing = next((c for c in characters if c.name == name), None)
                if not existing:
                    characters.append(Character(
                        id=f"char_{char_id}",
                        name=name,
                        description=f"Character in {self.topic}",
                        first_appearance=sentence.start
                    ))
                    char_id += 1
        
        return characters
    
    def _plan_bgm_tracks(self, shots: List[Shot]) -> List[BGMTrack]:
        """
        Step 8: Plan BGM tracks with mood shifts per beat.
        """
        bgm_tracks = []
        
        # Group shots by beat role
        beat_groups = {}
        for shot in shots:
            # Find the sentence that contains this shot's start time
            beat_role = "setup"  # default
            for sentence in self.result.sentences if self.result else []:
                if sentence.start <= shot.start < sentence.end:
                    beat_role = sentence.beat_role
                    break
            
            if beat_role not in beat_groups:
                beat_groups[beat_role] = []
            beat_groups[beat_role].append(shot)
        
        # Create BGM track for each beat group
        current_time = 0.0
        for beat_role in self.BEAT_ROLES:
            if beat_role in beat_groups:
                group_shots = beat_groups[beat_role]
                start_time = group_shots[0].start
                end_time = group_shots[-1].end
                
                # Use predefined mood or default
                mood = self.BGM_MOODS.get(beat_role, "neutral")
                
                bgm_tracks.append(BGMTrack(
                    id=f"bgm_{beat_role}",
                    path=f"assets/music/bgm/{mood}.mp3",
                    mood=mood,
                    start_time=start_time,
                    end_time=end_time,
                    volume_db=BGM_DUCKING_DB
                ))
        
        return bgm_tracks
    
    def _plan_sfx_events(self, sentences: List[Sentence], shots: List[Shot]) -> List[SFXEvent]:
        """
        Step 9: Plan SFX events based on content.
        """
        sfx_events = []
        event_id = 1
        
        # Check for SFX triggers in sentences
        for sentence in sentences:
            text_lower = sentence.text.lower()
            
            for trigger, sfx_type in self.SFX_TRIGGERS.items():
                if trigger in text_lower:
                    sfx_events.append(SFXEvent(
                        id=f"sfx_{event_id}",
                        sfx_type=sfx_type,
                        timestamp=sentence.start,
                        duration=0.5,
                        volume_db=-15.0
                    ))
                    event_id += 1
                    break  # One SFX per sentence max
        
        # Add SFX for shot transitions
        for i, shot in enumerate(shots):
            if i > 0 and i % 3 == 0:  # Every 3rd shot transition
                sfx_events.append(SFXEvent(
                    id=f"sfx_transition_{i}",
                    sfx_type="cartoon_whoosh",
                    timestamp=shot.start,
                    duration=0.3,
                    volume_db=-18.0
                ))
                event_id += 1
        
        return sfx_events
    
    def _calculate_mean_duration(self, shots: List[Shot]) -> float:
        """Calculate mean shot duration."""
        if not shots:
            return 0.0
        return sum(shot.duration for shot in shots) / len(shots)
    
    def _calculate_sfx_density(self, sfx_events: List[SFXEvent]) -> float:
        """Calculate SFX events per minute."""
        if not sfx_events:
            return 0.0
        last_time = max(e.timestamp for e in sfx_events)
        return len(sfx_events) / (last_time / 60) if last_time > 0 else 0.0
    
    def _validate_results(
        self,
        hook: Hook,
        open_loops: List[OpenLoop],
        sentences: List[Sentence],
        shots: List[Shot],
        metaphors: List[Metaphor],
        humor_lines: List[HumorLine],
        sfx_events: List[SFXEvent]
    ) -> Dict[str, Any]:
        """
        Validate all results against algorithm requirements.
        """
        validation = {
            "passed": True,
            "checks": {},
            "warnings": [],
            "errors": []
        }
        
        # Check 1: Hook duration
        hook_ok = hook.duration <= MAX_HOOK_DURATION
        validation["checks"]["hook_duration"] = {
            "passed": hook_ok,
            "value": hook.duration,
            "target": f"<={MAX_HOOK_DURATION}s"
        }
        if not hook_ok:
            validation["passed"] = False
            validation["errors"].append(f"Hook duration {hook.duration}s exceeds max {MAX_HOOK_DURATION}s")
        
        # Check 2: Open loops count
        loops_ok = len(open_loops) >= MIN_OPEN_LOOPS
        validation["checks"]["open_loops_count"] = {
            "passed": loops_ok,
            "value": len(open_loops),
            "target": f">={MIN_OPEN_LOOPS}"
        }
        if not loops_ok:
            validation["warnings"].append(f"Only {len(open_loops)} open loops, recommend >=4")
        
        # Check 3: Short sentence percentage
        total_sentences = len(sentences)
        short_sentences = sum(1 for s in sentences if s.is_short)
        short_pct = (short_sentences / total_sentences * 100) if total_sentences > 0 else 0
        short_ok = short_pct >= TARGET_SHORT_SENTENCE_PCT * 100
        validation["checks"]["short_sentence_pct"] = {
            "passed": short_ok,
            "value": f"{short_pct:.1f}%",
            "target": f">={TARGET_SHORT_SENTENCE_PCT * 100:.0f}%"
        }
        if not short_ok:
            validation["warnings"].append(f"Short sentence percentage {short_pct:.1f}% < {TARGET_SHORT_SENTENCE_PCT * 100:.0f}%")
        
        # Check 4: Shot duration
        mean_duration = self._calculate_mean_duration(shots)
        duration_ok = MIN_SHOT_DURATION <= mean_duration <= MAX_SHOT_DURATION
        validation["checks"]["mean_shot_duration"] = {
            "passed": duration_ok,
            "value": f"{mean_duration:.2f}s",
            "target": f"{MIN_SHOT_DURATION}-{MAX_SHOT_DURATION}s"
        }
        if not duration_ok:
            validation["warnings"].append(f"Mean shot duration {mean_duration:.2f}s outside {MIN_SHOT_DURATION}-{MAX_SHOT_DURATION}s range")
        
        # Check 5: Words per shot
        max_words = max((shot.word_count for shot in shots), default=0)
        words_ok = max_words <= MAX_WORDS_PER_SHOT
        validation["checks"]["max_words_per_shot"] = {
            "passed": words_ok,
            "value": max_words,
            "target": f"<={MAX_WORDS_PER_SHOT}"
        }
        if not words_ok:
            validation["errors"].append(f"Max words per shot {max_words} exceeds {MAX_WORDS_PER_SHOT}")
            validation["passed"] = False
        
        # Check 6: Metaphors count
        metaphors_ok = len(metaphors) >= 1
        validation["checks"]["metaphors_count"] = {
            "passed": metaphors_ok,
            "value": len(metaphors),
            "target": ">=1"
        }
        if not metaphors_ok:
            validation["warnings"].append(f"Only {len(metaphors)} metaphors, recommend >=1")
        
        # Check 7: Humor lines count
        humor_ok = len(humor_lines) >= 4
        validation["checks"]["humor_lines_count"] = {
            "passed": humor_ok,
            "value": len(humor_lines),
            "target": ">=4"
        }
        if not humor_ok:
            validation["warnings"].append(f"Only {len(humor_lines)} humor lines, recommend >=4")
        
        # Check 8: SFX density
        sfx_density = self._calculate_sfx_density(sfx_events)
        sfx_ok = SFX_DENSITY_PER_MINUTE * 0.8 <= sfx_density <= SFX_DENSITY_PER_MINUTE * 1.2
        validation["checks"]["sfx_density"] = {
            "passed": sfx_ok,
            "value": f"{sfx_density:.1f}/min",
            "target": f"~{SFX_DENSITY_PER_MINUTE}/min"
        }
        if not sfx_ok:
            validation["warnings"].append(f"SFX density {sfx_density:.1f}/min outside 6-12/min range")
        
        return validation


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Apply UNIVERSAL_VIDEO_ALGORITHM to generate production-ready assets"
    )
    parser.add_argument(
        "--topic",
        type=str,
        required=True,
        help="Video topic (e.g., 'History of Nvidia', 'How Bitcoin Works')"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/video_algorithm_output",
        help="Output directory for generated assets"
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save results to JSON file"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate existing results (for testing)"
    )
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    
    # Initialize agent
    agent = VideoAlgorithmAgent(output_dir, args.topic)

    if args.validate_only:
        target_json = output_dir / f"{args.topic.replace(' ', '_')}_algorithm_result.json"
        if not target_json.exists():
            target_json = output_dir / "algorithm_result.json"
        if target_json.exists():
            data = json.loads(target_json.read_text(encoding="utf-8"))
            print("\n" + "=" * 60)
            print(f"VALIDATING EXISTING RESULTS: {target_json}")
            print("=" * 60)
            val = data.get("validation", {})
            print(f"Overall: {'PASSED' if val.get('passed') else 'FAILED'}")
            for k, v in val.get("checks", {}).items():
                status = "✓" if v.get("passed") else "✗"
                print(f"  {status} {k}: {v.get('value')} (target: {v.get('target')})")
            if val.get("errors"):
                print("Errors:")
                for err in val["errors"]:
                    print(f"  ✗ {err}")
            if val.get("warnings"):
                print("Warnings:")
                for w in val["warnings"]:
                    print(f"  ⚠ {w}")
            print("=" * 60)
            return
        else:
            print(f"Cannot find existing results in {output_dir} to validate.")
            return
    
    # Apply algorithm
    result = agent.apply()
    
    # Print summary
    print("\n" + "=" * 60)
    print("UNIVERSAL_VIDEO_ALGORITHM RESULTS")
    print("=" * 60)
    print(f"Topic: {result.topic}")
    print(f"\nHook:")
    print(f"  Paradox: {result.hook.paradox}")
    print(f"  Question: {result.hook.question}")
    print(f"  Duration: {result.hook.duration}s")
    
    print(f"\nOpen Loops: {len(result.open_loops)}")
    for loop in result.open_loops:
        print(f"  Loop {loop.id}: {loop.question[:50]}...")
    
    print(f"\nSentences: {len(result.sentences)}")
    short_count = sum(1 for s in result.sentences if s.is_short)
    print(f"  Short sentences: {short_count} ({short_count/len(result.sentences)*100:.1f}%)")
    
    print(f"\nShots: {len(result.shots)}")
    mean_duration = agent._calculate_mean_duration(result.shots)
    print(f"  Mean duration: {mean_duration:.2f}s")
    max_words = max((s.word_count for s in result.shots), default=0)
    print(f"  Max words per shot: {max_words}")
    
    print(f"\nMetaphors: {len(result.metaphors)}")
    print(f"Humor lines: {len(result.humor_lines)}")
    print(f"Characters: {len(result.characters)}")
    print(f"BGM tracks: {len(result.bgm_tracks)}")
    print(f"SFX events: {len(result.sfx_events)}")
    
    print(f"\nValidation:")
    print(f"  Overall: {'PASSED' if result.validation.get('passed') else 'FAILED'}")
    for check_name, check_data in result.validation.get("checks", {}).items():
        status = "✓" if check_data["passed"] else "✗"
        print(f"  {status} {check_name}: {check_data['value']} (target: {check_data['target']})")
    
    if result.validation.get("errors"):
        print(f"\nErrors:")
        for error in result.validation["errors"]:
            print(f"  ✗ {error}")
    
    if result.validation.get("warnings"):
        print(f"\nWarnings:")
        for warning in result.validation["warnings"]:
            print(f"  ⚠ {warning}")
    
    # Save if requested
    if args.save:
        result.save(output_dir)
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
