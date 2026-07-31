#!/usr/bin/env python3
"""
Praxis Demo Recording Script — DBA Health Check scenario.

Records segments for the README demo GIFs.
Flow: health check chat -> save as agent -> run agent -> schedule agent

Pacing principle: pause after every meaningful state change so the viewer
can read what happened. Forms: pause after filling. Lists: pause after
appearing. Dialogs: pause after selection.

Run: python3 tools/record-demo.py
"""

import shutil
import time
from pathlib import Path

import requests
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE_URL = "http://localhost:5173"
API_URL = "http://localhost:8000/api/v1"
RECORDINGS_DIR = Path("assets/recordings")
VIEWPORT = {"width": 1280, "height": 720}

# ── Pacing constants (seconds) ──
PAUSE_PAGE_LOAD = 1.5  # after page fully loads, let viewer orient
PAUSE_AFTER_TYPE = 1.0  # after finishing typing, before clicking send
PAUSE_AFTER_SELECT = 1.5  # after selecting an option (datasource, agent card)
PAUSE_FORM_FILLED = 2.0  # after form is completely filled, before submit
PAUSE_AFTER_SUBMIT = 2.0  # after clicking submit, waiting for result
PAUSE_RESULT_VIEW = 3.0  # viewing a result page / list / final state
PAUSE_AFTER_CLICK = 0.8  # after clicking a button, before next action


def type_slowly(page: Page, selector: str, text: str, delay: int = 50):
    page.locator(selector).click()
    page.keyboard.type(text, delay=delay)


def wait_for_response_done(page: Page, timeout_ms: int = 180_000):
    """Wait until the assistant finishes streaming.

    The send button may briefly appear between tool calls, so we wait for it
    to be visible AND stable (visible for 3 consecutive seconds).
    """
    deadline = time.time() + timeout_ms / 1000
    stable_since = None
    while time.time() < deadline:
        send_btn = page.locator(".aui-composer-send")
        if send_btn.count() > 0 and send_btn.is_visible():
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= 3.0:
                return
        else:
            stable_since = None
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for response to finish")


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
    for c in api_get("/conversations"):
        api_delete(f"/conversations/{c['id']}")
    for a in api_get("/agents"):
        api_delete(f"/agents/{a['id']}")
    for s in api_get("/schedules"):
        api_delete(f"/schedules/{s['id']}")


# ── Segments ──


def seg1_health_check(browser: Browser) -> str:
    """Chat: run a database health check."""
    print("[1] Chat — database health check")
    ctx, page = new_page(browser)
    page.goto(f"{BASE_URL}/chat")
    page.wait_for_load_state("networkidle")
    time.sleep(PAUSE_PAGE_LOAD)

    # Click New conversation
    try:
        find_button(page, "New", "新建").click()
        time.sleep(PAUSE_AFTER_CLICK)
    except Exception:
        pass

    # Select datasource
    try:
        find_button(page, "Select datasource", "选择数据源").click()
        time.sleep(PAUSE_AFTER_CLICK)
        page.locator("text=mysql-test-1").first.click()
        time.sleep(PAUSE_AFTER_SELECT)  # let viewer see which datasource was picked
    except Exception:
        pass

    # Type the health check question
    type_slowly(
        page,
        ".aui-composer-input",
        "Check the health of this database — table sizes, index usage, and anything that needs attention",
    )
    time.sleep(PAUSE_AFTER_TYPE)  # let viewer read the question before sending

    # Send and wait for full response
    page.locator(".aui-composer-send").click()
    wait_for_response_done(page)
    time.sleep(PAUSE_RESULT_VIEW)  # let viewer read the final health check results

    url = page.url
    close_and_rename(ctx, page, "seg1-health-check")
    return url


def seg2_save_agent(browser: Browser, chat_url: str):
    """Chat: save the health check conversation as an agent."""
    print("[2] Chat — save as agent")
    ctx, page = new_page(browser)
    page.goto(chat_url)
    page.wait_for_load_state("networkidle")
    time.sleep(PAUSE_PAGE_LOAD)

    # Type "save as new agent"
    type_slowly(page, ".aui-composer-input", "save as new agent")
    time.sleep(PAUSE_AFTER_TYPE)

    # Send
    page.locator(".aui-composer-send").click()
    wait_for_response_done(page, timeout_ms=120_000)
    time.sleep(PAUSE_RESULT_VIEW)  # let viewer read the save confirmation + agent summary

    # Click "Go to edit" or "Save" if it appears
    try:
        for btn_text in ["Save", "Go to edit", "前往编辑", "保存"]:
            btn = page.locator("button", has_text=btn_text)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                time.sleep(PAUSE_AFTER_SUBMIT)
                break
    except Exception:
        pass

    # Fill agent name if edit dialog appears
    dialog = page.locator('[role="dialog"]')
    if dialog.count() > 0 and dialog.first.is_visible():
        name_input = dialog.locator("input").first
        if name_input.is_visible():
            name_input.clear()
            name_input.fill("DB Health Check")
            time.sleep(PAUSE_FORM_FILLED)  # let viewer see the filled form
        try:
            find_button(page, "Save", "保存", scope=dialog).click()
            time.sleep(PAUSE_RESULT_VIEW)  # let viewer see the result
        except Exception:
            pass

    close_and_rename(ctx, page, "seg2-save-agent")


