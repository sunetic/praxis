#!/usr/bin/env python3
"""
Praxis Demo Recording Script

Records segments of the product for the README demo GIF.
Run: python3 tools/record-demo.py

Flow: chat -> follow-up -> save as agent -> run agent -> schedule agent
"""

import shutil
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, Page, BrowserContext, Browser

BASE_URL = "http://localhost:5173"
API_URL = "http://localhost:8000/api/v1"
RECORDINGS_DIR = Path("assets/recordings")
VIEWPORT = {"width": 1280, "height": 720}


def type_slowly(page: Page, selector: str, text: str, delay: int = 50):
    page.locator(selector).click()
    page.keyboard.type(text, delay=delay)


def wait_for_response_done(page: Page, timeout_ms: int = 180_000):
    page.locator(".aui-composer-send").wait_for(state="visible", timeout=timeout_ms)


def find_button(page: Page, *candidates, scope=None):
    root = scope or page
    for text in candidates:
        btn = root.locator("button", has_text=text)
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
    raise Exception(f"Button not found: {candidates}")


def new_page(browser: Browser) -> tuple:
    ctx = browser.new_context(
        viewport=VIEWPORT,
        record_video_dir=str(RECORDINGS_DIR),
        record_video_size=VIEWPORT,
    )
    page = ctx.new_page()
    return ctx, page


def close_and_rename(ctx: BrowserContext, page: Page, name: str):
    video_path = page.video.path()
    ctx.close()
    dest = RECORDINGS_DIR / f"{name}.webm"
    shutil.move(video_path, dest)
    mb = dest.stat().st_size / 1024 / 1024
    print(f"  -> {dest} ({mb:.1f} MB)")


def api_get(ep):
    return requests.get(f"{API_URL}{ep}").json()

def api_delete(ep):
    requests.delete(f"{API_URL}{ep}")

def ensure_clean():
    for a in api_get("/agents"):
        if a["name"] == "Sales Analyst":
            api_delete(f"/agents/{a['id']}")
    for s in api_get("/schedules"):
        if s["name"] == "Daily Sales Report":
            api_delete(f"/schedules/{s['id']}")
    for c in api_get("/conversations"):
        api_delete(f"/conversations/{c['id']}")


# ── Segments ──


def seg1_chat(browser: Browser) -> str:
    """Chat: ask a question about the database."""
    print("[1] Chat — ask a question")
    ctx, page = new_page(browser)
    page.goto(f"{BASE_URL}/chat")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # New conversation
    try:
        find_button(page, "New", "新建").click()
        time.sleep(0.5)
    except Exception:
        pass

    # Select datasource
    try:
        ds = page.locator("button", has_text="mysql-test-1")
        if ds.count() == 0:
            # Click datasource dropdown
            find_button(page, "Select datasource", "选择数据源").click()
            time.sleep(0.3)
            page.locator("text=mysql-test-1").first.click()
            time.sleep(0.3)
    except Exception:
        pass

    type_slowly(page, ".aui-composer-input",
                "Which cities have the most customers? Show top 10")
    time.sleep(0.3)
    page.locator(".aui-composer-send").click()
    wait_for_response_done(page)
    time.sleep(2)

    url = page.url
    close_and_rename(ctx, page, "seg1-chat")
    return url


def seg2_followup(browser: Browser, chat_url: str):
    """Chat: follow-up question in the same conversation."""
    print("[2] Chat — follow-up")
    ctx, page = new_page(browser)
    page.goto(chat_url)
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    type_slowly(page, ".aui-composer-input",
                "Show monthly order trends for the top 3 cities")
    time.sleep(0.3)
    page.locator(".aui-composer-send").click()
    wait_for_response_done(page)
    time.sleep(2)

    close_and_rename(ctx, page, "seg2-followup")


def seg3_save_as_agent(browser: Browser, chat_url: str):
    """Chat: type 'save as new agent' to trigger AgentSaveTool."""
    print("[3] Chat — save as agent")
    ctx, page = new_page(browser)
    page.goto(chat_url)
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    type_slowly(page, ".aui-composer-input", "save as new agent")
    time.sleep(0.3)
    page.locator(".aui-composer-send").click()

    # Wait for the agent save flow — the LLM calls agent_save tool,
    # then the frontend shows a save dialog/notification or navigates.
    # Wait for the response to finish first.
    wait_for_response_done(page, timeout_ms=120_000)
    time.sleep(2)

    # The frontend may show a "save as agent" prompt/dialog
    # Look for the save dialog or "Go to edit" button
    try:
        save_btn = page.locator("button", has_text="Save")
        go_edit_btn = page.locator("button", has_text="Go to edit")
        edit_btn = page.locator("button", has_text="前往编辑")

        for btn in [save_btn, go_edit_btn, edit_btn]:
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                time.sleep(1)
                break
    except Exception:
        pass

    time.sleep(2)

    # If we're now on the agent edit page, fill in the name
    dialog = page.locator('[role="dialog"]')
    if dialog.count() > 0 and dialog.first.is_visible():
        name_input = dialog.locator("input").first
        if name_input.is_visible():
            name_input.clear()
            name_input.fill("Sales Analyst")
            time.sleep(0.3)
        # Fill description
        inputs = dialog.locator("input")
        if inputs.count() > 1:
            desc = inputs.nth(1)
            if desc.is_visible():
                desc.clear()
                desc.fill("Analyzes sales data and customer trends")
                time.sleep(0.3)
        # Save
        try:
            find_button(page, "Save", "保存", scope=dialog).click()
            time.sleep(2)
        except Exception:
            pass

    close_and_rename(ctx, page, "seg3-save-agent")


