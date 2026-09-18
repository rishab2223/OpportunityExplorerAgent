"""What a page says beyond its form: why it is empty, what it asked its
servers for, and which system it runs on.

The Sep 17 2026 job page that drew only its shell was explained three ways
from its DOM, and wrong twice: "the form is behind its Apply button" (there
was none), then "the site is still showing its loading spinner" (the
loader's markup is always on the page; it was idle). The unit tests mocked
the spinner probe to say yes, so they proved the message and never the
probe. This runs the real probes against a replica of that page, and the
real network record against a request the check refuses.

Served from a made-up origin through Playwright routing: nothing leaves the
machine and no real site is contacted.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

ORIGIN = "https://careers.example.test"
FIXTURE = (HERE / "fixture_stale_signin.html").read_text(encoding="utf-8")
failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class Sess:
    stamp = "signals"

    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)


job_status = {"code": 401}


def serve(route):
    url = route.request.url
    if "/api/getJobDesc" in url:
        if job_status["code"] == 200:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"title": "Software Engineer",
                                           "text": "Build and run services. " * 40}))
        else:
            route.fulfill(status=job_status["code"], content_type="application/json",
                          body='{"error": "invalid_token"}')
    elif url.startswith("https://cdn.phenompeople.com/"):
        route.fulfill(status=200, body="")
    elif url.startswith(ORIGIN) or url.startswith("https://jobs.employer.example"):
        route.fulfill(status=200, content_type="text/html", body=FIXTURE
                      if url.startswith(ORIGIN) else
                      '<html><head><link rel="stylesheet" href="https://cdn.phenompeople.com/a.css">'
                      '</head><body><img src="https://cdn.phenompeople.com/logo.png">'
                      '<form><label>Name <input></label></form></body></html>')
    else:
        route.abort()             # nothing reaches the real network


def settle_on_job_request(page):
    for _ in range(40):
        if any("/api/getJobDesc" in c["path"] for c in browser.data_calls(page)):
            return
        page.wait_for_timeout(50)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    ctx.route("**/*", serve)
    browser.watch_data_calls(ctx)       # as browser.launch does, before navigating
    page = ctx.new_page()

    print("the stale sign-in: the job request is refused")
    page.goto(f"{ORIGIN}/jobdesc?jobReferenceCode=REF-1")
    settle_on_job_request(page)
    page.wait_for_timeout(200)
    check("the scan finds no form, as it did", browser.snapshot(page) == [],
          str(len(browser.snapshot(page))))
    check("the idle loader is not a spinner", browser.loading_indicator(page) == "",
          repr(browser.loading_indicator(page)))
    check("there is no Apply button to point at", browser.apply_control(page) == "",
          repr(browser.apply_control(page)))
    refused = browser.refused_data_calls(page)
    check("the refused request is on record",
          refused == ["401 careers.example.test/api/getJobDesc"], str(refused))
    reason = worker._no_fields_reason(page)
    print("   ->", reason)
    check("the reason names it", "401 careers.example.test/api/getJobDesc" in reason)
    check("and makes neither false claim",
          "behind its" not in reason and "spinner" not in reason)

    print("\nthe dump carries the request, and never its query string")
    worker.DUMP_DIR = Path(tempfile.mkdtemp(prefix="oea_signals_"))
    sess = Sess()
    worker._dump_page(page, sess)
    out = sorted(worker.DUMP_DIR.iterdir())[-1]
    calls = json.loads((out / "network.json").read_text(encoding="utf-8"))
    check("network.json has the 401",
          any(c["status"] == 401 and c["path"] == "/api/getJobDesc" for c in calls), str(calls))
    raw = (out / "network.json").read_text(encoding="utf-8")
    check("with no query string", "jobReferenceCode" not in raw and "REF-1" not in raw)
    check("and the log line still names the folder for the Dump button",
          any(line.startswith(f"Page dumped to {out} (page.html") for line in sess.lines),
          sess.lines[-1][:90] if sess.lines else "")

    print("\nthe loader actually turning IS a spinner")
    page.evaluate("() => document.getElementById('overlay').classList.add('loading-foreground')")
    spinner = browser.loading_indicator(page)
    # The overlay or the spinner in it, whichever comes first: both are the
    # loader, and both were in the DOM, unseen, a moment ago.
    check("it is seen", spinner.startswith("ngx-"), repr(spinner))
    check("and wins over the refused request",
          "loading spinner" in worker._no_fields_reason(page))

    print("\na fresh profile: the job arrives and has an Apply button")
    job_status["code"] = 200
    page.goto(f"{ORIGIN}/jobdesc?jobReferenceCode=REF-1")
    page.wait_for_selector("button.apply")
    check("the Apply button is found by what it says", browser.apply_control(page) == "Apply",
          repr(browser.apply_control(page)))
    check("a 200 is not refused", browser.refused_data_calls(page) == [],
          str(browser.refused_data_calls(page)))
    check("the reason points at it",
          "behind its 'Apply' button" in worker._no_fields_reason(page))

    print("\na tracking system on the employer's own domain")
    page.goto("https://jobs.employer.example/apply")
    page.wait_for_timeout(100)
    site = worker._site_of(page)
    check("is named from where it loads its assets", site == "phenom", repr(site))

    b.close()

print()
if failures:
    print("PAGE SIGNALS CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("PAGE SIGNALS CHECK PASSED")
