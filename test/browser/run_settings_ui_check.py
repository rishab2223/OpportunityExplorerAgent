"""The run settings popup, in a real browser.

Start run opens it; what it sends is what the run is started with. The run
itself is intercepted, so nothing is scraped and no model is called. A pinned
config stands in for settings.yaml and the preferences file lives in a
scratch folder, so the candidate's own settings are never read or written.
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

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_runsettings_ui_"))
os.environ["JOB_HISTORY_DB"] = str(SCRATCH / "history.db")

from src import history  # noqa: E402
from src.config import AppConfig  # noqa: E402
from src.web import run_options, runs  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
runs.OUTPUT_DIR = SCRATCH / "outputs"
run_options.PREFERENCES_PATH = SCRATCH / "run_preferences.json"
_CFG = AppConfig.model_validate({
    "min_score": 7, "enrich_provider": "claude",
    "claude": {"enrich_model": "claude-opus-5", "effort": "medium"},
    "openai": {"score_model": "gpt-5.6-luna", "score_concurrency": 16, "enrich_concurrency": 8},
    "experience": {"enabled": True, "mode": "review", "review_min_score": 9, "tolerance_years": 1},
    "resume": {"local_path": "localData/resume.tex"},
    "scrape": {"max_detail_jobs": 150, "sources": ["indeed", "linkedin"], "posted_within": "3d"},
})
run_options.load_yaml_config = lambda: _CFG

LAST = runs.OUTPUT_DIR / "20261006T103501"
LAST.mkdir(parents=True)
(LAST / "run.json").write_text(json.dumps({
    "status": "ok", "raw_job_count": 266, "scored_count": 266, "match_count": 117,
    "timings": {"scrape": 178, "score": 170, "enrich": 2776, "pdf": 578}}), encoding="utf-8")

PORT = 8774

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


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1360, "height": 1000})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    sent: list[dict] = []

    def fake_run(route):
        if route.request.method != "POST":
            route.continue_()
            return
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=409, content_type="application/json",
                      body=json.dumps({"detail": "intercepted by the check"}))

    page.route("**/api/runs", fake_run)
    page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    modal = page.locator("#runmodal")

    print("== Start run opens the settings ==")
    page.click("#start")
    page.wait_for_selector("#runmodal", state="visible")
    check("the popup opens instead of a run", not sent and modal.is_visible())
    names = page.eval_on_selector_all("[data-source-card] b", "b => b.map(x => x.textContent)")
    check("every job source is offered", names == ["LinkedIn", "Indeed"], str(names))
    check("each source shows its limits", page.locator("[data-source-card=linkedin] .rs-hint").text_content() == "1–500 jobs")
    check("caps come from settings.yaml",
          page.input_value("input[type=number][data-cap=linkedin]") == "150")
    estimate = page.text_content("#rsestimate") or ""
    check("the total is counted", "Up to 300 jobs" in estimate, estimate)
    check("the last run's time is shown", "117 tailored" in estimate and "tailoring 46 min" in estimate, estimate)
    check("the posted-within choice shows 3 days",
          page.get_attribute(".rs-seg[data-field='scrape.posted_within'] [aria-checked=true]", "data-value") == "3d")
    if os.environ.get("OEA_SCREENSHOT"):   # a look at the design, on request
        page.screenshot(path=os.environ["OEA_SCREENSHOT"], full_page=False)
        page.eval_on_selector(".rs-body", "b => b.scrollTop = b.scrollHeight")
        page.screenshot(path=os.environ["OEA_SCREENSHOT"].replace(".png", "-2.png"), full_page=False)
        page.eval_on_selector(".rs-body", "b => b.scrollTop = 0")

    print("== the controls ==")
    page.fill("input[type=number][data-cap=linkedin]", "40")
    check("number and slider move together",
          page.input_value("input[type=range][data-cap=linkedin]") == "40")
    check("the total follows", "Up to 190 jobs" in (page.text_content("#rsestimate") or ""))
    page.click("[data-source-card=indeed] .switch")
    check("a source turned off greys out", "off" in (page.get_attribute("[data-source-card=indeed]", "class") or "")
          and page.is_disabled("input[type=number][data-cap=indeed]"))
    check("and stops counting", "Up to 40 jobs" in (page.text_content("#rsestimate") or ""))
    page.click("[data-source-card=linkedin] .switch")
    check("no source at all cannot start", page.is_disabled("#rsstart")
          and "at least one job source" in (page.text_content("#rserror") or ""))
    page.click("[data-source-card=linkedin] .switch")
    page.fill("input[type=number][data-cap=linkedin]", "900")
    check("a cap over the limit is refused", page.is_disabled("#rsstart")
          and "1–500" in (page.text_content("#rserror") or ""), page.text_content("#rserror") or "")
    page.fill("input[type=number][data-cap=linkedin]", "40")
    check("and starting is allowed again", not page.is_disabled("#rsstart"))
    # A bad value in a box that is switched off is neither a block here nor a
    # refusal on the server: the switched-off source keeps its loaded cap.
    page.click("[data-source-card=indeed] .switch")
    page.fill("input[type=number][data-cap=indeed]", "9000")
    page.click("[data-source-card=indeed] .switch")
    check("a switched-off source's box does not block the run", not page.is_disabled("#rsstart"),
          page.text_content("#rserror") or "")
    page.click("[data-source-card=indeed] .switch")
    page.fill("input[type=number][data-cap=indeed]", "150")

    page.fill("#rsminscore", "8")
    page.dispatch_event("#rsminscore", "input")
    check("the score pill follows the slider", page.text_content("output.rs-out") == "8")
    check("OpenAI model is hidden while Claude tailors",
          not page.locator("select[data-field='openai.enrich_model']").is_visible()
          and page.locator("select[data-field='claude.enrich_model']").is_visible())
    page.click(".rs-seg[data-field=enrich_provider] [data-value=openai]")
    check("picking OpenAI swaps the model list and hides effort",
          page.locator("select[data-field='openai.enrich_model']").is_visible()
          and not page.locator(".rs-seg[data-field='claude.effort']").is_visible())
    page.click(".rs-seg[data-field=enrich_provider] [data-value=claude]")
    page.click(".rs-seg[data-field='claude.effort'] [data-value=low]")
    page.select_option("select[data-field='claude.enrich_model']", "claude-sonnet-5")
    page.click(".rs-seg[data-field='experience.mode'] [data-value=drop]")
    check("drop mode hides the held-back settings",
          not page.locator("input[data-field='experience.review_max']").is_visible())
    page.fill("input[data-field='scrape.exclude_keywords']", "PHP,  Salesforce ,")

    print("== Esc closes, Reset restores ==")
    page.keyboard.press("Escape")
    check("Esc closes without starting", not modal.is_visible() and not sent)
    page.click("#start")
    page.wait_for_selector("#runmodal", state="visible")
    check("reopening starts from the defaults again",
          page.input_value("input[type=number][data-cap=linkedin]") == "150")
    page.fill("input[type=number][data-cap=linkedin]", "25")
    page.click("[data-rs=reset]")
    check("Reset puts settings.yaml back", page.input_value("input[type=number][data-cap=linkedin]") == "150")

    print("== a refusal keeps the popup open ==")
    refuse = {"on": True}

    def maybe_refuse(route):
        if route.request.method == "POST" and refuse["on"]:
            route.fulfill(status=400, content_type="application/json",
                          body=json.dumps({"detail": "openai.enrich_concurrency: must be 16 or less"}))
            return
        fake_run(route)

    page.unroute("**/api/runs")
    page.route("**/api/runs", maybe_refuse)
    page.click("#rsstart")
    page.wait_for_selector("#rserror:not([hidden])")
    check("the server's reason is shown in the popup", modal.is_visible()
          and "enrich_concurrency" in (page.text_content("#rserror") or ""))
    check("with the form still filled in", page.input_value("input[type=number][data-cap=linkedin]") == "150")
    refuse["on"] = False

    print("== Start run sends the choices ==")
    page.fill("input[type=number][data-cap=linkedin]", "60")
    page.click("[data-source-card=indeed] .switch")
    page.click(".rs-seg[data-field=interview_prep] [data-value=run]")
    page.select_option("select[data-field='claude.enrich_model']", "claude-sonnet-5")
    page.fill("input[data-field='scrape.exclude_keywords']", "PHP,  Salesforce ,")
    page.check("#rsremember")
    page.click("#rsstart")
    page.wait_for_selector("#rserror:not([hidden])")
    # The fake server refuses every run (409), so the popup stays up with
    # the reason; a run the server takes closes it (startRun returns a stamp).
    check("a refused run leaves the popup open with the reason", modal.is_visible()
          and "intercepted" in (page.text_content("#rserror") or ""))
    page.keyboard.press("Escape")
    body = sent[-1] if sent else {}
    settings = body.get("settings") or {}
    check("one run was asked for", len(sent) == 1, str(len(sent)))
    check("a switched-off source keeps its loaded cap", settings["scrape"]["max_jobs"].get("indeed") == 150,
          json.dumps(settings.get("scrape", {}).get("max_jobs")))
    check("sources and caps", settings.get("scrape", {}).get("sources") == ["linkedin"]
          and settings["scrape"]["max_jobs"].get("linkedin") == 60, json.dumps(settings.get("scrape")))
    check("the model choice", settings.get("claude", {}).get("enrich_model") == "claude-sonnet-5")
    check("interview prep during the run", settings.get("interview_prep") == "run")
    check("the keyword list is cleaned up",
          settings.get("scrape", {}).get("exclude_keywords") == ["PHP", "Salesforce"])
    check("remember is passed on", body.get("remember") is True)
    try:
        run_options.apply(_CFG, settings)
        accepted = True
    except run_options.BadOptions as exc:
        accepted = False
        print("   ", exc)
    check("what the popup sends validates on the server", accepted)
    kept = run_options.save_preferences(settings, _CFG)
    check("only what differs from settings.yaml is kept as a default",
          set(kept) <= {"scrape", "claude", "interview_prep"} and "min_score" not in kept, json.dumps(kept))

    print("== saved defaults are said, and can be forgotten ==")
    page.click("#start")
    page.wait_for_selector("#runmodal", state="visible")
    check("the notice shows", page.locator("#rssaved").is_visible())
    check("and the saved value is in the form", page.input_value("input[type=number][data-cap=linkedin]") == "60")
    page.click("[data-rs=forget]")
    page.wait_for_selector("#rssaved", state="hidden")
    check("Forget them puts settings.yaml back", page.input_value("input[type=number][data-cap=linkedin]") == "150"
          and not run_options.PREFERENCES_PATH.exists())
    page.keyboard.press("Escape")

    check("no script errors", not errors, "; ".join(errors)[:200])
    browser.close()

server.should_exit = True
print()
if problems:
    print("RUN SETTINGS UI CHECK FAILED:", ", ".join(problems))
    sys.exit(1)
print("RUN SETTINGS UI CHECK PASSED")
