"""Domain: enums, transition tables, canonical hash.

Single source of transition truth (plan v3.2 Appendix A + §2.3).
Raw UPDATE bypassing these tables is forbidden — services must call
`assert_shot_transition` / `assert_item_transition`.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum


class ShotStatus(str, Enum):
    PENDING = "pending"
    KEYFRAME_PENDING = "keyframe_pending"
    KEYFRAME_APPROVED = "keyframe_approved"
    GENERATING = "generating"
    GENERATED = "generated"
    QC_PASSED = "qc_passed"
    APPENDED = "appended"
    HELD = "held"
    FAILED = "failed"
    RETRY_LATER = "retry_later"
    SUPERSEDED = "superseded"


# Appendix A — the ONLY legal shot transitions. No jump over qc_passed.
SHOT_TRANSITIONS: dict[ShotStatus, set[ShotStatus]] = {
    ShotStatus.PENDING: {ShotStatus.KEYFRAME_PENDING, ShotStatus.SUPERSEDED},
    ShotStatus.KEYFRAME_PENDING: {
        ShotStatus.KEYFRAME_APPROVED, ShotStatus.HELD, ShotStatus.SUPERSEDED,
    },
    ShotStatus.KEYFRAME_APPROVED: {ShotStatus.GENERATING, ShotStatus.SUPERSEDED},
    ShotStatus.GENERATING: {
        ShotStatus.GENERATED, ShotStatus.RETRY_LATER, ShotStatus.HELD,
        ShotStatus.SUPERSEDED,
    },
    ShotStatus.RETRY_LATER: {
        ShotStatus.GENERATING, ShotStatus.SUPERSEDED,
    },
    ShotStatus.GENERATED: {ShotStatus.QC_PASSED, ShotStatus.HELD, ShotStatus.SUPERSEDED},
    ShotStatus.QC_PASSED: {ShotStatus.APPENDED, ShotStatus.HELD, ShotStatus.SUPERSEDED},
    ShotStatus.HELD: {
        ShotStatus.KEYFRAME_PENDING,  # retry keyframe (reason in keyframe group)
        ShotStatus.GENERATING,        # retry animate from approved keyframe
        ShotStatus.SUPERSEDED,
    },
    ShotStatus.FAILED: {ShotStatus.PENDING, ShotStatus.SUPERSEDED},  # admin retry
    ShotStatus.APPENDED: set(),       # terminal
    ShotStatus.SUPERSEDED: set(),     # terminal
}

# Retry-from-held rules (A12/A13): allowed target depends on held_reason group.
KEYFRAME_HELD_REASONS = {
    "KEYFRAME_STYLE_FAIL", "KEYFRAME_SIZE_FAIL", "KEYFRAME_TEXT_MISMATCH",
    "REF_IMAGE_MISSING",
}


class ItemStatus(str, Enum):
    DRAFT = "draft"
    PROCESSING_INPUT = "processing_input"
    ANALYZING = "analyzing"
    TODO = "todo"
    AWAITING_REVIEW = "awaiting_review"
    GENERATING = "generating"
    HELD_REVIEW = "held_review"
    ASSEMBLING = "assembling"
    READY = "ready"
    COMPLETED = "completed"
    EXPORTED = "exported"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    DELETED = "deleted"  # soft delete; purge is a separate background job


ITEM_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
    ItemStatus.DRAFT: {
        ItemStatus.PROCESSING_INPUT, ItemStatus.ANALYZING, ItemStatus.TODO,
        ItemStatus.FAILED, ItemStatus.CANCELLED, ItemStatus.DELETED,
    },
    ItemStatus.PROCESSING_INPUT: {
        ItemStatus.TODO, ItemStatus.AWAITING_REVIEW, ItemStatus.FAILED,
        ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.ANALYZING: {
        ItemStatus.TODO, ItemStatus.AWAITING_REVIEW, ItemStatus.FAILED,
        ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.TODO: {
        ItemStatus.TODO, ItemStatus.ASSEMBLING, ItemStatus.GENERATING,
        ItemStatus.FAILED, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.AWAITING_REVIEW: {
        ItemStatus.TODO, ItemStatus.GENERATING, ItemStatus.ASSEMBLING,
        ItemStatus.FAILED, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.GENERATING: {
        ItemStatus.ASSEMBLING, ItemStatus.HELD_REVIEW, ItemStatus.TODO,
        ItemStatus.FAILED, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.HELD_REVIEW: {
        ItemStatus.GENERATING, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.ASSEMBLING: {
        ItemStatus.COMPLETED, ItemStatus.READY, ItemStatus.TODO,
        ItemStatus.FAILED, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.READY: {
        ItemStatus.COMPLETED, ItemStatus.EXPORTED, ItemStatus.PAUSED, ItemStatus.DELETED,
    },
    ItemStatus.COMPLETED: {
        ItemStatus.TODO, ItemStatus.ASSEMBLING, ItemStatus.EXPORTED, ItemStatus.DELETED,
    },
    ItemStatus.EXPORTED: set(),  # terminal; assets retained
    ItemStatus.FAILED: {
        ItemStatus.PROCESSING_INPUT, ItemStatus.ANALYZING, ItemStatus.TODO,
        ItemStatus.AWAITING_REVIEW, ItemStatus.GENERATING, ItemStatus.ASSEMBLING,
        ItemStatus.DELETED,
    },
    ItemStatus.PAUSED: {
        ItemStatus.PROCESSING_INPUT, ItemStatus.ANALYZING, ItemStatus.TODO,
        ItemStatus.AWAITING_REVIEW, ItemStatus.GENERATING, ItemStatus.HELD_REVIEW,
        ItemStatus.CANCELLED, ItemStatus.DELETED,
    },
    ItemStatus.CANCELLED: {ItemStatus.DELETED},
    ItemStatus.DELETED: set(),
}


# ---------------------------------------------------------------------------
# Short Studio (Phụ lục G — docs/SHORT_STUDIO_PLAN.md). Domain song song với
# Item/Shot; KHÔNG đụng bảng transition Phụ lục A.
# ---------------------------------------------------------------------------

class ShortStatus(str, Enum):
    DRAFT = "draft"
    SCRIPTED = "scripted"
    PRODUCING = "producing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


SHORT_TRANSITIONS: dict[ShortStatus, set[ShortStatus]] = {
    ShortStatus.DRAFT: {ShortStatus.SCRIPTED, ShortStatus.FAILED, ShortStatus.CANCELLED},
    ShortStatus.SCRIPTED: {
        ShortStatus.PRODUCING, ShortStatus.SCRIPTED,  # self-loop: sửa/duyệt lại kịch bản
        ShortStatus.FAILED, ShortStatus.CANCELLED,
    },
    ShortStatus.PRODUCING: {
        ShortStatus.READY, ShortStatus.SCRIPTED, ShortStatus.FAILED, ShortStatus.CANCELLED,
    },
    ShortStatus.READY: {ShortStatus.PRODUCING, ShortStatus.CANCELLED},  # produce lại
    ShortStatus.FAILED: {ShortStatus.SCRIPTED, ShortStatus.PRODUCING, ShortStatus.CANCELLED},
    ShortStatus.CANCELLED: set(),
}

SHORT_LABELS_VI: dict[str, str] = {
    "draft": "Nháp",
    "scripted": "Đã có kịch bản",
    "producing": "Đang tổng hợp",
    "ready": "Đã có video",
    "failed": "Hỏng",
    "cancelled": "Đã hủy",
}


def assert_short_transition(cur: str, to: str) -> None:
    try:
        c, t = ShortStatus(cur), ShortStatus(to)
    except ValueError as e:
        raise TransitionError(f"unknown short status: {e}") from None
    if to not in {s.value for s in SHORT_TRANSITIONS[c]}:
        raise TransitionError(f"illegal short transition {cur} -> {to}")


class HeldReason(str, Enum):  # keyframe group (retry → keyframe_pending)
    KEYFRAME_STYLE_FAIL = "KEYFRAME_STYLE_FAIL"
    KEYFRAME_SIZE_FAIL = "KEYFRAME_SIZE_FAIL"
    KEYFRAME_TEXT_MISMATCH = "KEYFRAME_TEXT_MISMATCH"
    REF_IMAGE_MISSING = "REF_IMAGE_MISSING"
    # video QC group (retry → generating)
    DURATION_MISMATCH = "DURATION_MISMATCH"
    VIDEO_TEXT_LEAK = "VIDEO_TEXT_LEAK"
    RESOLUTION_FAIL = "RESOLUTION_FAIL"
    SPEC_MISMATCH = "SPEC_MISMATCH"
    QC_TOOL_UNAVAILABLE = "QC_TOOL_UNAVAILABLE"
    # provider group (retry → generating)
    PROVIDER_FATAL = "PROVIDER_FATAL"
    PROVIDER_AUTH = "PROVIDER_AUTH"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    # human group (retry only via human decision)
    CHARACTER_COLOR_SUSPECT = "CHARACTER_COLOR_SUSPECT"
    FACE_MISMATCH = "FACE_MISMATCH"
    HUMAN_REJECT = "HUMAN_REJECT"
    # workflow
    TIMECODE_SHIFTED = "TIMECODE_SHIFTED"

    @property
    def group(self) -> str:
        if self.value in KEYFRAME_HELD_REASONS:
            return "keyframe"
        if self.value in {"PROVIDER_FATAL", "PROVIDER_AUTH", "QUOTA_EXHAUSTED",
                          "DURATION_MISMATCH", "VIDEO_TEXT_LEAK", "RESOLUTION_FAIL",
                          "SPEC_MISMATCH"}:
            return "video"
        if self.value in {"CHARACTER_COLOR_SUSPECT", "FACE_MISMATCH", "HUMAN_REJECT"}:
            return "human"
        if self.value == "QC_TOOL_UNAVAILABLE":
            return "workflow"
        return "workflow"


HELD_LABELS_VI: dict[str, str] = {
    "KEYFRAME_STYLE_FAIL": "Keyframe sai style (không khớp Style Lock v3)",
    "KEYFRAME_SIZE_FAIL": "Keyframe sai kích thước (phải 1280x720)",
    "KEYFRAME_TEXT_MISMATCH": "Chữ trên keyframe không khớp on_screen_text",
    "REF_IMAGE_MISSING": "Thiếu ảnh tham chiếu nhân vật đã duyệt",
    "DURATION_MISMATCH": "Thời lượng video lệch audio quá ±0.25s",
    "VIDEO_TEXT_LEAK": "Có chữ lạ lọt vào video (không nằm trong allowlist)",
    "RESOLUTION_FAIL": "Sai độ phân giải",
    "SPEC_MISMATCH": "Sai render spec (fps/codec/pixel format)",
    "QC_TOOL_UNAVAILABLE": "Công cụ QC chưa cấu hình — không thể tự động đạt",
    "PROVIDER_FATAL": "Provider sinh video lỗi nghiêm trọng",
    "PROVIDER_AUTH": "Provider lỗi xác thực",
    "QUOTA_EXHAUSTED": "Hết quota provider",
    "CHARACTER_COLOR_SUSPECT": "Nghi vấn màu da nhân vật lệch (cần người soát)",
    "FACE_MISMATCH": "Mặt nhân vật không giống ảnh tham chiếu (cần người soát)",
    "HUMAN_REJECT": "Người duyệt giữ shot này",
    "TIMECODE_SHIFTED": "Timecode dịch chuyển do sửa scene phía trước",
}

# render_engine values ALLOWED on a shot that reaches APPENDED (plan §2.2).
ALLOWED_DONE_ENGINES = {"veo", "ken_burns_approved", "ken_burns_downgrade"}


class TransitionError(Exception):
    pass


def assert_shot_transition(cur: str, to: str) -> None:
    try:
        c, t = ShotStatus(cur), ShotStatus(to)
    except ValueError as e:
        raise TransitionError(f"unknown status: {e}") from None
    if to not in {s.value for s in SHOT_TRANSITIONS[c]}:
        raise TransitionError(f"illegal shot transition {cur} -> {to}")


def assert_item_transition(cur: str, to: str) -> None:
    try:
        c, t = ItemStatus(cur), ItemStatus(to)
    except ValueError as e:
        raise TransitionError(f"unknown status: {e}") from None
    if to not in {s.value for s in ITEM_TRANSITIONS[c]}:
        raise TransitionError(f"illegal item transition {cur} -> {to}")


# ---------------------------------------------------------------------------
# Canonical shots_hash — plan v3.2 §3.2.5 (verbatim contract):
# SHA256 over canonical JSON of the array of objects
# {index, narration, audio_start_sec, audio_end_sec, visual_prompt_clean,
#  on_screen_text, on_screen_type, keyframe_sha256}, keys sorted, numbers
# rounded to 3 decimals, no extra whitespace.
# ---------------------------------------------------------------------------

def _canon_num(x):
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, (int,)):
        return x
    if isinstance(x, float):
        return round(x, 3)
    return x


def canonical_shots_hash(shots: list[dict]) -> str:
    canon = []
    for s in shots:
        canon.append({
            "index": _canon_num(s["index"]),
            "narration": s["narration"],
            "audio_start_sec": _canon_num(float(s["audio_start_sec"])),
            "audio_end_sec": _canon_num(float(s["audio_end_sec"])),
            "visual_prompt_clean": s["visual_prompt_clean"],
            "on_screen_text": s.get("on_screen_text"),
            "on_screen_type": s.get("on_screen_type"),
            "keyframe_sha256": s.get("keyframe_sha256"),
        })
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