def seg3_run_agent(browser: Browser):
    """Agent page: run the saved agent."""
    print("[3] Run Agent")

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
    time.sleep(PAUSE_PAGE_LOAD)
    time.sleep(1.0)  # extra pause: let viewer see the agent list

    # Click Play on the agent row
    agent_row = page.locator("tr", has_text=agent_name)
    agent_row.wait_for(state="visible", timeout=10000)
    play_btn = agent_row.locator("button").first
    play_btn.click()
    time.sleep(PAUSE_AFTER_CLICK)

    # Run dialog — select datasource
    dialog = page.locator('[role="dialog"]')
    dialog.wait_for(state="visible", timeout=5000)
    time.sleep(0.5)  # let viewer see the dialog

    cb = dialog.locator('input[type="checkbox"]').first
    if cb.is_visible():
        cb.check()
        time.sleep(PAUSE_AFTER_SELECT)  # let viewer see what datasource was selected

    # Click Run
    find_button(page, "Run", "运行", scope=dialog).click()
    time.sleep(PAUSE_AFTER_SUBMIT)

    # Wait for agent to start auto-executing
    page.locator(".aui-composer-input").wait_for(state="visible", timeout=15000)
    time.sleep(15)  # let the agent run multi-step analysis

    close_and_rename(ctx, page, "seg3-run-agent")


def seg4_scheduler(browser: Browser):
    """Scheduler: configure the agent for daily execution."""
    print("[4] Schedule Agent")

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
    time.sleep(PAUSE_PAGE_LOAD)

    # Click New
    find_button(page, "New", "新建").click()
    time.sleep(PAUSE_AFTER_CLICK)

    # Dialog opens
    dialog = page.locator('[role="dialog"]')
    dialog.wait_for(state="visible", timeout=5000)
    time.sleep(0.5)

    # Select Agent tab
    agent_tab = dialog.locator('button[value="agent"]')
    if agent_tab.count() == 0:
        agent_tab = dialog.locator("button", has_text="Agent")
    if agent_tab.count() > 0 and agent_tab.first.is_visible():
        agent_tab.first.click()
        time.sleep(PAUSE_AFTER_CLICK)

    # Pick agent card
    card = dialog.locator("button", has_text=agent_name)
    if card.count() > 0 and card.first.is_visible():
        card.first.click()
        time.sleep(PAUSE_AFTER_SELECT)  # let viewer see which agent was selected

    # Fill schedule name
    inputs = dialog.locator("input")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        ph = inp.get_attribute("placeholder") or ""
        if any(k in ph.lower() for k in ["name", "health", "例如"]) or i == 0:
            inp.clear()
            inp.fill("Daily Health Check")
            time.sleep(PAUSE_AFTER_CLICK)
            break

    # Fill description
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        ph = inp.get_attribute("placeholder") or ""
        if any(k in ph.lower() for k in ["purpose", "notes", "备注"]):
            inp.fill("Automated daily database health check")
            time.sleep(PAUSE_AFTER_CLICK)
            break

    # AI scheduling intent
    ta = dialog.locator("textarea").first
    if ta.count() > 0 and ta.is_visible():
        ta.fill("Run every day at 9 AM")
        time.sleep(PAUSE_FORM_FILLED)  # let viewer see the complete form

    # Click Create
    find_button(page, "Create Scheduler", "创建 Scheduler", "Create", "创建", scope=dialog).click()
    time.sleep(PAUSE_AFTER_SUBMIT)

    # Wait for dialog to close and list to appear
    dialog.wait_for(state="hidden", timeout=10000)
    time.sleep(PAUSE_RESULT_VIEW)  # let viewer see the scheduler list with the new entry

    close_and_rename(ctx, page, "seg4-scheduler")


def main():
    print("=== Praxis Demo Recording (DBA Health Check) ===\n")
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    for f in RECORDINGS_DIR.glob("*.webm"):
        f.unlink()

    ensure_clean()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        try:
            url = seg1_health_check(browser)
            seg2_save_agent(browser, url)
            seg3_run_agent(browser)
            seg4_scheduler(browser)

            print("\n=== All segments recorded ===")
            for f in sorted(RECORDINGS_DIR.glob("seg*.webm")):
                mb = f.stat().st_size / 1024 / 1024
                print(f"  {f.name} ({mb:.1f} MB)")
            print("\nNext: bash tools/make-demo-gif.sh")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
