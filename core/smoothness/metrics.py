"""Measurable smoothness metrics for reference and produced videos.

Everything here is measured, not assumed: cut rhythm, camera motion vs local
motion (residual after homography), audio music bed (mid/side), loudness.
The same functions run on reference videos and on our own renders so the two
are directly comparable.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = 1
SAMPLE_RATE = 48000
FPS_SAMPLE = 6.0
MOTION_WIDTH = 640.0


def _run(cmd: Sequence[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


def ffprobe_streams(path: Path) -> Dict[str, Any]:
    p = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ])
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {p.stderr[-300:]}")
    data = json.loads(p.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if video and "/" in str(video.get("r_frame_rate", "")):
        num, den = str(video["r_frame_rate"]).split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    return {
        "duration_s": float(data.get("format", {}).get("duration") or 0.0),
        "fps": round(fps, 3),
        "width": int(video.get("width") or 0) if video else 0,
        "height": int(video.get("height") or 0) if video else 0,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "channels": int(audio.get("channels") or 0) if audio else 0,
    }


def detect_cuts(
    path: Path,
    threshold: float = 27.0,
    min_scene_len_s: float = 0.5,
    fps_hint: Optional[float] = None,
) -> List[float]:
    """Cut times (seconds), one detector for references and our renders alike."""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(str(path))
    fps = fps_hint or video.frame_rate or 30.0
    manager = SceneManager()
    manager.add_detector(ContentDetector(
        threshold=threshold,
        min_scene_len=max(1, int(round(min_scene_len_s * fps))),
    ))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()
    return [round(s[0].get_seconds(), 3) for s in scenes[1:]]


# ---------------------------------------------------------------- audio

def load_audio_stereo(path: Path) -> tuple:
    import numpy as np

    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn",
         "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    a = np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2)
    return a[:, 0].astype("float64"), a[:, 1].astype("float64")


def frame_rms_db(x, hop_s: float = 0.1):
    import numpy as np

    n = int(SAMPLE_RATE * hop_s)
    m = len(x) // n
    if m < 1:
        return np.zeros(0)
    seg = x[: m * n].reshape(m, n)
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2, axis=1)) + 1e-12)


def music_bed_present(audio: Dict[str, Any]) -> bool:
    """Same rule used at measurement time, re-applied to stored numbers."""
    if not audio or not audio.get("available", True):
        return False
    quiet_ratio = audio.get("side_minus_mid_when_quiet_db")
    quiet_side = audio.get("side_when_voice_quiet_db")
    if quiet_ratio is None or quiet_side is None:
        return bool(audio.get("looks_like_music_bed"))
    return bool(quiet_ratio > -20.0 and quiet_side > -60.0)


def music_bed_stats(mid_db, side_db) -> Dict[str, Any]:
    """Is there a music bed under the voice?

    Narration is centred (side ~ silence); a music bed is stereo, so it keeps
    energy in L-R even while the voice is quiet. Measured on 100 ms windows.
    """
    import numpy as np

    if len(mid_db) == 0:
        return {"available": False}
    quiet = mid_db <= np.percentile(mid_db, 20)
    side_minus_mid = side_db - mid_db
    quiet_side = float(np.median(side_db[quiet])) if quiet.any() else -240.0
    quiet_ratio = float(np.median(side_minus_mid[quiet])) if quiet.any() else -240.0
    active_share = float((side_db > -45.0).mean())
    return {
        "available": True,
        "mid_mean_dbfs": round(float(mid_db.mean()), 1),
        "side_minus_mid_mean_db": round(float(side_minus_mid.mean()), 1),
        "side_active_share": round(active_share, 3),
        "side_when_voice_quiet_db": round(quiet_side, 1),
        "side_minus_mid_when_quiet_db": round(quiet_ratio, 1),
        # Relative presence (voice is centred, a bed is not) above the noise
        # floor; absolute level varies too much between quiet and loud beds.
        "looks_like_music_bed": bool(quiet_ratio > -20.0 and quiet_side > -60.0),
    }


def measure_loudness(path: Path) -> Dict[str, Optional[float]]:
    p = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-",
    ])
    text = p.stderr
    out: Dict[str, Optional[float]] = {"integrated_lufs": None, "lra_lu": None, "true_peak_dbfs": None}
    i = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", text)
    lra = re.findall(r"LRA:\s+(-?\d+(?:\.\d+)?)\s+LU", text)
    peak = re.findall(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", text)
    if i:
        out["integrated_lufs"] = float(i[-1])
    if lra:
        out["lra_lu"] = float(lra[-1])
    if peak:
        out["true_peak_dbfs"] = float(peak[-1])
    return out


def estimate_tempo(side, hop_s: float = 256 / SAMPLE_RATE) -> Dict[str, Any]:
    """Weak tempo estimate from the voice-cancelled channel (pizzicato/percussion)."""
    import numpy as np

    if len(side) < SAMPLE_RATE:
        return {"bpm": None, "confidence": 0.0}
    # Decimate 48k -> 6k: beat-level onsets survive, matrices stay small.
    decim = 8
    x = side[::decim]
    hop = 128
    nfft = 256
    k = (len(x) - nfft) // hop + 1
    if k < 8:
        return {"bpm": None, "confidence": 0.0}
    idx = np.arange(nfft)[None, :] + hop * np.arange(k)[:, None]
    freqs = np.fft.rfftfreq(nfft, decim / SAMPLE_RATE)
    band = (freqs >= 30) & (freqs <= 250)
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(nfft), axis=1))[:, band]
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)
    ac = np.correlate(flux - flux.mean(), flux - flux.mean(), mode="full")[len(flux) - 1:]
    ac /= ac[0] + 1e-9
    lag_s = np.arange(len(ac)) * hop * decim / SAMPLE_RATE
    with np.errstate(divide="ignore"):
        bpm = 60.0 / lag_s
    mask = (bpm >= 55) & (bpm <= 190) & (np.arange(len(ac)) > 2)
    if not mask.any():
        return {"bpm": None, "confidence": 0.0}
    best = np.argmax(ac[mask])
    return {"bpm": round(float(bpm[mask][best]), 1), "confidence": round(float(ac[mask][best]), 3)}


def prepare_measurement_copy(path: Path, max_height: int = 480) -> tuple:
    """Return (file_to_measure, temp_dir_or_None).

    A small H.264 copy keeps measurement fast: decoding 20 minutes of AV1 at
    720p costs more than everything else in the benchmark combined.
    """
    info = ffprobe_streams(path)
    height = info["height"] or 0
    if info["video_codec"] == "h264" and 0 < height <= max_height:
        return Path(path), None

    tmpdir = Path(tempfile.mkdtemp(prefix="smoothmeas_"))
    out = tmpdir / "proxy.mp4"
    target = min(height, max_height) if height else max_height
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(path),
        "-vf", f"scale=-2:{target}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"proxy transcode failed: {result.stderr[-400:]}")
    return out, tmpdir


def detect_black_cards(path: Path, sample_fps: float = 2.0, min_dur_s: float = 0.4) -> Dict[str, Any]:
    """Full-length scan for black title/punchline cards.

    References use them to land a beat; a render with none has no visual
    punctuation at all. Detection runs at ``sample_fps`` because a card is at
    least ``min_dur_s`` long and 2 Hz is enough to see it.
    """
    p = _run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", f"fps={sample_fps},blackdetect=d={min_dur_s}:pix_th=0.10", "-an", "-f", "null", "-",
    ], timeout=3600)
    segments = [
        {"start_s": float(a), "end_s": float(b), "duration_s": round(float(c), 2)}
        for a, b, c in re.findall(
            r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", p.stderr)
    ]
    total = round(sum(s["duration_s"] for s in segments), 2)
    return {"available": True, "count": len(segments), "total_s": total,
            "longest_s": max((s["duration_s"] for s in segments), default=0.0),
            "segments": segments}


def measure_audio(path: Path) -> Dict[str, Any]:
    import numpy as np

    try:
        left, right = load_audio_stereo(path)
    except subprocess.CalledProcessError:
        return {"available": False}
    if len(left) < SAMPLE_RATE // 2:
        return {"available": False}
    mid = (left + right) / 2
    side = (left - right) / 2
    mid_db = frame_rms_db(mid)
    side_db = frame_rms_db(side)
    stats = music_bed_stats(mid_db, side_db)
    stats.update(measure_loudness(path))
    stats.update(estimate_tempo(side))
    stats["available"] = True
    return stats


# ---------------------------------------------------------------- motion

def measure_motion(path: Path, cut_times: Sequence[float], duration: float) -> Dict[str, Any]:
    """Per-shot camera motion vs local motion.

    camera: 4-dof affine between sampled frames (translation/zoom).
    local:  residual after warping with a full homography — this is content
            that moves inside the frame (chart bars growing, poses, i2v), the
            thing a still-image camera move cannot produce.
    """
    import numpy as np
    import cv2

    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1
    step = max(1, int(round(fps / FPS_SAMPLE)))
    scale = MOTION_WIDTH / width

    rows = []
    prev, prev_t, idx = None, 0.0, 0
    while True:
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = idx / fps
            gray = cv2.cvtColor(cv2.resize(frame, None, fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)
            if prev is not None and t > prev_t:
                dt = t - prev_t
                p0 = cv2.goodFeaturesToTrack(prev, maxCorners=400, qualityLevel=0.01, minDistance=8)
                if p0 is not None and len(p0) >= 12:
                    p1, status, _ = cv2.calcOpticalFlowPyrLK(prev, gray, p0, None)
                    a = p0[status.ravel() == 1].reshape(-1, 2)
                    b = p1[status.ravel() == 1].reshape(-1, 2)
                    if len(a) >= 12:
                        affine, _ = cv2.estimateAffinePartial2D(
                            a, b, method=cv2.RANSAC, ransacReprojThreshold=2.0)
                        if affine is not None:
                            trans = float(np.hypot(affine[0, 2], affine[1, 2])) / dt
                            s = float(np.sqrt(affine[0, 0] ** 2 + affine[1, 0] ** 2))
                            zoom = (s - 1.0) * 100.0 / dt
                            homography, _ = cv2.findHomography(a, b, cv2.RANSAC, 2.0)
                            if homography is not None:
                                warped = cv2.warpPerspective(
                                    prev, homography, (gray.shape[1], gray.shape[0]),
                                    flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                                residual = float(np.mean(np.abs(
                                    warped.astype(np.float32) - gray.astype(np.float32))))
                            else:
                                residual = float("nan")
                            raw = float(np.mean(np.abs(
                                prev.astype(np.float32) - gray.astype(np.float32))))
                            rows.append((t, trans, zoom, residual, raw))
            prev, prev_t = gray, t
        idx += 1
    cap.release()

    if not rows:
        return {"available": False}
    P = np.array([r[1:] for r in rows], dtype=float)
    T = np.array([r[0] for r in rows], dtype=float)
    valid = ~np.isnan(P).any(axis=1)
    P, T = P[valid], T[valid]
    if len(P) == 0:
        return {"available": False}

    bounds = [0.0] + list(cut_times) + [duration]
    shots = []
    for start, end in zip(bounds[:-1], bounds[1:]):
        mask = (T >= start) & (T < end)
        if mask.sum() < 2:
            continue
        seg = P[mask]
        shots.append({
            "start_s": round(float(start), 2),
            "dur_s": round(float(end - start), 2),
            "trans_px_s": round(float(np.median(seg[:, 0])), 2),
            "zoom_pct_s": round(float(np.median(seg[:, 1])), 3),
            "residual": round(float(np.median(seg[:, 2])), 2),
            "rawdiff": round(float(np.median(seg[:, 3])), 2),
        })
    if not shots:
        return {"available": False}

    residual = np.array([s["residual"] for s in shots])
    raw = np.array([s["rawdiff"] for s in shots])
    trans = np.array([s["trans_px_s"] for s in shots])
    zoom = np.abs(np.array([s["zoom_pct_s"] for s in shots]))
    durations = np.array([s["dur_s"] for s in shots])
    return {
        "available": True,
        "shots_measured": len(shots),
        "shot_dur_s": {
            "mean": round(float(durations.mean()), 2),
            "median": round(float(np.median(durations)), 2),
            "p90": round(float(np.percentile(durations, 90)), 2),
        },
        "camera": {
            "trans_px_s_median": round(float(np.median(trans)), 2),
            "zoom_pct_s_median": round(float(np.median(zoom)), 3),
            "share_trans_gt2px_s": round(float((trans > 2).mean()), 3),
            "share_zoom_gt0_5pct_s": round(float((zoom > 0.5).mean()), 3),
        },
        "local_motion": {
            "residual_median": round(float(np.median(residual)), 2),
            "rawdiff_median": round(float(np.median(raw)), 2),
            "share_static_rawdiff_lt0_5": round(float((raw < 0.5).mean()), 3),
            "share_rigid_residual_lt2": round(float((residual < 2).mean()), 3),
            "share_local_residual_2to6": round(float(((residual >= 2) & (residual < 6)).mean()), 3),
            "share_strong_local_residual_ge6": round(float((residual >= 6).mean()), 3),
        },
        "shots": shots,
    }


# ---------------------------------------------------------------- report

def measure_video(
    path: Path,
    *,
    video_id: Optional[str] = None,
    source: Optional[Dict[str, Any]] = None,
    cut_threshold: float = 27.0,
    min_scene_len_s: float = 0.5,
    audio_source: Optional[Path] = None,
    with_black_cards: bool = True,
    captions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    path = Path(path)
    info = ffprobe_streams(path)
    cuts = detect_cuts(path, threshold=cut_threshold,
                       min_scene_len_s=min_scene_len_s, fps_hint=info["fps"])
    duration = info["duration_s"]
    motion = measure_motion(path, cuts, duration)
    black = detect_black_cards(path) if with_black_cards else {"available": False}
    if info["audio_codec"]:
        audio = measure_audio(path)
    elif audio_source:
        audio = measure_audio(Path(audio_source))
    else:
        audio = {"available": False}

    shots = motion.get("shots_measured") or (len(cuts) + 1)
    report = {
        "schema_version": SCHEMA_VERSION,
        "id": video_id or path.stem,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source or {"type": "file", "path": str(path)},
        "detector": {
            "name": "pyscenedetect-content",
            "threshold": cut_threshold,
            "min_scene_len_s": min_scene_len_s,
        },
        "video": {
            "fps": info["fps"],
            "width": info["width"],
            "height": info["height"],
            "duration_s": round(duration, 2),
            "cuts": len(cuts),
            "shots": shots,
            "cuts_per_min": round(len(cuts) * 60.0 / duration, 2) if duration else 0.0,
            "motion": motion,
            "black_cards": black,
            "captions": captions,
        },
        "audio": audio,
    }
    return report


def write_report(report: Dict[str, Any], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
