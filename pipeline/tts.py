"""
TTS narration pipeline: synthesize script beats with edge-tts (Microsoft neural
voices) and overlay the narration track onto the rendered video.

Timing model:
- Each clip in the timeline may carry a "narration_text" (full beat text).
- The narration for a clip starts at the clip's OUTPUT start time
  (cumulative sum of enabled clip durations).
- If the synthesized audio is longer than the clip, it is sped up with
  `atempo` (capped) to roughly fit, so consecutive beats don't overlap.
"""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.exceptions import RenderError

logger = logging.getLogger(__name__)

# Limit TTS speed-up so speech stays intelligible.
MAX_TEMPO = 1.6

# Retry transient edge-tts network failures (e.g. NoAudioReceived).
_TTS_MAX_ATTEMPTS = 4
_TTS_RETRY_BACKOFF_SECONDS = 5.0
# Pause between consecutive edge-tts requests to avoid rate limiting
# (long scripts: ~90+ rapid requests trigger NoAudioReceived).
_TTS_INTER_REQUEST_PAUSE_SECONDS = 1.5
# Extra cool-down after a failed burst (rate-limit window is ~60s).
_TTS_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
# Only speed up when narration exceeds clip duration by more than 5%.
TEMPO_TRIGGER = 1.05

# Rough speaking rate for Vietnamese TTS at rate=+0% (chars per second).
DEFAULT_CHARS_PER_SECOND = 14.0

# Per-emotion TTS adjustments: (rate, pitch)
EMOTION_TTS_STYLE = {
    "hype": ("+8%", "+5Hz"),
    "hoang_mang": ("+4%", "+10Hz"),
    "bua": ("+2%", "+2Hz"),
    "xuc_dong": ("-4%", "-2Hz"),
    "gian": ("+6%", "-8Hz"),
    "thuong_hai": ("+2%", "+0Hz"),
    "neutral": ("+0%", "+0Hz"),
}

# xKiro voice catalog (verified via GET /v1/audio/voices on 2026-10-22):
# 4 Vietnamese voices + generic English-capable voices.
XKIRO_VOICES = {
    "confident-male-vietnamese",
    "sweet-female-vietnamese",
    "gentle-female-vietnamese",
    "standard-female-vietnamese",
    "male-voice",
    "female-voice",
    "deep-male",
    "expressive-male",
    "announcer",
}

# Explicit availability fallback: xKiro voice -> nearest edge-tts neural voice.
# Used ONLY when the xKiro gateway TTS call fails, and the swap is logged.
XKIRO_TO_EDGE = {
    "confident-male-vietnamese": "vi-VN-NamMinhNeural",
    "sweet-female-vietnamese": "vi-VN-HoaiMyNeural",
    "gentle-female-vietnamese": "vi-VN-HoaiMyNeural",
    "standard-female-vietnamese": "vi-VN-HoaiMyNeural",
    "male-voice": "en-US-GuyNeural",
    "female-voice": "en-US-JennyNeural",
    "deep-male": "en-US-ChristopherNeural",
    "expressive-male": "en-US-GuyNeural",
    "announcer": "en-US-GuyNeural",
}


def is_xkiro_voice(voice_id: str) -> bool:
    """Returns True if voice_id belongs to xKiro catalog."""
    if not voice_id:
        return False
    v = voice_id.lower().strip()
    return v in XKIRO_VOICES or v.startswith("xkiro:") or (
        "vietnamese" in v and not v.startswith("vi-")
    )


XKIRO_TTS_MAX_CHARS = 4000  # gateway hard limit is 4096 per speech request
# Skill-pack pacing. The gateway ignores a speed parameter and its VI voices
# speak 232-300 WPM natively, so audio is fitted locally. Naturalness wins over
# hitting the exact target: never slow a voice below 0.75x (atempo), even when
# that leaves it above TARGET_NARRATION_WPM. Remaining timeline time is handled
# with per-beat pauses by the audio audit, not by stretching the voice further.
TARGET_NARRATION_WPM = 205.0
MIN_NARRATION_WPM = 195.0
MAX_NARRATION_WPM = 214.0
MIN_ATEMPO = 0.75
MIN_WORDS_FOR_RATE_FIT = 8


def _read_llm_key() -> Optional[str]:
    import os

    try:
        from services.settings_service import get_llm_api_key

        key = get_llm_api_key()
    except Exception:
        key = None
    return key or os.environ.get("XKIRO_API_KEY")


