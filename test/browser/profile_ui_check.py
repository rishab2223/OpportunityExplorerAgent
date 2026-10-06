"""The Profile page, opened from the user menu, in a real browser.

Its own uvicorn on a spare port with a scratch profile file and history DB,
so the server on :8000 is never touched and the real profile is never read or
written.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_profile_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src.apply import profile  # noqa: E402

profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.com",
    "total_experience_years": "6",
    "work_authorization": "Yes",
    "jobs": [{"title": "Engineer", "company": "Example Co", "location": "Remote",
              "start": "01/2020", "end": "", "current": True, "description": "Built things."}],
}), encoding="utf-8")

PORT = 8771

import uvicorn  # noqa: E402
from src.web.app import app  # noqa: E402

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    probe = socket.socket()
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        probe.close()
        break
    probe.close()
    time.sleep(0.1)
else:
    raise SystemExit("server did not start")

from playwright.sync_api import sync_playwright  # noqa: E402

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


def on_disk() -> dict:
    return json.loads(profile.PROFILE_PATH.read_text(encoding="utf-8"))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

    print("== the user menu ==")
    check("menu starts closed", page.locator("#usermenu").is_hidden())
    page.click("#userbtn")
    check("the icon opens it", page.locator("#usermenu").is_visible())
    check("aria-expanded follows", page.get_attribute("#userbtn", "aria-expanded") == "true")
    page.keyboard.press("Escape")
    check("Escape closes it", page.locator("#usermenu").is_hidden())
    page.click("#userbtn")
    page.mouse.click(700, 600)
    check("a click elsewhere closes it", page.locator("#usermenu").is_hidden())

    print("== opening the profile ==")
    page.click("#userbtn")
    page.click("#usermenu >> text=Profile")
    page.wait_for_selector("#pf-full_name")
    check("the profile view shows", page.locator("#view-profile").is_visible())
    check("the jobs view hides", page.locator("#view-jobs").is_hidden())
    check("no tab is marked active", page.locator("#nav button.active").count() == 0)
    check("values come from the file", page.input_value("#pf-full_name") == "Test User")
    check("an unknown key shows under Other", page.locator("#pf-work_authorization").count() == 1)
    check("the job entry shows", page.input_value("#pj-0-company") == "Example Co")
    check("a current job's end is disabled", page.locator("#pj-0-end").is_disabled())
    notice = page.text_content("#profileempty") or ""
    marked = page.locator("#profileform .pfield[data-key].empty").count()
    check("empty fields are counted", notice.startswith(f"{marked} empty (")
          and "more), marked below" in notice, f"{marked} marked; {notice[:80]}")
    check("an empty field is marked",
          "empty" in (page.get_attribute(".pfield[data-key=notice_period]", "class") or ""))
    check("Save starts disabled", page.locator("#profilesave").is_disabled())

    print("== editing and saving ==")
    page.fill("#pf-total_experience_years", "6.5")
    page.fill("#pf-notice_period", "Immediate")
    check("Save enables", page.locator("#profilesave").is_enabled())
    check("unsaved changes are counted", "2 unsaved" in (page.text_content("#profilestate") or ""))
    check("a filled field stops being marked",
          "empty" not in (page.get_attribute(".pfield[data-key=notice_period]", "class") or ""))
    page.click("#profilesave")
    page.wait_for_function("document.querySelector('#profilestate').textContent === 'Saved'")
    data = on_disk()
    check("the change is on disk", data["total_experience_years"] == "6.5" and data["notice_period"] == "Immediate")
    check("untouched keys survive", data["work_authorization"] == "Yes" and data["full_name"] == "Test User")
    check("Save disables again", page.locator("#profilesave").is_disabled())

    print("== a bad value ==")
    page.fill("#pf-email", "not-an-email")
    page.click("#profilesave")
    page.wait_for_selector("[data-error-for=email]:not(:empty)")
    check("the error shows on the field", "email" in (page.text_content("[data-error-for=email]") or ""))
    check("nothing was written", on_disk()["email"] == "test@example.com")
    check("the state says why", "Not saved" in (page.text_content("#profilestate") or ""))
    page.click("#profilediscard")
    check("Discard restores the saved value", page.input_value("#pf-email") == "test@example.com")

    print("== work history ==")
    page.click("text=Add job")
    check("a new job goes on top", page.input_value("#pj-0-company") == "" and
          page.input_value("#pj-1-company") == "Example Co")
    page.fill("#pj-0-title", "Intern")
    page.fill("#pj-0-company", "Second Co")
    page.fill("#pj-0-start", "06/2019")
    page.fill("#pj-0-end", "07/2020")
    page.click("#profilesave")
    page.wait_for_function("document.querySelector('#profilestate').textContent === 'Saved'")
    jobs = on_disk()["jobs"]
    check("the job list is saved in order", [j["company"] for j in jobs] == ["Second Co", "Example Co"])
    page.fill("#pj-0-start", "2019")
    page.click("#profilesave")
    page.wait_for_selector("[data-error-for=jobs]:not(:empty)")
    check("a bad date is refused", "MM/YYYY" in (page.text_content("[data-error-for=jobs]") or ""))
    page.click("#profilediscard")

    print("== a choice field's suggestions ==")
    # Chrome's own <datalist> popup opened far from the box (Oct 2026); the
    # list is the page's own now, and opens directly under it.
    box = page.locator("#pf-willing_to_travel")
    box.scroll_into_view_if_needed()
    check("the list starts closed", page.locator("#pf-willing_to_travel-choices").is_hidden())
    box.click()
    choices = page.locator("#pf-willing_to_travel-choices")
    check("clicking the box opens it", choices.is_visible())
    under, near = box.bounding_box(), choices.bounding_box()
    check("right under the box", abs(near["y"] - (under["y"] + under["height"])) <= 6
          and abs(near["x"] - under["x"]) <= 2, f"box {under}, list {near}")
    choices.locator("li", has_text="No").click()
    check("a pick fills the box", box.input_value() == "No")
    check("and closes the list", choices.is_hidden())
    check("and counts as a change", "unsaved" in (page.text_content("#profilestate") or ""))
    box.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    check("the keyboard picks too", box.input_value() == "Yes")
    box.fill("Only within India")
    check("any other value can still be typed", box.input_value() == "Only within India")
    page.keyboard.press("Escape")
    check("Escape closes the list", choices.is_hidden())
    page.click("#profilediscard")

    print("== leaving and coming back keeps edits ==")
    page.fill("#pf-city", "Gurgaon")
    page.click("#nav button[data-view=jobs]")
    page.click("#userbtn")
    page.click("#usermenu >> text=Profile")
    check("an unsaved edit survives a trip to Jobs", page.input_value("#pf-city") == "Gurgaon")

    check("no script errors", not errors, "; ".join(errors)[:200])
    browser.close()

server.should_exit = True
print()
if problems:
    print("PROFILE UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("PROFILE UI CHECK PASSED")
