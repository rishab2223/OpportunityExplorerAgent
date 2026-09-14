"""The table pager: Prev, the first three pages, a box to type a page into,
the last two, Next. Driven in a real browser against the real app.js."""
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

WEB = ROOT / "src" / "web"
FAILED = []


def serve(directory: Path) -> str:
    import functools
    from http.server import HTTPServer, SimpleHTTPRequestHandler

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}: {got!r}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}: got {got!r}, wanted {want!r}")


LAYOUT = """
([page, pages]) => {
  if (document.activeElement) document.activeElement.blur();
  const el = document.getElementById('jobspager');
  window.__went = [];
  renderPager(el, page, pages, pages * PAGE_SIZE, 'job(s)', (p) => window.__went.push(p));
  return [...el.childNodes].map(n =>
    n.tagName === 'INPUT' ? '[' + n.value + ']' : (n.textContent || '').trim());
}
"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 900})
    page.on("pageerror", lambda e: None if "File not found" in str(e)
        else FAILED.append(f"page error: {str(e)[:120]}"))
    page.goto(serve(WEB) + "/static/index.html")
    page.wait_for_timeout(300)

    print("a long shortlist: first three, a box, last two")
    row = page.evaluate(LAYOUT, [6, 30])
    check("layout", row[:-1],
          ["‹ Prev", "1", "2", "3", "…", "[7]", "…", "29", "30", "Next ›"])
    check("the count is still shown", row[-1].split(" - ")[0], "Page 7 of 30")

    print("\nthe box holds the current page and jumps on Enter")
    page.evaluate(LAYOUT, [6, 30])
    box = page.locator("#jobspager input.pagebox")
    check("box shows the current page", box.input_value(), "7")
    box.fill("22")
    box.press("Enter")
    check("jumped to page 22", page.evaluate("window.__went")[-1:], [21])

    print("\nout-of-range typing is clamped, never an empty table")
    page.evaluate(LAYOUT, [6, 30])
    box = page.locator("#jobspager input.pagebox")
    box.fill("999")
    box.press("Enter")
    check("clamped to the last page", page.evaluate("window.__went")[-1:], [29])
    page.evaluate(LAYOUT, [6, 30])
    box = page.locator("#jobspager input.pagebox")
    box.fill("0")
    box.press("Enter")
    check("clamped to the first page", page.evaluate("window.__went")[-1:], [0])
    # A number input refuses letters outright, so the only bad input left is
    # an empty box: Enter on it must not navigate anywhere.
    page.evaluate(LAYOUT, [6, 30])
    box = page.locator("#jobspager input.pagebox")
    box.fill("")
    box.press("Enter")
    check("an empty box stays put", page.evaluate("window.__went"), [])
    check("and is put back to the current page", box.input_value(), "7")

    print("\nfew pages: every number, no box")
    row = page.evaluate(LAYOUT, [1, 5])
    check("layout", row[:-1], ["‹ Prev", "1", "2", "3", "4", "5", "Next ›"])
    check("no jump box", page.locator("#jobspager input.pagebox").count(), 0)

    print("\nthe ends disable their arrow")
    page.evaluate(LAYOUT, [0, 30])
    check("Prev off on page 1",
          page.locator("#jobspager button", has_text="Prev").is_disabled(), True)
    page.evaluate(LAYOUT, [29, 30])
    check("Next off on the last page",
          page.locator("#jobspager button", has_text="Next").is_disabled(), True)

    print("\nthe current page is marked when it is one of the numbered ones")
    page.evaluate(LAYOUT, [1, 30])
    check("page 2 is current",
          page.locator("#jobspager button.current").inner_text(), "2")
    check("the box is not also marked",
          page.locator("#jobspager input.pagebox.current").count(), 0)

    b.close()

print()
if FAILED:
    print("PAGER CHECK FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("PAGER CHECK PASSED")