def synthesize_one_xkiro(text: str, voice_id: str, out_path: Path, api_key: Optional[str] = None) -> float:
    """Synthesizes text using xKiro API and saves to out_path.

    A rejected key (401/403) triggers the same settings-DB self-heal the LLM
    client uses: the dead row is re-paired from .env and the call retried once.
    """
    import os
    import requests

    explicit_key = api_key is not None
    if not api_key:
        api_key = _read_llm_key()
    if not api_key:
        raise RenderError("xKiro API key is not configured (DB or XKIRO_API_KEY).")

    url = os.environ.get("XKIRO_TTS_URL", "https://api.xkiro.com/v1/audio/speech")
    payload = {
        "model": "xkiro-voice",
        "input": text,
        "voice": voice_id,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code in (401, 403) and not explicit_key:
        from pipeline.llm_client import _repair_credentials

        if _repair_credentials(
            RenderError(f"xKiro TTS returned {resp.status_code}: {resp.text[:120]}")
        ):
            retry_key = _read_llm_key()
            if retry_key and retry_key != api_key:
                api_key = retry_key
                headers["Authorization"] = f"Bearer {api_key}"
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RenderError(f"xKiro TTS API returned status {resp.status_code}: {resp.text[:200]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    return float(len(resp.content))


def _split_text_for_tts(text: str, max_chars: int = XKIRO_TTS_MAX_CHARS) -> list[str]:
    """Pack text into chunks under max_chars, splitting on sentence boundaries.

    The gateway rejects a single speech request above 4096 characters; a whole
    narration script is always longer, so it must be synthesized in parts.
    """
    import re

    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    units = [u.strip() for u in re.split(r"(?<=[.!?…])\s+|\n+", text) if u.strip()]
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(unit) <= max_chars:
            current = unit
            continue
        remaining = unit
        while len(remaining) > max_chars:
            cut = remaining.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        current = remaining
    if current:
        chunks.append(current)
    return chunks


def _is_vietnamese_voice(voice_id: str) -> bool:
    v = (voice_id or "").lower()
    return "vietnamese" in v or v.startswith("vi-")


def rate_fit_sidecar_path(audio_path: Path) -> Path:
    """Telemetry sidecar written next to a synthesized track."""
    return Path(audio_path).with_name(f"{Path(audio_path).name}.rate_fit.json")


def _write_rate_fit_sidecar(audio_path: Path, payload: Dict[str, Any]) -> None:
    try:
        rate_fit_sidecar_path(audio_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Could not write rate-fit sidecar for %s: %s", audio_path.name, exc
        )


def _fit_narration_rate(text: str, audio_path: Path) -> float:
    """Fit narration into the pacing band without unnatural slow-down.

    The slowdown is capped at MIN_ATEMPO (0.75x). If the native voice is so fast
    that reaching TARGET_NARRATION_WPM would require going below the cap, the
    audio keeps the capped rate and the remaining timeline time is filled with
    pauses by the audio audit. Returns the pre-fit WPM (0.0 when skipped).
    """
    words = len(text.split())
    if words < MIN_WORDS_FOR_RATE_FIT:
        return 0.0
    old_duration = _probe_duration(audio_path)
    if old_duration <= 0:
        return 0.0

    native_wpm = words / old_duration * 60.0
    desired = TARGET_NARRATION_WPM / native_wpm if native_wpm > 0 else 1.0
    factor = max(MIN_ATEMPO, min(MAX_TEMPO, desired))
    slowdown_capped = desired < MIN_ATEMPO
    action = "in_band"
    fitted_wpm = native_wpm
    fitted_duration = old_duration

    if not (MIN_NARRATION_WPM <= native_wpm <= MAX_NARRATION_WPM) and abs(factor - 1.0) >= 0.01:
        tmp = audio_path.with_name(f"{audio_path.stem}.rate.mp3")
        from pipeline.ffmpeg import run_ffmpeg

        try:
            run_ffmpeg(
                [
                    "-i", str(audio_path),
                    "-filter:a", f"atempo={factor:.4f}",
                    "-c:a", "libmp3lame",
                    "-q:a", "2",
                    str(tmp),
                ]
            )
            tmp.replace(audio_path)
            fitted_duration = _probe_duration(audio_path)
            fitted_wpm = words / max(fitted_duration, 0.001) * 60.0
            action = "applied"
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            factor = 1.0
            action = "failed"
            logging.getLogger(__name__).warning(
                "Rate fit failed for %s (%.1f WPM, atempo %.2f): %s",
                audio_path.name, native_wpm, factor, exc,
            )
    else:
        factor = 1.0

    _write_rate_fit_sidecar(
        audio_path,
        {
            "target_wpm": TARGET_NARRATION_WPM,
            "band_wpm": [MIN_NARRATION_WPM, MAX_NARRATION_WPM],
            "min_atempo": MIN_ATEMPO,
            "native_wpm": round(native_wpm, 1),
            "fitted_wpm": round(fitted_wpm, 1),
            "atempo": round(factor, 4),
            "slowdown_capped": slowdown_capped,
            "words": words,
            "native_duration_sec": round(old_duration, 3),
            "fitted_duration_sec": round(fitted_duration, 3),
            "action": action,
        },
    )
    logger = logging.getLogger(__name__)
    if slowdown_capped:
        logger.warning(
            "Rate fit %s: native %.1f WPM needs atempo %.2f (< %.2f cap) — "
            "keeping %.1f WPM for naturalness; use pauses for the remaining time",
            audio_path.name, native_wpm, desired, MIN_ATEMPO, fitted_wpm,
        )
    else:
        logger.info(
            "Rate fit %s: %.1f -> %.1f WPM (atempo %.3f)",
            audio_path.name, native_wpm, fitted_wpm, factor,
        )
    return native_wpm


def _edge_tts_save(text: str, edge_voice: str, out_path: Path) -> None:
    async def _edge() -> None:
        import edge_tts

        comm = edge_tts.Communicate(text, edge_voice)
        await comm.save(str(out_path))

    asyncio.run(_edge())


def _concat_audio_parts(parts: list[Path], out_path: Path) -> None:
    from pipeline.ffmpeg import run_ffmpeg

    list_file = out_path.with_name(f"{out_path.stem}.concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts),
        encoding="utf-8",
    )
    try:
        run_ffmpeg(
            [
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c:a", "libmp3lame",
                "-q:a", "2",
                str(out_path),
            ]
        )
    finally:
        list_file.unlink(missing_ok=True)


def _synthesize_chunked_xkiro_with_fallback(text: str, voice_id: str, out_path: Path) -> str:
    """Synthesize a long narration in parts, keeping one consistent voice.

    If any part falls back, the whole text is re-synthesized with edge-tts so a
    single audio track never mixes two voices.
    """
    import shutil
    import uuid

    chunks = _split_text_for_tts(text)
    if not chunks:
        raise RenderError("xKiro TTS text is empty")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parts_dir = out_path.parent / f".xkiro_parts_{uuid.uuid4().hex[:8]}"
    parts_dir.mkdir(parents=True, exist_ok=True)
    try:
        parts: list[Path] = []
        for i, chunk in enumerate(chunks):
            part = parts_dir / f"part_{i:03d}.mp3"
            synthesize_one_xkiro(chunk, voice_id, part)
            parts.append(part)
        _concat_audio_parts(parts, out_path)
        if _is_vietnamese_voice(voice_id):
            _fit_narration_rate(text, out_path)
        return voice_id
    except Exception as exc:
        edge_voice = XKIRO_TO_EDGE.get(voice_id)
        if not edge_voice:
            raise
        logging.getLogger(__name__).warning(
            "xKiro TTS failed on a %d-part narration (%s) — falling back to "
            "edge-tts '%s' for the whole track",
            len(chunks), exc, edge_voice,
        )
        _edge_tts_save(text, edge_voice, out_path)
        return edge_voice
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)


def synthesize_one_xkiro_with_fallback(text: str, voice_id: str, out_path: Path) -> str:
    """xKiro-first synthesis with an explicit edge-tts fallback.

    Returns the voice id actually used. The xKiro->edge swap is always logged
    (never silent) and the fallback edge voice is the mapped neural equivalent.
    """
    if len(text.strip()) > XKIRO_TTS_MAX_CHARS:
        return _synthesize_chunked_xkiro_with_fallback(text, voice_id, out_path)
    try:
        synthesize_one_xkiro(text, voice_id, out_path)
        if _is_vietnamese_voice(voice_id):
            _fit_narration_rate(text, out_path)
        return voice_id
    except Exception as exc:
        edge_voice = XKIRO_TO_EDGE.get(voice_id)
        if not edge_voice:
            raise
        logger = logging.getLogger(__name__)
        logger.warning(
            "xKiro TTS failed for voice '%s' (%s) — falling back to edge-tts '%s'",
            voice_id, exc, edge_voice,
        )
        _edge_tts_save(text, edge_voice, out_path)
        return edge_voice


def _split_sentences(text: str) -> list[str]:
    """Split Vietnamese text into sentence-ish cues."""
    import re

    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    cues = [p.strip() for p in parts if p.strip()]
    # Break very long sentences (no punctuation) into ~clause chunks
    out: list[str] = []
    for cue in cues:
        if len(cue) > 120:
            words = cue.split()
            chunk: list[str] = []
            n = 0
            for w in words:
                chunk.append(w)
                n += len(w) + 1
                if n > 90:
                    out.append(" ".join(chunk))
                    chunk, n = [], 0
            if chunk:
                out.append(" ".join(chunk))
        else:
            out.append(cue)
    return out


def estimate_narration_duration(text: str, chars_per_second: float = DEFAULT_CHARS_PER_SECOND) -> float:
    """Rough duration estimate for TTS of the given text."""
    return max(0.5, len(text) / max(chars_per_second, 1.0))


def distribute_cues_over_clip(
    text: str,
    clip_duration: float,
) -> list[dict]:
    """
    Split beat text into sentence cues and assign each cue an offset so the
    narration is spread over the whole clip instead of clumped at the start.

    If the estimated narration is shorter than the clip, cues are spaced out
    evenly (with small leading offset). If longer, cues pack tightly from the
    start (renderer will tempo-fit or the tail simply spills to next clip).
    """
    cues = _split_sentences(text)
    if not cues:
        return []

    total_est = sum(estimate_narration_duration(c) for c in cues)
    free = max(0.0, clip_duration - total_est)
    # Spread leftover time as inter-cue gaps (keep 0.4s lead-in).
    lead = min(0.4, clip_duration * 0.05) if clip_duration > 2 else 0.0
    gap = free / len(cues) if len(cues) > 1 and free > 0 else 0.0

    items = []
    t = lead
    for cue in cues:
        dur = estimate_narration_duration(cue)
        items.append({"text": cue, "output_start": round(t, 3), "clip_duration": dur})
        t += dur + gap
    return items


def narration_items_from_timeline(timeline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract narration items from a timeline: [{output_start, text, clip_duration}].
    Only enabled clips with non-empty narration_text are included.
    """
    items: List[Dict[str, Any]] = []
    accumulated = 0.0
    for clip in timeline.get("clips", []):
        if not clip.get("enabled", True):
            continue
        start = float(clip.get("source_start", 0.0))
        end = float(clip.get("source_end", 0.0))
        clip_dur = max(0.0, end - start)
        text = str(clip.get("narration_text") or "").strip()
        if text:
            items.append(
                {
                    "output_start": accumulated,
                    "text": text,
                    "clip_duration": clip_dur,
                }
            )
        accumulated += clip_dur
    return items


def _probe_duration(path: Path) -> float:
    from pipeline.ffmpeg import run_ffprobe

    try:
        info = run_ffprobe(["-show_format", str(path)])
        return float(info.get("format", {}).get("duration", 0.0) or 0.0)
    except Exception:
        return 0.0


_TTS_CACHE_DIR = Path("temp_tts_cache")


def _tts_cache_key(text: str, voice: str, rate: str, pitch_value) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(f"{voice}|{rate}|{pitch_value}|wpmfit1".encode("utf-8"))
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:32]


def _cache_lookup(text: str, voice: str, rate: str, pitch_value, dest: Path) -> bool:
    key = _tts_cache_key(text, voice, rate, pitch_value)
    cached = _TTS_CACHE_DIR / f"{key}.mp3"
    if cached.is_file() and cached.stat().st_size > 0:
        shutil.copyfile(cached, dest)
        # Restore the word-boundary sidecar for karaoke subtitles.
        cached_words = _TTS_CACHE_DIR / f"{key}.words.json"
        if cached_words.is_file():
            try:
                shutil.copyfile(cached_words, dest.with_suffix(".words.json"))
            except OSError:
                pass
        return True
    return False


def _cache_store(text: str, voice: str, rate: str, pitch_value, src: Path) -> None:
    try:
        _TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = _tts_cache_key(text, voice, rate, pitch_value)
        cached = _TTS_CACHE_DIR / f"{key}.mp3"
        if not cached.is_file():
            shutil.copyfile(src, cached)
        sidecar = src.with_suffix(".words.json")
        if sidecar.is_file():
            shutil.copyfile(sidecar, _TTS_CACHE_DIR / f"{key}.words.json")
    except OSError:
        pass


async def _synthesize_one(text: str, voice: str, rate: str, volume: str, out_path: Path) -> None:
    """
    Synthesize text to MP3. Also writes a word-boundary sidecar JSON
    (`<stem>.words.json`) with per-word timing, used for karaoke subtitles.
    """
    import edge_tts

    communicate = edge_tts.Communicate(
        text, voice, rate=rate, volume=volume, boundary="WordBoundary"
    )
    words: List[Dict[str, Any]] = []
    with open(out_path, "wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append(
                    {
                        "offset": chunk["offset"] / 1e7,  # 100ns ticks -> seconds
                        "duration": chunk["duration"] / 1e7,
                        "text": chunk["text"],
                    }
                )
    sidecar = out_path.with_suffix(".words.json")
    try:
        sidecar.write_text(json.dumps(words), encoding="utf-8")
    except OSError:
        pass


async def _synthesize_one_pitch(
    text: str, voice: str, rate: str, volume: str, pitch: str, out_path: Path
) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume, pitch=pitch)
    await communicate.save(str(out_path))


def synthesize_narration(
    items: List[Dict[str, Any]],
    out_dir: Path,
    voice: str = "vi-VN-HoaiMyNeural",
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Synthesize each item's text to an MP3 file in out_dir.
    Each item may carry its own "rate"/"pitch" (emotion style) which overrides
    the defaults. Returns items annotated with: audio_path, raw_duration, tempo.
    """
    if not items:
        return []

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []

    try:
        for i, item in enumerate(items):
            mp3_path = out_dir / f"narr_{i:03d}.mp3"
            item_rate = item.get("rate") or rate
            item_pitch = item.get("pitch") or pitch
            text = str(item["text"]).strip()
            if not text:
                raise RenderError(f"TTS synthesis failed for segment {i}: empty narration text")

            if is_xkiro_voice(voice):
                cached = _cache_lookup(text, voice, item_rate, None, mp3_path)
                if not cached:
                    synthesize_one_xkiro(text, voice, mp3_path)
                    if _is_vietnamese_voice(voice):
                        _fit_narration_rate(text, mp3_path)
                    _cache_store(text, voice, item_rate, None, mp3_path)
            else:
                # Edge-tts randomly returns NoAudioReceived ~5-10% of requests, even
                # with pitch="+0Hz"; omitting the pitch parameter entirely is the
                # only reliable fallback. Neutral pitch is skipped from the start.
                neutral_pitch = item_pitch in ("+0Hz", "+0hz", "0Hz", "")
                plan = (
                    [(None, _TTS_MAX_ATTEMPTS + 1)]
                    if neutral_pitch
                    else [(item_pitch, _TTS_MAX_ATTEMPTS), (None, _TTS_MAX_ATTEMPTS)]
                )
                last_exc: Exception | None = None
                # Persistent cache: previously synthesized cues are reused across
                # runs so a mid-batch failure only re-requests the failed segment.
                cache_pitch = None if neutral_pitch else item_pitch
                cached = _cache_lookup(text, voice, item_rate, cache_pitch, mp3_path)
                for pitch_value, attempts in ([] if cached else plan):
                    for attempt in range(attempts):
                        try:
                            if pitch_value is None:
                                synthesize_coro = _synthesize_one(
                                    text, voice, item_rate, volume, mp3_path
                                )
                            else:
                                synthesize_coro = _synthesize_one_pitch(
                                    text, voice, item_rate, volume, pitch_value, mp3_path
                                )
                            asyncio.run(synthesize_coro)
                            last_exc = None
                            break
                        except Exception as exc:  # noqa: BLE001
                            last_exc = exc
                            if attempt < attempts - 1:
                                time.sleep(_TTS_RETRY_BACKOFF_SECONDS * (attempt + 1))
                        else:
                            continue
                        # Retry exhausted for this pitch mode: cool down before
                        # switching strategy (rate-limit window is ~60s).
                        if attempts > 1:
                            time.sleep(_TTS_RATE_LIMIT_COOLDOWN_SECONDS)
                    if last_exc is None:
                        break

                if last_exc is not None:
                    raise RenderError(
                        f"TTS synthesis failed for segment {i}: {last_exc}"
                    ) from last_exc

                if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                    raise RenderError(f"TTS produced empty audio for segment {i}")

                _cache_store(text, voice, item_rate, cache_pitch, mp3_path)

            # Throttle consecutive requests; edge-tts rate-limits bursts.
            if i < len(items) - 1:
                time.sleep(_TTS_INTER_REQUEST_PAUSE_SECONDS)

            raw_dur = _probe_duration(mp3_path)
            clip_dur = float(item.get("clip_duration", 0.0))
            tempo = 1.0
            if clip_dur > 0 and raw_dur > clip_dur * TEMPO_TRIGGER:
                tempo = min(raw_dur / clip_dur, MAX_TEMPO)

            results.append({**item, "audio_path": mp3_path, "raw_duration": raw_dur, "tempo": tempo})

            if progress_callback:
                pct = int((i + 1) / len(items) * 100)
                progress_callback(pct, f"TTS narration {i + 1}/{len(items)}")
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise

    return results


def build_narration_track(
    narrated: List[Dict[str, Any]],
    total_duration: float,
    out_path: Path,
    gain_db: float = 0.0,
) -> Path:
    """
    Mix per-beat narration MP3s into a single timeline-aligned WAV track.
    Each piece is delayed to its output start, optionally tempo-fitted, and
    padded so the final track covers the full output duration.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not narrated:
        raise RenderError("No narration segments to build track from")

    from pipeline.ffmpeg import run_ffmpeg_with_progress

    # Windows limits a command line to ~32k chars; with hundreds of cues the
    # single ffmpeg invocation overflows (WinError 206). Mix in batches, then
    # combine the batch outputs.
    BATCH = 40

    def _run(args: List[str], label: str) -> None:
        try:
            run_ffmpeg_with_progress(
                args,
                total_duration=total_duration,
                progress_callback=None,
                step_label=label,
            )
        except Exception as exc:
            raise RenderError(f"Failed to build narration track: {exc}") from exc

    def _mix(items: List[Dict[str, Any]], out: Path, base_label: str) -> None:
        # NOTE: combining atempo+adelay in the same per-input chain inside a
        # large N-input amix graph is unreliable on some ffmpeg builds (output
        # truncates to the first adelay and goes silent). Instead each cue is
        # rendered to its own full-length bed via a single-input chain (verified
        # reliable), then the beds are amixed together.
        beds: List[Path] = []
        try:
            for i, item in enumerate(items):
                bed = out.with_name(f"{out.stem}_bed{i:03d}.wav")
                tempo = float(item.get("tempo", 1.0))
                delay_ms = int(max(0.0, float(item["output_start"])) * 1000)
                chain = "[0:a]aresample=48000"
                if tempo > 1.01:
                    chain += f",atempo={tempo:.4f}"
                if delay_ms > 0:
                    chain += f",adelay={delay_ms}:all=1"
                if abs(gain_db) > 0.01:
                    chain += f",volume={gain_db:.2f}dB"
                chain += "[aout]"
                _run(
                    ["-i", str(item["audio_path"]),
                     "-filter_complex", chain,
                     "-map", "[aout]",
                     "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                     "-y", str(bed)],
                    f"{base_label} (cue {i + 1}/{len(items)})",
                )
                if not bed.exists() or bed.stat().st_size == 0:
                    raise RenderError(f"Narration bed {i} was not created")
                beds.append(bed)

            # Large N-input amix graphs also truncate on some ffmpeg builds;
            # mix beds sequentially pairwise (2 inputs at a time), which is
            # verified reliable. Final pad+trim guarantees the exact total
            # duration because apad=whole_dur can undershoot on some builds.
            acc = beds[0]
            for bi in range(1, len(beds)):
                nxt = out.with_name(f"{out.stem}_acc{bi:03d}.wav")
                filter_complex = (
                    f"[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
                )
                _run(
                    ["-i", str(acc), "-i", str(beds[bi]),
                     "-filter_complex", filter_complex,
                     "-map", "[aout]",
                     "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                     "-y", str(nxt)],
                    f"{base_label} (mix {bi}/{len(beds) - 1})",
                )
                if bi > 1:
                    try:
                        acc.unlink()
                    except OSError:
                        pass
                acc = nxt
            shutil.copy2(acc, out)
            if acc != beds[0]:
                try:
                    acc.unlink()
                except OSError:
                    pass
            # Final pad+trim: apad=whole_dur can undershoot mid-chain, so pad
            # the finished mix in a dedicated last pass to hit total_duration.
            _run(
                ["-i", str(out),
                 "-af", f"apad=whole_dur={total_duration:.3f},atrim=duration={total_duration:.3f}",
                 "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                 "-y", str(out.with_suffix(".pad.wav"))],
                f"{base_label} (final pad)",
            )
            out.with_suffix(".pad.wav").replace(out)
            got = _probe_duration(out)
            if got < max(0.0, total_duration - 1.0):
                raise RenderError(
                    f"Narration track truncated: got {got:.2f}s, expected ~{total_duration:.2f}s"
                )
        finally:
            for bed in beds:
                try:
                    bed.unlink()
                except OSError:
                    pass

    if len(narrated) <= BATCH:
        _mix(narrated, out_path, "Building TTS narration track")
    else:
        batch_paths: List[Path] = []
        for b in range(0, len(narrated), BATCH):
            chunk = narrated[b:b + BATCH]
            bp = out_path.with_name(out_path.stem + f"_b{b // BATCH:02d}.wav")
            _mix(chunk, bp, f"Building TTS narration track (batch {b // BATCH + 1})")
            batch_paths.append(bp)
        # Combine batch tracks: same-length WAVs, plain amix without padding.
        args = []
        for bp in batch_paths:
            args += ["-i", str(bp)]
        chains = [f"[{i}:a]apad=whole_dur={total_duration:.3f}[b{i}]" for i in range(len(batch_paths))]
        filter_complex = (
            ";".join(chains)
            + f";{''.join(f'[b{i}]' for i in range(len(batch_paths)))}"
            + "amix=inputs=%d:duration=longest:dropout_transition=0:normalize=0," % len(batch_paths)
            + f"atrim=duration={total_duration:.3f},asetpts=PTS-STARTPTS[aout]"
        )
        args += [
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "pcm_s16le",
            "-ar", "48000",
            "-ac", "2",
            "-y",
            str(out_path),
        ]
        _run(args, "Combining TTS narration batches")
        for bp in batch_paths:
            try:
                bp.unlink()
            except OSError:
                pass

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError("Narration track was not created")
    return out_path


def overlay_narration(
    video_path: Path,
    narration_path: Path,
    output_path: Path,
    duck_gain_db: float = -12.0,
    replace_original: bool = False,
    subtitle_path: Optional[Path] = None,
) -> Path:
    """
    Overlay the narration track onto a finished (already audio-mixed) video.

    - replace_original=True: the TTS voice REPLACES the original voice entirely
      (original program audio is removed; only TTS narration remains).
    - replace_original=False: original/program audio is attenuated by
      duck_gain_db and the TTS voice is mixed on top.

    Video stream is stream-copied. Handles the no-source-audio case by making
    the narration the only audio track.
    """
    from pipeline.ffmpeg import run_ffmpeg_with_progress
    from pipeline.audio_mix import probe_joined_video

    video_path = Path(video_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.stem + ".tts.partial.mp4")
    if partial.exists():
        partial.unlink()

    has_audio, _ = probe_joined_video(video_path)

    # Burn subtitles when provided: re-encode video through the subtitles
    # filter (karaoke ASS or plain SRT). Otherwise stream-copy the video.
    vf_arg: List[str] = []
    video_codec = "copy"
    if subtitle_path is not None and Path(subtitle_path).is_file():
        sub_escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        vf_arg = ["-vf", f"subtitles=filename='{sub_escaped}'"]
        video_codec = "libx264"

    if not has_audio or replace_original:
        # TTS narration is the only audio track.
        filter_complex = "[1:a]aresample=48000[tts]"
        map_label = "[tts]"
        use_shortest = True
    else:
        filter_complex = (
            f"[1:a]aresample=48000[tts];"
            f"[0:a]volume={duck_gain_db:.2f}dB[bg];"
            f"[bg][tts]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        map_label = "[aout]"
        use_shortest = False

    args = [
        "-i", str(video_path),
        "-i", str(narration_path),
    ]
    if vf_arg:
        args += vf_arg
    args += [
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", map_label,
        "-c:v", video_codec,
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        "-f", "mp4",
        "-y",
        str(partial),
    ]
    if video_codec == "copy" and use_shortest:
        args.insert(args.index("-f"), "-shortest")
    try:
        run_ffmpeg_with_progress(
            args,
            total_duration=0.0,
            progress_callback=None,
            step_label="Overlaying TTS narration",
        )
    except Exception as exc:
        if partial.exists():
            partial.unlink()
        raise RenderError(f"TTS narration overlay failed: {exc}") from exc

    if not partial.exists():
        raise RenderError("TTS narration overlay produced no output")

    if output_path.exists():
        output_path.unlink()
    partial.replace(output_path)
    return output_path


def generate_and_overlay_narration(
    video_path: Path,
    timeline: Dict[str, Any],
    output_path: Path,
    voice: str = "vi-VN-HoaiMyNeural",
    rate: str = "+0%",
    gain_db: float = 0.0,
    duck_gain_db: float = -12.0,
    replace_original: bool = False,
    temp_dir: Path = Path("temp_tts"),
    progress_callback: Optional[Callable[[int, str], None]] = None,
    subtitle_mode: str = "none",
    output_size: tuple = (1280, 720),
) -> Optional[Path]:
    """
    Full narration stage: timeline beats -> TTS -> track -> overlay.

    Beat text is split into sentence cues spread across each clip so the
    narration follows the picture instead of clumping at clip start.
    Emotion per clip (if present) adjusts rate/pitch of the voice.
    Returns the new output path, or None when the timeline has no narration text.
    """
    # Build per-clip items with cue distribution and emotion style.
    items: List[Dict[str, Any]] = []
    accumulated = 0.0
    clip_bounds: List[tuple] = []  # (clip_start, clip_end) for cues of each clip
    for clip in timeline.get("clips", []):
        if not clip.get("enabled", True):
            continue
        # Prefer explicit output duration (script-led planner) over source span;
        # fall back to source span for legacy timelines.
        if "duration" in clip and float(clip["duration"]) > 0:
            clip_dur = float(clip["duration"])
        else:
            start = float(clip.get("source_start", 0.0))
            end = float(clip.get("source_end", 0.0))
            clip_dur = max(0.0, end - start)
        text = str(clip.get("narration_text") or "").strip()
        if text:
            emotion = str(clip.get("emotion") or "neutral")
            emo_rate, emo_pitch = EMOTION_TTS_STYLE.get(emotion, EMOTION_TTS_STYLE["neutral"])
            cues = distribute_cues_over_clip(text, clip_dur)
            first_idx = len(items)
            for cue in cues:
                items.append(
                    {
                        "output_start": accumulated + cue["output_start"],
                        "text": cue["text"],
                        "clip_duration": cue["clip_duration"],
                        "rate": emo_rate,
                        "pitch": emo_pitch,
                    }
                )
            clip_bounds.append((accumulated, accumulated + clip_dur, first_idx, len(items)))
        accumulated += clip_dur

    if not items:
        return None

    total = float(timeline.get("actual_duration", 0.0)) or accumulated

    if progress_callback:
        progress_callback(96, f"Synthesizing TTS narration ({len(items)} cues)")

    narrated = synthesize_narration(items, Path(temp_dir), voice=voice, rate=rate)

    # Re-schedule cues so real TTS audio never overlaps: tempo-fit each cue to
    # its allocated window, then push every cue to start after the previous
    # one ends (global cursor — spills from one clip never overlap the next).
    cursor = 0.0
    for (clip_start, clip_end, first_idx, last_idx) in clip_bounds:
        cues = narrated[first_idx:last_idx]
        # First pass: tempo-fit each cue to its allocated window.
        for cue in cues:
            clip_dur = float(cue.get("clip_duration", 0.0))
            raw = float(cue.get("raw_duration", 0.0))
            tempo = 1.0
            if clip_dur > 0 and raw > clip_dur * TEMPO_TRIGGER:
                tempo = min(raw / clip_dur, MAX_TEMPO)
            cue["tempo"] = tempo
        # Second pass: sequential starts — never begin a cue before the
        # previous one finishes (0.08s breather).
        for cue in cues:
            raw = float(cue.get("raw_duration", 0.0))
            tempo = max(float(cue.get("tempo", 1.0)), 0.01)
            cursor = max(cursor, clip_start)
            cue["output_start"] = round(max(float(cue["output_start"]), cursor), 3)
            cursor = cue["output_start"] + raw / tempo + 0.08

    if progress_callback:
        progress_callback(97, "Mixing narration track")

    track = build_narration_track(narrated, total, Path(temp_dir) / "narration.wav", gain_db=gain_db)

    # Subtitles: SRT sidecar always written for reuse; karaoke ASS burned in
    # when requested. Burning happens in overlay via the subtitles filter.
    subtitle_path: Optional[Path] = None
    if subtitle_mode != "none":
        from pipeline.subtitles import build_karaoke_ass, build_srt

        build_srt(narrated, Path(temp_dir) / "narration.srt")
        if subtitle_mode == "karaoke":
            subtitle_path = build_karaoke_ass(
                narrated,
                Path(temp_dir) / "narration_karaoke.ass",
                width=int(output_size[0]),
                height=int(output_size[1]),
            )
        else:
            subtitle_path = Path(temp_dir) / "narration.srt"

    if progress_callback:
        progress_callback(98, "Overlaying narration onto video")

    return overlay_narration(
        video_path,
        track,
        output_path,
        duck_gain_db=duck_gain_db,
        replace_original=replace_original,
        subtitle_path=subtitle_path,
    )
