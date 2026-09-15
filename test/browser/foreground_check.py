"""A click must not punch through an overlay the page put up on purpose.

The USP/UKG session of Sep 15 2026 ended with the site unusable - washed out,
every scroll and click ignored - and the agent had done it. While a Work
Experience entry is being edited, UKG raises that panel and covers the rest of
the page with a fixed, viewport-sized overlay (measured off the dump:
position:fixed, z-index:9, opacity:0.8, 1900x900).

browser.click's fallback dispatches the click on the element itself when an
overlay intercepts pointer events, so every "Clicked ... (direct)" in that
transcript was a control the page was refusing. Add, Save, Delete, Delete -
driven while UKG said not now, until its state came apart and the panel
collapsed to 0x0 with the overlay still up. Nothing left to dismiss it.

The fallback is still wanted: a dentsu Workday page left a STALE overlay
behind that blocked Accept Cookies, the phone code and the skills box alike,
with nothing live underneath. What tells the two apart is whether anything on
the page is still reachable - a live overlay foregrounds a panel you can use,
a stale one covers the lot.

Scratch profile, temp DB. Nothing real is read or written.
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402

TMP = tempfile.TemporaryDirectory()
failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def log_of(page):
    return page.locator("#log").inner_text().strip()


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto((HERE / "fixture_foreground.html").as_uri())
    page.wait_for_timeout(200)

    print("the page really is in the state that caused it")
    shape = page.evaluate("""() => {
      const o = document.getElementById('live-overlay');
      const s = getComputedStyle(o), r = o.getBoundingClientRect();
      return {pos: s.position, z: s.zIndex, w: Math.round(r.width), h: Math.round(r.height)};
    }""")
    check("a fixed overlay covers the viewport",
          shape["pos"] == "fixed" and shape["w"] >= 1280 and shape["h"] >= 900, str(shape))
    covered = page.evaluate("""() => {
      const el = document.getElementById('bg-add');
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return top !== el && !el.contains(top);
    }""")
    check("and the background controls are behind it", covered is True)

    print("\nthe panel the page raised is still usable")
    # The overlay is not a wall, it is a funnel: refusing every click would be
    # as wrong as punching through, because the one panel that matters works.
    out = browser.click(page.locator("#save"), timeout=3000, fallback_timeout=1000)
    check("Save inside the raised panel clicks normally", out == "clicked", repr(out))
    check("and the page saw it", log_of(page) == "save", repr(log_of(page)))
    page.fill("#job-title", "Software Engineer")
    check("its input takes a value too",
          page.input_value("#job-title") == "Software Engineer")

    print("\na control behind a LIVE overlay is refused, not forced")
    for what in ("#bg-add", "#bg-delete"):
        before = log_of(page)
        try:
            out = browser.click(page.locator(what), timeout=1500, fallback_timeout=800)
            check(f"{what} was refused", False, f"returned {out!r}")
        except Exception as exc:
            check(f"{what} was refused", True, type(exc).__name__)
            check(f"  and says why, in words the candidate can act on",
                  "overlay" in str(exc).lower(), str(exc)[:90])
        check(f"  and {what} never fired", log_of(page) == before,
              f"{before!r} -> {log_of(page)!r}")

    print("\na STALE overlay is still punched through")
    # dentsu: the overlay is a leftover, nothing on the page is reachable, and
    # refusing here would strand every form that ends up in this state.
    page.evaluate("() => goStale()")
    page.wait_for_timeout(100)
    reachable = page.evaluate("""() => {
      const el = document.getElementById('bg-add');
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return top === el || el.contains(top);
    }""")
    check("nothing is reachable any more", reachable is False)
    before = log_of(page)
    out = browser.click(page.locator("#bg-add"), timeout=1500, fallback_timeout=800)
    check("the click goes through directly", out == "clicked (direct)", repr(out))
    check("and the page received it", log_of(page) == (before + " add").strip(),
          repr(log_of(page)))

    print("\nand a page nobody can use is recognised as one")
    # The state USP reached twice: overlay up, the panel it raised collapsed
    # to nothing, not one control hittable anywhere. Forcing clicks through
    # THIS only takes it further from something the candidate can rescue, so
    # what matters is that it is named - the answer is a reload.
    check("the dead page reports which overlay has it",
          browser.page_blocked(page) == "bring-to-foreground-overlay",
          repr(browser.page_blocked(page)))

    b.close()

    print("\nand an ordinary page is not")
    # The cost of a false positive is stopping a working session dead, so
    # this must stay quiet on a page that is merely busy or modal.
    plain = b2 = None
    b2 = pw.chromium.launch()
    plain = b2.new_page(viewport={"width": 1280, "height": 900})
    plain.goto((HERE / "fixture_form.html").as_uri())
    plain.wait_for_timeout(200)
    check("a normal form is not blocked", browser.page_blocked(plain) == "",
          repr(browser.page_blocked(plain)))
    plain.goto((HERE / "fixture_foreground.html").as_uri())
    plain.wait_for_timeout(200)
    # A LIVE overlay is not a dead page: the panel it raised is still usable,
    # and stopping the session there would be wrong.
    check("a live overlay with a usable panel is not blocked either",
          browser.page_blocked(plain) == "", repr(browser.page_blocked(plain)))
    b2.close()
TMP.cleanup()

print()
if failures:
    print("FOREGROUND CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("FOREGROUND CHECK PASSED")
