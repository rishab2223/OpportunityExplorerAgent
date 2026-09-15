"""Wait for a form that rebuilds itself from the resume you just gave it.

UKG/UltiPro reads the uploaded resume and fills Work Experience and Education
from the parse, re-rendering both sections. On the USP application the agent
uploaded at 55.7s and clicked "Add Experience" at 56.3s - six tenths of a
second later, into a section the site had not finished building. The panel
that opened never completed: its overlay stayed up, the panel collapsed to
0x0, and the page was unusable for the candidate as much as for the agent.

browser.wait_quiet waits for the shape to stop moving. It is deliberately not
settle(), which waits for the page to CHANGE from a known shape and is right
after an action whose effect we expect: here no change may happen at all, and
a site that does nothing with the resume must not pay the whole cap for it.

Scratch only. Nothing real is read or written.
"""
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402

TMP = tempfile.TemporaryDirectory()
RESUME = Path(TMP.name) / "a_candidate_resume.pdf"
RESUME.write_bytes(b"%PDF-1.4\n% scratch\n")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def log_of(page):
    return page.locator("#log").inner_text().strip()


def titles(page):
    return page.eval_on_selector_all(".title", "els => els.map(e => e.value)")


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})

    print("without waiting, the click lands mid-parse and is thrown away")
    page.goto((HERE / "fixture_parse_rebuild.html").as_uri())
    page.wait_for_timeout(150)
    page.set_input_files("#resume", str(RESUME))
    page.wait_for_timeout(600)          # the 0.6s the real session waited
    page.click("#add")
    check("the click was accepted while the parse was running",
          "add-during-parse" in log_of(page), repr(log_of(page)))
    page.wait_for_timeout(1500)
    # This is the damage: the entry the agent believes it created is gone,
    # so it adds another, and another - "entry 2 of 2" on every run.
    check("and the rebuild threw that entry away",
          "" not in titles(page) and len(titles(page)) == 2, str(titles(page)))

    print("\nsettling first waits the rebuild out")
    page.goto((HERE / "fixture_parse_rebuild.html").as_uri())
    page.wait_for_timeout(150)
    page.set_input_files("#resume", str(RESUME))
    started = time.time()
    went_quiet = browser.wait_quiet(page, timeout=12000)
    waited = time.time() - started
    check("it reports the page went quiet", went_quiet is True)
    check("the parse had finished by then", "parsed" in log_of(page), repr(log_of(page)))
    check("and it waited for it, not past it", 1.4 <= waited <= 6.0, f"{waited:.1f}s")
    page.click("#add")
    check("the click now lands after the rebuild",
          "add-during-parse" not in log_of(page), repr(log_of(page)))
    check("and the entry survives",
          len(titles(page)) == 3 and titles(page)[-1] == "", str(titles(page)))

    print("\na form that is not rebuilding costs almost nothing")
    # A cap that behaved like a sleep would put this on every upload on every
    # site, which is how a 110s suite becomes a 300s one.
    page.goto((HERE / "fixture_form.html").as_uri())
    page.wait_for_timeout(200)
    started = time.time()
    quiet = browser.wait_quiet(page, timeout=12000)
    waited = time.time() - started
    check("a still page settles at once", quiet is True)
    check("and pays only the quiet window", waited <= 2.0, f"{waited:.1f}s")

    print("\nand a page that never stops moving is not waited on forever")
    page.set_content(
        "<div id='x'></div><script>setInterval(() => {"
        " document.getElementById('x').innerHTML = '<input value=\"' +"
        " Math.random() + '\">'; }, 100);</script>")
    page.wait_for_timeout(200)
    started = time.time()
    quiet = browser.wait_quiet(page, timeout=2000)
    waited = time.time() - started
    check("it gives up at the cap", quiet is False)
    check("and the cap is honoured", waited <= 4.0, f"{waited:.1f}s")

    b.close()
TMP.cleanup()

print()
if failures:
    print("SETTLE CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("SETTLE CHECK PASSED")
