"""English Audio Synthesizer, Exact Per-Shot Duration Timing & Subtitle Builder."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import struct
import subprocess
import wave
import zlib
from pathlib import Path
from typing import Any

from ..config import AUDIO_CONFIG

logger = logging.getLogger("audio_generator_en")

# Narrative acts mapped across timeline (UNIVERSAL_VIDEO_ALGORITHM)
BEAT_ROLE_POSITION = [
    (0.08, "hook"),
    (0.25, "setup"),
    (0.60, "rising"),
    (0.80, "crisis"),
    (0.94, "breakthrough"),
    (1.01, "payoff"),
]

BEAT_ROLE_MOODS = {
    "hook": ["mysterious"],
    "setup": ["nostalgic", "gentle"],
    "rising": ["energetic", "quirky"],
    "crisis": ["ominous"],
    "breakthrough": ["triumphant"],
    "payoff": ["celebratory"],
}


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _get_bgm_dir() -> Path:
    env_dir = os.environ.get("VIDEO_SYSTEM_BGM_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)
    cfg_dir = AUDIO_CONFIG.get("bgm", {}).get("bgm_dir")
    if cfg_dir and Path(cfg_dir).exists():
        return Path(cfg_dir)
    vps_dir = Path("/opt/video-system/assets/music/bgm")
    if vps_dir.exists():
        return vps_dir
    support_dir = Path("C:/app/work/support-video-edit/assets/music/bgm")
    if support_dir.exists():
        return support_dir
    local_dir = Path(__file__).resolve().parents[3] / "assets" / "music" / "bgm"
    if local_dir.exists():
        return local_dir
    return vps_dir if os.name != "nt" else support_dir


def _load_bgm_loudness(bgm_dir: Path) -> dict[str, dict[str, float]]:
    loudness_file = bgm_dir / "LOUDNESS.json"
    if loudness_file.exists():
        try:
            data = json.loads(loudness_file.read_text(encoding="utf-8"))
            return data.get("tracks", {})
        except Exception:
            pass
    return {}


def _role_for_position(pos: float) -> str:
    for limit, role in BEAT_ROLE_POSITION:
        if pos < limit:
            return role
    return "payoff"


def _build_narrative_bgm_cues(
    total_dur: float,
    shot_durations: list[float] | None = None,
) -> list[tuple[str, float, float]]:
    """Build narrative act cues across timeline with continuous coverage (no dead silence holes)."""
    if total_dur <= 0:
        return []
    MIN_CUE_S = 20.0

    if not shot_durations:
        raw_cues: list[tuple[str, float, float]] = []
        cur = 0.0
        for limit, role in BEAT_ROLE_POSITION:
            end = min(total_dur, round(limit * total_dur, 3))
            if end > cur:
                raw_cues.append((role, cur, end))
                cur = end
    else:
        weights = [max(0.5, float(d or 0)) for d in shot_durations]
        sum_w = sum(weights)
        scale = total_dur / sum_w if sum_w > 0 else 1.0
        segments: list[list[Any]] = []
        cursor = 0.0
        for w in weights:
            length = w * scale
            role = _role_for_position((cursor + length / 2) / total_dur)
            end = min(total_dur, cursor + length)
            if segments and segments[-1][0] == role:
                segments[-1][2] = end
            else:
                segments.append([role, cursor, end])
            cursor = end

        merged: list[list[Any]] = []
        for role, start, end in segments:
            if merged and (end - start) < MIN_CUE_S:
                merged[-1][2] = end
            else:
                merged.append([role, start, end])
        if merged:
            merged[-1][2] = total_dur
        raw_cues = [(m[0], round(m[1], 3), round(m[2], 3)) for m in merged]

    return raw_cues


def probe_media_duration(path: Path) -> float:
    """Measure exact media duration in seconds using wave (for .wav) or ffprobe."""
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                if rate > 0:
                    return round(w.getnframes() / float(rate), 3)
        except Exception:
            pass
    if shutil.which("ffprobe"):
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            return round(float(res.stdout.strip()), 3)
    raise RuntimeError(f"Unable to probe duration for {path}")


def probe_video_info(path: Path) -> dict[str, Any]:
    """Probe width, height, fps, and duration of an uploaded video file."""
    if shutil.which("ffprobe"):
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name,pix_fmt:format=duration",
            "-of", "json",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            streams = data.get("streams") or [{}]
            st = streams[0] if streams else {}
            fmt = data.get("format") or {}
            fps_raw = st.get("r_frame_rate", "24/1")
            try:
                if "/" in str(fps_raw):
                    n, d = str(fps_raw).split("/", 1)
                    fps = round(float(n) / float(d), 2) if float(d) else 24.0
                else:
                    fps = round(float(fps_raw), 2)
            except Exception:
                fps = 24.0
            dur = float(fmt.get("duration") or st.get("duration") or 0.0)
            if dur <= 0:
                raise ValueError("Video duration is 0 or unreadable")
            return {
                "duration_sec": round(dur, 3),
                "width": int(st.get("width") or 1280),
                "height": int(st.get("height") or 720),
                "fps": fps,
                "codec": st.get("codec_name") or "h264",
            }
    if path.suffix.lower() == ".wav":
        return {
            "duration_sec": probe_media_duration(path),
            "width": 1280,
            "height": 720,
            "fps": 24.0,
            "codec": "h264",
        }
    raise RuntimeError(f"Could not probe video file: {path}")


def _write_synthetic_speech_wav(path: Path, seconds: float, freq: float = 220.0) -> None:
    """Deterministic speech-like WAV synthesis used in offline test mode or fallback."""
    rate = 24000
    nframes = max(1, int(round(rate * seconds)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray(nframes * 2)
        for i in range(nframes):
            t = i / rate
            env = min(1.0, i / (rate * 0.03), (nframes - i) / (rate * 0.05))
            val = int(4500 * env * math.sin(2.0 * math.pi * freq * t))
            struct.pack_into("<h", buf, i * 2, val)
        w.writeframes(buf)


async def _edge_tts_to_file(text: str, out_mp3: Path, voice: str, rate: str) -> None:
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate)
    await comm.save(str(out_mp3))


def _synthesize_shot_wav(text: str, out_wav: Path, voice: str, rate: str) -> float:
    """Synthesize one English shot to WAV (24kHz mono PCM) and return its exact duration."""
    import time

    words = len(text.split())
    wps = float(AUDIO_CONFIG.get("tts_en", {}).get("words_per_sec_estimate", 2.45))
    est_seconds = max(1.8, round(words / wps, 2))

    use_offline = os.environ.get("VIDEO_OFFLINE_TTS", "").strip() in {"1", "true", "yes"}
    if not use_offline and _has_ffmpeg():
        tmp_mp3 = out_wav.with_suffix(".tmp.mp3")
        for attempt in range(4):
            try:
                asyncio.run(_edge_tts_to_file(text, tmp_mp3, voice=voice, rate=rate))
                if tmp_mp3.exists() and tmp_mp3.stat().st_size > 200:
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-i", str(tmp_mp3),
                        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(out_wav),
                    ]
                    subprocess.run(cmd, check=True, timeout=30)
                    tmp_mp3.unlink(missing_ok=True)
                    return probe_media_duration(out_wav)
            except Exception as exc:
                tmp_mp3.unlink(missing_ok=True)
                if attempt < 3:
                    time.sleep(0.6 * (attempt + 1))
                else:
                    logger.warning("edge-tts failed after 4 attempts for '%s': %s", text[:50], exc)

    _write_synthetic_speech_wav(out_wav, est_seconds)
    return probe_media_duration(out_wav)


def _snap_shot_boundaries_to_silence(
    raw_pcm_s16le: bytes,
    rate: int,
    bounds: list[tuple[float, float]],
    max_search_sec: float = 0.65,
) -> list[tuple[float, float]]:
    """Snap interior shot boundaries that land inside speech to the nearest acoustic silence midpoint."""
    from array import array

    if len(bounds) <= 1 or len(raw_pcm_s16le) < 4 or rate <= 0:
        return bounds

    samples = array("h")
    samples.frombytes(raw_pcm_s16le[: len(raw_pcm_s16le) - (len(raw_pcm_s16le) % 2)])
    n_total = len(samples)
    total_dur = round(n_total / float(rate), 3)
    win = max(1, rate // 100)  # 10ms window
    n_win = n_total // win
    if n_win < 10:
        return bounds

    rms_10ms: list[float] = []
    for w_idx in range(n_win):
        base = w_idx * win
        chunk = samples[base : base + win]
        sq_sum = 0.0
        for val in chunk:
            sq_sum += float(val) * float(val)
        rms_10ms.append(math.sqrt(sq_sum / win))

    search_wins = max(10, int(round(max_search_sec * 100)))
    cut_points: list[float] = [0.0]
    for i in range(len(bounds) - 1):
        nom_cut = float(bounds[i][1])
        w_cut = max(0, min(n_win - 1, int(round(nom_cut * 100))))
        local_slice = rms_10ms[max(0, w_cut - 2) : min(n_win, w_cut + 3)]
        local_max = max(local_slice) if local_slice else 0.0

        snapped_cut = nom_cut
        if local_max > 20.0:
            w0 = max(0, w_cut - search_wins)
            w1 = min(n_win, w_cut + search_wins)
            best_mid: float | None = None
            cur = w0
            while cur < w1:
                if rms_10ms[cur] < 18.0:
                    s0 = cur
                    while cur < w1 and rms_10ms[cur] < 18.0:
                        cur += 1
                    if cur - s0 >= 14:  # at least 140ms of clean silence
                        mid_s = round(((s0 + cur) / 2.0) * 0.01, 3)
                        if best_mid is None or abs(mid_s - nom_cut) < abs(best_mid - nom_cut):
                            best_mid = mid_s
                else:
                    cur += 1
            if best_mid is not None:
                snapped_cut = best_mid

        prev_cut = cut_points[-1]
        if snapped_cut <= prev_cut + 0.15:
            snapped_cut = round(max(prev_cut + 0.15, nom_cut), 3)
        cut_points.append(min(total_dur, snapped_cut))

    last_end = max(cut_points[-1] + 0.05, total_dur if total_dur > 0 else float(bounds[-1][1]))
    cut_points.append(round(last_end, 3))
    return [(round(cut_points[i], 3), round(cut_points[i + 1], 3)) for i in range(len(bounds))]


def _trim_and_smooth_voice_pcm(
    raw_pcm_s16le: bytes,
    rate: int = 24000,
    rms_threshold: float = 22.0,
    max_intra_pause_sec: float = 0.36,
    target_intra_pause_sec: float = 0.32,
    fade_in_ms: float = 18.0,
    fade_out_ms: float = 25.0,
) -> bytes:
    """Trim leading/trailing silence, tighten excessive intra-shot dead pauses, and apply cosine fades."""
    from array import array

    if len(raw_pcm_s16le) < 4:
        return raw_pcm_s16le

    samples = array("h")
    samples.frombytes(raw_pcm_s16le[: len(raw_pcm_s16le) - (len(raw_pcm_s16le) % 2)])
    n_total = len(samples)
    win = max(1, rate // 100)  # 10ms window (240 samples at 24kHz)
    n_win = n_total // win
    if n_win < 4:
        return samples.tobytes()

    is_speech: list[bool] = []
    for w_idx in range(n_win):
        base = w_idx * win
        chunk = samples[base : base + win]
        sq_sum = 0.0
        for val in chunk:
            sq_sum += float(val) * float(val)
        rms = math.sqrt(sq_sum / win)
        is_speech.append(rms >= rms_threshold)

    speech_indices = [i for i, sp in enumerate(is_speech) if sp]
    if not speech_indices:
        return samples.tobytes()

    first_sp = max(0, speech_indices[0] - 3)  # keep 30ms natural breath / unvoiced consonant onset
    last_sp = min(n_win - 1, speech_indices[-1] + 5)  # keep 50ms natural plosive release tail

    max_intra_wins = max(10, int(round(max_intra_pause_sec * 100)))
    target_intra_wins = max(8, min(max_intra_wins, int(round(target_intra_pause_sec * 100))))
    inner_fade_samples = max(1, int(round(rate * 0.015)))

    out_samples = array("h")
    w_cursor = first_sp
    while w_cursor <= last_sp:
        if is_speech[w_cursor]:
            out_samples.extend(samples[w_cursor * win : (w_cursor + 1) * win])
            w_cursor += 1
        else:
            sil_start = w_cursor
            while w_cursor <= last_sp and not is_speech[w_cursor]:
                w_cursor += 1
            sil_len = w_cursor - sil_start
            if sil_len > max_intra_wins:
                # Smooth fade-out on preceding speech tail before compressed pause
                n_out = len(out_samples)
                f_len = min(inner_fade_samples, n_out)
                for k in range(f_len):
                    gain = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / float(f_len)))
                    idx_s = n_out - f_len + k
                    out_samples[idx_s] = int(round(out_samples[idx_s] * gain))
                out_samples.extend([0] * (target_intra_wins * win))
                # Smooth fade-in on following speech start
                if w_cursor <= last_sp:
                    next_start = w_cursor * win
                    next_end = min(n_total, next_start + inner_fade_samples)
                    f_in = next_end - next_start
                    for k in range(f_in):
                        gain = 0.5 * (1.0 - math.cos(math.pi * (k + 1) / float(f_in)))
                        samples[next_start + k] = int(round(samples[next_start + k] * gain))
            else:
                out_samples.extend(samples[sil_start * win : w_cursor * win])

    if len(out_samples) < int(rate * 0.08):
        out_samples = samples[first_sp * win : (last_sp + 1) * win]

    n_final = len(out_samples)
    fi_len = min(max(1, int(round(rate * (fade_in_ms / 1000.0)))), n_final // 2)
    fo_len = min(max(1, int(round(rate * (fade_out_ms / 1000.0)))), n_final // 2)

    for k in range(fi_len):
        gain = 0.5 * (1.0 - math.cos(math.pi * (k + 1) / float(fi_len)))
        out_samples[k] = int(round(out_samples[k] * gain))
    for k in range(fo_len):
        gain = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / float(fo_len)))
        idx_s = n_final - fo_len + k
        out_samples[idx_s] = int(round(out_samples[idx_s] * gain))

    return out_samples.tobytes()


def _mix_bgm_bed(
    raw_voice_wav: Path,
    out_wav: Path,
    total_dur: float,
    shot_durations: list[float] | None = None,
    rotation_seed: str = "",
) -> None:
    """Mix multi-track narrative BGM cues under the voice track with steady non-pumping loudness."""
    lufs = AUDIO_CONFIG.get("master", {}).get("loudness_lufs", -16)
    tp = AUDIO_CONFIG.get("master", {}).get("true_peak_dbtp", -1.5)
    duck_db = float(AUDIO_CONFIG.get("bgm", {}).get("duck_db", -16.5))
    bed_target_lufs = float(AUDIO_CONFIG.get("bgm", {}).get("bed_target_lufs", -34.5))
    use_offline = os.environ.get("VIDEO_OFFLINE_TTS", "").strip() in {"1", "true", "yes"}

    if not _has_ffmpeg() or use_offline:
        if raw_voice_wav != out_wav and raw_voice_wav.exists():
            shutil.copy2(str(raw_voice_wav), str(out_wav))
        return

    bgm_dir = _get_bgm_dir()
    loudness_map = _load_bgm_loudness(bgm_dir)
    cues = _build_narrative_bgm_cues(total_dur, shot_durations)

    inputs: list[str] = ["-i", str(raw_voice_wav)]
    filter_parts: list[str] = []
    bgm_labels: list[str] = []

    valid_cues_count = 0
    n_cues = len(cues)
    crossfade_half_s = 0.85
    for k, (role, start_s, end_s) in enumerate(cues):
        eff_start = max(0.0, round(start_s - (crossfade_half_s if k > 0 else 0.0), 3))
        eff_end = min(total_dur, round(end_s + (crossfade_half_s if k < n_cues - 1 else 0.0), 3))
        cue_dur = round(eff_end - eff_start, 3)
        if cue_dur <= 0.5:
            continue
        mood_options = BEAT_ROLE_MOODS.get(role, ["mysterious"])
        pool: list[Path] = []
        for m in mood_options:
            pool.extend(bgm_dir.glob(f"{m}__*.mp3"))
        if not pool:
            continue
        pool.sort()
        track_id = f"beat_{role}_{k}"
        chosen_idx = zlib.crc32(f"{rotation_seed}|{track_id}".encode("utf-8")) % len(pool)
        track_file = pool[chosen_idx]

        entry = loudness_map.get(track_file.name)
        if entry and entry.get("lufs") is not None:
            gain_db = max(-30.0, min(-6.0, round(bed_target_lufs - float(entry["lufs"]), 1)))
        else:
            gain_db = duck_db

        input_idx = 1 + valid_cues_count
        valid_cues_count += 1
        inputs.extend(["-stream_loop", "-1", "-i", str(track_file)])

        fade_in_d = min(1.7, max(0.5, round(cue_dur * 0.12, 2))) if k > 0 else 0.6
        fade_out_d = min(1.7, max(0.5, round(cue_dur * 0.12, 2)))
        delay_ms = max(0, int(round(eff_start * 1000)))
        fade_out_st = max(0.0, round(cue_dur - fade_out_d, 3))

        flt = (
            f"[{input_idx}:a]aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono,"
            f"silenceremove=start_periods=1:start_threshold=-55dB:stop_periods=-1:stop_duration=0.35:stop_threshold=-55dB,"
            f"volume={gain_db}dB,"
            f"afade=t=in:st=0:d={fade_in_d}:curve=qsin,"
            f"afade=t=out:st={fade_out_st}:d={fade_out_d}:curve=qsin,"
            f"atrim=0:{cue_dur},"
            f"adelay={delay_ms}[bgm{k}]"
        )
        filter_parts.append(flt)
        bgm_labels.append(f"[bgm{k}]")

    if valid_cues_count > 0:
        pad_idx = 1 + valid_cues_count
        inputs.extend(["-f", "lavfi", "-i", f"anoisesrc=color=pink:r=24000:a=0.005:d={total_dur:.3f}"])
        filter_parts.append(
            f"[{pad_idx}:a]aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono,"
            f"lowpass=f=320,volume=-14dB[room_pad]"
        )
        bgm_labels.append("[room_pad]")
        filter_parts.append(
            f"{''.join(bgm_labels)}amix=inputs={len(bgm_labels)}:dropout_transition=0:normalize=0[all_bgm]"
        )
        all_bgm = "[all_bgm]"
        # Normalize voice track BEFORE mixing steady BGM bed so loudnorm never pumps BGM in speech pauses.
        # Explicitly resample loudnorm output (192kHz) back to 24kHz s16 mono before amix so FFmpeg never drops the 3s lookahead buffer.
        filter_parts.append(
            f"[0:a]loudnorm=I={lufs}:TP={tp}:LRA=11,"
            f"aresample=24000,aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono[voice_norm];"
            f"[voice_norm]{all_bgm}amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.891:level=disabled[out]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[out]",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_wav),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=180)
            return
        except Exception as exc:
            logger.warning("Real BGM mix failed (%s), falling back to ambient pink noise bed", exc)

    # Fallback: ambient pink noise bed
    cmd_fallback = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(raw_voice_wav),
        "-f", "lavfi", "-i", "anoisesrc=color=pink:r=24000:a=0.008",
        "-filter_complex",
        f"[0:a]loudnorm=I={lufs}:TP={tp}:LRA=11,"
        f"aresample=24000,aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono[voice_norm];"
        f"[1:a]aformat=sample_fmts=s16:sample_rates=24000:channel_layouts=mono,lowpass=f=400,volume={duck_db}dB[bgm];"
        f"[voice_norm][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit=0.891:level=disabled[out]",
        "-map", "[out]",
        "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out_wav),
    ]
    try:
        subprocess.run(cmd_fallback, check=True, timeout=60)
    except Exception as exc2:
        logger.warning("Fallback ambient mix failed (%s), keeping raw voice", exc2)
        if raw_voice_wav != out_wav and raw_voice_wav.exists():
            shutil.copy2(str(raw_voice_wav), str(out_wav))


def _concat_wav_segments(
    shot_wavs: list[Path],
    out_wav: Path,
    inter_shot_pause_sec: float | None = None,
    bgm_enabled: bool = True,
    rotation_seed: str = "",
) -> tuple[float, list[tuple[float, float]]]:
    """Concatenate per-shot WAV files into master WAV and return (total_dur, [(start, end), ...]).

    Each shot's window tiles the timeline contiguously (cut lands in the midpoint
    of the inter-shot pause, matching word_timing.py's pause-centered cut law).
    """
    pause_sec = (
        float(AUDIO_CONFIG.get("tts_en", {}).get("inter_shot_pause_sec", 0.24))
        if inter_shot_pause_sec is None
        else float(inter_shot_pause_sec)
    )
    rate = 24000
    pause_frames = int(round(rate * pause_sec))
    half_pause_sec = pause_sec / 2.0

    pcm_chunks: list[bytes] = []
    raw_durations: list[float] = []

    for idx, wav_path in enumerate(shot_wavs):
        with wave.open(str(wav_path), "rb") as r:
            frames = r.readframes(r.getnframes())
            f_rate = r.getframerate()
            if f_rate == rate and r.getsampwidth() == 2 and r.getnchannels() == 1:
                frames = _trim_and_smooth_voice_pcm(frames, rate=rate)
            dur = (len(frames) // 2) / float(f_rate)
            raw_durations.append(dur)
            pcm_chunks.append(frames)
        if idx < len(shot_wavs) - 1 and pause_frames > 0:
            pcm_chunks.append(b"\x00\x00" * pause_frames)

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    voice_clean_wav = out_wav.parent / "voice_clean.wav"
    raw_voice_wav = voice_clean_wav if bgm_enabled else out_wav
    with wave.open(str(raw_voice_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for chunk in pcm_chunks:
            w.writeframes(chunk)

    if not bgm_enabled and out_wav != voice_clean_wav:
        shutil.copy2(str(out_wav), str(voice_clean_wav))

    if bgm_enabled:
        voice_dur = probe_media_duration(raw_voice_wav)
        _mix_bgm_bed(
            raw_voice_wav, out_wav, total_dur=voice_dur,
            shot_durations=raw_durations, rotation_seed=rotation_seed,
        )

    total_dur = probe_media_duration(out_wav)

    # Compute exact pause-centered contiguous windows tiling [0.0, total_dur] without cumulative rounding drift
    windows: list[tuple[float, float]] = []
    exact_cursor = 0.0
    n = len(raw_durations)
    for i, dur in enumerate(raw_durations):
        start = 0.0 if i == 0 else windows[-1][1]
        if i == n - 1:
            end = round(total_dur, 3)
        else:
            add_pause = half_pause_sec + (half_pause_sec if i > 0 else 0.0)
            exact_cursor += dur + add_pause
            end = round(min(total_dur, exact_cursor), 3)
        windows.append((start, end))

    try:
        clean_shots_meta = [
            {"index": i + 1, "audio_start_sec": s_st, "audio_end_sec": s_en}
            for i, (s_st, s_en) in enumerate(windows)
        ]
        (out_wav.parent / "voice_clean_shots.json").write_text(
            json.dumps(clean_shots_meta, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    return total_dur, windows


def _fmt_srt_time(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_subtitles_en(shots: list[dict[str, Any]], srt_path: Path, ass_path: Path) -> None:
    """Write both .srt and .ass English subtitle files synced to the shot timings."""
    srt_lines: list[str] = []
    ass_lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1280",
        "PlayResY: 720",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,34,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,40,40,36,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for s in shots:
        idx = int(s.get("index") or s.get("index_num") or 1)
        start = float(s["audio_start_sec"])
        end = float(s["audio_end_sec"])
        text = str(s["narration"]).strip()
        srt_lines.append(f"{idx}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n{text}\n")
        clean_ass = text.replace("\n", " ").replace("{", "(").replace("}", ")")
        ass_lines.append(f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Default,,0,0,0,,{clean_ass}")

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    ass_path.write_text("\n".join(ass_lines) + "\n", encoding="utf-8")


def synthesize_en_todo_package(
    shots: list[dict[str, Any]],
    out_dir: Path,
    bgm_enabled: bool = True,
    voice: str | None = None,
) -> dict[str, Any]:
    """Synthesize full English Audio + timed Script JSON + SRT/ASS subtitles in out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp_shots"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    tts_cfg = AUDIO_CONFIG.get("tts_en", {})
    chosen_voice = voice or tts_cfg.get("default_voice", "en-US-AndrewMultilingualNeural")
    rate = tts_cfg.get("rate", "-6%")
    inter_pause = float(tts_cfg.get("inter_shot_pause_sec", 0.32))

    shot_wavs: list[Path] = [Path() for _ in shots]
    try:
        from concurrent.futures import ThreadPoolExecutor

        def _synth_one(item_pair: tuple[int, dict[str, Any]]) -> tuple[int, Path]:
            pos, s = item_pair
            idx = int(s.get("index") or s.get("index_num") or (pos + 1))
            wpath = tmp_dir / f"shot_{idx:04d}.wav"
            _synthesize_shot_wav(str(s["narration"]), wpath, voice=chosen_voice, rate=rate)
            return pos, wpath

        max_workers = 1 if os.environ.get("VIDEO_OFFLINE_TTS", "").strip() in {"1", "true", "yes"} else 5
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for pos, wpath in pool.map(_synth_one, enumerate(shots)):
                shot_wavs[pos] = wpath

        audio_path = out_dir / "audio_en.wav"
        total_dur, windows = _concat_wav_segments(
            shot_wavs, audio_path, inter_shot_pause_sec=inter_pause, bgm_enabled=bgm_enabled,
            rotation_seed=str(out_dir.name),
        )

        timed_shots: list[dict[str, Any]] = []
        for s, (start_sec, end_sec) in zip(shots, windows):
            dur_sec = round(end_sec - start_sec, 3)
            s_merged = dict(s)
            s_merged.update({
                "index": int(s.get("index") or s.get("index_num") or 1),
                "shot_type": s.get("shot_type", "scene"),
                "narration": s["narration"],
                "visual_prompt": s.get("visual_prompt") or s.get("visual_prompt_clean") or "",
                "visual_prompt_clean": s.get("visual_prompt_clean") or s.get("visual_prompt") or "",
                "on_screen_text": s.get("on_screen_text"),
                "on_screen_type": s.get("on_screen_type"),
                "audio_start_sec": start_sec,
                "audio_end_sec": end_sec,
                "duration_sec": dur_sec,
            })
            timed_shots.append(s_merged)

        srt_path = out_dir / "subtitles_en.srt"
        ass_path = out_dir / "subtitles_en.ass"
        write_subtitles_en(timed_shots, srt_path, ass_path)

        from .skill_bundle import get_skill_summary

        script_path = out_dir / "script_en.json"
        script_doc = {
            "language": "en",
            "voice": chosen_voice,
            "bgm_enabled": bgm_enabled,
            "audio_duration_sec": total_dur,
            "total_shots": len(timed_shots),
            "skill_bundle": get_skill_summary(),
            "shots": timed_shots,
        }
        script_path.write_text(json.dumps(script_doc, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "audio_path": audio_path,
            "script_path": script_path,
            "srt_path": srt_path,
            "ass_path": ass_path,
            "audio_duration_sec": total_dur,
            "shots": timed_shots,
            "voice": chosen_voice,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def retime_and_mix_existing_audio(
    audio_path: Path,
    shots: list[dict[str, Any]],
    out_dir: Path,
    slow_factor: float = 0.96,
    extra_pause_sec: float = 0.04,
    bgm_enabled: bool = True,
    bgm_duck_db: float = -16.5,
    rotation_seed: str = "",
) -> dict[str, Any]:
    """Retime an existing audio's per-shot voice segments with atempo and mix multi-track BGM.

    - Prefers out_dir / 'voice_clean.wav' over a BGM-mixed audio_en.wav so BGM is never sliced.
    - Trims pre-existing inter-shot pauses and smooths each voice chunk with cosine fades.
    - Inserts crisp inter-shot pause (inter_shot_pause_sec = 0.20 + extra_pause_sec).
    - Mixes continuous multi-track BGM cues (-34.5 LUFS) after voice normalization.
    - Recomputes exact contiguous timings for every shot and regenerates script + subtitle files.
    """
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if not shots:
        raise ValueError("Shots list cannot be empty")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp_retime"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    inter_shot_pause = round(max(0.16, 0.20 + extra_pause_sec), 3)
    half_pause = inter_shot_pause / 2.0
    rate = 24000

    # Always prefer clean voice track over BGM-mixed track to avoid slicing BGM
    voice_clean_path = out_dir / "voice_clean.wav"
    voice_clean_shots_path = out_dir / "voice_clean_shots.json"
    had_voice_clean = voice_clean_path.exists()
    source_wav_for_slicing = voice_clean_path if had_voice_clean else audio_path
    clean_bounds_by_idx: dict[int, tuple[float, float]] = {}
    if had_voice_clean and voice_clean_shots_path.exists():
        try:
            clean_meta = json.loads(voice_clean_shots_path.read_text(encoding="utf-8"))
            if isinstance(clean_meta, list) and len(clean_meta) == len(shots):
                for cm in clean_meta:
                    c_ix = int(cm.get("index") or 0)
                    if c_ix > 0:
                        clean_bounds_by_idx[c_ix] = (
                            float(cm.get("audio_start_sec") or 0.0),
                            float(cm.get("audio_end_sec") or 0.0),
                        )
        except Exception:
            pass

    try:
        with wave.open(str(source_wav_for_slicing), "rb") as w:
            in_rate = w.getframerate()
            in_sampwidth = w.getsampwidth()
            in_nchannels = w.getnchannels()
            in_nframes = w.getnframes()
            full_pcm = w.readframes(in_nframes)

        initial_bounds: list[tuple[float, float]] = []
        for idx, s in enumerate(shots):
            idx_num = int(s.get("index") or s.get("index_num") or (idx + 1))
            if idx_num in clean_bounds_by_idx:
                start_s, end_s = clean_bounds_by_idx[idx_num]
            else:
                start_s = float(s.get("audio_start_sec") or 0.0)
                end_s = float(s.get("audio_end_sec") or (start_s + 1.0))
            initial_bounds.append((start_s, end_s))

        if in_sampwidth == 2 and in_nchannels == 1:
            snapped_bounds = _snap_shot_boundaries_to_silence(full_pcm, in_rate, initial_bounds)
        else:
            snapped_bounds = initial_bounds

        # If voice_clean.wav existed but voice_clean_shots.json was missing or had unaligned bounds, persist snapped bounds for the clean source
        if had_voice_clean and not voice_clean_shots_path.exists():
            try:
                voice_clean_shots_path.write_text(
                    json.dumps(
                        [
                            {
                                "index": int(s.get("index") or s.get("index_num") or (i + 1)),
                                "audio_start_sec": b_st,
                                "audio_end_sec": b_en,
                            }
                            for i, (s, (b_st, b_en)) in enumerate(zip(shots, snapped_bounds))
                        ],
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

        retimed_pcm_chunks: list[bytes] = []
        retimed_durations: list[float] = []

        for idx, (s, (start_s, end_s)) in enumerate(zip(shots, snapped_bounds)):
            idx_num = int(s.get("index") or s.get("index_num") or (idx + 1))

            start_f = max(0, min(in_nframes, int(round(start_s * in_rate))))
            end_f = max(start_f + 1, min(in_nframes, int(round(end_s * in_rate))))
            if start_f >= in_nframes:
                start_f = max(0, in_nframes - int(round(0.2 * in_rate)))
                end_f = in_nframes

            raw_chunk = full_pcm[start_f * in_sampwidth * in_nchannels : end_f * in_sampwidth * in_nchannels]
            if len(raw_chunk) == 0:
                raw_chunk = b"\x00\x00" * int(in_rate * 0.2)

            orig_wav = tmp_dir / f"orig_{idx_num:04d}.wav"
            with wave.open(str(orig_wav), "wb") as ow:
                ow.setnchannels(in_nchannels)
                ow.setsampwidth(in_sampwidth)
                ow.setframerate(in_rate)
                ow.writeframes(raw_chunk)

            retimed_wav = tmp_dir / f"retimed_{idx_num:04d}.wav"
            if _has_ffmpeg() and abs(slow_factor - 1.0) > 0.001:
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(orig_wav),
                    "-filter:a", f"atempo={slow_factor:.3f}",
                    "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                    str(retimed_wav),
                ]
                subprocess.run(cmd, check=True, timeout=30)
            elif _has_ffmpeg():
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(orig_wav),
                    "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                    str(retimed_wav),
                ]
                subprocess.run(cmd, check=True, timeout=30)
            else:
                shutil.copy2(str(orig_wav), str(retimed_wav))

            with wave.open(str(retimed_wav), "rb") as rw:
                chunk_bytes = rw.readframes(rw.getnframes())
                rw_rate = rw.getframerate()
                rw_sw = rw.getsampwidth()
                rw_ch = rw.getnchannels()

            if rw_rate == rate and rw_sw == 2 and rw_ch == 1:
                chunk_bytes = _trim_and_smooth_voice_pcm(chunk_bytes, rate=rate)
                dur = (len(chunk_bytes) // 2) / float(rate)
            else:
                dur = probe_media_duration(retimed_wav)

            retimed_durations.append(dur)
            retimed_pcm_chunks.append(chunk_bytes)

            orig_wav.unlink(missing_ok=True)
            retimed_wav.unlink(missing_ok=True)

        # Concatenate retimed voice chunks with inter-shot pause
        pause_frames = int(round(rate * inter_shot_pause))
        voice_wav = tmp_dir / "voice_retimed.wav"
        with wave.open(str(voice_wav), "wb") as vw:
            vw.setnchannels(1)
            vw.setsampwidth(2)
            vw.setframerate(rate)
            for idx_c, ch in enumerate(retimed_pcm_chunks):
                vw.writeframes(ch)
                if idx_c < len(retimed_pcm_chunks) - 1 and pause_frames > 0:
                    vw.writeframes(b"\x00\x00" * pause_frames)

        # Only populate voice_clean.wav if no clean voice source existed prior to this call
        # (never overwrite the original 1.0x clean voice track with an atempo-retimed track).
        if not had_voice_clean:
            shutil.copy2(str(voice_wav), str(voice_clean_path))

        voice_dur = probe_media_duration(voice_wav)
        final_audio_path = out_dir / "audio_en.wav"

        if bgm_enabled:
            _mix_bgm_bed(
                voice_wav, final_audio_path, total_dur=voice_dur,
                shot_durations=retimed_durations, rotation_seed=rotation_seed,
            )
        else:
            shutil.copy2(str(voice_wav), str(final_audio_path))

        final_dur = probe_media_duration(final_audio_path)

        # Compute exact pause-centered contiguous timing windows tiling [0, final_dur]
        windows: list[tuple[float, float]] = []
        exact_cursor = 0.0
        n = len(retimed_durations)
        for i, dur in enumerate(retimed_durations):
            start = 0.0 if i == 0 else windows[-1][1]
            if i == n - 1:
                end = round(final_dur, 3)
            else:
                add_pause = half_pause + (half_pause if i > 0 else 0.0)
                exact_cursor += dur + add_pause
                end = round(min(final_dur, exact_cursor), 3)
            windows.append((start, end))

        if not had_voice_clean:
            try:
                clean_shots_meta = [
                    {
                        "index": int(s.get("index") or s.get("index_num") or (i + 1)),
                        "audio_start_sec": w_st,
                        "audio_end_sec": w_en,
                    }
                    for i, (s, (w_st, w_en)) in enumerate(zip(shots, windows))
                ]
                voice_clean_shots_path.write_text(
                    json.dumps(clean_shots_meta, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

        updated_shots: list[dict[str, Any]] = []
        for s, (s_start, s_end) in zip(shots, windows):
            s_copy = dict(s)
            s_copy["audio_start_sec"] = s_start
            s_copy["audio_end_sec"] = s_end
            s_copy["duration_sec"] = round(s_end - s_start, 3)
            vp = str(s.get("visual_prompt") or s.get("visual_prompt_clean") or "")
            s_copy["visual_prompt"] = vp
            s_copy["visual_prompt_clean"] = s.get("visual_prompt_clean") or vp
            updated_shots.append(s_copy)

        srt_path = out_dir / "subtitles_en.srt"
        ass_path = out_dir / "subtitles_en.ass"
        write_subtitles_en(updated_shots, srt_path, ass_path)

        from .skill_bundle import get_skill_summary

        script_path = out_dir / "script_en.json"
        existing_script_doc = {}
        if script_path.exists():
            try:
                existing_script_doc = json.loads(script_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        existing_shots_map = {
            int(es.get("index") or es.get("index_num") or idx + 1): es
            for idx, es in enumerate(existing_script_doc.get("shots", []))
        }
        final_shots_for_doc: list[dict[str, Any]] = []
        for s in updated_shots:
            s_idx = int(s.get("index") or s.get("index_num") or 1)
            orig = existing_shots_map.get(s_idx, {})
            final_shots_for_doc.append({**orig, **s})

        script_doc = {
            **existing_script_doc,
            "language": "en",
            "audio_duration_sec": final_dur,
            "total_shots": len(final_shots_for_doc),
            "bgm_enabled": bgm_enabled,
            "skill_bundle": get_skill_summary(),
            "shots": final_shots_for_doc,
        }
        script_path.write_text(json.dumps(script_doc, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "audio_path": final_audio_path,
            "script_path": script_path,
            "srt_path": srt_path,
            "ass_path": ass_path,
            "audio_duration_sec": final_dur,
            "shots": updated_shots,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
