"""Image Ken Burns Provider (B-roll & Graceful Fallback).

Generates a still image (via Imagen 3 or DALL-E 3, crayon Style Bible) and applies
a Ken Burns pan/zoom effect sized to the shot's planned duration using FFmpeg.
Used for:
1. Secondary B-roll shots in Mode 2.
2. Graceful fallback when a Veo video shot fails 3 times, allowing the documentary to complete.

No placeholder images: when no image provider succeeds the generation FAILS
loudly (zero-fake policy) instead of rendering a caption card.
"""

import logging
from pathlib import Path
import shutil
import subprocess
import time
from typing import Optional

from services.gemini_webapi_provider import GeminiWebApiProvider
from services.video_provider import ErrorKind, ProviderResult, SessionValidationResult, VideoProvider
from services.prompt_sanitizer import sanitize_veo_prompt

logger = logging.getLogger("image_kenburns_provider")

# Crayon Style Bible prefix for every generated still (matches the Veo prompt style).
CRAYON_STYLE_PREFIX = (
    "Whimsical hand-drawn editorial cartoon illustration, rough wax crayon and "
    "colored pencil sketch strokes, grainy paper texture, flat 2D minimalist "
    "composition, generous negative space around subject, limited muted color "
    "palette with vibrant yellow and red crayon accents, off-white textured "
    "sketchbook paper background, no gradients, no 3D shading, no photorealism. "
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
        safe_prompt = sanitize_veo_prompt(prompt)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img_path = out_path.with_suffix(".jpg")
        kb_duration = max(2.0, min(float(duration or 10.0), 15.0))

        # Step 1: Generate or obtain high-res image
        has_image = False

        # Priority 0: Use canonical character reference image if provided
        if ref_image and Path(ref_image).is_file():
            try:
                shutil.copyfile(str(ref_image), str(img_path))
                has_image = True
                logger.info("Ken Burns using canonical character reference image: %s", ref_image)
            except Exception as e:
                logger.warning("Failed to copy reference image %s: %s", ref_image, e)

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

        # Step 2: Apply Ken Burns Zoom/Pan animation sized to kb_duration (25fps, 1280x720) with timeout (P1.6)
        try:
            # Subtle zoom from 1.0 to 1.15 centered across the whole clip
            kb_frames = max(1, int(kb_duration * 25))
            cmd_kb = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(img_path),
                "-vf", f"zoompan=z='min(zoom+{0.15 / kb_frames:.6f},1.15)':d={kb_frames}:s=1280x720:fps=25",
                "-t", f"{kb_duration:.2f}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
            subprocess.run(cmd_kb, check=True, capture_output=True, timeout=180)

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
                error_message=f"Ken Burns animation timed out after 180s: {e}",
                duration_sec=time.time() - start_time,
            )
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.FATAL,
                error_message=f"Ken Burns animation error: {e}",
                duration_sec=time.time() - start_time,
            )

