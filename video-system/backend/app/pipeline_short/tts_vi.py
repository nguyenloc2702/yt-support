"""TTS cho Short (VI/EN theo `tts_vi`/`tts_en` config) với karaoke (G.5/G.12).

Online: edge-tts stream (audio mp3 + sự kiện WordBoundary offset 100ns) → mp3
→ ffmpeg chuyển WAV 24kHz mono → nối PCM + pause → timeline từ tuyệt đối.
Lỗi transient edge-tts → retry `_EDGE_RETRIES` lần/cue; 1 cue vẫn fail chỉ cue
đó fallback offline, các cue sau thử lại bình thường.
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
import time
import wave
from pathlib import Path

from ..config import SHORT_CONFIG, data_dir

logger = logging.getLogger("video_system.pipeline_short.tts_vi")

_WAV_RATE = 24000
_EDGE_RETRIES = 3          # edge-tts lỗi transient → retry trước khi fallback
_sleep = time.sleep        # tách ra để test monkeypatch không chờ thật


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

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
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


_DEFAULT_EDGE_STREAM_CUE = _edge_stream_cue


def _trim_pcm_silence(in_wav: Path, out_wav: Path, thresh: int = 380,
                      pad_lead_s: float = 0.03, pad_tail_s: float = 0.05) -> tuple[float, float, float]:
    """Cắt khoảng lặng đầu/cuối WAV 16-bit mono để nhịp nói dứt khoát."""
    with wave.open(str(in_wav), "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    n = len(raw) // 2
    if n == 0:
        shutil.copy2(in_wav, out_wav)
        return 0.0, 0.0, 0.0
    samples = struct.unpack(f"<{n}h", raw)
    orig_dur = n / float(rate)
    first = next((i for i, s in enumerate(samples) if abs(s) > thresh), 0)
    last = next((i for i in range(n - 1, -1, -1) if abs(samples[i]) > thresh), n - 1)
    start_idx = max(0, first - int(pad_lead_s * rate))
    end_idx = min(n, last + int(pad_tail_s * rate))
    if end_idx <= start_idx:
        start_idx, end_idx = 0, n
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(raw[start_idx * 2 : end_idx * 2])
    return orig_dur, start_idx / float(rate), (end_idx - start_idx) / float(rate)


def _norm_tok(s: str) -> str:
    import re
    return re.sub(r"[^0-9A-Za-zÀ-ỹ]", "", s.lower())


def _assign_cues_from_words(texts: list[str], words: list[dict], total_dur: float) -> list[dict]:
    """Chia word timeline 1-call về từng cue theo token alignment."""
    import re
    if not words:
        per = total_dur / max(1, len(texts))
        return [{"text": t, "start": round(i * per, 3), "end": round((i + 1) * per, 3)}
                for i, t in enumerate(texts)]
    cue_toks = [[_norm_tok(x) for x in re.findall(r"[0-9A-Za-zÀ-ỹ]+", c) if _norm_tok(x)]
                for c in texts]
    total_exp = sum(len(t) for t in cue_toks) or 1
    cue_end_idx: list[int] = []
    search_start = 0
    cum_exp = 0
    for toks in cue_toks[:-1]:
        cum_exp += len(toks)
        est = min(len(words) - 2, max(search_start, int(round(len(words) * (cum_exp / total_exp))) - 1))
        last_tok = toks[-1] if toks else ""
        chosen = est
        if last_tok:
            lo = max(search_start, est - 6)
            hi = min(len(words) - 2, est + 6)
            best_dist = 999
            for k in range(lo, hi + 1):
                if _norm_tok(words[k]["text"]) == last_tok and abs(k - est) < best_dist:
                    best_dist = abs(k - est)
                    chosen = k
        cue_end_idx.append(chosen)
        search_start = chosen + 1
    cue_end_idx.append(len(words) - 1)

    spans: list[dict] = []
    span_start = 0.0
    for ci, text in enumerate(texts):
        end_wi = cue_end_idx[ci]
        if ci < len(texts) - 1 and end_wi + 1 < len(words):
            span_end = round((words[end_wi]["end"] + words[end_wi + 1]["start"]) / 2.0, 3)
        else:
            span_end = round(total_dur, 3)
        spans.append({"text": text, "start": round(span_start, 3),
                      "end": max(round(span_start + 0.5, 3), span_end)})
        span_start = spans[-1]["end"]
    return spans


def synthesize_short_voice(texts: list[str], out_dir: Path, short_id: str,
                           voice: str | None = None, lang: str = "vi") -> dict:
    """Tổng hợp toàn bộ dãy thoại → 1 audio + word timeline tuyệt đối.
    lang='en' dùng block tts_en (mảng quốc tế); voice override đè config."""
    if not texts:
        raise ValueError("Danh sách thoại rỗng.")
    cfg_key = "tts_en" if lang == "en" else "tts_vi"
    cfg = SHORT_CONFIG[cfg_key]
    chosen_voice = voice or cfg["default_voice"]
    fallback_voice = cfg.get("fallback_voice") or chosen_voice
    rate = cfg["rate"]
    pause = float(cfg.get("inter_scene_pause_sec", 0.14))
    wps = float(cfg.get("words_per_sec_estimate", 2.6))

    work = out_dir / "audio"
    work.mkdir(parents=True, exist_ok=True)
    final_wav = work / f"voice_{short_id}.wav"
    offline = _offline() or not _has_ffmpeg()

    # Đường nhanh ổn định (production): gọi 1 lần duy nhất cho toàn bộ kịch bản
    # để tránh rate-limit Microsoft + giữ nhịp đọc tự nhiên. Bỏ qua nếu test
    # monkeypatch `_edge_stream_cue`.
    if not offline and len(texts) > 1 and _edge_stream_cue is _DEFAULT_EDGE_STREAM_CUE:
        import asyncio
        full_text = " ".join(t.strip() for t in texts if t.strip())
        full_mp3 = work / "full_tts.tmp.mp3"
        full_raw = work / "full_raw.tmp.wav"
        for try_voice in (chosen_voice, fallback_voice):
            try:
                raw_words = asyncio.run(_edge_stream_cue(full_text, try_voice, rate, full_mp3))
                if full_mp3.exists() and full_mp3.stat().st_size > 2000:
                    _mp3_to_wav(full_mp3, full_raw)
                    full_mp3.unlink(missing_ok=True)
                    orig_dur, trim_s, trimmed_dur = _trim_pcm_silence(full_raw, final_wav)
                    full_raw.unlink(missing_ok=True)
                    if not raw_words:
                        words_out = _cue_words_offline(full_text, 0.0, trimmed_dur)
                    else:
                        words_out = []
                        for w in raw_words:
                            ws = max(0.0, round(w["start"] - trim_s, 3))
                            we = min(trimmed_dur, max(ws + 0.05, round(w["end"] - trim_s, 3)))
                            words_out.append({"text": w["text"], "start": ws, "end": we})
                    cue_spans = _assign_cues_from_words(texts, words_out, trimmed_dur)
                    return {
                        "audio_path": str(final_wav),
                        "duration": round(trimmed_dur, 3),
                        "words": words_out,
                        "cue_spans": cue_spans,
                        "voice": try_voice,
                        "offline": False,
                        "object_key": f"shorts/{short_id}/audio/voice_{short_id}.wav",
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("single-call edge-tts (%s) thất bại, fallback per-cue: %s",
                               try_voice, exc)
                full_mp3.unlink(missing_ok=True)
                full_raw.unlink(missing_ok=True)

    words: list[dict] = []
    cue_spans: list[dict] = []
    cue_paths: list[Path] = []
    cue_start = 0.0

    for idx, text in enumerate(texts):
        cue_wav = work / f"cue_{idx:02d}.wav"
        cue_words: list[dict] = []
        got_audio = False
        if not offline:
            # Retry 3 lần: edge-tts thỉnh thoảng lỗi transient ("No audio was
            # received" — throttle dịch vụ). 1 cue fail KHÔNG tắt các cue sau.
            import asyncio
            for attempt in range(_EDGE_RETRIES):
                try:
                    mp3 = work / f"cue_{idx:02d}.tmp.mp3"
                    cue_words = asyncio.run(_edge_stream_cue(text, chosen_voice, rate, mp3))
                    _mp3_to_wav(mp3, cue_wav)
                    mp3.unlink(missing_ok=True)
                    got_audio = True
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("edge-tts cue %d lần %d/%d thất bại (%s)",
                                   idx, attempt + 1, _EDGE_RETRIES, exc)
                    cue_words = []
                    _sleep(min(2.0, 0.5 * (attempt + 1)))
        if not got_audio:
            # Chỉ cue NÀY fallback offline (tone giả + word estimate);
            # các cue tiếp theo vẫn thử edge-tts bình thường.
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
