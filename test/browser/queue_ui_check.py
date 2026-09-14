"""Two dashboard bugs around the queue, in a real browser.

1. Ticks belonged to no run. The set keys on job_id alone, so ticks made on
   one run were still in it after switching to another: the button counted
   jobs that were not on the table, and Start queue would have sent them
   under the NEW run's stamp - the wrong folder for the resume.

2. A reload in the hand-off gap stopped following. Between two queued jobs
   there is no session at all for a few seconds while the browser closes so
   the next can open. resumeActiveApply is the only thing that ran at boot,
   it found nothing to attach to, and the page went quiet while the queue
   carried on opening jobs it never showed.

Its own uvicorn on a spare port with a scratch history DB, so the server on
:8000 is never touched and no real application is read or written.
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
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_queue_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

PORT = 8767

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
        problems.append(label)


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    page.wait_for_timeout(800)

    print("== a tick belongs to the run it was made on ==")
    # The stamps are made up on purpose: the bug IS the stamp comparison, and
    # a name the server cannot resolve exercises it without needing two real
    # runs on the machine the check happens to run on.
    same = page.evaluate("""async () => {
        currentStamp = 'run-a';
        queueTicks.clear();
        queueTicks.add('j1');
        await loadJobs('run-a');            // a refresh of the same run
        return queueTicks.size;
    }""")
    check("a refresh of the same run keeps the ticks", same == 1, str(same))

    moved = page.evaluate("""async () => {
        currentStamp = 'run-a';
        queueTicks.clear();
        queueTicks.add('j1');
        queueTicks.add('j2');
        refreshQueueButton();               // the button really does say (2)
        const before = document.querySelector('[data-act="startqueue"]').textContent;
        await loadJobs('run-b');            // another run
        return {size: queueTicks.size, before: before,
                button: document.querySelector('[data-act="startqueue"]').textContent,
                disabled: document.querySelector('[data-act="startqueue"]').disabled};
    }""")
    check("the button counted them while the run was current",
          moved["before"].strip() == "Start queue (2)", repr(moved["before"]))
    check("switching run drops them", moved["size"] == 0, str(moved["size"]))
    check("and the button stops counting jobs that are not on the table",
          moved["button"].strip() == "Start queue", repr(moved["button"]))
    check("with nothing to start", moved["disabled"] is True)

    print("\n== a reload in the hand-off gap keeps following ==")
    # The queue is live and between jobs: /api/apply/status has no session
    # yet. Stub the two reads rather than opening a real browser session.
    page.evaluate("""() => {
        window.__asked = [];
        getJSON = async (url) => {
            window.__asked.push(url);
            if (url.indexOf('/api/apply/queue') === 0) {
                return {current: {job_id: 'j9', label: 'Initech Engineer'},
                        pending: [], parked: [], done: [], note: '', active: true};
            }
            if (url.indexOf('/api/apply/status') === 0) {
                return {session_id: '', status: 'idle', job_id: ''};
            }
            return {};
        };
    }""")
    # The boot chain is loadQueue THEN resumeQueueFollow, so do both: the
    # state the guard reads is the one that read has just refreshed.
    page.evaluate("async () => { applySessionId = ''; await loadQueue(); }")
    page.evaluate("() => { resumeQueueFollow(); }")   # not awaited: it polls
    page.wait_for_timeout(600)
    log = page.locator("#applylog").inner_text()
    check("it says it is waiting for the next job",
          "waiting for the browser to close" in log,
          repr(log.strip()[-80:]))
    check("and it went back to the server for the state",
          "/api/apply/queue" in page.evaluate("() => window.__asked"))

    print("\n== but not when something is already attached ==")
    page.evaluate("() => { resetApply(''); applySessionId = 'live-session'; }")
    page.evaluate("() => { resumeQueueFollow(); }")
    page.wait_for_timeout(500)
    check("a live session is left alone",
          page.locator("#applylog").inner_text().strip() == "",
          repr(page.locator("#applylog").inner_text().strip()[:80]))

    print("\n== and not when the queue is not running ==")
    page.evaluate("""() => {
        applySessionId = '';
        queueState = {current: null, pending: [], parked: [], done: [],
                      note: '', active: false};
    }""")
    page.evaluate("() => { resumeQueueFollow(); }")
    page.wait_for_timeout(400)
    check("an idle queue says nothing",
          page.locator("#applylog").inner_text().strip() == "",
          repr(page.locator("#applylog").inner_text().strip()[:80]))

    browser.close()

server.should_exit = True

print()
if problems:
    print("QUEUE UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("QUEUE UI CHECK PASSED")
