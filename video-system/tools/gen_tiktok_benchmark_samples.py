"""Generate 2 TikTok-benchmark 9:16 Short MP4 samples (VI + EN) without touching production core.

Sample 1 (VI): `sample_vi_viral_redpill.mp4`
  - Voice: `vi-VN-NamMinhNeural` (+10% rate, PCM silence trimmed, 0.10s pause)
  - Hook (0 -> hook_end): `@thethao247.vn` lower-third dark-navy/crimson gradient card + 3-line hook
  - Subtitles (after hook): `@tkmmonews` 2-line ALL-CAPS bold-italic with word-by-word rounded Red (#E50914) / Gold (#FFB800) pill highlight
  - Audio: Mood-matched ominous BGM (`ominous__crypto.mp3`) + synthesized Impact & Whoosh SFX + loudnorm (-16 LUFS)

Sample 2 (EN): `sample_en_split60_news.mp4`
  - Voice: `en-US-GuyNeural` (+6% rate, PCM silence trimmed, 0.10s pause)
  - Hook (0 -> hook_end): `@cgtnamerica` 50/50 vertical split-screen + cyan/white headline + `IN 30 SECONDS` divider badge
  - Mid-beats: `@geolyth__` red/yellow floating callout pills + 1 animated Data Card (`+240%` / `$4,800 / FEU`) + fast 2-3s cuts
  - Audio: Mood-matched energetic BGM (`energetic__cortosis.mp3`) + Impact & Whoosh SFX + loudnorm (-16 LUFS)

Run:
  python video-system/tools/gen_tiktok_benchmark_samples.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path

import edge_tts
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "verification_artifacts" / "short_samples"
CACHE_DIR = OUT_DIR / "_cache"
WORK_DIR = OUT_DIR / "_work"
BGM_DIR = REPO_ROOT / "assets" / "music" / "bgm"

W, H, FPS = 1080, 1920, 30
WAV_RATE = 24000
STEREO_RATE = 48000

FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_BOLD_ITALIC = "C:/Windows/Fonts/arialbi.ttf"
FONT_SEGOE_BOLD = "C:/Windows/Fonts/segoeuib.ttf"


# --------------------------------------------------------------------------- Env & Stock Media
def load_pixabay_key() -> str:
    key = os.environ.get("VIDEO_PIXABAY_API_KEY", "").strip()
    if key:
        return key
    for cand in (REPO_ROOT / ".env", REPO_ROOT / "video-system" / ".env", Path("C:/app/work/video-system/.env")):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("VIDEO_PIXABAY_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""


def _download_cached(url: str, ext: str) -> Path | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    dst = CACHE_DIR / f"{h}{ext}"
    if dst.exists() and dst.stat().st_size > 10_000:
        return dst
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            r = client.get(url)
        r.raise_for_status()
        if len(r.content) < 10_000:
            return None
        dst.write_bytes(r.content)
        return dst
    except Exception as exc:
        print(f"[stock] download failed {url[:70]}: {exc}")
        return None


def _pick_pixabay_video(variants: dict) -> dict | None:
    mp4s = [
        v for v in (variants or {}).values()
        if isinstance(v, dict) and str(v.get("url") or "").split("?")[0].endswith(".mp4")
        and int(v.get("width") or 0) > 0 and int(v.get("height") or 0) > 0
    ]
    if not mp4s:
        return None
    portrait = [v for v in mp4s if int(v["height"]) >= int(v["width"]) and int(v["width"]) <= 1440]
    if portrait:
        return max(portrait, key=lambda v: int(v["width"]))
    hd_fit = [v for v in mp4s if 640 <= int(v["width"]) <= 1920]
    if hd_fit:
        return max(hd_fit, key=lambda v: int(v["width"]))
    return max(mp4s, key=lambda v: int(v["width"]))


def fetch_stock_broll(queries: list[str], key: str, used_urls: set[str]) -> dict | None:
    """Video-first deduplicated Pixabay fetcher, falling back to vertical photo."""
    if not key:
        return None
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        # 1) Try Pixabay Videos across query candidates
        for q in queries:
            try:
                r = client.get("https://pixabay.com/api/videos/", params={
                    "key": key, "q": q, "per_page": 8, "safesearch": "true",
                })
                r.raise_for_status()
                hits = r.json().get("hits") or []
            except Exception as exc:
                print(f"[stock] video search failed ({q}): {exc}")
                hits = []
            # Prefer vertical hits first
            hits_sorted = sorted(
                hits,
                key=lambda h: 0 if (_pick_pixabay_video(h.get("videos") or {}) or {}).get("height", 0) >= (_pick_pixabay_video(h.get("videos") or {}) or {}).get("width", 1) else 1,
            )
            for hit in hits_sorted:
                best = _pick_pixabay_video(hit.get("videos") or {})
                if not best:
                    continue
                url = best["url"]
                if url in used_urls:
                    continue
                path = _download_cached(url, ".mp4")
                if path:
                    used_urls.add(url)
                    return {"kind": "video", "path": path, "query": q}

        # 2) Fallback: Pixabay Photos (vertical preferred)
        for q in queries:
            try:
                r = client.get("https://pixabay.com/api/", params={
                    "key": key, "q": q, "image_type": "photo",
                    "orientation": "vertical", "per_page": 8, "safesearch": "true",
                })
                r.raise_for_status()
                hits = r.json().get("hits") or []
            except Exception as exc:
                print(f"[stock] photo search failed ({q}): {exc}")
                hits = []
            for hit in hits:
                url = hit.get("largeImageURL") or hit.get("webformatURL")
                if not url or url in used_urls:
                    continue
                path = _download_cached(url, ".jpg")
                if path:
                    used_urls.add(url)
                    return {"kind": "image", "path": path, "query": q}
    return None


# --------------------------------------------------------------------------- TTS + PCM Trim + Audio Mix
async def _edge_stream_cue(text: str, voice: str, rate: str, mp3_path: Path) -> list[dict]:
    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    words: list[dict] = []
    with open(mp3_path, "wb") as af:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                af.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                st = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append({"text": chunk["text"], "start": st, "end": st + dur})
    if not mp3_path.exists() or mp3_path.stat().st_size < 1000:
        raise RuntimeError(f"empty mp3 from edge-tts for voice={voice}")
    return words


def _mp3_to_pcm_wav(mp3: Path, wav: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
         "-ar", str(WAV_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
        check=True, timeout=60,
    )


def _trim_wav_silence(src_wav: Path, dst_wav: Path, threshold: int = 350, pad_ms: int = 30) -> tuple[float, float, float]:
    """Trim leading/trailing near-silence from 16-bit mono PCM WAV.
    Returns (orig_dur, trim_start_sec, trimmed_dur).
    """
    with wave.open(str(src_wav), "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    n = len(raw) // 2
    if n == 0:
        shutil.copy2(src_wav, dst_wav)
        return 0.0, 0.0, 0.0
    samples = struct.unpack(f"<{n}h", raw)
    orig_dur = n / float(rate)

    first = 0
    while first < n and abs(samples[first]) < threshold:
        first += 1
    last = n - 1
    while last > first and abs(samples[last]) < threshold:
        last -= 1

    pad = int(rate * (pad_ms / 1000.0))
    start_idx = max(0, first - pad)
    end_idx = min(n, last + pad + 1)
    if end_idx <= start_idx:
        start_idx, end_idx = 0, n

    sliced = raw[start_idx * 2: end_idx * 2]
    with wave.open(str(dst_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(sliced)

    trim_start = start_idx / float(rate)
    trimmed_dur = (end_idx - start_idx) / float(rate)
    return orig_dur, trim_start, trimmed_dur


def _concat_wavs(paths: list[Path], pause_sec: float, out_path: Path) -> float:
    pause_frames = max(0, int(round(pause_sec * WAV_RATE)))
    silence = b"\x00\x00" * pause_frames
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(WAV_RATE)
        for idx, p in enumerate(paths):
            with wave.open(str(p), "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))
            if idx < len(paths) - 1 and pause_frames > 0:
                out.writeframes(silence)
    with wave.open(str(out_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _norm_tok(s: str) -> str:
    return re.sub(r"[^0-9A-Za-zÀ-ỹ]", "", s.lower())


def _assign_cues_from_words(cues: list[str], words: list[dict], total_dur: float, voice_used: str) -> list[dict]:
    """Partition single-call WordBoundary list into per-cue spans using token alignment."""
    if not words:
        per = total_dur / max(1, len(cues))
        return [{"cue_idx": i, "text": t, "start": round(i * per, 3), "end": round((i + 1) * per, 3), "voice": voice_used} for i, t in enumerate(cues)]

    # Build expected token count per cue to guide search window
    cue_toks = [[_norm_tok(x) for x in re.findall(r"[0-9A-Za-zÀ-ỹ]+", c) if _norm_tok(x)] for c in cues]
    total_exp = sum(len(t) for t in cue_toks) or 1

    cue_end_word_idx: list[int] = []
    search_start = 0
    cum_exp = 0
    for ci, toks in enumerate(cue_toks[:-1]):
        cum_exp += len(toks)
        est_idx = min(len(words) - 2, max(search_start, int(round(len(words) * (cum_exp / total_exp))) - 1))
        last_tok = toks[-1] if toks else ""
        chosen = est_idx
        if last_tok:
            lo = max(search_start, est_idx - 6)
            hi = min(len(words) - 2, est_idx + 6)
            best_dist = 999
            for k in range(lo, hi + 1):
                if _norm_tok(words[k]["text"]) == last_tok and abs(k - est_idx) < best_dist:
                    best_dist = abs(k - est_idx)
                    chosen = k
        cue_end_word_idx.append(chosen)
        search_start = chosen + 1
    cue_end_word_idx.append(len(words) - 1)

    cue_spans: list[dict] = []
    w_cursor = 0
    span_start = 0.0
    for ci, text in enumerate(cues):
        end_wi = cue_end_word_idx[ci]
        for wi in range(w_cursor, end_wi + 1):
            words[wi]["cue_idx"] = ci
        if ci < len(cues) - 1 and end_wi + 1 < len(words):
            span_end = round((words[end_wi]["end"] + words[end_wi + 1]["start"]) / 2.0, 3)
        else:
            span_end = round(total_dur, 3)
        cue_spans.append({
            "cue_idx": ci,
            "text": text,
            "start": round(span_start, 3),
            "end": max(round(span_start + 0.5, 3), span_end),
            "voice": voice_used,
        })
        span_start = cue_spans[-1]["end"]
        w_cursor = end_wi + 1
    return cue_spans


def synthesize_tight_voice(
    cues: list[str],
    work_dir: Path,
    voice: str,
    fallback_voice: str,
    rate: str,
    pause_sec: float = 0.10,
) -> dict:
    """Single-call edge-tts synthesis (avoids per-cue throttling) + PCM silence trim + cue alignment."""
    work_dir.mkdir(parents=True, exist_ok=True)
    full_text = " ".join(c.strip() for c in cues if c.strip())
    cache_key = hashlib.sha1(f"{voice}|{rate}|{full_text}".encode("utf-8")).hexdigest()[:16]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached_mp3 = CACHE_DIR / f"tts_{cache_key}.mp3"
    cached_meta = CACHE_DIR / f"tts_{cache_key}.json"

    mp3 = work_dir / "full_tts.mp3"
    raw_wav = work_dir / "full_raw.wav"
    voice_wav = work_dir / "voice_full.wav"

    raw_words: list[dict] = []
    used_voice = voice
    if cached_mp3.exists() and cached_mp3.stat().st_size > 5000 and cached_meta.exists():
        shutil.copy2(cached_mp3, mp3)
        meta = json.loads(cached_meta.read_text(encoding="utf-8"))
        raw_words = meta["words"]
        used_voice = meta["voice"]
    else:
        for attempt_voice, attempt_rate, wait_s in [
            (voice, rate, 2.0),
            (voice, rate, 6.0),
            (fallback_voice, rate, 6.0),
            (fallback_voice, "+0%", 2.0),
        ]:
            try:
                raw_words = asyncio.run(_edge_stream_cue(full_text, attempt_voice, attempt_rate, mp3))
                used_voice = attempt_voice
                shutil.copy2(mp3, cached_mp3)
                cached_meta.write_text(json.dumps({"voice": used_voice, "words": raw_words}, ensure_ascii=False), encoding="utf-8")
                break
            except Exception as exc:
                print(f"[tts] full-script retry ({attempt_voice} {attempt_rate}): {exc}")
                time.sleep(wait_s)
        else:
            raise RuntimeError(f"TTS failed for voice={voice}")

    _mp3_to_pcm_wav(mp3, raw_wav)
    _orig_dur, trim_start, trimmed_dur = _trim_wav_silence(raw_wav, voice_wav)

    all_words: list[dict] = []
    if raw_words:
        shifted: list[dict] = []
        for w in raw_words:
            s = max(0.0, w["start"] - trim_start)
            e = max(s + 0.04, w["end"] - trim_start)
            shifted.append({"text": w["text"], "start": s, "end": e})
        max_end = max(w["end"] for w in shifted)
        scale = min(1.05, trimmed_dur / max_end) if max_end > trimmed_dur and max_end > 0.05 else 1.0
        for w in shifted:
            ws = round(min(trimmed_dur - 0.04, w["start"] * scale), 3)
            we = round(min(trimmed_dur, max(w["start"] * scale + 0.04, w["end"] * scale)), 3)
            all_words.append({"text": w["text"], "start": ws, "end": we, "cue_idx": 0})

    cue_spans = _assign_cues_from_words(cues, all_words, trimmed_dur, used_voice)
    return {
        "voice_wav": voice_wav,
        "duration": round(trimmed_dur, 3),
        "words": all_words,
        "cue_spans": cue_spans,
    }


def generate_sfx_assets(sfx_dir: Path) -> tuple[Path, Path]:
    sfx_dir.mkdir(parents=True, exist_ok=True)
    impact_path = sfx_dir / "sfx_impact.wav"
    whoosh_path = sfx_dir / "sfx_whoosh.wav"

    # 1) Sub-drop impact (0.45s): 95Hz -> 36Hz + brief punch transient
    dur_i = 0.45
    n_i = int(STEREO_RATE * dur_i)
    buf_i = bytearray(n_i * 4)
    phase = 0.0
    for i in range(n_i):
        t = i / STEREO_RATE
        freq = 95.0 - 59.0 * min(1.0, t / 0.25)
        phase += 2.0 * math.pi * freq / STEREO_RATE
        env = min(1.0, t / 0.008) * math.exp(-t * 7.5)
        click = math.exp(-t * 140.0) * math.sin(2.0 * math.pi * 620.0 * t) * 0.35
        val = int(max(-32000, min(32000, (math.sin(phase) * env * 0.85 + click) * 18000)))
        struct.pack_into("<hh", buf_i, i * 4, val, val)
    with wave.open(str(impact_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(STEREO_RATE)
        w.writeframes(buf_i)

    # 2) Whoosh transition (0.26s): smoothed noise swell
    dur_w = 0.26
    n_w = int(STEREO_RATE * dur_w)
    buf_w = bytearray(n_w * 4)
    rng = random.Random(42)
    lp = 0.0
    for i in range(n_w):
        t = i / STEREO_RATE
        u = t / dur_w
        env = math.sin(math.pi * u) ** 1.6
        raw_n = rng.uniform(-1.0, 1.0)
        alpha = 0.06 + 0.25 * math.sin(math.pi * u)
        lp = lp + alpha * (raw_n - lp)
        val = int(max(-32000, min(32000, lp * env * 13000)))
        struct.pack_into("<hh", buf_w, i * 4, val, val)
    with wave.open(str(whoosh_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(STEREO_RATE)
        w.writeframes(buf_w)

    return impact_path, whoosh_path


def mix_broadcast_audio(
    voice_wav: Path,
    bgm_file: Path,
    impact_wav: Path,
    whoosh_wav: Path,
    whoosh_times: list[float],
    duration: float,
    out_wav: Path,
) -> None:
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(voice_wav),
        "-stream_loop", "-1", "-i", str(bgm_file),
        "-i", str(impact_wav),
        "-i", str(whoosh_wav),
    ]
    filter_parts = [
        "[0:a]aresample=48000,aformat=channel_layouts=stereo[v]",
        "[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=-18dB[bgm]",
        "[2:a]volume=-6dB,adelay=0|0[imp]",
    ]
    mix_labels = ["[v]", "[bgm]", "[imp]"]

    if len(whoosh_times) == 1:
        ms = max(0, int(round(whoosh_times[0] * 1000)))
        filter_parts.append(f"[3:a]volume=-7dB,adelay={ms}|{ms}[w0]")
        mix_labels.append("[w0]")
    elif len(whoosh_times) >= 2:
        splits = "".join(f"[ws{i}]" for i in range(len(whoosh_times)))
        filter_parts.append(f"[3:a]volume=-7dB,asplit={len(whoosh_times)}{splits}")
        for i, wt in enumerate(whoosh_times):
            ms = max(0, int(round(wt * 1000)))
            filter_parts.append(f"[ws{i}]adelay={ms}|{ms}[w{i}]")
            mix_labels.append(f"[w{i}]")

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=2:normalize=0,"
        "loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    )
    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]",
        "-ar", str(STEREO_RATE), "-ac", "2", "-c:a", "pcm_s16le",
        "-t", f"{duration:.3f}",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True, timeout=180)


# --------------------------------------------------------------------------- Fast-Cut Visual Builder
def build_subscenes(cue_spans: list[dict], queries_per_cue: list[list[list[str]]], max_sub_dur: float = 3.3) -> list[dict]:
    """Split cues longer than max_sub_dur into 2 rapid sub-scenes (2.0-3.2s each)."""
    subscenes: list[dict] = []
    seq = 1
    for i, sp in enumerate(cue_spans):
        dur = sp["end"] - sp["start"]
        q_cands = queries_per_cue[i]
        if dur > max_sub_dur and len(q_cands) >= 2:
            mid = round(sp["start"] + dur * 0.5, 3)
            parts = [(sp["start"], mid, q_cands[0]), (mid, sp["end"], q_cands[1])]
        else:
            parts = [(sp["start"], sp["end"], q_cands[0])]
        for s_t, e_t, q_list in parts:
            subscenes.append({
                "seq": seq,
                "cue_idx": i,
                "start": s_t,
                "end": e_t,
                "dur": round(e_t - s_t, 3),
                "queries": q_list,
            })
            seq += 1
    return subscenes


def _render_clip_916(asset: dict | None, dur: float, out_mp4: Path, punch: bool = False) -> None:
    frames = max(2, int(round(dur * FPS)))
    if asset and asset["kind"] == "video":
        if punch:
            vf = (
                f"scale=1188:2112:force_original_aspect_ratio=increase,"
                f"crop=1188:2112,crop=1080:1920:54:96,setsar=1,fps={FPS}"
            )
        else:
            vf = (
                f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS}"
            )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-stream_loop", "-1", "-i", str(asset["path"]),
            "-t", f"{dur:.3f}", "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(out_mp4),
        ]
    elif asset and asset["kind"] == "image":
        vf = (
            "scale=1216:2160:force_original_aspect_ratio=increase,crop=1216:2160,setsar=1,"
            f"zoompan=z='min(zoom+0.0014,1.16)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={W}x{H}:fps={FPS}"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.3f}",
            "-i", str(asset["path"]), "-vf", vf, "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(out_mp4),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", f"color=c=0x101828:s={W}x{H}:d={dur:.3f}:r={FPS}",
            "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(out_mp4),
        ]
    subprocess.run(cmd, check=True, timeout=180)


def _render_split_50_50_hook(top_asset: dict | None, bot_asset: dict | None, dur: float, out_mp4: Path) -> None:
    """@cgtnamerica 50/50 vertical split-screen: top 1080x960 darkened hero, bottom 1080x960 live b-roll."""
    frames = max(2, int(round(dur * FPS)))
    half_h = H // 2

    def _inp_args(ast: dict | None) -> list[str]:
        if ast and ast["kind"] == "video":
            return ["-stream_loop", "-1", "-i", str(ast["path"])]
        if ast and ast["kind"] == "image":
            return ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(ast["path"])]
        return ["-f", "lavfi", "-i", f"color=c=0x0E1624:s={W}x{half_h}:d={dur:.3f}:r={FPS}"]

    cmd = ["ffmpeg", "-y", "-v", "error"] + _inp_args(top_asset) + _inp_args(bot_asset) + [
        "-filter_complex",
        (
            f"[0:v]scale={W}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{half_h},setsar=1,fps={FPS},eq=brightness=-0.16:contrast=1.12[top];"
            f"[1:v]scale={W}:{half_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{half_h},setsar=1,fps={FPS}[bot];"
            "[top][bot]vstack=inputs=2[v]"
        ),
        "-map", "[v]", "-t", f"{dur:.3f}", "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_mp4),
    ]
    subprocess.run(cmd, check=True, timeout=180)


def _render_animated_data_card(dur: float, tmp_dir: Path, out_mp4: Path) -> None:
    """Animated 30fps financial terminal Data Card (+240% / $4,800 per FEU)."""
    frames_dir = tmp_dir / "datacard_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    n_frames = max(2, int(round(dur * FPS)))

    f_tag = ImageFont.truetype(FONT_SEGOE_BOLD, 30)
    f_big = ImageFont.truetype(FONT_BOLD, 148)
    f_sub = ImageFont.truetype(FONT_BOLD, 50)
    f_axis = ImageFont.truetype(FONT_SEGOE_BOLD, 28)

    bar_vals = [0.22, 0.28, 0.35, 0.52, 0.74, 1.0]
    bar_labels = ["JUL", "AUG", "SEP", "OCT", "NOV", "NOW"]

    for fi in range(n_frames):
        p = min(1.0, fi / max(1.0, n_frames * 0.65))
        eased = 1.0 - (1.0 - p) ** 3

        img = Image.new("RGB", (W, H), (10, 14, 24))
        d = ImageDraw.Draw(img)

        # Subtle grid
        for gx in range(0, W, 90):
            d.line([(gx, 0), (gx, H)], fill=(20, 28, 44), width=1)
        for gy in range(0, H, 90):
            d.line([(0, gy), (W, gy)], fill=(20, 28, 44), width=1)

        # Dark card frame
        d.rounded_rectangle([64, 340, 950, 1230], radius=28, fill=(17, 24, 39), outline=(45, 62, 92), width=3)

        # Top badge
        tag_txt = "FREIGHT INDEX · SPOT RATE"
        tb = d.textbbox((0, 0), tag_txt, font=f_tag)
        d.rounded_rectangle([96, 376, 120 + (tb[2] - tb[0]) + 28, 434], radius=14, fill=(217, 30, 24))
        d.text((120, 386), tag_txt, font=f_tag, fill=(255, 255, 255))

        # Big animated counter
        cur_pct = int(round(240 * eased))
        cur_usd = int(round(4800 * eased))
        d.text((96, 465), f"+{cur_pct}%", font=f_big, fill=(255, 214, 0))
        d.text((96, 635), f"${cur_usd:,} / 40ft CONTAINER", font=f_sub, fill=(0, 229, 255))

        # Rising bar chart
        chart_base_y = 1120
        max_bar_h = 340
        bar_w = 96
        gap = 36
        start_x = 110
        for bi, val in enumerate(bar_vals):
            bx = start_x + bi * (bar_w + gap)
            bh = int(max_bar_h * val * eased)
            by = chart_base_y - bh
            col = (229, 9, 20) if bi == len(bar_vals) - 1 else (0, 184, 217)
            d.rounded_rectangle([bx, by, bx + bar_w, chart_base_y], radius=10, fill=col)
            d.text((bx + 18, chart_base_y + 16), bar_labels[bi], font=f_axis, fill=(160, 175, 198))

        img.save(frames_dir / f"f_{fi:04d}.png")

    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f_%04d.png"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_mp4),
    ], check=True, timeout=120)


def concat_subscenes(clips: list[Path], exact_dur: float, work_dir: Path, out_mp4: Path) -> None:
    lst = work_dir / "clips_concat.txt"
    lst.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in clips), encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-t", f"{exact_dur:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", str(out_mp4),
    ], check=True, timeout=180)


# --------------------------------------------------------------------------- Pillow RGBA Overlay Compositor
def _chunk_words(words: list[dict], max_words: int = 4, pause_split: float = 0.35) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            same_cue = w.get("cue_idx") == cur[-1].get("cue_idx")
            if not same_cue or gap > pause_split or len(cur) >= max_words:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _split_two_lines(chunk: list[dict]) -> list[list[dict]]:
    if len(chunk) <= 2:
        return [chunk]
    mid = (len(chunk) + 1) // 2
    return [chunk[:mid], chunk[mid:]]


def _draw_text_stroke_shadow(
    draw: ImageDraw.ImageDraw,
    pos: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
    stroke_width: int = 6,
    shadow_offset: tuple[int, int] = (4, 5),
) -> None:
    x, y = pos
    if shadow_offset != (0, 0):
        draw.text(
            (x + shadow_offset[0], y + shadow_offset[1]),
            text, font=font, fill=(0, 0, 0, 185),
            stroke_width=stroke_width, stroke_fill=(0, 0, 0, 185),
        )
    draw.text(
        (x, y), text, font=font, fill=fill,
        stroke_width=stroke_width, stroke_fill=stroke_fill,
    )


def _draw_source_pill(draw: ImageDraw.ImageDraw, label: str, x: int = 48, y: int = 104) -> None:
    font = ImageFont.truetype(FONT_SEGOE_BOLD, 25)
    tb = draw.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad_x, pad_y = 20, 12
    dot_space = 22
    w_box = pad_x * 2 + dot_space + tw
    h_box = pad_y * 2 + th + 4
    draw.rounded_rectangle([x, y, x + w_box, y + h_box], radius=h_box // 2, fill=(14, 18, 28, 215), outline=(255, 255, 255, 55), width=2)
    draw.ellipse([x + pad_x, y + h_box // 2 - 7, x + pad_x + 14, y + h_box // 2 + 7], fill=(229, 9, 20, 255))
    draw.text((x + pad_x + dot_space, y + pad_y - 2), label, font=font, fill=(248, 250, 252, 255))


def _is_vi_highlight_keyword(word: str) -> bool:
    clean = word.strip(".,!?:;\"'()-").upper()
    if re.search(r"[0-9%$€£]", clean):
        return True
    return clean in {"TỶ", "TRIỆU", "OTP", "BỐC", "HƠI", "ĐÓNG", "BĂNG", "KHÓA", "MẤT"}


def _is_en_highlight_keyword(word: str) -> bool:
    clean = word.strip(".,!?:;\"'()-").upper()
    if re.search(r"[0-9%$€£]", clean):
        return True
    return clean in {"240%", "$4,800", "SURGED", "DETOURS", "INFLATION", "RATES", "DAYS"}


def _draw_vi_hook_card(img: Image.Image) -> None:
    """@thethao247.vn style dark-navy/crimson gradient lower-third hook card."""
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y_top = 1130
    for y in range(y_top, H):
        u = min(1.0, (y - y_top) / 140.0)
        alpha = int(242 * u)
        r = int(12 + 28 * max(0.0, (y - 1400) / 520.0))
        g = int(18 - 6 * max(0.0, (y - 1400) / 520.0))
        b = int(46 - 14 * max(0.0, (y - 1400) / 520.0))
        gd.line([(0, y), (W, y)], fill=(r, g, b, alpha))
    img.alpha_composite(grad)

    draw = ImageDraw.Draw(img)
    # Channel/category badge on upper edge
    f_badge = ImageFont.truetype(FONT_SEGOE_BOLD, 30)
    badge_txt = "CẢNH BÁO TÀI CHÍNH"
    bt = draw.textbbox((0, 0), badge_txt, font=f_badge)
    bw, bh = bt[2] - bt[0] + 44, bt[3] - bt[1] + 22
    bx = 72
    by = 1208
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=12, fill=(229, 9, 20, 255), outline=(255, 255, 255, 240), width=3)
    draw.text((bx + 22, by + 8), badge_txt, font=f_badge, fill=(255, 255, 255, 255))

    # 3 stacked ALL-CAPS hook lines bounded safely within x in [72, 920]
    f_l1 = ImageFont.truetype(FONT_BOLD, 62)
    f_l2 = ImageFont.truetype(FONT_BOLD, 70)
    lines = [
        ("CUỘC GỌI 45 GIÂY", f_l1, (255, 255, 255, 255), (0, 0, 0, 255), 6),
        ("BỐC HƠI 3,2 TỶ ĐỒNG", f_l2, (255, 36, 36, 255), (255, 255, 255, 255), 8),
        ("TRONG MỘT ĐÊM!", f_l2, (255, 36, 36, 255), (255, 255, 255, 255), 8),
    ]
    cy = 1285
    for text, fnt, fill, stroke, sw in lines:
        _draw_text_stroke_shadow(draw, (72, cy), text, fnt, fill, stroke, sw, shadow_offset=(4, 5))
        cy += 88


def _draw_tk_redpill_karaoke(img: Image.Image, chunk: list[dict], active_idx: int) -> None:
    """@tkmmonews 2-line ALL-CAPS bold-italic karaoke with word-by-word Red/Gold rounded pill."""
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_BOLD_ITALIC, 56)
    lines = _split_two_lines(chunk)
    center_x = 500  # shifted slightly left from 540 to give extra clearance from TikTok right icon bar
    y_start = 1295 if len(lines) == 2 else 1350
    line_gap = 82

    flat_counter = 0
    for li, line_words in enumerate(lines):
        # Compute total line width with spaces
        space_w = 22
        word_metrics = []
        total_w = 0
        for wi, w in enumerate(line_words):
            txt = w["text"].upper()
            tb = draw.textbbox((0, 0), txt, font=font)
            tw = tb[2] - tb[0]
            th = tb[3] - tb[1]
            word_metrics.append((txt, tw, th, tb[0], tb[1], flat_counter))
            total_w += tw + (space_w if wi < len(line_words) - 1 else 0)
            flat_counter += 1

        # Clamp line horizontally within [64, 932]
        cur_x = max(64, min(932 - total_w, center_x - total_w // 2))
        y = y_start + li * line_gap

        # Pass 1: active pill background
        px = cur_x
        for txt, tw, th, ox, oy, g_idx in word_metrics:
            if g_idx == active_idx:
                is_gold = _is_vi_highlight_keyword(txt)
                pill_col = (255, 184, 0, 255) if is_gold else (229, 9, 20, 255)
                draw.rounded_rectangle(
                    [px - 14, y - 4, px + tw + 14, y + 66],
                    radius=14,
                    fill=pill_col,
                )
            px += tw + space_w

        # Pass 2: crisp text over pill
        px = cur_x
        for txt, tw, th, ox, oy, g_idx in word_metrics:
            if g_idx == active_idx and _is_vi_highlight_keyword(txt):
                _draw_text_stroke_shadow(
                    draw, (px - ox, y), txt, font,
                    fill=(15, 18, 24, 255),
                    stroke_fill=(255, 224, 130, 255),
                    stroke_width=2,
                    shadow_offset=(0, 0),
                )
            elif g_idx == active_idx:
                _draw_text_stroke_shadow(
                    draw, (px - ox, y), txt, font,
                    fill=(255, 255, 255, 255),
                    stroke_fill=(0, 0, 0, 255),
                    stroke_width=4,
                    shadow_offset=(2, 2),
                )
            else:
                _draw_text_stroke_shadow(
                    draw, (px - ox, y), txt, font,
                    fill=(255, 255, 255, 255),
                    stroke_fill=(0, 0, 0, 255),
                    stroke_width=6,
                    shadow_offset=(4, 5),
                )
            px += tw + space_w


def _draw_en_brand_badge(draw: ImageDraw.ImageDraw) -> None:
    cx, cy, r = 935, 148, 62
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 184, 217, 245), outline=(255, 255, 255, 220), width=3)
    f_top = ImageFont.truetype(FONT_SEGOE_BOLD, 24)
    f_bot = ImageFont.truetype(FONT_SEGOE_BOLD, 20)
    t1 = "GLOBAL"
    t2 = "BRIEF"
    b1 = draw.textbbox((0, 0), t1, font=f_top)
    b2 = draw.textbbox((0, 0), t2, font=f_bot)
    draw.text((cx - (b1[2] - b1[0]) // 2, cy - 26), t1, font=f_top, fill=(255, 255, 255, 255))
    draw.text((cx - (b2[2] - b2[0]) // 2, cy + 4), t2, font=f_bot, fill=(224, 247, 250, 255))


def _draw_en_split_hook(img: Image.Image) -> None:
    """@cgtnamerica split-screen top headline + centered IN 30 SECONDS divider badge."""
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    # Darken lower part of top half (y=460..960) so headline pops clearly
    for y in range(440, 960):
        u = min(1.0, (y - 440) / 260.0)
        gd.line([(0, y), (W, y)], fill=(8, 12, 22, int(195 * u)))
    img.alpha_composite(grad)

    draw = ImageDraw.Draw(img)
    f_hl1 = ImageFont.truetype(FONT_BOLD, 62)
    f_hl2 = ImageFont.truetype(FONT_BOLD, 58)
    _draw_text_stroke_shadow(draw, (60, 560), "“+240% FREIGHT SURGE”", f_hl1, (0, 229, 255, 255), (0, 0, 0, 255), 5)
    _draw_text_stroke_shadow(draw, (60, 646), "Red Sea detours rattle", f_hl2, (255, 255, 255, 255), (0, 0, 0, 255), 5)
    _draw_text_stroke_shadow(draw, (60, 722), "global supply chains", f_hl2, (255, 255, 255, 255), (0, 0, 0, 255), 5)

    # Split border line + centered white badge
    draw.line([(0, 960), (W, 960)], fill=(255, 255, 255, 230), width=4)
    f_div = ImageFont.truetype(FONT_BOLD, 38)
    label = "IN 30 SECONDS"
    tb = draw.textbbox((0, 0), label, font=f_div)
    bw, bh = (tb[2] - tb[0]) + 48, (tb[3] - tb[1]) + 24
    bx = (W - bw) // 2
    by = 960 - bh // 2
    draw.rectangle([bx, by, bx + bw, by + bh], fill=(255, 255, 255, 255), outline=(10, 14, 20, 255), width=5)
    draw.text((bx + 24, by + 8), label, font=f_div, fill=(10, 14, 20, 255))


def _draw_geolyth_callout_pill(draw: ImageDraw.ImageDraw, text: str, y: int = 235) -> None:
    font = ImageFont.truetype(FONT_BOLD, 34)
    tb = draw.textbbox((0, 0), text, font=font)
    bw, bh = (tb[2] - tb[0]) + 48, (tb[3] - tb[1]) + 26
    bx = 56
    draw.rounded_rectangle([bx, y, bx + bw, y + bh], radius=14, fill=(217, 30, 24, 245), outline=(255, 255, 255, 255), width=4)
    draw.text((bx + 24, y + 10), text, font=font, fill=(255, 230, 0, 255))


def _draw_en_broadcast_caption(img: Image.Image, chunk: list[dict], active_idx: int, y_base: int = 1345) -> None:
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_SEGOE_BOLD, 50)
    lines = _split_two_lines(chunk)
    center_x = 500
    line_gap = 68

    flat_counter = 0
    for li, line_words in enumerate(lines):
        space_w = 16
        word_metrics = []
        total_w = 0
        for wi, w in enumerate(line_words):
            txt = w["text"]
            tb = draw.textbbox((0, 0), txt, font=font)
            tw = tb[2] - tb[0]
            word_metrics.append((txt, tw, tb[0], flat_counter))
            total_w += tw + (space_w if wi < len(line_words) - 1 else 0)
            flat_counter += 1

        cur_x = max(64, min(932 - total_w, center_x - total_w // 2))
        y = y_base + li * line_gap
        px = cur_x
        for txt, tw, ox, g_idx in word_metrics:
            is_kw = _is_en_highlight_keyword(txt)
            if g_idx == active_idx:
                col = (255, 230, 0, 255) if is_kw else (0, 229, 255, 255)
            elif is_kw:
                col = (255, 214, 70, 255)
            else:
                col = (255, 255, 255, 255)
            _draw_text_stroke_shadow(draw, (px - ox, y), txt, font, col, (0, 0, 0, 255), 5, (3, 4))
            px += tw + space_w


def build_overlay_concat_and_mux(
    base_mp4: Path,
    mixed_wav: Path,
    duration: float,
    intervals: list[tuple[float, float, Image.Image]],
    work_dir: Path,
    out_mp4: Path,
) -> None:
    ov_dir = work_dir / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    last_png: Path | None = None
    for idx, (t0, t1, img) in enumerate(intervals):
        dur = round(t1 - t0, 4)
        if dur <= 0.005:
            continue
        # Ensure non-zero alpha pixel so PIL always saves true 32-bit RGBA PNG
        img.putpixel((0, 0), (0, 0, 0, 1))
        png_path = ov_dir / f"ov_{idx:04d}.png"
        img.save(png_path, format="PNG")
        lines.append(f"file '{png_path.resolve().as_posix()}'")
        lines.append(f"duration {dur:.4f}")
        last_png = png_path
    if last_png is not None:
        lines.append(f"file '{last_png.resolve().as_posix()}'")

    concat_txt = work_dir / "overlay_concat.txt"
    concat_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(base_mp4),
        "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-i", str(mixed_wav),
        "-filter_complex", "[1:v]fps=30,format=rgba[ov];[0:v][ov]overlay=0:0:shortest=1:format=auto,format=yuv420p[vout]",
        "-map", "[vout]", "-map", "2:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(STEREO_RATE),
        "-movflags", "+faststart", "-t", f"{duration:.3f}",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, timeout=300)


def build_vi_intervals(words: list[dict], cue_spans: list[dict], duration: float) -> list[tuple[float, float, Image.Image]]:
    hook_end = cue_spans[0]["end"]
    intervals: list[tuple[float, float, Image.Image]] = []

    # 0.0 -> hook_end: @thethao247.vn hook card
    img_hook = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _draw_vi_hook_card(img_hook)
    _draw_source_pill(ImageDraw.Draw(img_hook), "TIN NÓNG · Nguồn: VnExpress")
    intervals.append((0.0, hook_end, img_hook))

    # After hook_end: @tkmmonews 2-line red-pill karaoke
    post_words = [w for w in words if w["start"] >= hook_end - 0.05]
    chunks = _chunk_words(post_words, max_words=4, pause_split=0.35)
    cursor = hook_end
    for ci, ch in enumerate(chunks):
        ch_end_limit = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else duration
        for wi, w in enumerate(ch):
            w_end = ch[wi + 1]["start"] if wi + 1 < len(ch) else ch_end_limit
            t0 = max(cursor, w["start"])
            t1 = max(t0 + 0.02, min(duration, w_end))
            if t0 > cursor + 0.02:
                blank = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                _draw_source_pill(ImageDraw.Draw(blank), "TIN NÓNG · Nguồn: VnExpress")
                intervals.append((cursor, t0, blank))
            frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            _draw_source_pill(ImageDraw.Draw(frame), "TIN NÓNG · Nguồn: VnExpress")
            _draw_tk_redpill_karaoke(frame, ch, wi)
            intervals.append((t0, t1, frame))
            cursor = t1
    if cursor < duration - 0.02:
        tail = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _draw_source_pill(ImageDraw.Draw(tail), "TIN NÓNG · Nguồn: VnExpress")
        intervals.append((cursor, duration, tail))
    return intervals


def build_en_intervals(words: list[dict], cue_spans: list[dict], duration: float) -> list[tuple[float, float, Image.Image]]:
    hook_end = cue_spans[0]["end"]
    beat1_start, beat1_end = cue_spans[1]["start"], cue_spans[1]["end"]
    beat2_start, beat2_end = cue_spans[2]["start"], cue_spans[2]["end"]

    chunks = _chunk_words(words, max_words=5, pause_split=0.35)
    intervals: list[tuple[float, float, Image.Image]] = []
    cursor = 0.0

    def _make_en_frame(t_mid: float, ch: list[dict] | None, active_wi: int) -> Image.Image:
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        if t_mid < hook_end:
            _draw_en_split_hook(frame)
            _draw_source_pill(draw, "SOURCE: REUTERS · MARKET BRIEF")
            _draw_en_brand_badge(draw)
            if ch is not None:
                _draw_en_broadcast_caption(frame, ch, active_wi, y_base=1420)
        else:
            _draw_source_pill(draw, "SOURCE: REUTERS · MARKET BRIEF")
            _draw_en_brand_badge(draw)
            if beat1_start <= t_mid < beat1_end:
                _draw_geolyth_callout_pill(draw, "BAB EL-MANDEB DETOUR · +14 DAYS")
            elif beat2_start <= t_mid < beat2_end:
                _draw_geolyth_callout_pill(draw, "SPOT RATE: $4,800 / CONTAINER")
            if ch is not None:
                _draw_en_broadcast_caption(frame, ch, active_wi, y_base=1345)
        return frame

    for ci, ch in enumerate(chunks):
        ch_end_limit = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else duration
        for wi, w in enumerate(ch):
            w_end = ch[wi + 1]["start"] if wi + 1 < len(ch) else ch_end_limit
            t0 = max(cursor, w["start"])
            t1 = max(t0 + 0.02, min(duration, w_end))
            # Respect hook_end boundary so split-hook disappears cleanly right on transition
            if t0 < hook_end < t1:
                intervals.append((t0, hook_end, _make_en_frame((t0 + hook_end) / 2, ch, wi)))
                intervals.append((hook_end, t1, _make_en_frame((hook_end + t1) / 2, ch, wi)))
                cursor = t1
                continue
            if t0 > cursor + 0.02:
                intervals.append((cursor, t0, _make_en_frame((cursor + t0) / 2, None, -1)))
            intervals.append((t0, t1, _make_en_frame((t0 + t1) / 2, ch, wi)))
            cursor = t1
    if cursor < duration - 0.02:
        intervals.append((cursor, duration, _make_en_frame((cursor + duration) / 2, None, -1)))
    return intervals


# --------------------------------------------------------------------------- Sample Orchestrators
def produce_sample_vi(pix_key: str, impact_wav: Path, whoosh_wav: Path) -> Path:
    print("\n=== Building Sample 1 (VI): sample_vi_viral_redpill.mp4 ===")
    work = WORK_DIR / "vi_sample"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    cues = [
        "Chỉ một cuộc gọi 45 giây mạo danh nhân viên ngân hàng hoàn phí thẻ tín dụng, 3,2 tỷ đồng vừa bốc hơi ngay trong đêm!",
        "Kẻ gian gửi một đường link giả y hệt trang chủ ngân hàng, dụ nạn nhân xác thực khuôn mặt để nhận lại hai triệu tiền hoàn.",
        "Ngay khi vừa cấp quyền truy cập, màn hình điện thoại lập tức bị đóng băng, toàn bộ mã OTP tự động chuyển thẳng về máy kẻ lừa đảo.",
        "Chỉ trong năm phút, trung bình mỗi nạn nhân mất từ 50 đến 300 triệu đồng mà không hề nhận được tin nhắn cảnh báo.",
        "Bạn đã bật cảnh báo giao dịch và khóa chuyển khoản đêm trên app ngân hàng chưa?",
    ]
    queries_per_cue = [
        [["smartphone night", "phone call"], ["stressed man", "bank building"]],
        [["typing smartphone", "mobile phone"], ["credit card", "online payment"]],
        [["hacker laptop", "computer code"], ["digital security", "cybersecurity"]],
        [["counting money", "cash bills"], ["stock chart", "financial loss"]],
        [["smartphone screen", "mobile app"]],
    ]

    tts = synthesize_tight_voice(
        cues, work / "tts",
        voice="vi-VN-HoaiMyNeural",
        fallback_voice="vi-VN-HoaiMyNeural",
        rate="+10%",
        pause_sec=0.10,
    )
    print(f"[vi] audio duration={tts['duration']}s, words={len(tts['words'])}")
    for sp in tts["cue_spans"]:
        print(f"  cue {sp['cue_idx']}: {sp['start']:.2f}s -> {sp['end']:.2f}s | {sp['text'][:45]}")

    bgm_file = BGM_DIR / "ominous__crypto.mp3"
    mixed_wav = work / "mixed.wav"
    whoosh_times = [tts["cue_spans"][0]["end"], max(0.0, tts["cue_spans"][-1]["start"] - 0.12)]
    mix_broadcast_audio(tts["voice_wav"], bgm_file, impact_wav, whoosh_wav, whoosh_times, tts["duration"], mixed_wav)

    subscenes = build_subscenes(tts["cue_spans"], queries_per_cue, max_sub_dur=3.3)
    used_urls: set[str] = set()
    clip_paths: list[Path] = []
    for sub in subscenes:
        ast = fetch_stock_broll(sub["queries"], pix_key, used_urls)
        clip_p = work / f"sub_{sub['seq']:02d}.mp4"
        _render_clip_916(ast, sub["dur"], clip_p, punch=(sub["seq"] % 2 == 0))
        clip_paths.append(clip_p)
        print(f"  sub #{sub['seq']:02d}: {sub['dur']:.2f}s | {ast['kind'] if ast else 'fallback'} ({ast['query'] if ast else '-'})")

    base_mp4 = work / "base_916.mp4"
    concat_subscenes(clip_paths, tts["duration"], work, base_mp4)

    intervals = build_vi_intervals(tts["words"], tts["cue_spans"], tts["duration"])
    out_mp4 = OUT_DIR / "sample_vi_viral_redpill.mp4"
    build_overlay_concat_and_mux(base_mp4, mixed_wav, tts["duration"], intervals, work, out_mp4)
    print(f"[vi] DONE -> {out_mp4}")
    return out_mp4


def produce_sample_en(pix_key: str, impact_wav: Path, whoosh_wav: Path) -> Path:
    print("\n=== Building Sample 2 (EN): sample_en_split60_news.mp4 ===")
    work = WORK_DIR / "en_sample"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    cues = [
        "Container shipping rates on key Asia to Europe lanes just surged 240% as Red Sea detours rattle global supply chains.",
        "Major carriers are rerouting vessels around Africa, adding 14 extra sailing days and burning millions in extra fuel per voyage.",
        "Spot prices just hit $4,800 per 40-foot container, squeezing factory margins from electronics to everyday retail.",
        "Economists warn these higher freight bills could hit supermarket shelves and consumer prices within 60 days.",
        "Will shipping inflation push interest rates higher again, or will supply chains adapt fast?",
    ]
    queries_per_cue = [
        [["cargo ship ocean aerial", "container port cranes"], ["freighter sea waves", "shipping containers dock"]],
        [["cargo ship sailing sea", "ocean freight route"], ["industrial fuel port", "shipping vessel night"]],
        [["DATACARD"], ["factory warehouse workers", "electronics manufacturing line"]],
        [["supermarket grocery aisles", "shopping cart retail"], ["money inflation prices", "stock market chart screen"]],
        [["global logistics port aerial", "world financial district"]],
    ]

    tts = synthesize_tight_voice(
        cues, work / "tts",
        voice="en-US-GuyNeural",
        fallback_voice="en-US-ChristopherNeural",
        rate="+6%",
        pause_sec=0.10,
    )
    print(f"[en] audio duration={tts['duration']}s, words={len(tts['words'])}")
    for sp in tts["cue_spans"]:
        print(f"  cue {sp['cue_idx']}: {sp['start']:.2f}s -> {sp['end']:.2f}s | {sp['text'][:45]}")

    bgm_file = BGM_DIR / "energetic__cortosis.mp3"
    mixed_wav = work / "mixed.wav"
    whoosh_times = [tts["cue_spans"][0]["end"], max(0.0, tts["cue_spans"][-1]["start"] - 0.12)]
    mix_broadcast_audio(tts["voice_wav"], bgm_file, impact_wav, whoosh_wav, whoosh_times, tts["duration"], mixed_wav)

    subscenes = build_subscenes(tts["cue_spans"], queries_per_cue, max_sub_dur=3.3)
    used_urls: set[str] = set()
    clip_paths: list[Path] = []

    for sub in subscenes:
        clip_p = work / f"sub_{sub['seq']:02d}.mp4"
        if sub["cue_idx"] == 0 and sub["seq"] == 1:
            # True 50/50 split screen on the first hook sub-scene
            top_ast = fetch_stock_broll(["cargo container ship ocean", "freight ship sea"], pix_key, used_urls)
            bot_ast = fetch_stock_broll(["shipping port cranes", "container terminal dock"], pix_key, used_urls)
            _render_split_50_50_hook(top_ast, bot_ast, sub["dur"], clip_p)
            print(f"  sub #{sub['seq']:02d}: {sub['dur']:.2f}s | 50/50 split-screen hook")
        elif sub["cue_idx"] == 0:
            # Second half of hook keeps split-screen rhythm with fresh bottom port footage
            top_ast = fetch_stock_broll(["container ship waves", "ocean cargo route"], pix_key, used_urls)
            bot_ast = fetch_stock_broll(["cargo containers stacked", "port logistics aerial"], pix_key, used_urls)
            _render_split_50_50_hook(top_ast, bot_ast, sub["dur"], clip_p)
            print(f"  sub #{sub['seq']:02d}: {sub['dur']:.2f}s | 50/50 split-screen hook #2")
        elif sub["queries"][0] == "DATACARD":
            _render_animated_data_card(sub["dur"], work, clip_p)
            print(f"  sub #{sub['seq']:02d}: {sub['dur']:.2f}s | animated Data Card (+240% / $4,800)")
        else:
            ast = fetch_stock_broll(sub["queries"], pix_key, used_urls)
            _render_clip_916(ast, sub["dur"], clip_p, punch=(sub["seq"] % 2 == 0))
            print(f"  sub #{sub['seq']:02d}: {sub['dur']:.2f}s | {ast['kind'] if ast else 'fallback'} ({ast['query'] if ast else '-'})")
        clip_paths.append(clip_p)

    base_mp4 = work / "base_916.mp4"
    concat_subscenes(clip_paths, tts["duration"], work, base_mp4)

    intervals = build_en_intervals(tts["words"], tts["cue_spans"], tts["duration"])
    out_mp4 = OUT_DIR / "sample_en_split60_news.mp4"
    build_overlay_concat_and_mux(base_mp4, mixed_wav, tts["duration"], intervals, work, out_mp4)
    print(f"[en] DONE -> {out_mp4}")
    return out_mp4


def ffprobe_verify(mp4: Path) -> dict:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    data = json.loads(res.stdout)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    num, _, den = str(v.get("avg_frame_rate", "30/1")).partition("/")
    fps = float(num) / float(den or 1)
    summary = {
        "file": str(mp4),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "v_codec": v["codec_name"],
        "pix_fmt": v["pix_fmt"],
        "fps": round(fps, 2),
        "v_dur": round(float(v.get("duration") or data["format"]["duration"]), 3),
        "a_codec": a["codec_name"],
        "a_rate": int(a["sample_rate"]),
        "a_channels": int(a["channels"]),
        "a_dur": round(float(a.get("duration") or data["format"]["duration"]), 3),
        "size_mb": round(mp4.stat().st_size / (1024 * 1024), 2),
    }
    assert summary["width"] == 1080 and summary["height"] == 1920, summary
    assert summary["v_codec"] == "h264" and summary["pix_fmt"] == "yuv420p", summary
    assert abs(summary["fps"] - 30.0) <= 0.5, summary
    assert summary["a_codec"] == "aac" and summary["a_rate"] == 48000 and summary["a_channels"] == 2, summary
    assert abs(summary["v_dur"] - summary["a_dur"]) <= 0.25, summary
    return summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pix_key = load_pixabay_key()
    print("Pixabay key configured:", bool(pix_key))
    impact_wav, whoosh_wav = generate_sfx_assets(WORK_DIR / "sfx")

    vi_mp4 = produce_sample_vi(pix_key, impact_wav, whoosh_wav)
    time.sleep(2.5)
    en_mp4 = produce_sample_en(pix_key, impact_wav, whoosh_wav)

    print("\n=== ffprobe verification ===")
    print("VI:", json.dumps(ffprobe_verify(vi_mp4), ensure_ascii=False))
    print("EN:", json.dumps(ffprobe_verify(en_mp4), ensure_ascii=False))


if __name__ == "__main__":
    main()
