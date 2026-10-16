"""The Applied page (#40, #42), in a real browser.

A scratch history DB with three applications and an outputs folder of its
own, served by its own uvicorn on a spare port: the real server, runs and
history are never touched. Drafting and the message chat are answered by the
check (page.route), so no model is called.
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

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_applied_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import history, tracker  # noqa: E402
from src.web import runs  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
runs.OUTPUT_DIR = SCRATCH / "outputs"
STAMP = "20261010T100000"
(runs.OUTPUT_DIR / STAMP).mkdir(parents=True)
(runs.OUTPUT_DIR / STAMP / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")


def job(job_id, company, source="linkedin", relevance=84):
    return {"job_id": job_id, "company": company, "title": "Backend Engineer", "source": source,
            "location": "Bengaluru", "apply_url": f"https://example.test/{job_id}", "relevance": relevance,
            "score_scale": 100, "description": "Build Node.js services on AWS."}


JOBS = [job("a", "Northwind"), job("b", "Contoso", "indeed", 91), job("c", "Litware", "linkedin", 7)]
JOBS[2]["score_scale"] = 10
(runs.OUTPUT_DIR / STAMP / "shortlisted.json").write_text(json.dumps(JOBS), encoding="utf-8")
for j in JOBS:
    history.record(j, "applied", stamp=STAMP)
tracker.set_stage("b", "interview")

# An older run with two applications - one quiet for 15 days, so a follow-up
# is due - and one application recorded with no run at all.
OLD = "20260901T090000"
(runs.OUTPUT_DIR / OLD).mkdir(parents=True)
(runs.OUTPUT_DIR / OLD / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
OLD_JOBS = [job("d", "Fabrikam"), job("e", "Tailspin")]
(runs.OUTPUT_DIR / OLD / "shortlisted.json").write_text(json.dumps(OLD_JOBS), encoding="utf-8")
for j in OLD_JOBS:
    history.record(j, "applied", stamp=OLD)
history.record(job("f", "Proseware"), "applied", stamp="")
from datetime import datetime, timedelta, timezone  # noqa: E402

from src import db  # noqa: E402

_conn = db.connect()
with _conn:
    _conn.execute("UPDATE job_history SET marked_at = ? WHERE job_id = 'e'",
                  ((datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),))
_conn.close()

PORT = 8781

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
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + str(detail)) if detail else ''}")
    if not ok:
        problems.append(label)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("dialog", lambda dialog: dialog.dismiss())
    asked: list[dict] = []

    def fake_model(route):
        body = json.loads(route.request.post_data or "{}")
        asked.append({"url": route.request.url, **body})
        if route.request.url.endswith("/draft"):
            history_reply = {"draft": "Hi [Name], I applied for Backend Engineer at Northwind.", "reply": "Wrote it."}
        else:
            history_reply = {"draft": "Short note.", "reply": "Made it shorter."}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(history_reply))

    page.route("**/messages/*/draft", fake_model)
    page.route("**/messages/*/chat", fake_model)
    warmed: list[str] = []
    # Opening the page starts the message model; here nothing real starts.
    page.route("**/api/applied/warm", lambda route: (warmed.append("yes"), route.fulfill(
        status=200, content_type="application/json", body='{"warming": false}')))
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

    print("== the tab ==")
    page.wait_for_function("document.querySelector('#nav-applied').textContent === 'Applied (6)'")
    check("the tab counts every application, from every run", page.text_content("#nav-applied") == "Applied (6)")
    page.click("#nav-applied")
    page.wait_for_selector("#aplboard .aplcol")

    print("== one run at a time ==")
    options = page.eval_on_selector_all("#aplrun option", "o => o.map(x => [x.value, x.textContent])")
    check("the Run menu: all runs, then each run newest first with its count, then no run",
          [v for v, _ in options] == ["", STAMP, OLD, "__none__"]
          and options[0][1] == "All runs (6)" and options[1][1].endswith("· 3 applications")
          and options[2][1].endswith("· 2 applications") and options[3][1] == "No run · 1 application"
          and "T" not in options[1][1].split("·")[0], options)
    check("it opens on the newest run", page.input_value("#aplrun") == STAMP)
    check("  showing only that run's applications",
          sorted(page.eval_on_selector_all("#aplboard .aplcard", "c => c.map(x => x.dataset.jobId)")) == ["a", "b", "c"])
    check("  and the counts follow it", page.text_content("#aplinfo") == "3 application(s)", page.text_content("#aplinfo"))
    page.select_option("#aplrun", OLD)
    check("another run shows its own", sorted(page.eval_on_selector_all(
        "#aplboard .aplcard", "c => c.map(x => x.dataset.jobId)")) == ["d", "e"])
    check("  with its follow-up due counted", "1 follow-up(s) due" in (page.text_content("#aplinfo") or ""),
          page.text_content("#aplinfo"))
    page.check("#apldue")
    check("'Follow-up due only' leaves the one waiting on you",
          page.eval_on_selector_all("#aplboard .aplcard", "c => c.map(x => x.dataset.jobId)") == ["e"])
    page.select_option("#aplrun", STAMP)
    check("  and says so plainly when a run has none", page.locator("#aplboardempty").is_visible()
          and "No follow-ups due here" in page.text_content("#aplboardempty"))
    page.uncheck("#apldue")
    page.select_option("#aplrun", "__none__")
    check("an application with no run is still reachable",
          page.eval_on_selector_all("#aplboard .aplcard", "c => c.map(x => x.dataset.jobId)") == ["f"])
    page.select_option("#aplrun", "")
    check("All runs shows all six", page.locator("#aplboard .aplcard").count() == 6)
    page.reload(wait_until="networkidle")
    page.click("#nav-applied")
    page.wait_for_selector("#aplboard .aplcol")
    check("the choice is remembered", page.input_value("#aplrun") == "")
    page.select_option("#aplrun", STAMP)
    check("no stray 'null' in the menu", "null" not in (page.text_content("#aplrun") or ""))
    # Two page loads by now (the run choice was checked across a reload).
    check("opening the page starts the message model, once per page load", warmed == ["yes", "yes"], warmed)
    counts = page.eval_on_selector_all("#aplstages .aplstage", "s => s.map(x => x.querySelector('.aplcount').textContent)")
    check("the stage bar counts each stage", counts[:3] == ["2", "—", "1"], counts)
    columns = page.eval_on_selector_all("#aplboard .aplcol h3 span:first-child", "s => s.map(x => x.textContent)")
    check("columns: the six stages and Rejected", columns == ["Applied", "Screening", "Interview", "Assessment",
                                                              "Offer", "Accepted", "Rejected"], columns)
    if os.environ.get("SHOT"):
        page.screenshot(path=os.environ["SHOT"], full_page=False)
    chips = page.eval_on_selector_all("#aplboard [data-job-id=c] .aplchip.score", "s => s.map(x => x.textContent)")
    check("an old run's score reads out of 10", chips == ["7/10"], chips)

    print("== table ==")
    page.click("#apllayouttable")
    page.wait_for_selector("#apltable tbody tr")
    check("the table lists the chosen run's three", page.locator("#apltable tbody tr").count() == 3)
    page.select_option("#aplrun", OLD)
    check("  and follows the Run menu", sorted(page.eval_on_selector_all(
        "#apltable tbody tr", "r => r.map(x => x.dataset.jobId)")) == ["d", "e"])
    page.check("#apldue")
    check("  and the follow-up filter", page.eval_on_selector_all(
        "#apltable tbody tr", "r => r.map(x => x.dataset.jobId)") == ["e"])
    page.uncheck("#apldue")
    page.select_option("#aplrun", STAMP)
    page.fill("#aplsearch", "contoso")
    check("search narrows it", page.locator("#apltable tbody tr").count() == 1)
    page.fill("#aplsearch", "")
    page.select_option("#apltable tr[data-job-id=a] select", "screening")
    page.wait_for_function("document.querySelector('#aplstages .aplstage:nth-child(2) .aplcount').textContent === '1'")
    check("changing a stage in the table is saved", tracker.detail("a")["stage"] == "screening")
    page.reload(wait_until="networkidle")
    page.click("#nav-applied")
    page.wait_for_selector("#apltable tbody tr")
    check("the layout is remembered", not page.locator("#apltableview").is_hidden()
          and page.locator("#aplboardview").is_hidden())

    print("== the side panel ==")
    page.click("#apltable tr[data-job-id=a] td:first-child")
    page.wait_for_selector("#apldrawer:not([hidden]) h2")
    check("it opens on the application", page.text_content("#apldrawer h2") == "Northwind")
    check("  with no stray 'null' in it", "null" not in (page.text_content("#apldrawer") or ""))
    timeline = page.text_content("#apldrawer .apltl") or ""
    check("the timeline shows the stage change", "Applied via linkedin" in timeline and "Moved to Screening" in timeline,
          timeline)
    tabs = page.eval_on_selector_all("#apldrawer .apltabs button", "b => b.map(x => x.textContent)")
    check("message kinds: referral, outreach, follow-up, thank you", tabs == ["Referral", "Outreach", "Follow-up",
                                                                               "Thank you"], tabs)
    page.fill("#apldrawer [data-contact=name]", "Priya S.")
    page.fill("#apldrawer [data-contact=relation]", "we both worked at Acme")
    with page.expect_response("**/contact"):
        page.click("#apldrawer button:text-is('Save contact')")
    page.wait_for_function("document.querySelector('#apldrawer [data-contact=name]').value === 'Priya S.'")
    check("the contact is saved", tracker.detail("a")["contact"]["relation"] == "we both worked at Acme")

    page.click("#apldrawer button:text-is('Draft it')")
    page.wait_for_function("document.querySelector('#apldrawer .apldraft').value.includes('Northwind')")
    check("Draft it fills the message", "Northwind" in page.input_value("#apldrawer .apldraft"))
    page.fill("#apldrawer .aplchatin input", "make it shorter")
    page.press("#apldrawer .aplchatin input", "Enter")
    page.wait_for_function("document.querySelector('#apldrawer .apldraft').value === 'Short note.'")
    bubbles = page.eval_on_selector_all("#apldrawer .aplchat .b", "b => b.map(x => x.textContent)")
    check("the chat edits the draft and answers", bubbles[-2:] == ["make it shorter", "Made it shorter."], bubbles)
    check("  sending the draft it is editing", asked and asked[-1].get("draft", "").startswith("Hi [Name]"),
          asked[-1] if asked else "")

    if os.environ.get("SHOT"):          # a picture to look at, on request
        page.screenshot(path=os.environ["SHOT"].replace(".png", "_panel.png"), full_page=False)
    page.click("#apldrawer button:text-is('Mark sent')")
    page.wait_for_function("(document.querySelector('#apldrawer .aplsent') || {}).textContent?.startsWith('Sent')")
    sent = tracker.detail("a")
    check("Mark sent is recorded with a follow-up date",
          sent["messages"]["outreach"]["sent_at"] and sent["follow_up_at"], sent["follow_up_at"])
    check("  and the tab says so", "Outreach ✓" in (page.text_content("#apldrawer .apltabs") or ""))
    page.keyboard.press("Escape")
    check("Escape closes the panel", page.locator("#apldrawer").is_hidden())
    # A reload of the panel still on its way must not open it again.
    page.click("#apltable tr[data-job-id=b] td:first-child")
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    check("  and it stays closed when a late answer arrives", page.locator("#apldrawer").is_hidden())
    check("no script errors", not errors, errors)
    browser.close()

server.should_exit = True
print()
if problems:
    print("APPLIED UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("APPLIED UI CHECK PASSED")
