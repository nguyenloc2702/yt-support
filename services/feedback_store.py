"""Retention feedback loop (Phase 5): capture post-publish ratings for /remake
jobs and fold lessons back into future script generation.

Store: <data_dir>/retention_feedback.json — records appended by the /feedback
Telegram command. ScriptFactory reads the brief built from recent records so
new variants keep what worked and avoid previously criticized patterns.
Empty store -> empty brief -> prompts unchanged (no synthetic feedback).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from config.settings import settings
from core.logging_config import get_logger

logger = get_logger(__name__)


def _store_path() -> Path:
    return Path(settings.data_dir) / "retention_feedback.json"


def add_feedback(
    job_id: str,
    rating: float,
    note: str = "",
    angle: str = "",
    project_dir: str = "",
) -> Dict[str, Any]:
    rating_f = float(rating)
    if not 0.0 <= rating_f <= 10.0:
        raise ValueError("rating must be within 0..10")
    record = {
        "job_id": job_id,
        "rating": rating_f,
        "note": (note or "").strip(),
        "angle": angle or "",
        "project_dir": project_dir or "",
        "ts": time.time(),
    }
    path = _store_path()
    records: List[Dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                records = loaded
        except (OSError, json.JSONDecodeError):
            records = []
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    logger.info("Retention feedback recorded for %s: rating %.1f/10", job_id, rating_f)
    return record


def load_feedback(limit: int = 50) -> List[Dict[str, Any]]:
    path = _store_path()
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(loaded, list):
        return []
    return loaded[-limit:]


def build_feedback_brief(limit: int = 50) -> str:
    """LLM-ready lessons from recent ratings; '' when the store is empty."""
    records = load_feedback(limit)
    if not records:
        return ""
    rated = [r for r in records if isinstance(r.get("rating"), (int, float))]
    if not rated:
        return ""
    avg = sum(r["rating"] for r in rated) / len(rated)
    best = max(rated, key=lambda r: r["rating"])
    worst = min(rated, key=lambda r: r["rating"])

    def _line(rec: Dict[str, Any], label: str) -> str:
        note = rec.get("note") or ""
        return (
            f"- {label}: angle '{rec.get('angle') or 'unknown'}' rated {rec['rating']:.1f}/10"
            + (f" — {note}" if note else "")
        )

    return "\n".join([
        "RETENTION FEEDBACK from previously published videos:",
        f"- {len(rated)} rated video(s), average rating {avg:.1f}/10.",
        _line(best, "Best"),
        _line(worst, "Worst"),
        "Fold these lessons into your rewrite: keep what made the best-rated video work, "
        "avoid the patterns criticized in the worst-rated one.",
    ])
