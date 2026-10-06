"""A photo box is never handed the resume, and the candidate's own file
dialog opens on it.

CryptoMize (Oct 2 2026): once the resume was approved, every file picker the
candidate opened was caught and given the resume PDF - including "Upload
photo", four times - and since the agent was listening for pickers, the
browser never showed its own dialog at all.

Headless, so no real dialog can appear. What is checked is what decides it:
whether Playwright is still intercepting pickers (it does exactly while a
listener is attached) and what went into each box.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import worker  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_photo_"))
RESUME = SCRATCH / "Test_User_Resume.pdf"
RESUME.write_bytes(b"%PDF-1.4\n%%EOF\n")

problems: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        problems.append(label)


class Sess:
    def __init__(self):
        self.lines: list[str] = []

    def log(self, text):
        self.lines.append(text)
        print("    LOG", text[:110])


def files(page, selector):
    return page.eval_on_selector(selector, "e => Array.from(e.files).map(f => f.name)")


def intercepting(page) -> bool:
    return bool(page._impl_obj.listeners("filechooser"))


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto((HERE / "fixture_photo_upload.html").as_uri())
    sess = Sess()
    attach = types.SimpleNamespace(resume_path=str(RESUME), letter_pdf="")

    worker._arm_file_chooser(page, attach, sess)
    check("armed: pickers are caught", intercepting(page))

    print("\n== the resume box still gets the resume ==")
    page.click("label[for=cv]")
    page.wait_for_timeout(300)
    check("the resume went in", files(page, "#cv") == [RESUME.name], str(files(page, "#cv")))

    print("\n== a box the page calls a photo ==")
    page.click("label[for=pic]")
    page.wait_for_timeout(300)
    check("the photo box is not given the resume", files(page, "#pic") == [], str(files(page, "#pic")))
    check("pickers are no longer caught, so the dialog will open",
          not intercepting(page))
    check("the candidate is told to click again",
          any("wants a photo" in line for line in sess.lines))

    print("\n== during the pause, arming does nothing ==")
    worker._arm_file_chooser(page, attach, sess)
    check("still not intercepting", not intercepting(page))

    print("\n== after the pause, the resume picker comes back ==")
    page._oea_chooser_paused_until = 0
    worker._arm_file_chooser(page, attach, sess)
    check("re-armed", intercepting(page))

    print("\n== a box that takes only images ==")
    page.click("label[for=img]")
    page.wait_for_timeout(300)
    check("an image-only box is not given a PDF", files(page, "#img") == [], str(files(page, "#img")))
    check("and it steps aside there too", not intercepting(page))

    print("\n== human checks are recognised, never answered ==")
    from src.apply import browser as agent_browser

    page.goto((HERE / "fixture_turnstile.html").as_uri())
    check("Cloudflare's widget is named", agent_browser.human_check(page) == "cloudflare",
          agent_browser.human_check(page))
    page.goto((HERE / "fixture_photo_upload.html").as_uri())
    check("a plain form carries none", agent_browser.human_check(page) == "",
          agent_browser.human_check(page))
    page.goto((HERE / "fixture_recaptcha.html").as_uri())
    check("an invisible reCAPTCHA is not a box to tick",
          agent_browser.human_check(page) == "", agent_browser.human_check(page))
    page.evaluate("() => { document.getElementById('visible').style.display = 'block'; }")
    check("the 'I'm not a robot' box is", agent_browser.human_check(page) == "recaptcha",
          agent_browser.human_check(page))

    browser.close()

print()
if problems:
    print("PHOTO PICKER CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("PHOTO PICKER CHECK PASSED")
