"""Image Ken Burns Provider (B-roll & Graceful Fallback).

Generates a still image (via Imagen 3 or creates a gradient/title card) and applies
a cinematic 10-second Ken Burns pan/zoom effect using FFmpeg.
Used for:
1. Secondary B-roll shots in Mode 2.
2. Graceful fallback when a Veo video shot fails 3 times, allowing the documentary to complete.
"""

import logging
from pathlib import Path
import subprocess
import time
from typing import Optional

from services.gemini_webapi_provider import GeminiWebApiProvider
from services.video_provider import ErrorKind, ProviderResult, SessionValidationResult, VideoProvider

logger = logging.getLogger("image_kenburns_provider")


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
    ) -> ProviderResult:
        start_time = time.time()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img_path = out_path.with_suffix(".jpg")

        # Step 1: Generate or obtain high-res image
        # Attempt to generate image via gemini_webapi if session provided
        has_image = False
        if session_file and session_file.exists():
            try:
                import asyncio
                from gemini_webapi import GeminiClient
                from services.gemini_webapi_provider import extract_cookies_from_file

                psid, psidts = extract_cookies_from_file(session_file)
                if psid:
                    async def _gen_img():
                        c = GeminiClient(secure_1psid=psid, secure_1psidts=psidts)
                        await c.init(timeout=60, auto_refresh=True, verbose=False)
                        out = await c.generate_content(f"High-quality 4k photorealistic image for documentary: {prompt}")
                        if out.images:
                            await out.images[0].save(path=str(img_path.parent), filename=img_path.name)
                        await c.close()

                    asyncio.run(_gen_img())
                    if img_path.exists() and img_path.stat().st_size > 1000:
                        has_image = True
            except Exception as e:
                logger.warning("Image generation via WebAPI failed (%s). Checking DALL-E / external provider.", e)

        # Secondary fallback: Attempt OpenAI / DALL-E if key is available
        if not has_image:
            try:
                import os
                import base64
                from services.settings_service import get_llm_api_key
                api_key = get_llm_api_key() or os.environ.get("OPENAI_API_KEY")
                if api_key:
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)
                    resp = client.images.generate(
                        model="dall-e-3",
                        prompt=f"Cinematic historical documentary scene, atmospheric 4k: {prompt[:300]}",
                        n=1,
                        size="1024x1024",
                        response_format="b64_json",
                    )
                    if resp.data and resp.data[0].b64_json:
                        img_path.write_bytes(base64.b64decode(resp.data[0].b64_json))
                        has_image = True
            except Exception as err:
                logger.debug("DALL-E secondary image fallback skipped/failed: %s", err)

        # Tertiary fallback: create high-res cinematic vignette background with FFmpeg
        if not has_image or not img_path.exists():
            try:
                cmd_img = [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", "color=c=0x0d131f:s=1280x720:d=1,vignette=PI/4",
                    "-frames:v", "1",
                    str(img_path),
                ]
                subprocess.run(cmd_img, check=True, capture_output=True)
            except Exception as e:
                return ProviderResult(
                    ok=False,
                    error_kind=ErrorKind.FATAL,
                    error_message=f"Failed to generate base image: {e}",
                    duration_sec=time.time() - start_time,
                )

        # Step 2: Apply Ken Burns Zoom/Pan animation (10 seconds, 25fps = 250 frames, 1280x720)
        try:
            # Subtle zoom from 1.0 to 1.15 centered
            cmd_kb = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", str(img_path),
                "-vf", "zoompan=z='min(zoom+0.0006,1.15)':d=250:s=1280x720:fps=25",
                "-t", "10",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ]
            subprocess.run(cmd_kb, check=True, capture_output=True)

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
        except Exception as e:
            return ProviderResult(
                ok=False,
                error_kind=ErrorKind.FATAL,
                error_message=f"Ken Burns animation error: {e}",
                duration_sec=time.time() - start_time,
            )
