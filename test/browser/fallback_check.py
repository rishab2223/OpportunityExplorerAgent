"""The page-wide row scan (dead locator) and the id fallback of locate()
against the real Workday dump with the How Did You Hear popup open."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

DOM = ROOT / "outputs" / "dom"
d = next(p for p in DOM.iterdir() if p.name.endswith("225211"))
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.goto((d / "page.html").as_uri())
    page.wait_for_timeout(300)
    n = page.evaluate(f"(id) => ({worker.ROWS_JS})(null, id)", "")
    texts = page.locator("[data-oea-row='1']").all_inner_texts()
    print("page-wide rows:", n, texts)
    assert n == 6 and "Campus Campaign" in texts, "page-wide scan failed"
    fields = browser.snapshot(page)
    f = next(x for x in fields if x["label"] == "How Did You Hear About Us?*")
    loc = browser.locate(page, f["id"], f["elid"])
    print("locate with marker:", loc.count(), loc.get_attribute("id"))
    # simulate Workday replacing the node: the marker is gone, the id stays
    page.evaluate("() => document.getElementById('source--source').removeAttribute('data-oea-id')")
    dead = browser.locate(page, f["id"])
    alive = browser.locate(page, f["id"], f["elid"])
    print("after re-render - marker only:", dead.count(), "| with id fallback:", alive.count(), alive.get_attribute("id"))
    assert dead.count() == 0 and alive.count() == 1
    options, texts = worker._visible_options(page, dead)
    print("_visible_options on the dead locator:", len(texts), texts[:3])
    assert "Campus Campaign" in texts
    b.close()
print("FALLBACK CHECK PASSED")
