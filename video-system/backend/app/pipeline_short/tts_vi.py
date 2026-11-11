"""TTS tiếng Việt với WordBoundary cho karaoke (Phụ lục G.5).

Online: edge-tts stream (audio mp3 + sự kiện WordBoundary offset 100ns) → mp3
→ ffmpeg chuyển WAV 24kHz mono → nối PCM + pause → timeline từ tuyệt đối.
Offline (`VIDEO_OFFLINE_TTS=1` hoặc thiếu ffmpeg/edge-tts): WAV tổng hợp
deterministic + timeline từ phân bổ đều (pattern `_write_synthetic_speech_wav`
của pipeline_en) — test không cần mạng.

Trả: {audio_path, duration, words: [{text, start, end}], voice, offline: bool}
"""
from __future__ import annotations

import logging
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from ..config import SHORT_CONFIG, data_dir

logger = logging.getLogger("video_system.pipeline_short.tts_vi")

_WAV_RATE = 24000


def _offline() -> bool:
    return os.environ.get("VIDEO_OFFLINE_TTS", "").strip() in {"1", "true", "yes"}


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _write_synthetic_speech_wav(path: Path, seconds: float, freq: float = 220.0) -> None:
    """Deterministic speech-like WAV (pipeline_en pattern)."""
    rate = _WAV_RATE
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


def _mp3_to_wav(mp3: Path, wav: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
         "-ar", str(_WAV_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        check=True, timeout=60,
    )


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _concat_wavs(paths: list[Path], pause_sec: float, out_path: Path) -> float:
    """Nối PCM 16-bit mono cùng sample rate, chèn pause giữa cue. Trả tổng duration."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pause_frames = max(0, int(round(pause_sec * _WAV_RATE)))
    silence = b"\x00\x00" * pause_frames
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(_WAV_RATE)
        for p in paths:
            with wave.open(str(p), "rb") as w:
                if w.getframerate() != _WAV_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise ValueError(f"wav format không đồng nhất: {p}")
                out.writeframes(w.readframes(w.getnframes()))
            out.writeframes(silence)
    return _wav_duration(out_path)


def _cue_words_offline(text: str, cue_start: float, duration: float) -> list[dict]:
    """Phân bổ đều từ trong cue duration (offline — honest cho test)."""
    words = text.split()
    if not words or duration <= 0:
        return []
    per = duration / len(words)
    return [
        {"text": w, "start": round(cue_start + i * per, 3),
         "end": round(cue_start + (i + 1) * per, 3)}
        for i, w in enumerate(words)
    ]


async def _edge_stream_cue(text: str, voice: str, rate: str, mp3_path: Path) -> list[dict]:
    """Synthesize 1 cue, thu WordBoundary. Trả words offset giây theo đầu cue."""
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate)
    words: list[dict] = []
    with open(mp3_path, "wb") as af:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                af.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts offset/duration theo đơn vị 100 nano giây
                start = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append({"text": chunk["text"], "start": round(start, 3),
                              "end": round(start + dur, 3)})
    return words


def synthesize_short_voice(texts: list[str], out_dir: Path, short_id: str,
                           voice: str | None = None) -> dict:
    """Tổng hợp toàn bộ dãy thoại → 1 audio + word timeline tuyệt đối."""
    if not texts:
        raise ValueError("Danh sách thoại rỗng.")
    cfg = SHORT_CONFIG["tts_vi"]
    chosen_voice = voice or cfg["default_voice"]
    rate = cfg["rate"]
    pause = float(cfg.get("inter_scene_pause_sec", 0.24))
    wps = float(cfg.get("words_per_sec_estimate", 2.6))

    work = out_dir / "audio"
    work.mkdir(parents=True, exist_ok=True)

    words: list[dict] = []
    cue_spans: list[dict] = []
    cue_paths: list[Path] = []
    cue_start = 0.0
    offline = _offline() or not _has_ffmpeg()

    for idx, text in enumerate(texts):
        cue_wav = work / f"cue_{idx:02d}.wav"
        cue_words: list[dict] = []
        if not offline:
            try:
                import asyncio
                mp3 = work / f"cue_{idx:02d}.tmp.mp3"
                cue_words = asyncio.run(_edge_stream_cue(text, chosen_voice, rate, mp3))
                _mp3_to_wav(mp3, cue_wav)
                mp3.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("edge-tts cue %d thất bại (%s) → fallback offline", idx, exc)
                cue_words = []
                offline = True
        if offline:
            est = max(1.2, round(len(text.split()) / wps, 2))
            _write_synthetic_speech_wav(cue_wav, est)
            cue_words = []
        cue_paths.append(cue_wav)

        cue_dur = _wav_duration(cue_wav)
        if not cue_words:
            cue_words = _cue_words_offline(text, cue_start, cue_dur)
        else:
            # edge-tts word offset có thể chệch đầu cue → scale vào duration thật
            scale = cue_dur / max(0.05, cue_words[-1]["end"]) if cue_words else 1.0
            cue_words = [
                {"text": w["text"],
                 "start": round(cue_start + w["start"] * scale, 3),
                 "end": round(cue_start + w["end"] * scale, 3)}
                for w in cue_words
            ]
        words.extend(cue_words)
        cue_end = round(cue_start + cue_dur, 3)
        cue_spans.append({"text": text, "start": round(cue_start, 3), "end": cue_end})
        cue_start = round(cue_start + cue_dur + (pause if idx < len(texts) - 1 else 0.0), 3)

    final_wav = work / f"voice_{short_id}.wav"
    duration = _concat_wavs(cue_paths, pause, final_wav)
    for p in cue_paths:
        if p != final_wav:
            p.unlink(missing_ok=True)

    return {
        "audio_path": str(final_wav),
        "duration": round(duration, 3),
        "words": words,
        "cue_spans": cue_spans,
        "voice": chosen_voice,
        "offline": offline,
        "object_key": f"shorts/{short_id}/audio/voice_{short_id}.wav",
    }


def objects_root(short_id: str) -> Path:
    return data_dir() / "objects" / "shorts" / short_id
