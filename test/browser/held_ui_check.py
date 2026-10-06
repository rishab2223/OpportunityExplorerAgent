"""The Held back tab, in a real browser.

A made-up run in a temporary outputs folder, a scratch history DB, its own
uvicorn on a spare port: the server on :8000, the real runs and the real
history are never touched. Start apply is intercepted, so no browser session
is ever opened.
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

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_held_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import history  # noqa: E402
from src.apply import profile  # noqa: E402
from src.config import AppConfig  # noqa: E402
from src.web import held_insight, runs  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
runs.OUTPUT_DIR = SCRATCH / "outputs"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
profile.PROFILE_PATH.write_text(json.dumps({"total_experience_years": "6"}), encoding="utf-8")
# The suggestion reads settings.yaml; pinned here so the check does not
# depend on how the candidate has theirs set.
_CFG = AppConfig.model_validate({"experience": {
    "enabled": True, "mode": "review", "review_min_score": 9, "tolerance_years": 1}})
held_insight.load_yaml_config = lambda: _CFG

# An earlier run where three held-back jobs were kept: enough for a suggestion.
EARLIER = runs.OUTPUT_DIR / "20261001T100000"
EARLIER.mkdir(parents=True)
(EARLIER / "run.json").write_text(json.dumps({"status": "ok", "match_count": 2}), encoding="utf-8")
# ...and one it held that is still waiting, two days later.
(EARLIER / "held_back.json").write_text(json.dumps([
    {"source": "indeed", "job_id": f"old{i}", "company": f"Old {i}", "title": "Engineer",
     "relevance": 9, "held_back": "asks for 8+ years; your limit is 7"} for i in range(3)] + [
    {"source": "indeed", "job_id": "monday", "company": "Monday Co", "title": "Engineer monday",
     "relevance": 9, "held_back": "asks for 8+ years; your limit is 7"}]),
    encoding="utf-8")
(EARLIER / "applications.json").write_text(json.dumps(
    {f"old{i}": {"decision": "yes"} for i in range(3)}), encoding="utf-8")
STAMP = "20261002T100000"
RUN = runs.OUTPUT_DIR / STAMP
RUN.mkdir(parents=True)


def job(job_id, relevance, held=""):
    return {"source": "indeed", "job_id": job_id, "title": f"Engineer {job_id}",
            "company": f"Co {job_id}", "location": "Remote", "relevance": relevance,
            "why_score": "Strong backend match.", "apply_url": "https://example.test/apply",
            "held_back": held}


(RUN / "run.json").write_text(json.dumps({"status": "ok", "raw_job_count": 5, "match_count": 1,
                                           "held_back_count": 3}), encoding="utf-8")
(RUN / "shortlisted.json").write_text(json.dumps([job("short", 8)]), encoding="utf-8")
(RUN / "held_back.json").write_text(json.dumps([
    job("move", 10, "asks for 8+ years; your limit is 7"),
    job("skip", 9, "asks for 9+ years; your limit is 7"),
    job("apply", 9, "asks for 8+ years; your limit is 7"),
]), encoding="utf-8")

PORT = 8773

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


def ids(page, table):
    return page.eval_on_selector_all(f"#{table} tbody tr[data-job-id]",
                                     "rows => rows.map(r => r.dataset.jobId)")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("dialog", lambda dialog: dialog.dismiss())
    started: list[str] = []

    def fake_start(route):
        started.append(route.request.post_data or "")
        route.fulfill(status=409, content_type="application/json",
                      body=json.dumps({"detail": "intercepted by the check"}))

    page.route("**/api/apply/start", fake_start)
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    page.wait_for_selector("#jobs tbody tr[data-job-id]")

    print("== readable runs ==")
    options = page.eval_on_selector_all("#stamp option", "o => o.map(x => x.textContent)")
    check("the dropdown names runs by date, with their size",
          len(options) == 2 and "Oct" in options[0] and options[0].endswith("· 1 job")
          and "T100000" not in options[0], str(options))

    print("== the two lists ==")
    check("the shortlist has only the shortlisted job", ids(page, "jobs") == ["short"], str(ids(page, "jobs")))
    check("the tab counts what waits in every run", page.text_content("#nav-held") == "Held back (4)",
          page.text_content("#nav-held"))
    check("the run line says so", "3 held back" in (page.text_content("#runinfo") or ""))
    page.click("#nav-held")
    check("the held view shows", page.locator("#view-held").is_visible())
    check("held jobs from both runs are listed, strongest first",
          ids(page, "heldback") == ["move", "skip", "apply", "monday"], str(ids(page, "heldback")))
    found = page.text_content("#heldback tr[data-job-id=monday] td.heldfound") or ""
    check("Found names the earlier run by date", "Oct" in found and "T1" not in found, found)
    check("the info line counts runs", "from 2 runs" in (page.text_content("#heldinfo") or ""))
    reason = page.text_content("#heldback tr[data-job-id=move] td.heldreason") or ""
    check("the reason is shown", "8+ years" in reason, reason)
    page.wait_for_selector("#heldinsight:not([hidden])")
    insight = page.text_content("#heldinsight") or ""
    check("keeping held-back jobs earns a suggestion", "tolerance_years: 2" in insight, insight[:120])
    buttons = page.eval_on_selector_all("#heldback tr[data-job-id=move] button", "b => b.map(x => x.textContent)")
    check("the row has the actions", buttons[:3] == ["Start apply", "Move to shortlist", "Skip"], str(buttons))

    print("== acting on a row takes it off the list ==")
    page.click("#heldback tr[data-job-id=move] button:text-is(\"Move to shortlist\")")
    page.wait_for_function("document.querySelector('#nav-held').textContent === 'Held back (3)'")
    check("Move to shortlist puts it in the Jobs table", "move" in ids(page, "jobs"), str(ids(page, "jobs")))
    tag = page.locator("#jobs tr[data-job-id=move] .heldtag")
    check("it still says it was held back", tag.count() == 1)
    check("there is no jump to Jobs", page.locator("#view-held").is_visible())

    page.click("#heldback tr[data-job-id=skip] button:text-is(\"Skip\")")
    page.wait_for_function("document.querySelector('#nav-held').textContent === 'Held back (2)'")
    check("Skip takes it off the list", "skip" not in ids(page, "heldback"))
    check("a skip is recorded in history", (history.lookup("skip") or {}).get("status") == "skipped")

    print("== a job from an earlier run ==")
    page.click("#heldback tr[data-job-id=monday] button:text-is(\"Move to shortlist\")")
    # The first move's message is still up; wait for this one's.
    page.wait_for_function("(document.querySelector('#heldmoved').textContent || '').includes('Monday Co')")
    moved = page.text_content("#heldmoved") or ""
    check("the message names the run it went to", "Monday Co" in moved and found in moved, moved)
    check("still no jump", page.locator("#view-held").is_visible())
    decisions = json.loads((EARLIER / "applications.json").read_text(encoding="utf-8"))
    check("it went to the earlier run's shortlist", decisions.get("monday", {}).get("decision") == "yes")
    check("and not the run on screen", "monday" not in json.loads(
        (RUN / "applications.json").read_text(encoding="utf-8")))
    page.click("#heldmoved button:text-is(\"Show it\")")
    page.wait_for_selector("#jobs tbody tr[data-job-id=monday]")
    check("Show it opens Jobs on that run", page.locator("#view-jobs").is_visible()
          and page.input_value("#stamp") == "20261001T100000")
    check("with the row picked out", "selected" in (
        page.get_attribute("#jobs tbody tr[data-job-id=monday]", "class") or ""))
    page.click("#nav-held")

    print("== applying from Held back ==")
    page.click("#heldback tr[data-job-id=apply] button:text-is(\"Start apply\")")
    page.wait_for_timeout(500)
    check("Start apply reaches the server for this job, in its own run",
          any('"apply"' in body and STAMP in body for body in started), str(started))
    check("the Jobs view switches to that run", page.input_value("#stamp") == STAMP)
    check("the Jobs view, with the Apply card, comes forward", page.locator("#view-jobs").is_visible()
          and page.locator("#applycard").is_visible())

    check("no script errors", not errors, "; ".join(errors)[:200])
    browser.close()

server.should_exit = True
print()
if problems:
    print("HELD UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("HELD UI CHECK PASSED")
