"""Image Ken Burns Provider (B-roll & Graceful Fallback).

Generates a still image (via xKiro image, Imagen 3 or DALL-E 3, crayon Style
Bible) and applies a Ken Burns pan/zoom effect sized to the shot's planned
duration using FFmpeg.
Used for:
1. Secondary B-roll shots in Mode 2.
2. Graceful fallback when a Veo video shot fails 3 times, allowing the documentary to complete.

No placeholder images: when no image provider succeeds the generation FAILS
loudly (zero-fake policy) instead of rendering a caption card.
"""

import logging
import re
from pathlib import Path
import shutil
import subprocess
import time
from typing import Optional

from services.gemini_webapi_provider import GeminiWebApiProvider
from services.video_provider import ErrorKind, ProviderResult, SessionValidationResult, VideoProvider
from services.prompt_sanitizer import (
    CRAYON_STYLE_PREFIX,
    sanitize_veo_prompt,
)

logger = logging.getLogger("image_kenburns_provider")

KENBURNS_FPS = 25
KENBURNS_WIDTH = 2304
KENBURNS_HEIGHT = 1296
KENBURNS_OUTPUT = "1920x1080"
# Alternate directions per shot, otherwise every clip reads as the same still.
KENBURNS_MOTIONS = (
    "zoom_in",
    "pan_lr",
    "zoom_out",
    "pan_rl",
    "zoom_diag",
    "pan_up",
    "pan_down",
)
# Shots longer than this get a mid-clip reframe cut so the image changes
# framing while the narration continues.
REFRAAME_CUT_MIN_SECONDS = 5.5
# Hand-drawn "boil": the ink line wobbles every few frames, which is what makes
# flat explainer art read as animated instead of as a frozen still.
BOIL_PIXELS = 3
BOIL_HOLD_FRAMES = 3
# One drawing cannot move, so each shot is drawn in a few poses and blended:
# the same character visibly changes action across the clip.
POSES_PER_SHOT = 3
POSE_BLEND_SECONDS = 0.30
POSE_CUES = (
    "the character takes one step forward with an arm swinging",
    "the character raises an arm and leans slightly toward the viewer",
    "the character turns the head and gestures with an open hand",
    "the character brings both hands down onto the surface in front",
    "the character looks up with both arms lifted",
) 


def _shot_seed(out_path: Path) -> int:
    match = re.search(r"(\d+)", out_path.stem)
    if match:
        return int(match.group(1))
    return sum(out_path.stem.encode("utf-8")) % 97


def _boil_expr(seed: int, phase: float) -> str:
    """Stepped pseudo-random offset in input pixels, recomputed every hold."""
    rate = 12.9898 + (seed % 7) * 0.73 + phase
    return f"{BOIL_PIXELS}*sin(floor(on/{BOIL_HOLD_FRAMES})*{rate:.4f})"


def _zoompan_filter(motion: str, frames: int, seed: int = 0) -> str:
    """zoompan expression for one camera move over `frames` output frames."""
    progress = f"(on/{max(1, frames - 1)})"
    center_x = "(iw-iw/zoom)/2"
    center_y = "(ih-ih/zoom)/2"
    if motion == "zoom_in":
        z, x, y = f"(1.02+0.28*{progress})", center_x, center_y
    elif motion == "zoom_out":
        z, x, y = f"(1.30-0.28*{progress})", center_x, center_y
    elif motion == "pan_lr":
        z, x, y = "1.22", f"(iw-iw/zoom)*{progress}", center_y
    elif motion == "pan_rl":
        z, x, y = "1.22", f"(iw-iw/zoom)*(1-{progress})", center_y
    elif motion == "pan_down":
        z, x, y = "1.22", center_x, f"(ih-ih/zoom)*{progress}"
    elif motion == "pan_up":
        z, x, y = "1.22", center_x, f"(ih-ih/zoom)*(1-{progress})"
    else:  # zoom_diag
        z, x, y = f"(1.08+0.22*{progress})", f"(iw-iw/zoom)*{progress}", f"(ih-ih/zoom)*(1-{progress})"
    # Clamp after wobbling so the crop never runs past the canvas edge.
    x = f"min(max(({x})+({_boil_expr(seed, 0.0)}),0),iw-iw/zoom)"
    y = f"min(max(({y})+({_boil_expr(seed, 5.1)}),0),ih-ih/zoom)"
    return (
        f"zoompan=z='{z}':x='{x}':y='{y}'"
        f":d={frames}:s={KENBURNS_OUTPUT}:fps={KENBURNS_FPS}"
    )


