import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config.settings import settings
from core.exceptions import AnalysisError, JobCancelledError
from pipeline.ffmpeg import FFMPEG


def _empty_state() -> Dict[str, Any]:
    return {"language": None, "language_probability": None, "segments": []}


def _read_checkpoint(path: Optional[Path]) -> Dict[str, Any]:
    """Load the partial transcript written by a previous, interrupted run."""
    if path is None or not path.exists():
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    segments = [
        seg
        for seg in (data.get("segments") or [])
        if isinstance(seg, dict) and "end" in seg and "text" in seg
    ]
    return {
        "language": data.get("language"),
        "language_probability": data.get("language_probability"),
        "segments": segments,
    }


def _write_checkpoint(path: Path, state: Dict[str, Any]) -> None:
    """Atomically persist the partial transcript so a restart can resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    tmp.replace(path)


# Loading a Whisper model takes seconds and allocates hundreds of MB, so the
# model is cached per (size, device, compute_type) for the lifetime of the
# process. A lock guards against two job threads loading the same model twice.
_MODEL_CACHE: Dict[tuple, Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_model(model_size: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel

    key = (model_size, device, compute_type)
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _MODEL_CACHE[key] = model
        return model


def _trim_audio(source: Path, start: float, target: Path) -> Path:
    """Re-encode `source` from `start` seconds to the end into `target`."""
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(target),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        detail = (result.stderr or "").strip()[-300:]
        raise AnalysisError(f"Failed to trim audio for resume: {detail}")
    return target


def transcribe_audio(
    audio_path: Path,
    model_size: Optional[str] = None,
    language: Optional[str] = None,
    device: Optional[str] = None,
    compute_type: Optional[str] = None,
    beam_size: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    cancellation_token=None,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 25,
) -> Dict[str, Any]:
    """
    Transcribe audio with faster-whisper.

    Segments are streamed so multi-hour files report progress incrementally and
    can be cancelled mid-run. When `checkpoint_path` is given, partial segments
    are written there as the run proceeds; if the process dies, the next call
    trims the audio past the last checkpointed segment and continues instead of
    redoing the whole transcription.

    Returns a dict:
        {"language", "language_probability", "duration", "segments": [...]}.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AnalysisError("faster-whisper is not installed") from exc

    audio_path = Path(audio_path)
    checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    model_size = model_size or settings.whisper_model
    device = device or settings.whisper_device
    compute_type = compute_type or settings.whisper_compute_type
    beam_size = beam_size or settings.whisper_beam_size
    language = None if (not language or language == "auto") else language

    state = _read_checkpoint(checkpoint_path)
    done: List[Dict[str, Any]] = list(state["segments"])
    resume_from = float(done[-1]["end"]) if done else 0.0
    # A previous run already detected the language; reuse it so a short audio
    # tail is not re-detected with a different answer.
    if language is None and state.get("language"):
        language = state["language"]

    def report(pct: int, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    work_audio = audio_path
    trimmed: Optional[Path] = None
    if resume_from > 0:
        trimmed = audio_path.with_name(f"{audio_path.stem}_resume{audio_path.suffix}")
        _trim_audio(audio_path, resume_from, trimmed)
        work_audio = trimmed
        report(0, f"Resuming transcription from {resume_from:.1f}s...")

    try:
        model = _get_model(model_size, device, compute_type)
    except Exception as exc:
        raise AnalysisError(f"Failed to load Whisper model '{model_size}': {exc}")

    try:
        segments_iter, info = model.transcribe(
            str(work_audio),
            language=language,
            beam_size=beam_size,
            vad_filter=settings.whisper_vad,
            vad_parameters={"min_silence_duration_ms": settings.whisper_vad_min_silence_ms},
            word_timestamps=True,
        )
    except Exception as exc:
        raise AnalysisError(f"Transcription failed to start: {exc}")

    total = resume_from + float(getattr(info, "duration", 0.0) or 0.0)
    new_segments: List[Dict[str, Any]] = []

    def snapshot() -> Dict[str, Any]:
        return {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "segments": done + new_segments,
        }

    try:
        for seg in segments_iter:
            if cancellation_token is not None and cancellation_token.is_set():
                raise JobCancelledError("Transcription cancelled by user")
            text = (seg.text or "").strip()
            if not text:
                continue
            words = []
            for w in getattr(seg, "words", None) or []:
                w_text = (getattr(w, "word", "") or "").strip()
                if not w_text:
                    continue
                words.append(
                    {
                        "word": w_text,
                        "start": resume_from + float(w.start),
                        "end": resume_from + float(w.end),
                    }
                )
            new_segments.append(
                {
                    "id": seg.id,
                    "start": resume_from + float(seg.start),
                    "end": resume_from + float(seg.end),
                    "text": text,
                    "words": words,
                }
            )
            if progress_callback and total > 0:
                ratio = min((resume_from + float(seg.end)) / total, 1.0)
                pct = int(ratio * 100)
                report(pct, f"Transcribing... {pct}%")
            if checkpoint_path is not None and len(new_segments) % checkpoint_every == 0:
                _write_checkpoint(checkpoint_path, snapshot())
    except JobCancelledError:
        if checkpoint_path is not None and new_segments:
            _write_checkpoint(checkpoint_path, snapshot())
        raise
    except Exception as exc:
        if checkpoint_path is not None and new_segments:
            _write_checkpoint(checkpoint_path, snapshot())
        raise AnalysisError(f"Transcription failed: {exc}")
    finally:
        if trimmed is not None:
            try:
                trimmed.unlink()
            except OSError:
                pass

    if checkpoint_path is not None:
        try:
            checkpoint_path.unlink()
        except OSError:
            pass

    return {
        "language": getattr(info, "language", None) or state.get("language"),
        "language_probability": getattr(info, "language_probability", None)
        or state.get("language_probability"),
        "duration": total,
        "segments": done + new_segments,
    }