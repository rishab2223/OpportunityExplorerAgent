"""The Saved answers page, opened from the user menu, in a real browser.

A scratch history DB (which holds the answer bank), its own uvicorn on a
spare port: the server on :8000 and the real answers are never touched.
"""

from __future__ import annotations

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

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_answers_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import answers, history  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
answers._invalidate()
answers.remember("Notice period", "30 days")
answers.remember("How did you hear about us?", "LinkedIn")
answers.remember("Will you now or in the future require sponsorship?", "No")

PORT = 8775

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


def keys(page):
    return page.eval_on_selector_all("#answers tbody tr[data-key]", "rows => rows.map(r => r.dataset.key)")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

    print("== opening ==")
    page.click("#userbtn")
    page.click("#usermenu button:text-is(\"Saved answers\")")
    page.wait_for_selector("#answers tbody tr[data-key]")
    check("the view shows", page.locator("#view-answers").is_visible())
    check("every answer is listed", sorted(keys(page)) == sorted(
        ["notice_period", "referral_source", "sponsorship"]), str(keys(page)))
    kind = page.text_content("#answers tr[data-key=sponsorship] .akind")
    check("a legal declaration is marked sensitive", kind == "sensitive", kind)

    print("== search ==")
    page.fill("#answersearch", "linkedin")
    check("search narrows the list", keys(page) == ["referral_source"], str(keys(page)))
    page.fill("#answersearch", "")

    print("== edit ==")
    row = "#answers tr[data-key=notice_period]"
    page.click(f"{row} button:text-is(\"Edit\")")
    page.fill(f"{row} textarea", "Immediate")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('#answersinfo').textContent === 'Saved'")
    answers._invalidate()
    check("the edit is stored", (answers.lookup("notice_period") or {}).get("answer") == "Immediate")
    check("the row shows it", (page.text_content(f"{row} .aanswer") or "").strip() == "Immediate")

    page.click(f"{row} button:text-is(\"Edit\")")
    page.fill(f"{row} textarea", "my password is hunter2")
    page.click(f"{row} button:text-is(\"Save\")")
    page.wait_for_selector(f"{row} .perror:not(:empty)")
    check("a secret is refused", "never stored" in (page.text_content(f"{row} .perror") or ""))
    page.keyboard.press("Escape")
    answers._invalidate()
    check("the stored answer is unchanged", (answers.lookup("notice_period") or {}).get("answer") == "Immediate")

    print("== delete ==")
    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#answers tr[data-key=referral_source] button:text-is(\"Delete\")")
    page.wait_for_function("document.querySelector('#answersinfo').textContent === 'Deleted'")
    answers._invalidate()
    check("it is gone from the bank", answers.lookup("referral_source") is None)
    check("and from the page", "referral_source" not in keys(page))

    check("no script errors", not errors, "; ".join(errors)[:200])
    browser.close()

server.should_exit = True
print()
if problems:
    print("ANSWERS UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("ANSWERS UI CHECK PASSED")