def build_kenburns_filter(seed: int, duration: float, image_count: int = 1) -> str:
    """Return an ffmpeg filter_complex graph: one cover crop plus 1-2 moves.

    The source image is cover-cropped to the overscan canvas (never stretched),
    so pans have real room to travel and faces keep their proportions. With
    more than one image the stills are blended pose-to-pose, which is the only
    on-screen movement available when no video model is reachable.
    """
    total_frames = max(1, int(round(duration * KENBURNS_FPS)))
    cover = (
        f"scale={KENBURNS_WIDTH}:{KENBURNS_HEIGHT}:force_original_aspect_ratio=increase,"
        # Height overflow is biased upward so square/portrait character sheets
        # keep the face instead of losing the top of the head.
        f"crop={KENBURNS_WIDTH}:{KENBURNS_HEIGHT}:0:(ih-oh)*0.10"
    )
    if image_count > 1:
        return _pose_graph(seed, duration, image_count, cover)

    first = KENBURNS_MOTIONS[seed % len(KENBURNS_MOTIONS)]

    if duration < REFRAAME_CUT_MIN_SECONDS:
        return (
            f"[0:v]{cover},{_zoompan_filter(first, total_frames, seed)},"
            f"format=yuv420p[v]"
        )

    frames_a = total_frames // 2
    frames_b = total_frames - frames_a
    second = KENBURNS_MOTIONS[(seed + 3) % len(KENBURNS_MOTIONS)]
    return (
        f"[0:v]{cover},split=2[a][b];"
        f"[a]{_zoompan_filter(first, frames_a, seed)},trim=end_frame={frames_a},"
        f"setpts=PTS-STARTPTS[va];"
        f"[b]{_zoompan_filter(second, frames_b, seed + 1)},trim=end_frame={frames_b},"
        f"setpts=PTS-STARTPTS[vb];"
        f"[va][vb]concat=n=2:v=1:a=0,format=yuv420p[v]"
    )


def _pose_graph(seed: int, duration: float, image_count: int, cover: str) -> str:
    """Blend N pose drawings over the shot's real duration."""
    blend = POSE_BLEND_SECONDS
    segment = (duration + blend * (image_count - 1)) / image_count
    frames = max(2, int(round(segment * KENBURNS_FPS)))
    parts = []
    for i in range(image_count):
        motion = KENBURNS_MOTIONS[(seed + i * 2) % len(KENBURNS_MOTIONS)]
        parts.append(
            f"[{i}:v]{cover},{_zoompan_filter(motion, frames, seed + i)},"
            # xfade rejects a stream without an explicit constant frame rate.
            f"trim=end_frame={frames},setpts=PTS-STARTPTS,fps={KENBURNS_FPS},setsar=1[p{i}]"
        )
    previous = "p0"
    offset = segment - blend
    for i in range(1, image_count):
        label = f"x{i}"
        parts.append(
            f"[{previous}][p{i}]xfade=transition=fade:duration={blend:.2f}:"
            f"offset={offset:.3f}[{label}]"
        )
        previous = label
        offset += segment - blend
    parts.append(f"[{previous}]format=yuv420p[v]")
    return ";".join(parts)


def _pose_edit_prompt(cue: str) -> str:
    return (
        "Keep this exact same drawing: same characters, same framing, same colors, "
        "same background, same flat crayon stick-figure style, same props. "
        f"Change only the action so that {cue}. "
        "Do not restyle, do not reframe, do not add text."
    )


