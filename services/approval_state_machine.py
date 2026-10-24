"""Four-Gate Approval State Machine for Video AI Production Pipeline.

Implements the architecture specified in plans/four_gate_approval_test_plan.md:
- Gate 1: Script Approval (VI and EN dual-scripts, beats, required characters)
- Gate 2: Audio Approval (VI and EN voiceovers, independent tracking, parallel generation)
- Gate 3: Character Sheet Approval (canonical reference images for required characters)
- Gate 4: Video Readiness & Generation Manifest (immutable lock before costly shot generation)

Quy tắc cốt lõi:
1. Fail-closed: Video generation chỉ chạy khi:
   script_approved AND vi_audio_approved AND en_audio_approved AND
   all_required_characters_approved AND timeline_valid AND required_assets_exist.
2. Versioning & Invalidation: Mọi artifact gắn với script_version/content_hash.
   Khi script đổi -> invalidate audio, characters, timeline, và video readiness.
3. Không fallback âm thầm sang text-only prompt hoặc audio chưa duyệt.
"""

import contextlib
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("approval_state_machine")


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 10.0):
    """Cross-process file lock protecting approval_state.json from concurrent write corruption."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start_time = time.time()
    handle = None
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt
                handle = open(lock_path, "a+b")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            else:
                import fcntl
                handle = open(lock_path, "a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
        except (IOError, OSError):
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
                handle = None
            if time.time() - start_time > timeout:
                raise TimeoutError(f"Could not acquire file lock on {lock_path} within {timeout}s")
            time.sleep(0.05)
    try:
        yield
    finally:
        if handle:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass


# =============================================================================
# ENUMS
# =============================================================================

class ScriptStatus(str, Enum):
    DRAFT = "draft"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"


class AudioStatus(str, Enum):
    PENDING = "pending"
    GENERATED = "generated"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


class CharacterStatus(str, Enum):
    MISSING = "missing"
    GENERATED = "generated"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"


class VideoReadinessStatus(str, Enum):
    NOT_READY = "NOT_READY"
    READY = "READY"
    APPROVED = "APPROVED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def compute_content_hash(content: Any) -> str:
    """Computes a deterministic SHA-256 hash of text or serialized JSON object."""
    if isinstance(content, str):
        payload = content.encode("utf-8")
    elif isinstance(content, (dict, list)):
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
    elif isinstance(content, bytes):
        payload = content
    else:
        payload = str(content).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def compute_file_hash(file_path: Optional[Path]) -> Optional[str]:
    """Computes SHA-256 of a file if it exists."""
    if not file_path:
        return None
    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception as e:
        logger.warning("Failed to compute hash for %s: %s", file_path, e)
        return None


# =============================================================================
# STATE DATACLASSES
# =============================================================================

@dataclass
class BeatIntent:
    """A single story beat with stable ID."""
    id: int
    text_vi: str
    text_en: str
    emotion: str = "neutral"
    character_ids: List[str] = field(default_factory=list)
    duration: float = 4.75

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BeatIntent":
        return cls(
            id=int(data.get("id", 0)),
            text_vi=str(data.get("text_vi", "")),
            text_en=str(data.get("text_en", "")),
            emotion=str(data.get("emotion", "neutral")),
            character_ids=list(data.get("character_ids", [])),
            duration=float(data.get("duration", 4.75)),
        )


@dataclass
class AudioTrackState:
    """Tracks state and approval for a single voiceover language."""
    language: str  # "vi" or "en"
    status: AudioStatus = AudioStatus.PENDING
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    duration: float = 0.0
    voice_id: str = ""
    script_version: int = 1
    script_hash: str = ""
    approved_at: Optional[float] = None
    approver: Optional[str] = None
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioTrackState":
        status_val = data.get("status", AudioStatus.PENDING.value)
        try:
            status = AudioStatus(status_val)
        except ValueError:
            status = AudioStatus.PENDING
        return cls(
            language=data.get("language", "vi"),
            status=status,
            file_path=data.get("file_path"),
            file_hash=data.get("file_hash"),
            duration=float(data.get("duration", 0.0)),
            voice_id=data.get("voice_id", ""),
            script_version=int(data.get("script_version", 1)),
            script_hash=data.get("script_hash", ""),
            approved_at=data.get("approved_at"),
            approver=data.get("approver"),
            rejection_reason=data.get("rejection_reason"),
        )


@dataclass
class CharacterSheetState:
    """Tracks canonical reference image and approval for a required character."""
    id: str
    name: str
    description: str
    visual_features: Dict[str, Any] = field(default_factory=dict)
    reference_image_path: Optional[str] = None
    image_hash: Optional[str] = None
    shot_ids: List[int] = field(default_factory=list)
    status: CharacterStatus = CharacterStatus.MISSING
    script_version: int = 1
    approved_at: Optional[float] = None
    approver: Optional[str] = None
    is_optional: bool = False
    rejection_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CharacterSheetState":
        status_val = data.get("status", CharacterStatus.MISSING.value)
        try:
            status = CharacterStatus(status_val)
        except ValueError:
            status = CharacterStatus.MISSING
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            visual_features=data.get("visual_features", {}),
            reference_image_path=data.get("reference_image_path"),
            image_hash=data.get("image_hash"),
            shot_ids=data.get("shot_ids", []),
            status=status,
            script_version=int(data.get("script_version", 1)),
            approved_at=data.get("approved_at"),
            approver=data.get("approver"),
            is_optional=bool(data.get("is_optional", False)),
            rejection_reason=data.get("rejection_reason"),
        )


@dataclass
class VideoGenerationManifest:
    """Immutable manifest locked when Gate 4 is approved."""
    job_id: str
    manifest_id: str
    script_version: int
    script_hash: str
    audio_vi_hash: str
    audio_en_hash: str
    character_sheet_hashes: Dict[str, str]
    shot_manifest: List[Dict[str, Any]]
    manifest_hash: str
    created_at: float
    approver: str
    bgm_plan: Dict[str, Any] = field(default_factory=dict)
    sfx_plan: Dict[str, Any] = field(default_factory=dict)
    provider_config: Dict[str, Any] = field(default_factory=dict)
    locked: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoGenerationManifest":
        return cls(
            job_id=data["job_id"],
            manifest_id=data["manifest_id"],
            script_version=int(data["script_version"]),
            script_hash=data["script_hash"],
            audio_vi_hash=data["audio_vi_hash"],
            audio_en_hash=data["audio_en_hash"],
            character_sheet_hashes=dict(data.get("character_sheet_hashes", {})),
            shot_manifest=list(data.get("shot_manifest", [])),
            manifest_hash=data["manifest_hash"],
            created_at=float(data["created_at"]),
            approver=data.get("approver", ""),
            bgm_plan=dict(data.get("bgm_plan", {})),
            sfx_plan=dict(data.get("sfx_plan", {})),
            provider_config=dict(data.get("provider_config", {})),
            locked=bool(data.get("locked", True)),
        )


# =============================================================================
# FOUR GATE APPROVAL STATE MACHINE
# =============================================================================

class ApprovalStateMachine:
    """
    Manages the 4-gate lifecycle for an automated video job:
    Gate 1: Script VI/EN Approval
    Gate 2: Audio VI/EN Approval
    Gate 3: Character Sheet Approval
    Gate 4: Video Readiness & Immutable Generation Manifest
    """

    def __init__(self, project_dir: Path, job_id: str):
        self.project_dir = Path(project_dir)
        self.job_id = job_id
        self.state_file = self.project_dir / "approval_state.json"

        # Gate 1 — Script
        self.script_version: int = 1
        self.script_status_vi: ScriptStatus = ScriptStatus.DRAFT
        self.script_status_en: ScriptStatus = ScriptStatus.DRAFT
        self.script_hash: str = ""
        self.script_vi_text: str = ""
        self.script_en_text: str = ""
        self.required_characters: List[str] = []
        self.beats: List[BeatIntent] = []
        self.script_approved: bool = False
        self.script_approved_at: Optional[float] = None
        self.script_approver: Optional[str] = None
        self.script_audit_report: Dict[str, Any] = {}

        # Gate 2 — Audio VI / EN
        self.audio_vi: AudioTrackState = AudioTrackState(language="vi")
        self.audio_en: AudioTrackState = AudioTrackState(language="en")

        # Gate 3 — Characters
        self.characters: Dict[str, CharacterSheetState] = {}

        # Gate 4 — Video Readiness & Manifest
        self.video_status: VideoReadinessStatus = VideoReadinessStatus.NOT_READY
        self.readiness_errors: List[str] = []
        self.readiness_warnings: List[str] = []
        self.active_manifest: Optional[VideoGenerationManifest] = None
        self.shots_plan: List[Dict[str, Any]] = []

        # Load existing if available
        self.load()

    # -------------------------------------------------------------------------
    # PERSISTENCE
    # -------------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "project_dir": str(self.project_dir),
            "script_gate": {
                "version": self.script_version,
                "status_vi": self.script_status_vi.value,
                "status_en": self.script_status_en.value,
                "hash": self.script_hash,
                "script_vi_text": self.script_vi_text,
                "script_en_text": self.script_en_text,
                "required_characters": self.required_characters,
                "beats": [b.to_dict() for b in self.beats],
                "approved": self.script_approved,
                "approved_at": self.script_approved_at,
                "approver": self.script_approver,
                "audit_report": self.script_audit_report,
            },
            "audio_gate": {
                "vi": self.audio_vi.to_dict(),
                "en": self.audio_en.to_dict(),
                "gate_passed": self.is_audio_gate_passed(),
            },
            "character_gate": {
                "characters": {cid: char.to_dict() for cid, char in self.characters.items()},
                "gate_passed": self.is_character_gate_passed(),
            },
            "video_gate": {
                "status": self.video_status.value,
                "readiness_errors": self.readiness_errors,
                "readiness_warnings": self.readiness_warnings,
                "manifest": self.active_manifest.to_dict() if self.active_manifest else None,
                "shots_plan": self.shots_plan,
            },
            "updated_at": time.time(),
        }

    def save(self) -> Path:
        """Persists state to approval_state.json atomically with file locking."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        lock_file = self.project_dir / "approval_state.lock"
        with file_lock(lock_file):
            temp_file = self.project_dir / f"approval_state_{time.time_ns()}.tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
        return self.state_file

    def load(self) -> bool:
        """Loads state from approval_state.json if present with file locking."""
        if not self.state_file.exists():
            return False
        lock_file = self.project_dir / "approval_state.lock"
        try:
            with file_lock(lock_file):
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
            sg = data.get("script_gate", {})
            self.script_version = int(sg.get("version", 1))
            self.script_status_vi = ScriptStatus(sg.get("status_vi", ScriptStatus.DRAFT.value))
            self.script_status_en = ScriptStatus(sg.get("status_en", ScriptStatus.DRAFT.value))
            self.script_hash = sg.get("hash", "")
            self.script_vi_text = sg.get("script_vi_text", "")
            self.script_en_text = sg.get("script_en_text", "")
            self.required_characters = list(sg.get("required_characters", []))
            self.beats = [BeatIntent.from_dict(b) for b in sg.get("beats", [])]
            self.script_approved = bool(sg.get("approved", False))
            self.script_approved_at = sg.get("approved_at")
            self.script_approver = sg.get("approver")
            self.script_audit_report = sg.get("audit_report", {})

            ag = data.get("audio_gate", {})
            if "vi" in ag:
                self.audio_vi = AudioTrackState.from_dict(ag["vi"])
            if "en" in ag:
                self.audio_en = AudioTrackState.from_dict(ag["en"])

            cg = data.get("character_gate", {})
            self.characters = {
                cid: CharacterSheetState.from_dict(cdata)
                for cid, cdata in cg.get("characters", {}).items()
            }

            vg = data.get("video_gate", {})
            status_val = vg.get("status", VideoReadinessStatus.NOT_READY.value)
            try:
                self.video_status = VideoReadinessStatus(status_val)
            except ValueError:
                self.video_status = VideoReadinessStatus.NOT_READY
            self.readiness_errors = list(vg.get("readiness_errors", []))
            self.readiness_warnings = list(vg.get("readiness_warnings", []))
            m_data = vg.get("manifest")
            self.active_manifest = VideoGenerationManifest.from_dict(m_data) if m_data else None
            self.shots_plan = list(vg.get("shots_plan", []))

            return True
        except Exception as e:
            logger.warning("Failed to load approval state from %s: %s", self.state_file, e)
            return False

    # -------------------------------------------------------------------------
    # GATE 1: SCRIPT APPROVAL
    # -------------------------------------------------------------------------

    def set_script(
        self,
        script_vi: str,
        script_en: str,
        beats: Optional[List[BeatIntent]] = None,
        required_characters: Optional[List[str]] = None,
        audit_report: Optional[Dict[str, Any]] = None,
    ):
        """Sets or updates the initial dual-script draft."""
        new_hash = compute_content_hash({"vi": script_vi, "en": script_en, "beats": [b.to_dict() for b in (beats or [])]})
        if self.script_hash and new_hash == self.script_hash:
            # Idempotent call: identical content does not revoke approval or bump version
            self.beats = beats or self.beats
            self.required_characters = required_characters or self.required_characters
            self.script_audit_report = audit_report or self.script_audit_report
            self.save()
            return

        old_char_ids = set(self.characters.keys())
        new_char_ids = {str(c).lower().replace(" ", "_") for c in (required_characters or [])}
        characters_changed = (old_char_ids != new_char_ids)

        if self.script_hash and new_hash != self.script_hash:
            self._invalidate_downstream(reason="Script content updated", characters_changed=characters_changed)
            self.script_version += 1

        self.script_vi_text = script_vi.strip()
        self.script_en_text = script_en.strip()
        self.script_hash = new_hash
        self.beats = beats or []
        self.required_characters = required_characters or []
        self.script_audit_report = audit_report or {}
        self.script_status_vi = ScriptStatus.DRAFT
        self.script_status_en = ScriptStatus.DRAFT
        self.script_approved = False
        self.script_approved_at = None
        self.script_approver = None
        self.save()

    def edit_beat(self, beat_id: int, new_text_vi: Optional[str] = None, new_text_en: Optional[str] = None):
        """Modifies a specific beat. Automatically invalidates approval and downstream assets without resetting characters."""
        found = False
        for b in self.beats:
            if b.id == beat_id:
                if new_text_vi is not None:
                    b.text_vi = new_text_vi.strip()
                if new_text_en is not None:
                    b.text_en = new_text_en.strip()
                found = True
                break

        if not found:
            raise KeyError(f"Beat ID {beat_id} not found")

        # Update full script texts
        self.script_vi_text = "\n".join(b.text_vi for b in self.beats if b.text_vi)
        self.script_en_text = "\n".join(b.text_en for b in self.beats if b.text_en)
        self.script_version += 1
        self.script_hash = compute_content_hash({"vi": self.script_vi_text, "en": self.script_en_text, "beats": [b.to_dict() for b in self.beats]})
        self._invalidate_downstream(reason=f"Beat {beat_id} modified", characters_changed=False)
        self.save()

    def approve_script(self, approver: str = "user") -> Tuple[bool, List[str]]:
        """
        Validates and approves Gate 1 (Script).
        Requires non-empty VI and EN scripts, valid non-empty beats, and no duplicate IDs.
        """
        errors = []
        if not self.script_vi_text or not self.script_vi_text.strip():
            errors.append("Script thiếu bản tiếng Việt (VI).")
        if not self.script_en_text or not self.script_en_text.strip():
            errors.append("Script thiếu bản tiếng Anh (EN).")

        # Check beats
        if self.beats:
            seen_ids = set()
            for b in self.beats:
                if b.id in seen_ids:
                    errors.append(f"Trùng lặp beat ID: {b.id}")
                seen_ids.add(b.id)
                if not b.text_vi.strip() and not b.text_en.strip():
                    errors.append(f"Beat {b.id} rỗng cả 2 ngôn ngữ.")

        # Check audit report if provided
        if self.script_audit_report and not self.script_audit_report.get("is_valid", True):
            issues = self.script_audit_report.get("issues", [])
            errors.append(f"Script audit chưa đạt: {'; '.join(issues[:2])}")

        if errors:
            self.script_approved = False
            self.save()
            return False, errors

        self.script_status_vi = ScriptStatus.APPROVED
        self.script_status_en = ScriptStatus.APPROVED
        self.script_approved = True
        self.script_approved_at = time.time()
        self.script_approver = approver
        logger.info("Gate 1 (Script) approved for job %s by %s (version %d)", self.job_id, approver, self.script_version)

        # Transition downstream audio to PENDING for parallel generation
        if self.audio_vi.status in (AudioStatus.STALE, AudioStatus.PENDING):
            self.audio_vi.status = AudioStatus.PENDING
            self.audio_vi.script_version = self.script_version
            self.audio_vi.script_hash = self.script_hash
        if self.audio_en.status in (AudioStatus.STALE, AudioStatus.PENDING):
            self.audio_en.status = AudioStatus.PENDING
            self.audio_en.script_version = self.script_version
            self.audio_en.script_hash = self.script_hash

        # Initialize missing required characters in character gate
        for cname in self.required_characters:
            cid = cname.lower().replace(" ", "_")
            if cid not in self.characters:
                self.characters[cid] = CharacterSheetState(
                    id=cid,
                    name=cname,
                    description=f"Character {cname}",
                    status=CharacterStatus.MISSING,
                    script_version=self.script_version,
                )

        self.save()
        return True, []

    def reject_script(self, reason: str = "Rejected by user", approver: str = "user"):
        """Rejects Gate 1 (Script)."""
        self.script_status_vi = ScriptStatus.REJECTED
        self.script_status_en = ScriptStatus.REJECTED
        self.script_approved = False
        self.script_approver = approver
        self._invalidate_downstream(reason=f"Script rejected: {reason}")
        self.save()

    def is_script_gate_passed(self) -> bool:
        return bool(self.script_approved and self.script_vi_text and self.script_en_text)

    # -------------------------------------------------------------------------
    # GATE 2: AUDIO APPROVAL (VI / EN)
    # -------------------------------------------------------------------------

    def set_audio_generated(
        self,
        language: str,
        file_path: Path,
        duration: float,
        voice_id: str = "",
    ) -> bool:
        """Records generation of an audio track (VI or EN) after Gate 1 is passed."""
        if not self.is_script_gate_passed():
            raise RuntimeError("Cannot record generated audio before Gate 1 (Script) is approved")

        lang = language.lower()
        track = self.audio_vi if lang == "vi" else self.audio_en
        p = Path(file_path)

        track.file_path = str(p.resolve()) if p.exists() else str(p)
        track.file_hash = compute_file_hash(p)
        track.duration = duration
        track.voice_id = voice_id
        track.script_version = self.script_version
        track.script_hash = self.script_hash
        track.status = AudioStatus.GENERATED
        track.approved_at = None
        track.approver = None

        # Reset video readiness
        self._reset_video_readiness(reason=f"Audio {lang.upper()} regenerated")
        self.save()
        return True

    def approve_audio(self, language: str, approver: str = "user") -> Tuple[bool, List[str]]:
        """Approves audio for a specific language (VI or EN)."""
        lang = language.lower().strip()
        if lang not in ("vi", "en"):
            return False, [f"Ngôn ngữ audio không hợp lệ: '{language}'. Chỉ chấp nhận 'vi' hoặc 'en'."]

        errors = []
        if not self.is_script_gate_passed():
            errors.append("Không thể duyệt audio khi Script (Gate 1) chưa được duyệt.")

        track = self.audio_vi if lang == "vi" else self.audio_en

        if not track.file_path:
            errors.append(f"File audio {lang.upper()} chưa được tạo.")
        else:
            p = Path(track.file_path)
            if not p.is_file():
                errors.append(f"File audio {lang.upper()} không tồn tại trên đĩa: {track.file_path}")
            elif p.stat().st_size < 100:
                errors.append(f"File audio {lang.upper()} rỗng hoặc hỏng.")

        if track.script_version != self.script_version or (track.script_hash and track.script_hash != self.script_hash):
            errors.append(f"File audio {lang.upper()} đã lỗi thời (thuộc script v{track.script_version}, hiện tại v{self.script_version}).")

        if track.duration <= 0.0:
            errors.append(f"Thời lượng audio {lang.upper()} không hợp lệ ({track.duration}s).")
        else:
            # Check duration tolerance against planned timeline. Baseline is the
            # shots_plan the script was built on (beats carry a 5.0s placeholder).
            planned_dur = sum(float(s.get("duration", 0.0)) for s in self.shots_plan)
            if planned_dur <= 0 and self.beats:
                planned_dur = sum(b.duration for b in self.beats)
            if planned_dur > 0:
                diff = abs(track.duration - planned_dur)
                max_diff = max(15.0, planned_dur * 0.3)
                if diff > max_diff:
                    errors.append(
                        f"Thời lượng audio {lang.upper()} ({track.duration:.1f}s) lệch quá nhiều so với timeline dự kiến ({planned_dur:.1f}s, dung sai ±{max_diff:.1f}s)."
                    )

        if errors:
            self.save()
            return False, errors

        track.status = AudioStatus.APPROVED
        track.approved_at = time.time()
        track.approver = approver
        logger.info("Audio %s approved by %s for job %s", lang.upper(), approver, self.job_id)
        self.save()
        return True, []

    def reject_audio(self, language: str, reason: str = "Rejected", approver: str = "user") -> Tuple[bool, List[str]]:
        """Rejects audio for a specific language."""
        lang = language.lower().strip()
        if lang not in ("vi", "en"):
            return False, [f"Ngôn ngữ audio không hợp lệ: '{language}'. Chỉ chấp nhận 'vi' hoặc 'en'."]
        track = self.audio_vi if lang == "vi" else self.audio_en
        track.status = AudioStatus.REJECTED
        track.rejection_reason = reason
        track.approver = approver
        self._reset_video_readiness(reason=f"Audio {lang.upper()} rejected")
        self.save()
        return True, []

    def is_audio_gate_passed(self) -> bool:
        """Gate 2 ONLY passes when BOTH VI and EN are approved and valid."""
        if not self.is_script_gate_passed():
            return False
        vi_ok = (
            self.audio_vi.status == AudioStatus.APPROVED
            and self.audio_vi.file_path is not None
            and Path(self.audio_vi.file_path).is_file()
            and self.audio_vi.script_version == self.script_version
        )
        en_ok = (
            self.audio_en.status == AudioStatus.APPROVED
            and self.audio_en.file_path is not None
            and Path(self.audio_en.file_path).is_file()
            and self.audio_en.script_version == self.script_version
        )
        return vi_ok and en_ok

    # -------------------------------------------------------------------------
    # GATE 3: CHARACTER SHEET APPROVAL
    # -------------------------------------------------------------------------

    def register_character(
        self,
        char_id: str,
        name: str,
        description: str,
        visual_features: Optional[Dict[str, Any]] = None,
        shot_ids: Optional[List[int]] = None,
        is_optional: bool = False,
    ) -> CharacterSheetState:
        """Registers a character required in the video."""
        cid = char_id.lower().replace(" ", "_")
        char = CharacterSheetState(
            id=cid,
            name=name,
            description=description,
            visual_features=visual_features or {},
            shot_ids=shot_ids or [],
            status=CharacterStatus.MISSING,
            script_version=self.script_version,
            is_optional=is_optional,
        )
        self.characters[cid] = char
        self._reset_video_readiness(reason=f"Character {name} registered")
        self.save()
        return char

    def set_character_reference(self, char_id: str, image_path: Path):
        """Sets the canonical reference image for a character."""
        cid = char_id.lower().replace(" ", "_")
        if cid not in self.characters:
            raise KeyError(f"Character {char_id} not registered")

        p = Path(image_path)
        char = self.characters[cid]
        char.reference_image_path = str(p.resolve()) if p.exists() else str(p)
        char.image_hash = compute_file_hash(p)
        char.status = CharacterStatus.GENERATED
        char.approved_at = None
        char.approver = None
        char.script_version = self.script_version

        self._reset_video_readiness(reason=f"Character {cid} reference updated")
        self.save()

    def approve_character(self, char_id: str, approver: str = "user") -> Tuple[bool, List[str]]:
        """Approves a single character's canonical reference sheet."""
        cid = char_id.lower().replace(" ", "_")
        if cid not in self.characters:
            return False, [f"Nhân vật {char_id} không tồn tại trong danh mục."]

        char = self.characters[cid]
        errors = []
        if not char.reference_image_path:
            errors.append(f"Nhân vật '{char.name}' chưa có ảnh tham chiếu (reference image).")
        else:
            p = Path(char.reference_image_path)
            if not p.is_file():
                errors.append(f"Ảnh tham chiếu của '{char.name}' không tồn tại: {char.reference_image_path}")
            elif p.stat().st_size < 100:
                errors.append(f"Ảnh tham chiếu của '{char.name}' bị rỗng hoặc hỏng.")

        if errors:
            self.save()
            return False, errors

        char.status = CharacterStatus.APPROVED
        char.approved_at = time.time()
        char.approver = approver
        logger.info("Character %s approved by %s for job %s", cid, approver, self.job_id)
        self.save()
        return True, []

    def approve_all_characters(self, approver: str = "user") -> Tuple[bool, List[str]]:
        """Approves all required characters currently registered if valid."""
        all_errors = []
        for cid in list(self.characters.keys()):
            char = self.characters[cid]
            if not char.is_optional:
                ok, errs = self.approve_character(cid, approver=approver)
                if not ok:
                    all_errors.extend(errs)
        if all_errors:
            return False, all_errors
        return True, []

    def reject_character(self, char_id: str, reason: str = "Rejected", approver: str = "user"):
        """Rejects a character's reference image."""
        cid = char_id.lower().replace(" ", "_")
        if cid in self.characters:
            self.characters[cid].status = CharacterStatus.REJECTED
            self.characters[cid].rejection_reason = reason
            self.characters[cid].approver = approver
            self._reset_video_readiness(reason=f"Character {cid} rejected")
            self.save()

    def is_character_gate_passed(self) -> bool:
        """Gate 3 passes when all non-optional required characters have approved valid reference images."""
        if not self.is_script_gate_passed():
            return False
        # If no characters declared in script and none registered, Gate 3 automatically passes
        if not self.characters and not self.required_characters:
            return True

        if self.required_characters:
            for cname in self.required_characters:
                cid = cname.lower().replace(" ", "_")
                char = self.characters.get(cid)
                if not char:
                    for c in self.characters.values():
                        if c.name.lower() == cname.lower():
                            char = c
                            break
                if not char:
                    return False
                if char.is_optional:
                    continue
                if char.status != CharacterStatus.APPROVED:
                    return False
                if not char.reference_image_path or not Path(char.reference_image_path).is_file():
                    return False
            return True
        else:
            for char in self.characters.values():
                if char.is_optional:
                    continue
                if char.status != CharacterStatus.APPROVED:
                    return False
                if not char.reference_image_path or not Path(char.reference_image_path).is_file():
                    return False
            return True

    # -------------------------------------------------------------------------
    # GATE 4: VIDEO READINESS & GENERATION MANIFEST
    # -------------------------------------------------------------------------

    def set_shots_plan(self, shots: List[Dict[str, Any]]):
        """Sets the shot plan for the video."""
        self.shots_plan = shots
        self._reset_video_readiness(reason="Shots plan updated")
        self.save()

    def check_video_readiness(self, algorithm_mode: bool = False) -> Tuple[bool, List[str], List[str]]:
        """
        Executes strict fail-closed validation across all gates before allowing video generation.
        Returns (is_ready, errors, warnings).
        """
        errors = []
        warnings = []

        # /remake plans use 4-8s algorithm scenes; the marker file lets every
        # caller (queue, manifest, approve) apply the same duration window.
        if not algorithm_mode and (self.project_dir / "algorithm_result.json").is_file():
            algorithm_mode = True

        # 1. Gate 1 - Script
        if not self.is_script_gate_passed():
            errors.append("Gate 1 (Script): Kịch bản chưa được phê duyệt đầy đủ cả VI và EN.")

        # 2. Gate 2 - Audio
        if not self.audio_vi.status == AudioStatus.APPROVED:
            errors.append(f"Gate 2 (Audio VI): Chưa được duyệt (trạng thái: {self.audio_vi.status.value}).")
        elif not self.audio_vi.file_path or not Path(self.audio_vi.file_path).is_file():
            errors.append("Gate 2 (Audio VI): File âm thanh đã duyệt không tồn tại trên đĩa.")

        if not self.audio_en.status == AudioStatus.APPROVED:
            errors.append(f"Gate 2 (Audio EN): Chưa được duyệt (trạng thái: {self.audio_en.status.value}).")
        elif not self.audio_en.file_path or not Path(self.audio_en.file_path).is_file():
            errors.append("Gate 2 (Audio EN): File âm thanh đã duyệt không tồn tại trên đĩa.")

        # 3. Gate 3 - Characters
        if not self.is_character_gate_passed():
            missing_chars = [
                c.name for c in self.characters.values()
                if not c.is_optional and c.status != CharacterStatus.APPROVED
            ]
            errors.append(f"Gate 3 (Character Sheet): Có nhân vật chưa duyệt ({', '.join(missing_chars)}).")

        # 4. Shot Plan
        if not self.shots_plan:
            errors.append("Gate 4: Danh sách shot plan rỗng.")
        else:
            # Gate 2 rewrote plan durations from the real narration audio the
            # human already approved; a fixed 4-8s band here would reject that
            # audio for being what it naturally reads. Hard-fail only on
            # physically broken values, warn on algorithm-band drift.
            min_dur = 3.0 if algorithm_mode else 1.0
            max_dur = 10.5 if algorithm_mode else 20.0
            for idx, s in enumerate(self.shots_plan, 1):
                dur = float(s.get("duration", 0.0))
                if dur < min_dur or dur > max_dur:
                    errors.append(f"Shot {idx}: Thời lượng {dur}s bất thường (ngoài {min_dur}s-{max_dur}s).")
                elif algorithm_mode and not (4.0 <= dur <= 8.0):
                    warnings.append(f"Shot {idx}: Thời lượng {dur}s ngoài band 4-8s (theo audio thật đã duyệt ở Gate 2).")
                if not s.get("prompt"):
                    errors.append(f"Shot {idx}: Thiếu visual prompt.")

                # Check character reference mapping for shots declaring characters
                shot_chars = s.get("character_ids", [])
                for cid in shot_chars:
                    norm_cid = str(cid).lower().replace(" ", "_")
                    if norm_cid not in self.characters:
                        errors.append(f"Shot {idx}: Khai báo nhân vật '{cid}' không tồn tại trong character manifest.")
                    else:
                        c_state = self.characters[norm_cid]
                        if not c_state.reference_image_path or not Path(c_state.reference_image_path).is_file():
                            errors.append(f"Shot {idx}: Nhân vật '{c_state.name}' thiếu file ảnh tham chiếu.")
                        elif c_state.status != CharacterStatus.APPROVED:
                            errors.append(f"Shot {idx}: Nhân vật '{c_state.name}' chưa được phê duyệt ảnh tham chiếu.")

        # Update readiness state
        self.readiness_errors = errors
        self.readiness_warnings = warnings

        if errors:
            self.video_status = VideoReadinessStatus.NOT_READY
            self.save()
            return False, errors, warnings

        if self.video_status != VideoReadinessStatus.APPROVED:
            self.video_status = VideoReadinessStatus.READY
        self.save()
        return True, [], warnings

    def build_generation_manifest(
        self,
        approver: str = "user",
        bgm_plan: Optional[Dict[str, Any]] = None,
        sfx_plan: Optional[Dict[str, Any]] = None,
        provider_config: Optional[Dict[str, Any]] = None,
    ) -> VideoGenerationManifest:
        """
        Builds the immutable VideoGenerationManifest when readiness passes.
        """
        ready, errors, _ = self.check_video_readiness()
        if not ready:
            raise RuntimeError(f"Cannot build generation manifest when readiness check fails: {'; '.join(errors)}")

        char_hashes = {
            cid: char.image_hash or ""
            for cid, char in self.characters.items()
            if char.status == CharacterStatus.APPROVED
        }

        # Build shot manifest mapping each shot to its reference images
        resolved_shots = []
        for s in self.shots_plan:
            shot_copy = dict(s)
            ref_images = []
            for cid in s.get("character_ids", []):
                norm_cid = str(cid).lower().replace(" ", "_")
                if norm_cid in self.characters and self.characters[norm_cid].reference_image_path:
                    ref_images.append(self.characters[norm_cid].reference_image_path)
            shot_copy["reference_images"] = ref_images
            resolved_shots.append(shot_copy)

        bgm_info = bgm_plan or {
            "ducking_db": -18.0,
            "target_lufs": -16.0,
            "fade_in_sec": 1.5,
            "fade_out_sec": 2.5,
            "track": "assets/bgm/documentary_cinematic.mp3",
        }
        sfx_info = sfx_plan or {
            "enabled": True,
            "transition_whoosh": True,
            "dramatic_hits": True,
        }
        prov_info = provider_config or {
            "primary": "veo_ai",
            "fallback": "ken_burns",
            "aspect_ratio": "16:9",
            "resolution": "1080p",
        }

        manifest_payload = {
            "job_id": self.job_id,
            "script_version": self.script_version,
            "script_hash": self.script_hash,
            "audio_vi_hash": self.audio_vi.file_hash or "",
            "audio_en_hash": self.audio_en.file_hash or "",
            "characters": char_hashes,
            "shots": resolved_shots,
            "bgm_plan": bgm_info,
            "sfx_plan": sfx_info,
            "provider_config": prov_info,
        }
        m_hash = compute_content_hash(manifest_payload)
        manifest_id = f"manifest_{self.job_id}_v{self.script_version}_{m_hash[:8]}"

        manifest = VideoGenerationManifest(
            job_id=self.job_id,
            manifest_id=manifest_id,
            script_version=self.script_version,
            script_hash=self.script_hash,
            audio_vi_hash=self.audio_vi.file_hash or "",
            audio_en_hash=self.audio_en.file_hash or "",
            character_sheet_hashes=char_hashes,
            shot_manifest=resolved_shots,
            manifest_hash=m_hash,
            created_at=time.time(),
            approver=approver,
            bgm_plan=bgm_info,
            sfx_plan=sfx_info,
            provider_config=prov_info,
            locked=True,
        )

        # Write immutable manifest file
        m_file = self.project_dir / "video_generation_manifest.json"
        with open(m_file, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)

        self.active_manifest = manifest
        self.save()
        logger.info("Created immutable video generation manifest %s (hash: %s)", manifest_id, m_hash)
        return manifest

    def approve_video_generation(self, approver: str = "user") -> Tuple[bool, List[str]]:
        """
        Final Gate 4 approval. Verifies readiness, locks manifest, and authorizes video worker.
        """
        if self.video_status in (VideoReadinessStatus.GENERATING, VideoReadinessStatus.COMPLETED):
            return False, [f"Job đã ở trạng thái {self.video_status.value}, không thể phê duyệt lại."]

        ready, errors, _ = self.check_video_readiness()
        if not ready:
            return False, errors

        # Phase 5 hard gate: AI-genesis jobs must clear the QA scorecard first.
        qa_ok, qa_errors = self._run_qa_hard_gate()
        if not qa_ok:
            return False, errors + qa_errors

        # If already locked and script_version matches, keep current immutable manifest
        if self.active_manifest and self.active_manifest.locked and self.active_manifest.script_version == self.script_version:
            self.video_status = VideoReadinessStatus.APPROVED
            self.save()
            return True, []

        manifest = self.build_generation_manifest(approver=approver)
        self.video_status = VideoReadinessStatus.APPROVED
        self.save()
        logger.info("Gate 4 (Video Generation) APPROVED for job %s with manifest %s", self.job_id, manifest.manifest_id)
        return True, []

    def _run_qa_hard_gate(self) -> Tuple[bool, List[str]]:
        """Scorecard hard gate for /remake (AI genesis) jobs.

        Enforced only when algorithm_result.json exists (marker of the remake
        pipeline) so legacy reup jobs are unaffected. Fail-closed: any error
        computing the scorecard blocks approval.
        """
        if not (self.project_dir / "algorithm_result.json").is_file():
            return True, []
        try:
            from services.algorithm_qa import QA_GATE_MIN_SCORE, compute_scorecard

            card = compute_scorecard(self.project_dir, stage="final")
            card.save(self.project_dir)
            failed_ids = [r.id for r in card.failed()]
            logger.info(
                "QA hard gate for %s: score %.2f/%.1f (%d/%d passed, failed: %s)",
                self.job_id, card.score, QA_GATE_MIN_SCORE,
                card.passed_count, len(card.results), ",".join(failed_ids) or "none",
            )
            if card.score < QA_GATE_MIN_SCORE:
                top = "; ".join(f"{r.id} {r.name}" for r in card.failed()[:5])
                return False, [
                    f"QA scorecard {card.score:.1f}/10 < {QA_GATE_MIN_SCORE:.1f} — không cho phép render video. "
                    f"Lỗi chính: {top}. Chi tiết: qa_scorecard.json"
                ]
            return True, []
        except Exception as exc:
            logger.error("QA hard gate crashed for %s: %s", self.job_id, exc)
            return False, [f"QA hard gate lỗi (fail-closed): {exc}"]

    def get_qa_report(self) -> Optional[str]:
        """Telegram-formatted QA scorecard report from the last saved run, if any."""
        try:
            from services.algorithm_qa import Scorecard

            card = Scorecard.load(self.project_dir)
            return card.format_telegram_report() if card else None
        except Exception:
            return None

    def mark_generating(self):
        """Transitions state to GENERATING when worker begins generating shots."""
        self.video_status = VideoReadinessStatus.GENERATING
        self.save()

    def mark_completed(self):
        """Transitions state to COMPLETED when all shots and assembly are verified."""
        self.video_status = VideoReadinessStatus.COMPLETED
        self.save()

    def verify_manifest_integrity(self) -> Tuple[bool, Optional[str]]:
        """Worker validation: verifies current manifest has not been tampered with or invalidated."""
        if not self.active_manifest or not self.active_manifest.locked:
            return False, "Không có manifest đã khóa cho video generation."
        if self.video_status not in (VideoReadinessStatus.APPROVED, VideoReadinessStatus.GENERATING, VideoReadinessStatus.COMPLETED):
            return False, f"Trạng thái video ({self.video_status.value}) không ở mức APPROVED/GENERATING."

        # Re-verify disk manifest file exists and matches active_manifest
        m_file = self.project_dir / "video_generation_manifest.json"
        if not m_file.is_file():
            return False, "File video_generation_manifest.json không tồn tại trên đĩa."
        try:
            m_disk = json.loads(m_file.read_text(encoding="utf-8"))
            if m_disk.get("manifest_hash") != self.active_manifest.manifest_hash:
                return False, "Manifest trên đĩa bị thay đổi sau khi khóa (hash không khớp)."
        except Exception as e:
            return False, f"Không thể đọc file manifest: {e}"

        # Re-check audio file hashes
        if self.active_manifest.audio_vi_hash and self.audio_vi.file_path:
            current_h = compute_file_hash(Path(self.audio_vi.file_path))
            if current_h != self.active_manifest.audio_vi_hash:
                return False, "Audio VI file hash không khớp manifest (file bị sửa đổi sau duyệt)."

        if self.active_manifest.audio_en_hash and self.audio_en.file_path:
            current_h = compute_file_hash(Path(self.audio_en.file_path))
            if current_h != self.active_manifest.audio_en_hash:
                return False, "Audio EN file hash không khớp manifest (file bị sửa đổi sau duyệt)."

        # Re-check character reference image files and hashes
        for cid, char_hash in self.active_manifest.character_sheet_hashes.items():
            if cid in self.characters:
                c_state = self.characters[cid]
                if not c_state.reference_image_path or not Path(c_state.reference_image_path).is_file():
                    return False, f"Ảnh tham chiếu của nhân vật '{c_state.name}' bị xóa sau khi khóa manifest."
                curr_char_h = compute_file_hash(Path(c_state.reference_image_path))
                if curr_char_h != char_hash:
                    return False, f"Ảnh tham chiếu của nhân vật '{c_state.name}' bị sửa đổi sau khi khóa manifest."

        # Verify shot count matches
        if len(self.active_manifest.shot_manifest) != len(self.shots_plan):
            return False, "Số lượng cảnh trong manifest không khớp với shot plan hiện hành."

        return True, None

    # -------------------------------------------------------------------------
    # INTERNAL INVALIDATION LOGIC
    # -------------------------------------------------------------------------

    def _invalidate_downstream(self, reason: str = "", characters_changed: bool = False):
        """Invalidates downstream gates when script is modified. Characters only invalidated if character list/features changed."""
        logger.info("Invalidating downstream gates for %s: %s (characters_changed=%s)", self.job_id, reason, characters_changed)
        self.script_approved = False
        self.script_status_vi = ScriptStatus.EDITED
        self.script_status_en = ScriptStatus.EDITED

        # Mark audio stale
        if self.audio_vi.status != AudioStatus.PENDING:
            self.audio_vi.status = AudioStatus.STALE
            self.audio_vi.approved_at = None
        if self.audio_en.status != AudioStatus.PENDING:
            self.audio_en.status = AudioStatus.STALE
            self.audio_en.approved_at = None

        # Only mark characters stale if character list/features were bound to old script
        if characters_changed:
            for char in self.characters.values():
                if char.status != CharacterStatus.MISSING:
                    char.status = CharacterStatus.STALE
                    char.approved_at = None

        self._reset_video_readiness(reason=reason)

    def _reset_video_readiness(self, reason: str = ""):
        """Resets Gate 4 video readiness and revokes manifest."""
        self.video_status = VideoReadinessStatus.NOT_READY
        self.active_manifest = None
        # Remove old manifest file if exists
        m_file = self.project_dir / "video_generation_manifest.json"
        if m_file.exists():
            try:
                m_file.unlink()
            except Exception:
                pass
        logger.debug("Video readiness reset for %s: %s", self.job_id, reason)

    # -------------------------------------------------------------------------
    # TELEGRAM / UI STATUS FORMATTING
    # -------------------------------------------------------------------------

    def format_telegram_status(self) -> str:
        """Renders rich HTML status breakdown of all 4 gates for Telegram display."""
        g1_icon = "✅" if self.is_script_gate_passed() else ("⏳" if self.script_vi_text else "⚪")
        g2_icon = "✅" if self.is_audio_gate_passed() else ("⏳" if (self.audio_vi.file_path or self.audio_en.file_path) else "⚪")
        g3_icon = "✅" if self.is_character_gate_passed() else ("⏳" if self.characters else "⚪")
        g4_icon = "✅" if self.video_status == VideoReadinessStatus.APPROVED else ("🟡" if self.video_status == VideoReadinessStatus.READY else "⚪")

        lines = [
            f"📊 <b>[TIẾN ĐỘ 4 GATE APPROVAL - JOB {self.job_id}]</b>",
            "",
            f"<b>1. Gate 1 (Kịch bản): {g1_icon} {self.script_status_vi.value.upper()} (v{self.script_version})</b>",
            f"   • Tiếng Việt: {'Đã duyệt' if self.script_status_vi == ScriptStatus.APPROVED else self.script_status_vi.value}",
            f"   • Tiếng Anh: {'Đã duyệt' if self.script_status_en == ScriptStatus.APPROVED else self.script_status_en.value}",
            f"   • Số beat: {len(self.beats)} | Nhân vật: {len(self.required_characters)}",
            "",
            f"<b>2. Gate 2 (Giọng đọc): {g2_icon} {'HOÀN TẤT' if self.is_audio_gate_passed() else 'ĐANG CHỜ'}</b>",
            f"   • Audio VI: {self.audio_vi.status.value.upper()} ({self.audio_vi.voice_id or 'Chưa chọn'}, {self.audio_vi.duration:.1f}s)",
            f"   • Audio EN: {self.audio_en.status.value.upper()} ({self.audio_en.voice_id or 'Chưa chọn'}, {self.audio_en.duration:.1f}s)",
            "",
            f"<b>3. Gate 3 (Nhân vật): {g3_icon} {'HOÀN TẤT' if self.is_character_gate_passed() else 'ĐANG CHỜ'}</b>",
        ]

        if not self.characters and not self.required_characters:
            lines.append("   • Không có nhân vật yêu cầu cố định.")
        else:
            for cid, c in self.characters.items():
                c_icon = "✓" if c.status == CharacterStatus.APPROVED else "✗"
                lines.append(f"   • {c_icon} {c.name}: {c.status.value}")

        lines.extend([
            "",
            f"<b>4. Gate 4 (Sẵn sàng Render): {g4_icon} {self.video_status.value}</b>",
        ])

        if self.readiness_errors:
            lines.append(f"   ⚠️ <i>Còn vướng {len(self.readiness_errors)} điều kiện để kích hoạt video.</i>")

        # Next action hint
        lines.append("")
        if not self.is_script_gate_passed():
            lines.append(f"👉 <b>Bước kế tiếp:</b> Gõ <code>/approve_script {self.job_id}</code> để chốt kịch bản.")
        elif not self.is_audio_gate_passed():
            lines.append(f"👉 <b>Bước kế tiếp:</b> Nghe và duyệt audio (<code>/approve_audio_vi {self.job_id}</code> & <code>/approve_audio_en {self.job_id}</code>).")
        elif not self.is_character_gate_passed():
            lines.append(f"👉 <b>Bước kế tiếp:</b> Duyệt ảnh nhân vật: <code>/approve_character {self.job_id} all</code>.")
        elif self.video_status != VideoReadinessStatus.APPROVED:
            lines.append(f"👉 <b>Bước kế tiếp:</b> Mọi gate đã đủ! Bấm <code>/approve_video {self.job_id}</code> để sinh video.")
        else:
            lines.append("🚀 <b>Đang trong tiến trình sinh và render video!</b>")

        return "\n".join(lines)
