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
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.exceptions import RenderError

# Limit TTS speed-up so speech stays intelligible.
MAX_TEMPO = 1.6
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


async def _synthesize_one(text: str, voice: str, rate: str, volume: str, out_path: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await communicate.save(str(out_path))


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
            try:
                asyncio.run(
                    _synthesize_one_pitch(item["text"], voice, item_rate, volume, item_pitch, mp3_path)
                )
            except Exception as exc:
                raise RenderError(f"TTS synthesis failed for segment {i}: {exc}") from exc

            if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                raise RenderError(f"TTS produced empty audio for segment {i}")

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

    args: List[str] = []
    for item in narrated:
        args += ["-i", str(item["audio_path"])]

    chains: List[str] = []
    labels: List[str] = []
    for i, item in enumerate(narrated):
        tempo = float(item.get("tempo", 1.0))
        delay_ms = int(max(0.0, float(item["output_start"])) * 1000)
        chain = f"[{i}:a]aresample=48000"
        if tempo > 1.01:
            chain += f",atempo={tempo:.4f}"
        if delay_ms > 0:
            chain += f",adelay={delay_ms}:all=1"
        if abs(gain_db) > 0.01:
            chain += f",volume={gain_db:.2f}dB"
        chain += f",apad=whole_dur={total_duration:.3f}[n{i}]"
        chains.append(chain)
        labels.append(f"[n{i}]")

    filter_complex = (
        ";".join(chains)
        + f";{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,"
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
    try:
        run_ffmpeg_with_progress(
            args,
            total_duration=total_duration,
            progress_callback=None,
            step_label="Building TTS narration track",
        )
    except Exception as exc:
        raise RenderError(f"Failed to build narration track: {exc}") from exc

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError("Narration track was not created")
    return out_path


def overlay_narration(
    video_path: Path,
    narration_path: Path,
    output_path: Path,
    duck_gain_db: float = -12.0,
    replace_original: bool = False,
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
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", map_label,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        "-f", "mp4",
        "-y",
        str(partial),
    ]
    if use_shortest:
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
        accumulated += clip_dur

    if not items:
        return None

    total = float(timeline.get("actual_duration", 0.0)) or accumulated

    if progress_callback:
        progress_callback(96, f"Synthesizing TTS narration ({len(items)} cues)")

    narrated = synthesize_narration(items, Path(temp_dir), voice=voice, rate=rate)

    if progress_callback:
        progress_callback(97, "Mixing narration track")

    track = build_narration_track(narrated, total, Path(temp_dir) / "narration.wav", gain_db=gain_db)

    if progress_callback:
        progress_callback(98, "Overlaying narration onto video")

    return overlay_narration(
        video_path, track, output_path, duck_gain_db=duck_gain_db, replace_original=replace_original
    )