class ImageKenBurnsProvider(VideoProvider):
    def __init__(self, fallback_provider: Optional[GeminiWebApiProvider] = None):
        self.webapi = fallback_provider or GeminiWebApiProvider()

    def validate_session(self, session_file: Path) -> SessionValidationResult:
        return self.webapi.validate_session(session_file)

    def generate(
        self,
        prompt: str,
        out_path: Path,
        session_file: Optional[Path] = None,
        ref_image: Optional[Path] = None,
        duration: float = 10.0,
        **kwargs,
    ) -> ProviderResult:
        start_time = time.time()
        safe_prompt = sanitize_veo_prompt(prompt, for_video=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img_path = out_path.with_suffix(".jpg")
        kb_duration = max(2.0, min(float(duration or 10.0), 15.0))

        # Step 1: Generate or obtain high-res image
        has_image = False
        image_from_xkiro = False

        # Priority 0: Use canonical character reference image if provided
        if ref_image and Path(ref_image).is_file():
            try:
                shutil.copyfile(str(ref_image), str(img_path))
                has_image = True
                logger.info("Ken Burns using canonical character reference image: %s", ref_image)
            except Exception as e:
                logger.warning("Failed to copy reference image %s: %s", ref_image, e)

        # Priority 1: xKiro image gateway (gpt-image-2.5 -> Sensenova). This is
        # the provider that actually works when Gemini cookies are stale.
        if not has_image:
            try:
                from services.xkiro_image_provider import generate_xkiro_image

                used_model = generate_xkiro_image(
                    f"{CRAYON_STYLE_PREFIX}{safe_prompt}",
                    img_path,
                    size="1792x1024",
                )
                if img_path.exists() and img_path.stat().st_size > 1000:
                    has_image = True
                    image_from_xkiro = True
                    logger.info("Ken Burns image via xKiro (%s): %s", used_model, img_path)
            except Exception as e:
                logger.warning(
                    "xKiro image generation failed (%s). Trying Gemini WebAPI.", e
                )

        # Attempt to generate image via gemini_webapi if session provided
        if not has_image and session_file and session_file.exists():
            try:
                import asyncio
                from gemini_webapi import GeminiClient
                from services.gemini_webapi_provider import (
                    extract_cookies_from_file,
                    persist_rotated_cookie,
                )

                psid, psidts = extract_cookies_from_file(session_file)
                if psid:
                    async def _gen_img():
                        c = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
                        try:
                            await c.init(timeout=60, auto_refresh=False, verbose=False)
                            out = await c.generate_content(f"{CRAYON_STYLE_PREFIX}{safe_prompt}")
                            if out.images:
                                await out.images[0].save(path=str(img_path.parent), filename=img_path.name)
                        finally:
                            persist_rotated_cookie(c, session_file, psid, psidts)
                            await c.close()

                    asyncio.run(asyncio.wait_for(_gen_img(), timeout=120.0))
                    if img_path.exists() and img_path.stat().st_size > 1000:
                        has_image = True
            except Exception as e:
                logger.warning("Image generation via WebAPI failed (%s). Checking DALL-E / external provider.", e)

        # Secondary fallback: Attempt OpenAI / DALL-E if explicit OPENAI_API_KEY is available (P1.8)
        if not has_image:
            import os
            openai_key = os.environ.get("OPENAI_API_KEY")
            if openai_key:
                try:
                    import base64
                    from openai import OpenAI
                    client = OpenAI(api_key=openai_key, timeout=60.0)
                    resp = client.images.generate(
                        model="dall-e-3",
                        prompt=f"{CRAYON_STYLE_PREFIX}{prompt[:300]}",
                        n=1,
                        size="1024x1024",
                        response_format="b64_json",
                    )
                    if resp.data and resp.data[0].b64_json:
                        img_path.write_bytes(base64.b64decode(resp.data[0].b64_json))
                        has_image = True
                except Exception as err:
                    logger.warning("DALL-E image generation fallback failed: %s", err)

        if not has_image or not img_path.exists():
            logger.error(
                "Ken Burns FAIL (zero-fake): không có image provider nào thành công cho shot: %s",
                prompt[:60],
            )
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.FATAL,
                error_message="No image provider available (Imagen session + OPENAI_API_KEY both unavailable/failed)",
                duration_sec=time.time() - start_time,
            )

        # Step 2: Redraw a few poses of the same drawing so the character moves
        seed = _shot_seed(out_path)
        images = [img_path]
        if image_from_xkiro and POSES_PER_SHOT > 1:
            from services.xkiro_image_provider import edit_xkiro_image

            for idx in range(1, POSES_PER_SHOT):
                pose_path = img_path.with_name(f"{img_path.stem}_pose{idx}.jpg")
                cue = POSE_CUES[(seed + idx) % len(POSE_CUES)]
                try:
                    edit_xkiro_image(_pose_edit_prompt(cue), img_path, pose_path)
                except Exception as e:
                    logger.warning(
                        "Pose keyframe %d failed (%s) — rendering with %d drawing(s)",
                        idx, e, len(images),
                    )
                    break
                if pose_path.exists() and pose_path.stat().st_size > 1000:
                    images.append(pose_path)

        # Step 3: Animate with a cover-cropped, per-shot varied camera move
        try:
            graph = build_kenburns_filter(seed, kb_duration, image_count=len(images))
            cmd_kb = ["ffmpeg", "-y"]
            for pose_image in images:
                cmd_kb += ["-loop", "1", "-i", str(pose_image)]
            cmd_kb += [
                "-filter_complex", graph,
                "-map", "[v]",
                "-t", f"{kb_duration:.2f}",
                "-r", str(KENBURNS_FPS),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
            subprocess.run(cmd_kb, check=True, capture_output=True, timeout=300)

            if out_path.exists() and out_path.stat().st_size > 5000:
                return ProviderResult(
                    ok=True,
                    video_path=out_path,
                    thumbnail_path=img_path,
                    duration_sec=time.time() - start_time,
                )
            else:
                return ProviderResult(
                    ok=False,
                    error_kind=ErrorKind.FATAL,
                    error_message="Ken Burns render output file too small",
                    duration_sec=time.time() - start_time,
                )
        except subprocess.TimeoutExpired as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.FATAL,
                error_message=f"Ken Burns animation timed out after 300s: {e}",
                duration_sec=time.time() - start_time,
            )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.FATAL,
                error_message=f"Ken Burns animation error: {e}",
                duration_sec=time.time() - start_time,
            )

