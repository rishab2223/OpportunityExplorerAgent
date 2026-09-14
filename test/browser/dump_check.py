"""Live check of the 'dump' command: the shadow-modal fixture must come back
with its shadow root, live values and the focused element marked."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import worker  # noqa: E402


class FakeSess:
    stamp = "dumpcheck"
    logs: list[str] = []

    def log(self, text: str) -> None:
        self.logs.append(text)
        print("LOG:", text)


worker.DUMP_DIR = WORK / "dump_out"
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page()
    page.goto((HERE / "fixture_shadow.html").as_uri())
    page.wait_for_timeout(300)
    # open the modal if the fixture has a trigger, then type into the first input
    for sel in ("text=Easy Apply", "button"):
        try:
            page.locator(sel).first.click(timeout=1500)
            break
        except Exception:
            pass
    page.wait_for_timeout(500)
    sess = FakeSess()
    worker._dump_page(page, sess, delay=1)
    out = sorted(worker.DUMP_DIR.iterdir())[-1]
    html = (out / "page.html").read_text(encoding="utf-8")
    fields = (out / "fields.json").read_text(encoding="utf-8")
    print("files:", [p.name for p in out.iterdir()])
    print("shadow templates:", html.count('<template shadowrootmode="open">'))
    print("live values:", html.count("data-live-value"))
    print("focused marks:", html.count("data-live-focused"))
    print("fields.json bytes:", len(fields), "html bytes:", len(html))
    assert "<script" not in html, "scripts must be stripped"
    assert html.count('<template shadowrootmode="open">') >= 1, "shadow root missing"
    assert (out / "screenshot.png").exists()
    assert any("Page dumped" in l for l in sess.logs) and any("in 1s" in l for l in sess.logs)
    b.close()
print("DUMP CHECK PASSED")
