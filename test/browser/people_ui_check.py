"""The People page and the Settings page's spending card (#51), in a real
browser.

A scratch history DB and outputs folder, served by its own uvicorn on a
spare port. Apify is the unit tests' fake client, the posting reader is a
stand-in (no Chrome profile is opened), and drafts are answered by the check
(page.route): nothing is spent, no model is called, and the real server,
runs, history and Apify account are never touched.
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
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_people_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import history, people  # noqa: E402
from src.web import app as web_app  # noqa: E402
from src.web import outreach, runs  # noqa: E402
from test.test_people import ENGINEERS, MANAGERS, RECRUITERS, FakeClient  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
runs.OUTPUT_DIR = SCRATCH / "outputs"


def job(job_id, company, location="Bengaluru, Karnataka, India", relevance=84, source="linkedin"):
    url = (f"https://in.linkedin.com/jobs/view/{job_id}" if source == "linkedin"
           else f"https://in.indeed.com/viewjob?jk={job_id}")
    return {"job_id": job_id, "company": company, "title": f"Backend Engineer {job_id}", "source": source,
            "location": location, "listing_url": url, "apply_url": url, "relevance": relevance, "score_scale": 100,
            "posted_at": "2026-10-15", "description": "Join our Accounts Payable team. Node.js on AWS."}


def write_run(stamp, rows):
    directory = runs.OUTPUT_DIR / stamp
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (directory / "shortlisted.json").write_text(json.dumps(rows), encoding="utf-8")


NEW, OLD = "20261017T090000", "20261015T090000"
NEW_JOBS = [job("dl1", "Deltek, Inc.", relevance=90), job("dl2", "Deltek", relevance=70),
            job("pv", "Planview", "Hyderabad, Telangana, India", relevance=85),
            job("in", "Contoso", relevance=60, source="indeed"), job("ap", "Northwind"), job("rf", "Fabrikam")]
write_run(NEW, NEW_JOBS)
write_run(OLD, [job("o1", "Litware"), job("o2", "Tailspin")])
history.record(NEW_JOBS[4], "applied", stamp=NEW)
history.record(NEW_JOBS[5], "referral_pending", stamp=NEW, contact="Ben")
history.record({"job_id": "o2", "company": "Tailspin", "title": "Backend Engineer o2"}, "applied", stamp=OLD)

# Apify: the fake. Planview's search finds no engineers in Hyderabad.
FAKE = FakeClient(results={"Engineering Manager": MANAGERS, "Talent Acquisition": RECRUITERS,
                           "engineers": ENGINEERS}, used=0.69)
people._client = lambda token: FAKE
people._own_profile = lambda: "my-own-profile"
web_app.load_env = lambda: SimpleNamespace(apify_token="fake-token")
outreach.warm = lambda: False
posters_read: list[str] = []


def fake_read_poster(found_job, launch=None):
    posters_read.append(found_job["job_id"])
    if not people.linkedin_posting(found_job):
        return people._save_poster(found_job["job_id"], "not_linkedin", [])
    if found_job["job_id"] == "pv":
        return {"result": "busy", "people": [], "read_at": ""}
    return people._save_poster(found_job["job_id"], "found", [
        {"name": "Rita Sharma", "headline": "Engineering Manager at Deltek | Hiring",
         "profile_url": "https://www.linkedin.com/in/rita-sharma"}])


people.read_poster = fake_read_poster

PORT = 8783
import uvicorn  # noqa: E402

server = uvicorn.Server(uvicorn.Config(web_app.app, host="127.0.0.1", port=PORT, log_level="error"))
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


def card(page, job_id):
    return f"#ppljobs [data-job-id='{job_id}']"


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("dialog", lambda dialog: dialog.accept())
    drafted: list[dict] = []

    def fake_draft(route):
        body = json.loads(route.request.post_data or "{}")
        drafted.append(body)
        who = body["person"]["name"].split()[0]
        text = (f"Hi {who}, I'm applying for the Backend Engineer role at Deltek. Would you refer me?"
                if body["which"] == "note" else f"Hi {who},\n\nThanks for connecting. A longer message.")
        if body.get("follow_up"):
            text = f"Hi {who}, following up on my note."
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": text, "reply": "Wrote it."}))

    page.route("**/api/people/runs/*/jobs/*/draft", fake_draft)
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")

    print("== the run picker and the tabs ==")
    check("a People tab", page.locator("#nav-people").is_visible() and page.text_content("#nav-people") == "People")
    page.click("#nav-people")
    page.wait_for_selector("#ppljobs .ppljob")
    options = page.eval_on_selector_all("#pplrun option", "o => o.map(x => [x.value, x.textContent])")
    check("each run, newest first, with its three counts",
          [v for v, _ in options] == [NEW, OLD]
          and options[0][1].endswith("· not applied 4 · applied 1 · referral 1")
          and options[1][1].endswith("· not applied 1 · applied 1 · referral 0"), options)
    tabs = page.eval_on_selector_all("[data-ppltab]", "b => b.map(x => x.textContent)")
    check("the tabs count the run's jobs", tabs == ["Not applied4", "Applied1", "Referral1"], tabs)
    order = page.eval_on_selector_all("#ppljobs .ppljob", "c => c.map(x => x.dataset.jobId)")
    check("not applied, best score first", order == ["dl1", "pv", "dl2", "in"], order)
    find = page.text_content(f"{card(page, 'dl1')} .pplunsearched button") or ""
    check("an unsearched job offers Find people with its cost", find == "Find people · ~6¢", find)
    check("  and says whom it looks for", "Who posted it, 3 managers, 3 recruiters and 4 engineers at Deltek, Inc. in Bengaluru"
          in (page.text_content(f"{card(page, 'dl1')} .pplunsearched") or ""))
    check("an Indeed job does not promise who posted it",
          "Who posted it" not in (page.text_content(f"{card(page, 'in')} .pplunsearched") or ""))

    print("== Find people ==")
    page.click(f"{card(page, 'dl1')} .pplunsearched button")
    page.wait_for_selector(f"{card(page, 'dl1')} .pplgroups")
    names = page.eval_on_selector_all(f"{card(page, 'dl1')} .pplperson .ppln", "n => n.map(x => x.textContent)")
    check("who posted it, managers, recruiters, engineers",
          names == ["Rita Sharma", "Asha Rao", "Ben Iyer", "Dee Shah", "Ed Paul", "Fay K"], names)
    check("three Apify searches, once", len(FAKE.calls) == 3, len(FAKE.calls))
    flags = page.eval_on_selector_all(f"{card(page, 'dl1')} .pplflag", "n => n.map(x => x.textContent)")
    check("people who may have left are marked", len(flags) == 2 and any("May have left" in f for f in flags), flags)
    check("the posting's team is a hint", "Accounts Payable" in (page.text_content(f"{card(page, 'dl1')} .pplhint") or ""))
    foot = page.text_content(f"{card(page, 'dl1')} .pplfoot") or ""
    check("the foot says when, what it cost and how long it is reused",
          "for $0.06" in foot and "reused for Deltek, Inc. jobs until" in foot and "Search again" in foot, foot)
    check("another Deltek job in Bengaluru shows the same people, at no cost",
          page.locator(f"{card(page, 'dl2')} .pplperson").count() >= 5 and len(FAKE.calls) == 3)
    if os.environ.get("SHOT"):          # a picture to look at, on request
        page.screenshot(path=os.environ["SHOT"], full_page=True)
    links = page.eval_on_selector_all(f"{card(page, 'dl1')} .pplacts a", "a => a.map(x => x.href)")
    check("each person links to their LinkedIn", all(h.startswith("https://www.linkedin.com/in/") for h in links), links)

    page.click(f"{card(page, 'pv')} .pplunsearched button")
    page.wait_for_selector(f"{card(page, 'pv')} .pplgroups")
    poster = page.text_content(f"{card(page, 'pv')} .pplgroup:first-child") or ""
    check("a posting that could not be read says why, with Try again",
          "while an apply window is open" in poster and "Try again" in poster, poster)
    check("  and nobody is listed as the poster", page.locator(f"{card(page, 'pv')} .pplgroup:first-child .pplperson").count() == 0)
    page.click(f"{card(page, 'in')} .pplunsearched button")
    page.wait_for_selector(f"{card(page, 'in')} .pplgroups")
    check("an Indeed posting: only LinkedIn shows who posted",
          "Only LinkedIn postings" in (page.text_content(f"{card(page, 'in')} .pplgroup:first-child") or ""))

    print("== a note ==")
    page.click(f"{card(page, 'dl1')} button[aria-label='Ask for referral: Fay K']")
    page.wait_for_function("document.querySelector('#ppldrawer textarea')?.value.startsWith('Hi Fay')")
    if os.environ.get("SHOT"):
        page.screenshot(path=os.environ["SHOT"].replace(".png", "_drawer.png"))
    check("the drawer opens on a referral request", page.text_content("#ppldh") == "Referral request")
    check("  the note is drafted on first look", drafted and drafted[-1]["which"] == "note"
          and drafted[-1]["person"]["kind"] == "engineers", drafted[-1:] or "")
    count = page.text_content("#ppldrawer .pplcount") or ""
    check("  with its characters against LinkedIn's 300", count.endswith("/ 300"), count)
    page.fill("#ppldrawer textarea", "x" * 310)
    check("  over 300 is flagged", "over" in (page.get_attribute("#ppldrawer .pplcount", "class") or ""))
    page.fill("#ppldrawer textarea", "Hi Fay, my own words.")
    page.locator("#ppldrawer textarea").blur()
    page.wait_for_timeout(300)
    saved = people.messages_for(["dl1"]).get("dl1", {}).get("fay", {})
    check("an edit is kept", saved.get("note") == "Hi Fay, my own words.", saved)
    page.click("#ppldrawer .aplseg button:text-is('Message after they accept')")
    page.wait_for_function("document.querySelector('#ppldrawer textarea')?.value.includes('longer message')")
    check("the message after they accept is drafted when opened", drafted[-1]["which"] == "message")
    page.click("#ppldrawer .aplseg button:text-is('Connection note')")
    check("  and the edited note is still there", page.input_value("#ppldrawer textarea") == "Hi Fay, my own words.")
    profile = page.get_attribute("#ppldrawer a.btnlike", "href")
    check("Open their LinkedIn", profile == "https://www.linkedin.com/in/fay", profile)

    page.click("#ppldrawer button:text-is('Mark sent')")
    page.wait_for_selector("#pplnotice:not([hidden])")
    notice = page.text_content("#pplnotice") or ""
    check("Mark sent says what happened", "Marked sent to Fay K." in notice and "moved to the Referral tab" in notice, notice)
    check("  the job is now a referral", (history.lookup("dl1") or {}).get("status") == "referral_pending")
    page.wait_for_function("document.querySelector('#ppln-referral').textContent === '2'")
    check("  the tabs and the run picker follow", page.text_content("#pplrun option:first-child").endswith("referral 2"))
    page.click("#pplnotice button:text-is('Undo')")
    page.wait_for_function("document.querySelector('#ppln-referral').textContent === '1'")
    check("Undo puts it back", history.lookup("dl1") is None)

    page.click(f"{card(page, 'dl1')} button[aria-label='Note to recruiter: Dee Shah']")
    page.wait_for_function("document.querySelector('#ppldrawer textarea')?.value.startsWith('Hi Dee')")
    page.click("#ppldrawer button:text-is('Mark sent')")
    page.click("[data-ppltab=referral]")
    page.wait_for_selector(f"{card(page, 'dl1')} .pplsent")
    check("a sent note shows on the person", "Note sent" in (page.text_content(f"{card(page, 'dl1')} .pplsent") or ""))
    check("  and their button says sent", page.locator(f"{card(page, 'dl1')} button[aria-label='Sent ✓: Dee Shah']").count() == 1)
    page.click(f"{card(page, 'dl1')} button[aria-label='Sent ✓: Dee Shah']")
    page.wait_for_selector("#ppldrawer .pplsentline")
    check("reopened, it says when it went, with Undo", "Not sent? Undo" in (page.text_content("#ppldrawer .pplsentline") or ""))
    check("  and it is not redrafted", drafted[-1]["person"]["name"] == "Dee Shah" and drafted[-1]["which"] == "note"
          and len([d for d in drafted if d["person"]["name"] == "Dee Shah"]) == 1)
    page.click("#ppldrawer button:text-is('Write a follow-up')")
    page.wait_for_function("document.querySelector('#ppldrawer textarea')?.value.includes('following up')")
    check("Write a follow-up drafts one", drafted[-1].get("follow_up") is True)
    page.keyboard.press("Escape")
    check("Escape closes the drawer", page.locator("#ppldrawer").is_hidden())

    print("== spending settings ==")
    page.click("#userbtn")
    check("Settings is in the user menu", page.locator("#usermenu [data-open-view=settings]").is_visible())
    page.click("#usermenu [data-open-view=settings]")
    page.wait_for_function("document.querySelector('#setbudgettext').textContent.startsWith('People search')")
    text = page.text_content("#setbudgettext") or ""
    check("the meter line: people search, the account, the next search",
          "People search this month: $0.18 of $3.00" in text and "Apify account: $0." in text and "of $5.00" in text
          and "next search about 6¢" in text and "resets 23 Oct" in text, text)
    check("the limits are filled in", (page.input_value("#setsoft"), page.input_value("#setpause")) == ("3.00", "4.50"))
    page.fill("#setsoft", "5")
    page.click("#setsave")
    page.wait_for_function("document.querySelector('#setstate').textContent.startsWith('Not saved:')")
    check("a soft limit above the pause is refused", "pause" in (page.text_content("#setstate") or ""),
          page.text_content("#setstate"))
    page.fill("#setsoft", "0.10")
    page.click("#setsave")
    page.wait_for_function("document.querySelector('#setstate').textContent === 'Saved.'")
    check("over the soft limit: the alert", "Over this month's $0.10" in (page.text_content("#setalert") or ""))
    if os.environ.get("SHOT"):
        page.screenshot(path=os.environ["SHOT"].replace(".png", "_settings.png"))
    page.click("#nav-people")
    page.wait_for_selector("#pplalert:not([hidden])")
    check("the People page says searches are smaller",
          "2 managers, 2 recruiters and 1 engineer" in (page.text_content("#pplalert") or ""),
          page.text_content("#pplalert"))
    page.click("[data-ppltab=not_applied]")
    page.wait_for_selector(f"{card(page, 'dl2')} .pplfoot button")
    check("  and a search costs less", page.text_content(f"{card(page, 'dl2')} .pplfoot button") == "Search again · ~4¢",
          page.text_content(f"{card(page, 'dl2')} .pplfoot button"))
    page.click("#pplalert button:text-is('Spending settings')")
    page.wait_for_selector("#view-settings:not([hidden])")
    check("the alert links to the settings", page.locator("#view-settings").is_visible())
    page.fill("#setpause", "0.80")
    page.click("#setsave")
    page.wait_for_function("document.querySelector('#setstate').textContent === 'Saved.'")
    check("near the pause line: paused", "paused" in (page.text_content("#setbudgettext") or ""),
          page.text_content("#setbudgettext"))
    page.click("#nav-people")
    page.wait_for_selector("#pplalert:not([hidden])")
    check("the People page says it is paused, and until when",
          "paused until 23 Oct" in (page.text_content("#pplalert") or ""), page.text_content("#pplalert"))
    page.select_option("#pplrun", OLD)
    page.wait_for_selector(f"{card(page, 'o1')}")
    unsearched = page.text_content(f"{card(page, 'o1')} .pplunsearched") or ""
    check("paused: no Find people, but the posting can still be read free",
          "Read the posting's hiring team · free" in unsearched and "Find people" not in unsearched, unsearched)
    calls = len(FAKE.calls)
    page.click(f"{card(page, 'o1')} .pplunsearched button")
    page.wait_for_selector(f"{card(page, 'o1')} .pplgroups .pplperson")
    check("  which reads it and spends nothing", len(FAKE.calls) == calls and "o1" in posters_read)
    page.select_option("#pplrun", NEW)
    page.wait_for_selector(f"{card(page, 'dl2')}")
    again = page.locator(f"{card(page, 'dl2')} .pplfoot button")
    check("paused: Search again is off", again.is_disabled())
    check("the run choice is remembered", page.evaluate("localStorage.getItem('oea.people.run')") == NEW)

    print("== small screens ==")
    page.set_viewport_size({"width": 380, "height": 900})
    for view in ("people", "settings"):
        page.evaluate(f"showView('{view}')")
        page.wait_for_timeout(300)
        wide = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        check(f"{view}: no sideways scroll at phone width", not wide)
    check("no script errors", not errors, errors)
    browser.close()

server.should_exit = True
print()
if problems:
    print("PEOPLE UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("PEOPLE UI CHECK PASSED")