def seg4_run_agent(browser: Browser):
    """Agent page: find the saved agent and run it."""
    print("[4] Run Agent")

    # Find the most recently created agent
    agents = api_get("/agents")
    if not agents:
        print("  SKIP — no agents found")
        return
    latest = max(agents, key=lambda a: a["id"])
    agent_name = latest["name"]
    print(f"  Using agent: {agent_name!r}")

    ctx, page = new_page(browser)
    page.goto(f"{BASE_URL}/agent")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Find the agent row by name
    agent_row = page.locator("tr", has_text=agent_name)
    agent_row.wait_for(state="visible", timeout=10000)

    # Click play button (first button in the row)
    play_btn = agent_row.locator("button").first
    play_btn.click()
    time.sleep(0.5)

    # Run dialog — select datasource
    dialog = page.locator('[role="dialog"]')
    dialog.wait_for(state="visible", timeout=5000)

    cb = dialog.locator('input[type="checkbox"]').first
    if cb.is_visible():
        cb.check()
        time.sleep(0.3)

    find_button(page, "Run", "运行", scope=dialog).click()
    time.sleep(2)

    # In the new chat, ask a question
    page.locator(".aui-composer-input").wait_for(state="visible", timeout=15000)
    time.sleep(0.5)

    type_slowly(page, ".aui-composer-input", "What is the refund rate by month?")
    time.sleep(0.3)
    page.locator(".aui-composer-send").click()
    wait_for_response_done(page)
    time.sleep(2)

    close_and_rename(ctx, page, "seg4-run-agent")


def seg5_scheduler(browser: Browser):
    """Scheduler: configure the agent for daily execution."""
    print("[5] Schedule Agent")

    agents = api_get("/agents")
    if not agents:
        print("  SKIP — no agents found")
        return
    latest = max(agents, key=lambda a: a["id"])
    agent_name = latest["name"]
    print(f"  Using agent: {agent_name!r}")

    ctx, page = new_page(browser)
    page.goto(f"{BASE_URL}/scheduler")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    find_button(page, "New", "新建").click()
    time.sleep(1)

    dialog = page.locator('[role="dialog"]')
    dialog.wait_for(state="visible", timeout=5000)

    # Select Agent tab
    agent_tab = dialog.locator('button[value="agent"]')
    if agent_tab.count() == 0:
        agent_tab = dialog.locator("button", has_text="Agent")
    if agent_tab.count() > 0 and agent_tab.first.is_visible():
        agent_tab.first.click()
        time.sleep(0.5)

    # Pick the agent
    card = dialog.locator("button", has_text=agent_name)
    if card.count() > 0 and card.first.is_visible():
        card.first.click()
        time.sleep(0.3)

    # Fill name
    inputs = dialog.locator("input")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        ph = inp.get_attribute("placeholder") or ""
        if any(k in ph.lower() for k in ["name", "health", "例如"]) or i == 0:
            inp.clear()
            inp.fill("Daily Sales Report")
            time.sleep(0.2)
            break

    # Fill description
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        ph = inp.get_attribute("placeholder") or ""
        if any(k in ph.lower() for k in ["purpose", "notes", "备注"]):
            inp.fill("Automated daily sales summary")
            time.sleep(0.2)
            break

    # AI scheduling
    ta = dialog.locator("textarea").first
    if ta.count() > 0 and ta.is_visible():
        ta.fill("Run every day at 9 AM")
        time.sleep(0.3)

    # Create
    find_button(page, "Create Scheduler", "创建 Scheduler", "Create", "创建", scope=dialog).click()
    time.sleep(3)

    close_and_rename(ctx, page, "seg5-scheduler")


def main():
    print("=== Praxis Demo Recording ===\n")
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    for f in RECORDINGS_DIR.glob("*.webm"):
        f.unlink()

    ensure_clean()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        try:
            url = seg1_chat(browser)
            seg2_followup(browser, url)
            seg3_save_as_agent(browser, url)
            seg4_run_agent(browser)
            seg5_scheduler(browser)

            print("\n=== All segments recorded ===")
            for f in sorted(RECORDINGS_DIR.glob("seg*.webm")):
                mb = f.stat().st_size / 1024 / 1024
                print(f"  {f.name} ({mb:.1f} MB)")
            print("\nNext: bash tools/make-demo-gif.sh")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
