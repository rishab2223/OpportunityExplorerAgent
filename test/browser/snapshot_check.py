"""The snapshot on the real Workday dumps: chip-style prompts report their
pills as the value, and the pills never leak into a neighbouring box."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser  # noqa: E402

DOM = ROOT / "outputs" / "dom"
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name in ("225225", "225450"):
        d = next(p for p in DOM.iterdir() if p.name.endswith(name))
        page = b.new_page()
        page.goto((d / "page.html").as_uri())
        page.wait_for_timeout(300)
        fields = browser.snapshot(page)
        print("=" * 10, name)
        for f in fields:
            if f["tag"] in ("input", "textarea") and f.get("type") not in ("checkbox", "radio", "file"):
                print(f"  {f['label'][:45]!r:48} value={f['value'][:60]!r}")
        by_label = {f["label"]: f for f in fields if f["tag"] in ("input", "textarea")}
        if name == "225225":
            assert by_label["Country Phone Code*"]["value"] == "India (+91)", by_label["Country Phone Code*"]
            assert by_label["How Did You Hear About Us?*"]["value"] == "LinkedIn corporate page"
            assert by_label["Phone Extension"]["value"] == "", "pills leaked into a neighbour"
        else:
            assert by_label["Type to Add Skills"]["value"] == "JavaScript", by_label["Type to Add Skills"]
        page.close()
    b.close()
print("SNAPSHOT CHECK PASSED")
