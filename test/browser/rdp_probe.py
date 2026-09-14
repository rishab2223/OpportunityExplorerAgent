"""The real react-datepicker: what commits a typed date, and does the
calendar fallback work on a host form that ignores typed text (Esko)?"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
FIXTURE = (HERE / "rdp" / "fixture.html").as_uri()
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import worker  # noqa: E402

STOCK = '[id="experienceData[0].fromTo.startDate"]'
STRICT = '[id="strict.startDate"]'


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


def ready(page):
    page.goto(FIXTURE)
    page.wait_for_function("() => document.getElementById('status').textContent === 'ready'", timeout=30000)
    page.wait_for_timeout(200)


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1200, "height": 900})
    page.on("pageerror", lambda e: print("   pageerror:", str(e)[:160]))
    ready(page)

    # 1. what a typed value does on each widget, with the picker then closed
    for name, selector in (("stock", STOCK), ("esko-style", STRICT)):
        box = page.locator(selector)
        box.focus()
        box.press_sequentially("07/2020", delay=30)
        while_typing = box.input_value()
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        print(f"  {name:11} typed -> {while_typing!r}, after the picker closed -> {box.input_value()!r}")

    # 2. the agent's own date path on both
    for name, selector, field in (
        ("stock", STOCK, {"id": 1, "tag": "input", "type": "text", "label": "From*",
                          "elid": "experienceData[0].fromTo.startDate", "value": ""}),
        ("esko-style", STRICT, {"id": 2, "tag": "input", "type": "text", "label": "From*",
                                "elid": "strict.startDate", "value": ""}),
    ):
        ready(page)
        sess = Sess()
        box = page.locator(selector)
        try:
            worker._type_date_box(page, box, "Jul 2020", "From*", "[model] ", sess, field)
        except Exception as exc:
            failures.append(f"{name}: {str(exc).splitlines()[0][:140]}")
            print(f"  {name:11} RAISED {str(exc).splitlines()[0][:120]}")
            continue
        shown = box.input_value()
        print(f"  {name:11} box now {shown!r}; pickers open: {page.locator('.react-datepicker').count()}")
        if shown != "07/2020":
            failures.append(f"{name}: box shows {shown!r}")
        if page.locator(".react-datepicker").count():
            failures.append(f"{name}: the picker was left open")

    # 3. a year far from today, both directions
    for value, want in (("Jun 2019", "06/2019"), ("Jan 2026", "01/2026")):
        ready(page)
        sess = Sess()
        box = page.locator(STRICT)
        field = {"id": 2, "tag": "input", "type": "text", "label": "To*", "elid": "strict.startDate", "value": ""}
        try:
            worker._type_date_box(page, box, value, "To*", "[model] ", sess, field)
        except Exception as exc:
            failures.append(f"{value}: {str(exc).splitlines()[0][:120]}")
            continue
        print(f"  {value:9} -> {box.input_value()!r}")
        if box.input_value() != want:
            failures.append(f"{value}: box shows {box.input_value()!r}, wanted {want}")
    b.close()

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nDATEPICKER CHECK PASSED")
