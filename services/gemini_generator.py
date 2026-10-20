"""Gemini Video Generation Engine using Headless Playwright.

Controls Gemini web interface using captured session cookies to:
1. Send video generation prompts.
2. Monitor progress.
3. Detect rate limits / quota limits.
4. Download the generated MP4 clip.
"""

import logging
from pathlib import Path
import time
from typing import Optional, Tuple
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger("gemini_generator")

GEMINI_APP_URL = "https://gemini.google.com/app"
DEFAULT_TEMP_DIR = Path("data/temp_shots")


class GeminiGenerator:
    def __init__(self, temp_dir: Optional[Path] = None, headless: bool = True):
        self.temp_dir = Path(temp_dir or DEFAULT_TEMP_DIR)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless

    def generate_shot(
        self,
        prompt: str,
        session_file: Path,
        expected_filename: str,
        max_wait_seconds: int = 300,
    ) -> Tuple[bool, Optional[Path], Optional[str]]:
        """
        Runs headless Playwright with session cookies, submits prompt, waits for video,
        and saves MP4 to temp_dir / expected_filename.

        Returns (success: bool, downloaded_file_path: Optional[Path], error_or_reason: Optional[str]).
        """
        output_path = self.temp_dir / expected_filename

        if not session_file.exists():
            return False, None, f"Session file not found: {session_file}"

        logger.info("Starting generation with session %s for %s", session_file.stem, expected_filename)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                storage_state=str(session_file),
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.set_default_timeout(45000)

            try:
                # 1. Navigate to Gemini App
                logger.info("Navigating to Gemini...")
                page.goto(GEMINI_APP_URL, wait_until="networkidle")

                # Check if session is expired (redirected to Google login)
                if "accounts.google.com" in page.url:
                    browser.close()
                    return False, None, "AUTH_EXPIRED: Redirected to Google login"

                # 2. Locate input box
                # Gemini prompt box can be div[contenteditable="true"], .ql-editor, or rich-textarea
                input_selector = 'div[contenteditable="true"], rich-textarea, textarea, .ql-editor'
                page.wait_for_selector(input_selector, timeout=20000)

                # Focus and fill prompt
                page.click(input_selector)
                page.fill(input_selector, prompt)
                time.sleep(1)

                # 3. Submit prompt
                send_button_selector = 'button[aria-label*="Send"], button[aria-label*="Gửi"], button.send-button'
                if page.locator(send_button_selector).count() > 0:
                    page.click(send_button_selector)
                else:
                    page.keyboard.press("Enter")

                logger.info("Prompt submitted. Waiting for response/video...")

                # 4. Monitor response & wait for video element or download button
                start_time = time.time()
                video_downloaded = False

                while time.time() - start_time < max_wait_seconds:
                    page_text = page.inner_text("body")

                    # Check for rate limit or quota exceeded
                    if any(msg in page_text for msg in [
                        "reached your limit",
                        "chạm giới hạn",
                        "tạm thời bị giới hạn",
                        "Please try again later",
                        "hãy thử lại sau",
                    ]):
                        browser.close()
                        return False, None, "RATE_LIMIT: Quota exceeded on this account"

                    # Check for video element
                    video_elements = page.locator("video")
                    if video_elements.count() > 0:
                        first_video = video_elements.first
                        video_src = first_video.get_attribute("src")
                        if video_src and video_src.startswith("http"):
                            logger.info("Video element found! Source URL detected: %s", video_src[:60])
                            # Download via Playwright request or page eval
                            response = page.request.get(video_src)
                            if response.status == 200:
                                output_path.write_bytes(response.body())
                                video_downloaded = True
                                break

                    # Also check for download button
                    download_btn = page.locator('button[aria-label*="Download"], a[download]')
                    if download_btn.count() > 0:
                        with page.expect_download(timeout=30000) as download_info:
                            download_btn.first.click()
                        download = download_info.value
                        download.save_as(str(output_path))
                        video_downloaded = True
                        break

                    time.sleep(5)

                browser.close()

                if video_downloaded and output_path.exists() and output_path.stat().st_size > 1024:
                    logger.info("Shot generated and saved successfully: %s (%d bytes)", output_path, output_path.stat().st_size)
                    return True, output_path, None
                else:
                    return False, None, f"TIMEOUT: Video was not generated within {max_wait_seconds}s"

            except PlaywrightTimeoutError as te:
                browser.close()
                return False, None, f"Playwright Timeout: {te}"
            except Exception as e:
                browser.close()
                return False, None, f"Generation error: {e}"


if __name__ == "__main__":
    gen = GeminiGenerator()
    print("GeminiGenerator initialized.")
