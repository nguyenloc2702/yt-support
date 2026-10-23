"""
Character Consistency Manager for Video Production Pipeline

Provides character reference image management to ensure consistent
character appearance across all video shots.

This addresses Bottleneck #2 from the UNIVERSAL_VIDEO_ALGORITHM deployment.

Usage:
    from pipeline.character_manager import CharacterManager
    
    manager = CharacterManager()
    char_id = manager.create_character("Elon Musk", "CEO of Tesla and SpaceX")
    ref_image = manager.get_reference_image(char_id)
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.exceptions import AnalysisError

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Character:
    """A character with reference image and metadata."""
    id: str
    name: str
    description: str
    reference_image_path: Optional[Path] = None
    first_appearance_ms: float = 0.0
    shot_ids: List[int] = field(default_factory=list)
    visual_features: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "reference_image_path": str(self.reference_image_path) if self.reference_image_path else None,
            "first_appearance_ms": self.first_appearance_ms,
            "shot_ids": self.shot_ids,
            "visual_features": self.visual_features
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Character":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            reference_image_path=Path(data["reference_image_path"]) if data["reference_image_path"] else None,
            first_appearance_ms=data.get("first_appearance_ms", 0.0),
            shot_ids=data.get("shot_ids", []),
            visual_features=data.get("visual_features", {})
        )


@dataclass
class CharacterDatabase:
    """Complete character database for a video."""
    characters: Dict[str, Character] = field(default_factory=dict)
    video_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_id": self.video_id,
            "characters": {cid: char.to_dict() for cid, char in self.characters.items()}
        }
    
    def save(self, path: Path):
        """Save database to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, path: Path) -> "CharacterDatabase":
        """Load database from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        db = cls(
            video_id=data.get("video_id", ""),
            characters={}
        )
        
        for cid, char_data in data.get("characters", {}).items():
            db.characters[cid] = Character.from_dict(char_data)
        
        return db


# =============================================================================
# CHARACTER MANAGER
# =============================================================================

class CharacterManagerError(AnalysisError):
    """Raised when character management fails."""
    pass


class CharacterManager:
    """
    Manages character consistency for video production.
    
    Features:
    - Create and store character reference images
    - Track character appearances across shots
    - Validate character consistency
    - Generate character descriptions for image generation
    """
    
    # Default character directory
    DEFAULT_CHARACTER_DIR = Path("assets/characters")
    
    # Visual feature keys for character descriptions
    VISUAL_FEATURE_KEYS = [
        "gender",
        "age",
        "ethnicity", 
        "hair_color",
        "hair_style",
        "eye_color",
        "facial_hair",
        "clothing_style",
        "accessories",
        "expression"
    ]
    
    def __init__(self, character_dir: Optional[Path] = None):
        """
        Initialize the character manager.
        
        Args:
            character_dir: Directory to store character assets.
                         Defaults to DEFAULT_CHARACTER_DIR.
        """
        self.character_dir = Path(character_dir) if character_dir else self.DEFAULT_CHARACTER_DIR
        self.character_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory character cache
        self._characters: Dict[str, Character] = {}
        
        # Load existing characters from disk
        self._load_existing_characters()
        
        logger.info(f"CharacterManager initialized at {self.character_dir}")
    
    def _load_existing_characters(self):
        """Load existing character directories."""
        for char_dir in self.character_dir.iterdir():
            if char_dir.is_dir():
                char_id = char_dir.name
                ref_image = char_dir / "reference.png"
                profile_file = char_dir / "profile.json"
                
                if profile_file.exists():
                    try:
                        with open(profile_file, "r", encoding="utf-8") as f:
                            profile = json.load(f)
                        
                        character = Character(
                            id=char_id,
                            name=profile.get("name", char_id),
                            description=profile.get("description", ""),
                            reference_image_path=ref_image if ref_image.exists() else None,
                            first_appearance_ms=profile.get("first_appearance_ms", 0.0),
                            shot_ids=profile.get("shot_ids", []),
                            visual_features=profile.get("visual_features", {})
                        )
                        self._characters[char_id] = character
                        logger.debug(f"Loaded character: {char_id}")
                    except Exception as e:
                        logger.warning(f"Failed to load character {char_id}: {e}")
    
    def create_character(
        self,
        name: str,
        description: str = "",
        visual_features: Optional[Dict[str, str]] = None,
        first_appearance_ms: float = 0.0
    ) -> str:
        """
        Create a new character with reference image directory.
        
        Args:
            name: Character name
            description: Character description
            visual_features: Dictionary of visual features (gender, age, etc.)
            first_appearance_ms: First appearance timestamp in milliseconds
        
        Returns:
            Character ID (UUID string)
        """
        char_id = str(uuid.uuid4())
        char_dir = self.character_dir / char_id
        char_dir.mkdir(parents=True, exist_ok=True)
        
        # Create profile
        profile = {
            "id": char_id,
            "name": name,
            "description": description,
            "visual_features": visual_features or {},
            "first_appearance_ms": first_appearance_ms,
            "shot_ids": []
        }
        
        profile_path = char_dir / "profile.json"
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2, ensure_ascii=False)
        
        # Create character object
        character = Character(
            id=char_id,
            name=name,
            description=description,
            reference_image_path=char_dir / "reference.png",
            first_appearance_ms=first_appearance_ms,
            visual_features=visual_features or {}
        )
        
        self._characters[char_id] = character
        
        logger.info(f"Created character {name} with ID: {char_id}")
        return char_id
    
    def get_character(self, char_id: str) -> Optional[Character]:
        """Get a character by ID."""
        return self._characters.get(char_id)
    
    def get_reference_image(self, char_id: str) -> Optional[Path]:
        """
        Get the reference image path for a character.
        
        Args:
            char_id: Character ID
        
        Returns:
            Path to reference image, or None if not found
        """
        character = self.get_character(char_id)
        if character and character.reference_image_path:
            if character.reference_image_path.exists():
                return character.reference_image_path
        
        # Try to find the image file
        char_dir = self.character_dir / char_id
        for ext in ["*.png", "*.jpg", "*.jpeg"]:
            for img_file in char_dir.glob(ext):
                return img_file
        
        return None
    
    def set_reference_image(self, char_id: str, image_path: Path) -> Path:
        """
        Set the reference image for a character.
        
        Args:
            char_id: Character ID
            image_path: Path to the reference image
        
        Returns:
            Path to the stored reference image
        
        Raises:
            CharacterManagerError: If character not found
        """
        character = self.get_character(char_id)
        if not character:
            raise CharacterManagerError(f"Character {char_id} not found")
        
        char_dir = self.character_dir / char_id
        char_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy or move the image
        ref_image_path = char_dir / "reference.png"
        
        try:
            import shutil
            if image_path != ref_image_path:
                shutil.copy2(image_path, ref_image_path)
            
            # Update character
            character.reference_image_path = ref_image_path
            
            # Update profile
            profile_path = char_dir / "profile.json"
            if profile_path.exists():
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
                profile["reference_image"] = str(ref_image_path)
                with open(profile_path, "w", encoding="utf-8") as f:
                    json.dump(profile, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Set reference image for character {char_id}: {ref_image_path}")
            return ref_image_path
            
        except Exception as e:
            raise CharacterManagerError(f"Failed to set reference image: {e}")
    
    def add_shot_appearance(self, char_id: str, shot_id: int, timestamp_ms: float = 0.0):
        """
        Add a shot appearance for a character.
        
        Args:
            char_id: Character ID
            shot_id: Shot ID where character appears
            timestamp_ms: Timestamp in milliseconds
        """
        character = self.get_character(char_id)
        if not character:
            raise CharacterManagerError(f"Character {char_id} not found")
        
        if shot_id not in character.shot_ids:
            character.shot_ids.append(shot_id)
        
        if character.first_appearance_ms == 0.0 or timestamp_ms < character.first_appearance_ms:
            character.first_appearance_ms = timestamp_ms
        
        # Update profile on disk
        char_dir = self.character_dir / char_id
        profile_path = char_dir / "profile.json"
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                profile = json.load(f)
            profile["shot_ids"] = character.shot_ids
            profile["first_appearance_ms"] = character.first_appearance_ms
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
        
        logger.debug(f"Added shot {shot_id} appearance for character {char_id}")
    
    def get_characters_in_shot(self, shot_id: int) -> List[Character]:
        """Get all characters that appear in a specific shot."""
        return [char for char in self._characters.values() if shot_id in char.shot_ids]
    
    def get_all_characters(self) -> List[Character]:
        """Get all characters."""
        return list(self._characters.values())
    
    def delete_character(self, char_id: str) -> bool:
        """
        Delete a character and all associated files.
        
        Args:
            char_id: Character ID
        
        Returns:
            True if deleted successfully
        """
        import shutil
        
        char_dir = self.character_dir / char_id
        if char_dir.exists():
            try:
                shutil.rmtree(char_dir)
                if char_id in self._characters:
                    del self._characters[char_id]
                logger.info(f"Deleted character {char_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete character {char_id}: {e}")
                return False
        return False
    
    def generate_character_prompt(self, char_id: str) -> str:
        """
        Generate a detailed prompt for character image generation.
        
        Args:
            char_id: Character ID
        
        Returns:
            Prompt string for image generation
        """
        character = self.get_character(char_id)
        if not character:
            raise CharacterManagerError(f"Character {char_id} not found")
        
        # Build prompt from visual features
        prompt_parts = [
            f"Character: {character.name}",
            f"Description: {character.description}",
        ]
        
        for key, value in character.visual_features.items():
            prompt_parts.append(f"{key.replace('_', ' ').title()}: {value}")
        
        # Add style requirements (crayon Style Bible — matches Veo/image prompts)
        prompt_parts.extend([
            "Style: Consistent appearance across all shots",
            "Quality: whimsical hand-drawn editorial cartoon, rough wax crayon and colored pencil sketch strokes, grainy paper texture, flat 2D minimalist",
            "Pose: Front-facing portrait for reference",
            "Lighting: Even, neutral lighting",
            "Background: Plain off-white textured sketchbook paper",
            "Expression: Neutral, suitable for various contexts",
        ])

        return " | ".join(prompt_parts)
    
    def validate_consistency(self, char_id: str, new_image_path: Path) -> Tuple[bool, str]:
        """
        Validate that a new image matches the character reference.
        
        Note: This is a placeholder for actual face matching implementation.
        In production, this would use face recognition to compare images.
        
        Args:
            char_id: Character ID
            new_image_path: Path to new image to validate
        
        Returns:
            Tuple of (is_consistent, message)
        """
        ref_image = self.get_reference_image(char_id)
        if not ref_image:
            return True, "No reference image exists yet"
        
        # Placeholder: In production, implement actual face matching
        # For now, always return True as a placeholder
        return True, "Consistency check passed (placeholder implementation)"
    
    def export_database(self, output_path: Path) -> Path:
        """
        Export the complete character database to a JSON file.
        
        Args:
            output_path: Path to save the database
        
        Returns:
            Path to the saved database file
        """
        db = CharacterDatabase(
            video_id="current",
            characters=self._characters.copy()
        )
        db.save(output_path)
        logger.info(f"Exported character database to: {output_path}")
        return output_path
    
    def import_database(self, input_path: Path) -> int:
        """
        Import a character database from a JSON file.
        
        Args:
            input_path: Path to the database file
        
        Returns:
            Number of characters imported
        """
        db = CharacterDatabase.load(input_path)
        
        for char_id, character in db.characters.items():
            self._characters[char_id] = character
            
            # Ensure character directory exists
            char_dir = self.character_dir / char_id
            char_dir.mkdir(parents=True, exist_ok=True)
            
            # Save profile
            profile_path = char_dir / "profile.json"
            with open(profile_path, "w", encoding="utf-8") as f:
                json.dump(character.to_dict(), f, indent=2, ensure_ascii=False)
        
        logger.info(f"Imported {len(db.characters)} characters from: {input_path}")
        return len(db.characters)
    
    def clear(self):
        """Clear all characters from memory and disk."""
        import shutil
        
        for char_id in list(self._characters.keys()):
            self.delete_character(char_id)
        
        self._characters.clear()
        logger.info("Cleared all characters")


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def detect_characters_in_text(text: str) -> List[Dict[str, str]]:
    """
    Detect potential characters in text using name patterns.
    
    Args:
        text: Text to analyze
    
    Returns:
        List of potential character dictionaries with name and context
    """
    import re
    
    # Name patterns (capitalized words)
    name_patterns = [
        r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b",  # "John Smith"
        r"\b([A-Z][a-z]+)\b",  # Single capitalized word
    ]
    
    potential_characters = []
    
    for pattern in name_patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            name = match.group(1)
            # Skip common false positives
            skip_words = ["I", "We", "You", "He", "She", "They", "It", "The", "A", "An"]
            if name not in skip_words and len(name) > 1:
                context = text[max(0, match.start() - 20):match.end() + 20]
                potential_characters.append({
                    "name": name,
                    "context": context
                })
    
    # Deduplicate
    seen = set()
    unique_characters = []
    for char in potential_characters:
        if char["name"] not in seen:
            seen.add(char["name"])
            unique_characters.append(char)
    
    return unique_characters


def detect_characters_in_text_llm(text: str) -> List[Dict[str, str]]:
    """
    LLM-based character extraction (Phase 1: replaces the regex approach that
    flooded Gate 3 with fake characters on English scripts).

    Returns a list of {"name", "context", "descriptor"} dicts. Empty list means
    the script has no real recurring characters (Gate 3 then auto-passes).
    Raises RuntimeError when the xKiro gateway is unavailable — callers must
    surface that instead of guessing characters with heuristics.
    """
    import os

    cleaned = (text or "").strip()
    if not cleaned:
        return []

    if os.environ.get("PYTEST_CURRENT_TEST"):
        # Never call the gateway during automated test suites; callers mock this.
        raise RuntimeError("LLM character detection disabled under pytest")

    from pipeline.llm_client import chat_json
    from services.settings_service import get_xkiro_generation_model

    system_prompt = (
        "Bạn là chuyên gia phân tích kịch bản. Trích xuất danh sách NHÂN VẬT THỰC "
        "(người hoặc thực thể được kể chuyện một cách rõ ràng, xuất hiện lặp lại hoặc "
        "đóng vai trò trung tâm) từ kịch bản. KHÔNG bịa nhân vật, KHÔNG tính đại từ, "
        "tên quốc gia, công ty trừ khi công ty đó được nhân cách hóa như nhân vật chính. "
        "Tối đa 6 nhân vật. Nếu không có nhân vật thực nào, trả về danh sách rỗng."
    )
    user_prompt = (
        f"KỊCH BẢN:\n\"\"\"{cleaned[:8000]}\"\"\"\n\n"
        "Trả về duy nhất JSON object:\n"
        '{"characters": [{"name": "Tên nhân vật", "context": "vai trò trong truyện", '
        '"descriptor": "mô tả ngoại hình tiếng Anh để vẽ minh họa cartoon crayon: '
        'đặc điểm khuôn mặt, tóc, trang phục, biểu cảm chủ đạo"}]}\n'
        "Nếu không có nhân vật: {\"characters\": []}"
    )

    try:
        parsed = chat_json(
            system_prompt,
            user_prompt,
            temperature=0.1,
            max_tokens=2000,
            model=get_xkiro_generation_model(),
        )
    except Exception as exc:
        raise RuntimeError(f"LLM character extraction failed: {exc}") from exc

    characters: List[Dict[str, str]] = []
    seen = set()
    for item in parsed.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        characters.append({
            "name": name,
            "context": str(item.get("context") or f"Nhân vật trong kịch bản: {name}"),
            "descriptor": str(item.get("descriptor") or name),
        })
    return characters[:6]


def extract_visual_features(text: str) -> Dict[str, str]:
    """
    Extract visual features from character description text.
    
    Args:
        text: Description text
    
    Returns:
        Dictionary of visual features
    """
    import re
    
    features = {}
    text_lower = text.lower()
    
    # Gender
    if any(word in text_lower for word in ["male", "man", "boy", "he", "him"]):
        features["gender"] = "male"
    elif any(word in text_lower for word in ["female", "woman", "girl", "she", "her"]):
        features["gender"] = "female"
    
    # Age
    age_matches = re.findall(r"(\d+)\s*(?:year|old|age)", text_lower)
    if age_matches:
        features["age"] = f"{age_matches[0]} years"
    
    # Hair
    for color in ["blonde", "brunette", "black", "brown", "red", "white", "gray"]:
        if color in text_lower:
            features["hair_color"] = color
            break
    
    # Clothing
    for style in ["suit", "casual", "formal", "uniform", "dress", "jeans", "shirt"]:
        if style in text_lower:
            features["clothing_style"] = style
            break
    
    return features


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """Command-line interface for character management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Character Manager for Video Production")
    parser.add_argument("--list", action="store_true", help="List all characters")
    parser.add_argument("--create", type=str, help="Create a new character (name)")
    parser.add_argument("--describe", type=str, help="Set character description")
    parser.add_argument("--delete", type=str, help="Delete a character (ID)")
    parser.add_argument("--export", type=str, help="Export database to file")
    parser.add_argument("--import-file", type=str, dest="import_file", help="Import database from file")
    parser.add_argument("--char-dir", type=str, default=None, help="Character directory")
    
    args = parser.parse_args()
    
    manager = CharacterManager(character_dir=args.char_dir)
    
    if args.list:
        print("Characters:")
        for char in manager.get_all_characters():
            ref_img = manager.get_reference_image(char.id)
            print(f"  {char.id}: {char.name} ({char.description})")
            print(f"    Reference: {ref_img if ref_img else 'None'}")
            print(f"    Shots: {char.shot_ids}")
    
    elif args.create:
        char_id = manager.create_character(args.create)
        print(f"Created character with ID: {char_id}")
    
    elif args.delete:
        success = manager.delete_character(args.delete)
        print(f"Deleted: {success}")
    
    elif args.export:
        output_path = Path(args.export)
        manager.export_database(output_path)
        print(f"Exported to: {output_path}")
    
    elif args.import_file:
        input_path = Path(args.import_file)
        count = manager.import_database(input_path)
        print(f"Imported {count} characters")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
