"""Load the dashboard in a real browser and check the restructured UI.

Runs its own uvicorn on a spare port with a scratch history DB, so the
server on :8000 is never touched. Reports console errors, checks the
toolbars and Job info wiring, and saves screenshots.
"""

from __future__ import annotations

import re
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

PORT = 8765
SHOTS = WORK / "ui_shots"
SHOTS.mkdir(exist_ok=True)

import uvicorn  # noqa: E402
from src.web.app import app  # noqa: E402

config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
server = uvicorn.Server(config)
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
        problems.append(f"{label} {detail}")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    page.wait_for_timeout(1200)

    print("== structure ==")
    check("two Start queue buttons (top and bottom)",
          page.locator('[data-act="startqueue"]').count() == 2,
          str(page.locator('[data-act="startqueue"]').count()))
    check("two Job info buttons",
          page.locator('[data-act="jobinfo"]').count() == 2,
          str(page.locator('[data-act="jobinfo"]').count()))
    check("three cards in the jobs view",
          page.locator("#view-jobs .card").count() == 3,
          str(page.locator("#view-jobs .card").count()))
    check("the selected-job panel starts closed",
          page.locator("#jobdetail[open]").count() == 0)
    check("the apply pane follows the shortlist card",
          page.locator("#view-jobs .card").nth(2).locator("#applylog").count() == 1)

    print("\n== the referrals tab still switches ==")
    page.click('#nav button[data-view="referrals"]')
    page.wait_for_timeout(300)
    check("jobs view hidden", page.locator("#view-jobs").is_hidden())
    check("referrals view shown", page.locator("#view-referrals").is_visible())
    check("the shortlist card went with it",
          page.locator('[data-act="startqueue"]').first.is_hidden())
    page.screenshot(path=str(SHOTS / "referrals.png"), full_page=True)
    page.click('#nav button[data-view="jobs"]')
    page.wait_for_timeout(300)
    check("jobs view back", page.locator("#view-jobs").is_visible())

    print("\n== rows, ticks and the Job info button ==")
    rows = page.locator("#jobs tbody tr")
    count = rows.count()
    print(f"  ({count} row(s) in the newest run)")
    if count and page.locator("#jobs tbody td.tick input").count():
        check("Job info starts disabled",
              page.locator('[data-act="jobinfo"]').first.is_disabled())
        # The Company cell: the Links cell deliberately stops propagation,
        # and a click in the middle of the row lands on it.
        rows.first.locator('td').nth(1).click()
        page.wait_for_timeout(400)
        check("a row click selects it",
              page.locator("#jobs tbody tr.selected").count() == 1)
        check("a row click does NOT open the big panel",
              page.locator("#jobdetail[open]").count() == 0)
        check("Job info is armed once a row is picked",
              not page.locator('[data-act="jobinfo"]').first.is_disabled())
        page.locator('[data-act="jobinfo"]').first.click()
        page.wait_for_timeout(400)
        check("Job info opens the panel",
              page.locator("#jobdetail[open]").count() == 1)
        page.locator('[data-act="jobinfo"]').first.click()
        page.wait_for_timeout(300)
        check("and closes it again",
              page.locator("#jobdetail[open]").count() == 0)

        page.locator("#jobs tbody td.tick input").first.check()
        page.wait_for_timeout(300)
        top = page.locator('[data-act="startqueue"]').first
        bottom = page.locator('[data-act="startqueue"]').last
        check("ticking arms BOTH queue buttons",
              not top.is_disabled() and not bottom.is_disabled())
        check("both show the same count",
              top.inner_text() == bottom.inner_text(),
              f"{top.inner_text()!r} vs {bottom.inner_text()!r}")
        check("the ticked row is marked",
              page.locator("#jobs tbody tr.queued").count() == 1)
        page.locator("#jobs tbody td.tick input").first.uncheck()
        page.wait_for_timeout(200)
        check("unticking disarms them", top.is_disabled())
    else:
        print("  (no rows in outputs/ - skipping row checks)")

    # --- select all pending on this page ------------------------------
    all_box = page.locator("#tickall")
    check("the tick column has a select-all box", all_box.count() == 1)
    pending = page.locator("#jobs tbody tr td.status-pending").count()
    all_box.check()
    page.wait_for_timeout(400)
    ticked = page.locator("#jobs tbody td.tick input:checked").count()
    check("it ticks every pending row on the page and only those",
          ticked == pending, f"{ticked} ticked, {pending} pending on screen")
    check("the queue button agrees",
          page.locator('[data-act="startqueue"]').first.inner_text() == f"Start queue ({pending})",
          page.locator('[data-act="startqueue"]').first.inner_text())
    check("rows already applied or closed are left alone",
          page.locator("#jobs tbody tr.queued td.status-applied").count() == 0
          and page.locator("#jobs tbody tr.queued td.status-closed").count() == 0)
    # Unticking one row must drop the header box to the dash, not to empty.
    page.locator("#jobs tbody td.tick input:checked").first.uncheck()
    page.wait_for_timeout(300)
    check("one row off leaves the header box indeterminate",
          page.evaluate("() => document.getElementById('tickall').indeterminate"))
    # A click on the dash is a click on a checkbox the browser treats as
    # unchecked, so it selects all - which is what the person clicking it
    # wants, and it is how every other tri-state box behaves.
    all_box.click()
    page.wait_for_timeout(300)
    check("clicking the dash selects them all again",
          page.locator("#jobs tbody td.tick input:checked").count() == pending,
          str(page.locator("#jobs tbody td.tick input:checked").count()))
    all_box.click()
    page.wait_for_timeout(300)
    check("and clicking again clears them",
          page.locator("#jobs tbody td.tick input:checked").count() == 0,
          str(page.locator("#jobs tbody td.tick input:checked").count()))

    # --- column filters on Status and Source -------------------------
    top = page.locator('#jobs .colfilter[data-filter="status"]')
    check("the Status header carries a filter", top.count() == 1)
    rows_before = rows.count()
    top.click()
    page.wait_for_timeout(300)
    items = page.locator(".colmenu .colmenu-item")

    def option_name(i):
        return items.nth(i).locator("span").first.inner_text().strip()

    def option_count(i):
        return int(items.nth(i).locator(".colmenu-count").inner_text().strip())

    def total_shown():
        """How many jobs the table is showing. The pager hides itself below
        one page, and then the rows on screen ARE the total."""
        found = re.search(r"(\d+) job", page.locator("#jobspager").inner_text())
        return int(found.group(1)) if found else page.locator("#jobs tbody tr").count()

    check("the menu opens with All plus the values in this run",
          items.count() >= 2, f"{items.count()} option(s)")
    labels = [option_name(i) for i in range(items.count())]
    check("All comes first", labels and labels[0] == "All", str(labels))
    check("the counts add up to the whole run",
          sum(option_count(i) for i in range(1, items.count())) == option_count(0))
    # Pick the first real value and check the table narrows to it.
    wanted = labels[1]
    items.nth(1).click()
    page.wait_for_timeout(400)
    check("the menu closes on a pick", page.locator(".colmenu").count() == 0)
    shown = page.locator("#jobs tbody tr td:nth-child(9)")
    values = {shown.nth(i).inner_text().strip() for i in range(shown.count())}
    check("every visible row has the picked status", values <= {wanted},
          f"picked {wanted!r}, saw {values}")
    check("the header says what it is filtering to",
          wanted in page.locator('#jobs .colfilter[data-filter="status"]').inner_text())
    # The status filter is still on while the source menu is open. Each menu
    # counts the set the OTHER filters have already left, so a count is the
    # number of rows clicking it actually produces - not a promise of 150
    # that the click then breaks.
    kept = total_shown()
    page.locator('#jobs .colfilter[data-filter="source"]').click()
    page.wait_for_timeout(300)
    check("the source menu counts what the status filter left, not the run",
          option_count(0) == kept, f"All = {option_count(0)}, filter kept {kept}")
    promised = option_count(1)
    source_name = option_name(1)
    items.nth(1).click()
    page.wait_for_timeout(400)
    delivered = total_shown()
    check("and a count is what the click delivers",
          delivered == promised, f"{source_name}: promised {promised}, got {delivered}")
    check("both filters are shown as on",
          page.locator("#jobs .colfilter.on").count() == 2)
    # Both filters on, and a menu open over them: the state worth looking at.
    page.locator('#jobs .colfilter[data-filter="status"]').click()
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "filters.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    page.locator('#jobs .colfilter[data-filter="source"]').click()
    page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check("Escape closes the menu", page.locator(".colmenu").count() == 0)
    # Back to All.
    page.locator('#jobs .colfilter[data-filter="status"]').click()
    page.wait_for_timeout(300)
    page.locator(".colmenu .colmenu-item").first.click()
    page.wait_for_timeout(400)
    check("All restores every row", rows.count() == rows_before,
          f"{rows.count()} vs {rows_before}")

    # Starting a session must bring the Apply card into view: it sits below
    # the table and both toolbars, so a session begun from a row down the page
    # started off-screen. Called directly - starting a real queue here would
    # open a real application against real data.
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(200)
    page.evaluate("showApplyCard()")
    page.wait_for_timeout(900)
    check("starting a session scrolls the Apply card into view",
          page.evaluate("""() => {
            const r = document.getElementById('applycard').getBoundingClientRect();
            return r.top >= -4 && r.top < window.innerHeight * 0.5;
          }"""))

    page.screenshot(path=str(SHOTS / "jobs.png"), full_page=True)
    page.set_viewport_size({"width": 420, "height": 900})
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / "jobs-narrow.png"), full_page=True)

    print("\n== console ==")
    real = [e for e in errors if "favicon" not in e.lower()]
    check("no JavaScript errors", not real, "; ".join(real[:3]))
    browser.close()

server.should_exit = True
time.sleep(0.5)
print(f"\nscreenshots: {SHOTS}")
print("FAILURES:" if problems else "\nUI CHECK PASSED")
for p in problems:
    print("  -", p)
sys.exit(1 if problems else 0)
