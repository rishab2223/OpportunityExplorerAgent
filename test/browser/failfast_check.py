"""An action aimed at a control that cannot take it must find out fast.

From the logs of 28 real sessions: on a UKG form mid-rebuild, To month, To
year and Description each failed after 15 s, one after another, and a
Description box the page had re-drawn took 20 s - the first attempt's 10 s,
then four click fallbacks at 3 s each, all looking for a node that no longer
existed. Delete buttons under an overlay took 15 s each to click, because
the real click waited out its whole timeout before the fallbacks ran.

Every case here uses the DEFAULT timeouts, so the times printed are the ones
a real session would see. What must NOT change is checked too: a control on
its way still gets clicked normally, a live overlay is still refused, and a
zero-size native radio still gets ticked by the bottom rung.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

FAST = 3.0          # seconds: comfortably above ACTION_SETTLE_MS, far below 10
failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class Sess:
    def log(self, line):
        pass


def timed(fn):
    start = time.monotonic()
    try:
        return fn(), None, time.monotonic() - start
    except Exception as exc:
        return None, exc, time.monotonic() - start


PAGE = """
<style> body { font-family: sans-serif; } .cover { position: fixed; background: rgba(0,0,0,.05); }
        .native { width: 0; height: 0; margin: 0; padding: 0; border: 0; } </style>
<div id="log"></div>
<p><button id="gone" onclick="log('gone')">Delete Work Experience 2</button></p>
<p><input id="desc" aria-label="Description"></p>
<p><input id="locked" aria-label="Locked" disabled></p>
<p><button id="under" onclick="log('under')">Save</button></p>
<p><button id="soon" onclick="log('soon')">Next</button></p>
<p><label><input class="native" type="radio" name="tier" id="tier1">
   <span role="radio" aria-checked="false">Tier 1</span></label></p>
<script>
  function log(w) { document.getElementById('log').textContent += ' ' + w; }
  function cover(id, ms) {
    const r = document.getElementById(id).getBoundingClientRect();
    const c = document.createElement('div');
    c.className = 'cover'; c.id = 'cover-' + id;
    Object.assign(c.style, {left: r.left - 4 + 'px', top: r.top - 4 + 'px',
                            width: r.width + 8 + 'px', height: r.height + 8 + 'px'});
    document.body.appendChild(c);
    if (ms) setTimeout(() => c.remove(), ms);
  }
</script>
"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1000, "height": 700})
    page.set_content(PAGE)
    sess = Sess()
    log_of = lambda: page.evaluate("() => document.getElementById('log').textContent.trim()")

    print("a control the page re-drew")
    target = page.locator("#gone")
    page.evaluate("() => document.getElementById('gone').remove()")
    _, exc, took = timed(lambda: browser.click(target))
    check("a click on it fails fast", exc is not None and took < FAST, f"{took:.1f}s, {exc!r}"[:90])
    check("  and says the form re-drew it", "re-drew" in str(exc))
    field = {"id": 999, "tag": "input", "type": "text", "label": "Description", "elid": "nope"}
    _, exc, took = timed(lambda: worker._apply_value(page, field, "Built things", "", sess))
    check("a fill into it fails fast", exc is not None and took < FAST, f"{took:.1f}s")

    print("\na disabled box")
    field = {"id": 998, "tag": "input", "type": "text", "label": "Locked", "elid": "locked"}
    _, exc, took = timed(lambda: worker._apply_value(page, field, "x", "", sess))
    check("a fill fails fast and says why",
          exc is not None and took < FAST and "disabled" in str(exc), f"{took:.1f}s, {exc}"[:90])

    print("\na button under something the page left behind")
    page.evaluate("() => cover('under')")
    out, exc, took = timed(lambda: browser.click(page.locator("#under")))
    check("it is clicked through at once, not after 10 s",
          out == "clicked (direct)" and took < FAST, f"{out}, {took:.1f}s")
    check("  and the page received it", "under" in log_of(), log_of())

    print("\nwhat must not change")
    page.evaluate("() => cover('soon', 400)")      # an animation, gone in 0.4 s
    out, exc, took = timed(lambda: browser.click(page.locator("#soon")))
    check("a control whose cover clears is clicked normally", out == "clicked",
          f"{out}, {took:.1f}s, {exc}")
    field = {"id": 997, "tag": "input", "type": "radio", "label": "Tier 1", "elid": "tier1"}
    out, exc, took = timed(lambda: browser.click(page.locator("#tier1")))
    ticked = page.evaluate("() => document.getElementById('tier1').checked")
    check("a zero-size native radio is still ticked by the bottom rung",
          ticked and took < FAST, f"{out}, {took:.1f}s, {exc}")
    _, _, took = timed(lambda: browser.await_usable(page.locator("#desc"), want_clear=True))
    check("a ready control costs almost nothing", took < 0.3, f"{took * 1000:.0f}ms")

    print("\na live overlay is still refused, now without the wait")
    page.set_content("""
      <button id="bg" onclick="document.title='bg'">Add Experience</button>
      <div style="position:fixed; inset:0; background:rgba(0,0,0,.4)"></div>
      <div style="position:fixed; top:40%; left:40%; background:#fff; padding:20px">
        <button id="panel">Save</button></div>""")
    _, exc, took = timed(lambda: browser.click(page.locator("#bg")))
    check("the background control is refused", isinstance(exc, browser.BlockedByOverlay),
          type(exc).__name__)
    check("  in well under the old 10 s", took < FAST, f"{took:.1f}s")
    check("  and never fired", page.title() != "bg")

    b.close()
TMP.cleanup()

print()
if failures:
    print("FAIL-FAST CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("FAIL-FAST CHECK PASSED")
