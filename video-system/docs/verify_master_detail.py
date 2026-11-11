"""Playwright E2E & Responsive Verification for Multi-Page Master-Detail Architecture on https://vidfac.gacdero.vn."""
from __future__ import annotations

import os
import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://vidfac.gacdero.vn"
PIN = "997220"
OUT_DIR = r"c:\app\work\video-system\docs\screenshots"


def check_no_horizontal_overflow(page, label: str) -> dict:
    metrics = page.evaluate(
        """() => {
            const doc = document.documentElement;
            const body = document.body;
            return {
                clientWidth: doc.clientWidth,
                scrollWidth: Math.max(doc.scrollWidth, body ? body.scrollWidth : 0),
                innerWidth: window.innerWidth
            };
        }"""
    )
    overflow = metrics["scrollWidth"] > metrics["clientWidth"]
    print(f"[{label}] clientWidth={metrics['clientWidth']} scrollWidth={metrics['scrollWidth']} overflow={overflow}")
    assert not overflow, f"Horizontal overflow on {label}: {metrics}"
    return metrics


def unlock_pin_if_needed(page):
    overlay = page.locator("#authOverlay")
    if overlay.is_visible():
        for i, ch in enumerate(PIN):
            page.locator(f"#pin{i}").fill(ch)
        page.wait_for_selector("#authOverlay.hidden", state="attached", timeout=15000)


def run_verification():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # =====================================================================
        # 1. DESKTOP (1440x900)
        # =====================================================================
        ctx_desk = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx_desk.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        unlock_pin_if_needed(page)

        # Wait for all 11 compact summary cards on Page 1
        page.wait_for_selector(".todo-summary-card", timeout=20000)
        summary_count = page.locator("#pageTodoList .todo-summary-card").count()
        print(f"[Desktop Page 1] Compact summary cards count: {summary_count}")
        assert summary_count >= 11, f"Expected >=11 compact summary cards on Page 1, got {summary_count}"

        # Ensure Page 1 has ZERO heavy shot grids or inline shot videos mounted
        inline_videos_on_page1 = page.locator("#pageTodoList video").count()
        shot_grids_on_page1 = page.locator("#pageTodoList .shot-video-grid").count()
        print(f"[Desktop Page 1] Inline videos={inline_videos_on_page1}, shot grids={shot_grids_on_page1}")
        assert inline_videos_on_page1 == 0, "Page 1 must not mount any <video> elements!"
        assert shot_grids_on_page1 == 0, "Page 1 must not mount any .shot-video-grid!"

        check_no_horizontal_overflow(page, "Desktop 1440x900 - Page 1 (Master List)")
        page.screenshot(path=os.path.join(OUT_DIR, "page1_todo_list_desktop.png"), full_page=False)

        # Click first job card to open Page 2 (Job Detail Page - Overview Tab)
        first_card = page.locator("#pageTodoList .todo-summary-card").first
        first_item_id = first_card.get_attribute("data-item-id")
        first_card.click()

        page.wait_for_selector("#pageTodoDetail:not(.hidden) .detail-page-nav", timeout=10000)
        assert page.locator("#pageTodoList").evaluate("el => el.classList.contains('hidden')")
        assert f"#/todos/{first_item_id}" in page.url
        print(f"[Desktop Page 2] Opened Job Detail URL: {page.url}")

        # Verify Overview Tab
        check_no_horizontal_overflow(page, "Desktop 1440x900 - Page 2 (Overview Tab)")
        page.screenshot(path=os.path.join(OUT_DIR, "page2_job_detail_overview_desktop.png"), full_page=False)

        # Switch to Flow Tab 2: Shots Grid
        page.locator("#pageTodoDetail .detail-flow-tab[data-flow-tab='shots']").click()
        page.wait_for_selector("#pageTodoDetail .shot-video-grid", timeout=10000)
        check_no_horizontal_overflow(page, "Desktop 1440x900 - Page 2 (Shots Grid Tab)")
        page.screenshot(path=os.path.join(OUT_DIR, "page2_job_detail_shots_desktop.png"), full_page=False)

        # Switch to Flow Tab 3: Checklist
        page.locator("#pageTodoDetail .detail-flow-tab[data-flow-tab='checklist']").click()
        page.wait_for_selector("#pageTodoDetail .checklist-table", timeout=10000)
        check_no_horizontal_overflow(page, "Desktop 1440x900 - Page 2 (Checklist Tab)")

        # Switch to Flow Tab 4: Assets & Uploads
        page.locator("#pageTodoDetail .detail-flow-tab[data-flow-tab='assets']").click()
        page.wait_for_selector("#pageTodoDetail .upload-forms-grid", timeout=10000)
        check_no_horizontal_overflow(page, "Desktop 1440x900 - Page 2 (Assets Tab)")

        # Test Back button to Page 1
        page.locator("#backToTodoListBtn").click()
        page.wait_for_selector("#pageTodoList:not(.hidden) .todo-summary-card", timeout=10000)
        assert page.locator("#pageTodoDetail").evaluate("el => el.classList.contains('hidden')")
        ctx_desk.close()

        # =====================================================================
        # 2. MOBILE (375x812 & 390x844)
        # =====================================================================
        for width, height in [(375, 812), (390, 844)]:
            ctx_mob = browser.new_context(
                viewport={"width": width, "height": height},
                is_mobile=True,
                has_touch=True,
            )
            mpage = ctx_mob.new_page()
            mpage.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            unlock_pin_if_needed(mpage)

            mpage.wait_for_selector(".todo-summary-card", timeout=20000)
            check_no_horizontal_overflow(mpage, f"Mobile {width}x{height} - Page 1 (Master List)")
            if width == 375:
                mpage.screenshot(path=os.path.join(OUT_DIR, "page1_todo_list_mobile_375.png"), full_page=False)

            # Click first job card to open Page 2 on Mobile
            mpage.locator("#pageTodoList .todo-summary-card").first.click()
            mpage.wait_for_selector("#pageTodoDetail:not(.hidden) .detail-page-nav", timeout=10000)
            check_no_horizontal_overflow(mpage, f"Mobile {width}x{height} - Page 2 (Overview Tab)")
            if width == 375:
                mpage.screenshot(path=os.path.join(OUT_DIR, "page2_job_detail_mobile_375.png"), full_page=False)

            # Check all flow tabs on Mobile for zero horizontal overflow
            for tab_key in ["shots", "checklist", "assets", "all"]:
                mpage.locator(f"#pageTodoDetail .detail-flow-tab[data-flow-tab='{tab_key}']").click()
                mpage.wait_for_timeout(200)
                check_no_horizontal_overflow(mpage, f"Mobile {width}x{height} - Page 2 ({tab_key} Tab)")

            ctx_mob.close()

        browser.close()
        print("ALL MASTER-DETAIL DESKTOP & MOBILE CHECKS PASSED!")


if __name__ == "__main__":
    run_verification()
