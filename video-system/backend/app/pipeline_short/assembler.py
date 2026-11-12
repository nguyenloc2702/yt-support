"""Assembler dọc 1080x1920 cho Short (Phụ lục G.3/G.5).

Mỗi scene: ảnh → zoompan dọc (1216x2160 → 1080x1920@30, seed theo short_id
để tái lập) → concat → mix voice + BGM bed → burn karaoke ASS → mux + QC
probe (fail-closed, geometry + duration ±0.25s).

Tôn trọng `VIDEO_FAKE_FFMPEG` / thiếu ffmpeg: ghi container fake deterministic
(giống `AssemblerService`) để test không cần ffmpeg thật.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zlib
from pathlib import Path

from ..config import RENDER_SPEC_SHORT, data_dir

logger = logging.getLogger("video_system.pipeline_short.assembler")

_MOTION_PREFIX = ("zoom_in", "zoom_out", "pan_lr", "pan_rl")


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and not os.environ.get("VIDEO_FAKE_FFMPEG")


def _prepare_scene_image(src: Path, dst_dir: Path, seq: int,
                         width: int = 1080, height: int = 1920) -> Path:
    """Chuẩn bị ảnh scene dọc (G.11): ảnh dọc → giữ nguyên (zoompan tự crop fill);
    ảnh ngang/square (ảnh gốc bài báo thường ngang) → blur-pad: nền phóng to + blur
    lấp đầy khung dọc, ảnh gốc nét contain ở giữa — không cắt mất nội dung tin."""
    from PIL import Image
    with Image.open(src) as im:
        w, h = im.size
    if h >= w:
        return src
    dst = dst_dir / f"composed_{seq:03d}.png"
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-filter_complex",
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=28[bg];"
        f"[0:v]scale={width}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2",
        "-frames:v", "1",
        str(dst),
    ]
    subprocess.run(cmd, check=True, timeout=120)
    return dst


def _kenburns_filter(seed_key: str, duration_sec: float, fps: int) -> str:
    """Zoompan dọc 1 ảnh → clip motion; chọn variant theo seed (tái lập)."""
    variant = _MOTION_PREFIX[zlib.crc32(seed_key.encode("utf-8")) % len(_MOTION_PREFIX)]
    frames = max(1, int(round(duration_sec * fps)))
    # zoompan input phải lớn hơn output để pan không mờ: scale trước 1216x2160
    pre = "scale=1216:2160:force_original_aspect_ratio=increase,crop=1216:2160,setsar=1"
    if variant == "zoom_in":
        z = "min(zoom+0.0010,1.18)"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif variant == "zoom_out":
        z = "if(lte(zoom,1.0),1.18,max(1.001,zoom-0.0010))"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif variant == "pan_lr":
        z = "1.12"
        x = f"(iw-iw/zoom)*min(on/{frames},1)"
        y = "ih/2-(ih/zoom/2)"
    else:  # pan_rl
        z = "1.12"
        x = f"(iw-iw/zoom)*(1-min(on/{frames},1))"
        y = "ih/2-(ih/zoom/2)"
    return (
        f"{pre},zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1080x1920:fps={fps}"
    )


def _mix_bgm_bed(voice_wav: Path, out_wav: Path, duration: float, seed: str) -> bool:
    """Trộn 1 track BGM loop dưới giọng đọc (duck đơn giản). Trả False nếu thiếu BGM."""
    repo_root = Path(__file__).resolve().parents[3]
    bgm_dir = repo_root / "assets" / "music" / "bgm"
    tracks = sorted(bgm_dir.glob("*.mp3")) if bgm_dir.exists() else []
    if not tracks:
        shutil.copy2(voice_wav, out_wav)
        return False
    track = tracks[zlib.crc32(seed.encode("utf-8")) % len(tracks)]
    duck_db = -20.0
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(voice_wav),
        "-stream_loop", "-1", "-i", str(track),
        "-filter_complex",
        f"[1:a]volume={duck_db}dB[bgm];[0:a][bgm]amix=inputs=2:duration=first:"
        "dropout_transition=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
        "-map", "[aout]", "-ar", "48000", "-ac", "2",
        "-t", f"{duration:.3f}",
        str(out_wav),
    ]
    subprocess.run(cmd, check=True, timeout=180)
    return True


def assemble_short(short_id: str, scenes: list[dict], audio_path: str,
                   words: list[dict], ass_path: str, out_dir: Path) -> dict:
    """Ghép video dọc từ ảnh scene + voice + karaoke. QC fail-closed.

    scenes: [{seq, text, start_sec, end_sec, image_path, clip_path?}]
    Trả: {video_path, duration, burned, probe}
    """
    spec = RENDER_SPEC_SHORT["video"]
    width, height, fps = int(spec["width"]), int(spec["height"]), int(spec["fps"])
    crf = str(spec.get("crf", 20))
    tol = float(RENDER_SPEC_SHORT["qc"]["duration_tolerance_sec"])

    if not scenes:
        raise ValueError("Không có scene nào để ghép video.")
    voice_wav = Path(audio_path)
    if not voice_wav.exists():
        raise FileNotFoundError(f"Không tìm thấy audio đã tổng hợp: {voice_wav}")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"short_{short_id}.mp4"

    audio_dur = _wav_duration(voice_wav)

    if not _has_ffmpeg():
        # Deterministic fake container (pattern AssemblerService) cho test/không ffmpeg
        header = (
            f"SHORT_MP4_CONTAINER|short={short_id}|scenes={len(scenes)}|"
            f"dur={audio_dur:.3f}|geometry={width}x{height}@{fps}|burn_subs=True\n"
        ).encode("utf-8")
        final_path.write_bytes(header + b"MP4_STREAM_DATA" * 200)
        return {"video_path": str(final_path), "duration": round(audio_dur, 3),
                "burned": True, "probe": None}

    # 1) Render từng scene: video b-roll → dùng thẳng (loop nếu ngắn hơn scene);
    #    ảnh → zoompan dọc; thiếu cả hai → nền màu đặc, honest
    norm_clips: list[Path] = []
    for sc in scenes:
        dur = max(0.5, float(sc["end_sec"]) - float(sc["start_sec"]))
        clip = tmp / f"scene_{int(sc['seq']):03d}.mp4"
        img = sc.get("image_path")
        clip_src = sc.get("clip_path")
        if clip_src and Path(clip_src).exists():
            # B-roll clip: scale/crop fill 9:16, chuẩn fps, MUTE audio (voice là chính)
            vf_clip = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                       f"crop={width}:{height},setsar=1,fps={fps}")
            cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1",
                   "-i", str(clip_src),
                   "-vf", vf_clip, "-an", "-t", f"{dur:.3f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                   "-pix_fmt", "yuv420p", str(clip)]
        else:
            vf = _kenburns_filter(f"{short_id}|scene_{int(sc['seq'])}", dur, fps)
            if img and Path(img).exists():
                # G.11: ảnh ngang (ảnh gốc bài báo) → blur-pad dọc trước khi zoompan
                try:
                    img = _prepare_scene_image(Path(img), tmp, int(sc["seq"]))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("prepare scene image %s failed: %s", img, exc)
                cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.3f}",
                       "-i", str(img), "-vf", vf, "-frames:v",
                       str(max(1, int(round(dur * fps)))), "-an",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                       "-pix_fmt", "yuv420p", str(clip)]
            else:
                # Không có ảnh → thẻ màu đặc (đúng luật: không im lặng, ghi rõ trong meta)
                cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                       "-i", f"color=c=0x1C1917:s={width}x{height}:d={dur:.3f}:r={fps}",
                       "-frames:v", str(max(1, int(round(dur * fps)))), "-an",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                       "-pix_fmt", "yuv420p", str(clip)]
        subprocess.run(cmd, check=True, timeout=300)
        norm_clips.append(clip)

    # 2) Concat
    concat_list = tmp / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in norm_clips), encoding="utf-8")
    joined = tmp / "joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat_list), "-c", "copy", str(joined)],
                   check=True, timeout=300)

    # 3) Mix voice + BGM (nếu có asset)
    mixed = tmp / "mixed.wav"
    bgm_used = _mix_bgm_bed(voice_wav, mixed, audio_dur, short_id)

    # 4) Burn karaoke ASS + mux audio
    ass_escaped = Path(ass_path).resolve().as_posix().replace(":", r"\:")
    cmd_final = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(joined), "-i", str(mixed),
        "-vf", f"ass='{ass_escaped}'",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(RENDER_SPEC_SHORT["audio"]["rate"]),
        "-movflags", "+faststart",
        "-t", f"{audio_dur:.3f}",
        str(final_path),
    ]
    subprocess.run(cmd_final, check=True, timeout=600)

    # 5) QC fail-closed
    probe = _probe_video(final_path)
    problems = []
    if int(probe["width"]) != width or int(probe["height"]) != height:
        problems.append(f"sai độ phân giải {probe['width']}x{probe['height']} (yêu cầu {width}x{height})")
    if abs(probe["fps"] - fps) > 0.5:
        problems.append(f"sai fps {probe['fps']} (yêu cầu {fps})")
    if probe["codec"] != spec["codec"]:
        problems.append(f"sai codec {probe['codec']} (yêu cầu {spec['codec']})")
    if abs(probe["duration"] - audio_dur) > tol:
        problems.append(
            f"thời lượng video {probe['duration']:.2f}s lệch audio {audio_dur:.2f}s quá ±{tol}s")
    if problems:
        raise ValueError("QC Short thất bại: " + "; ".join(problems) +
                         " — không xuất bản video không đạt.")

    shutil.rmtree(tmp, ignore_errors=True)
    return {"video_path": str(final_path), "duration": round(probe["duration"], 3),
            "burned": True, "probe": probe, "bgm_used": bgm_used}


def _wav_duration(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _probe_video(path: Path) -> dict:
    import json
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name,avg_frame_rate",
         "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(res.stdout)
    stream = (data.get("streams") or [{}])[0]
    rate = str(stream.get("avg_frame_rate") or "30/1")
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1) if den else float(num or 0)
    return {
        "width": stream.get("width"), "height": stream.get("height"),
        "codec": stream.get("codec_name"), "fps": round(fps, 2),
        "duration": float(data.get("format", {}).get("duration") or 0.0),
    }


def short_objects_dir(short_id: str) -> Path:
    return data_dir() / "objects" / "shorts" / short_id
