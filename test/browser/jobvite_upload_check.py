"""The Jobvite upload, end to end, in both the shapes Barracuda's page took.

The buttons front NO file input, so `_sole_hidden_file_input` finds nothing
and the candidate was told to click by hand. Clicking for them lands one of
two ways, and the dump of Sep 14 2026 showed it is the second one that
Jobvite actually does:

1. the click opens a file picker, which Playwright intercepts;
2. the click opens a source MENU - a role=dialog of Dropbox / File / Type or
   Paste Resume - and builds the real input inside it. No picker ever opens,
   and the menu then hides the whole form from the next snapshot: the agent
   saw one field, asked the model, got nothing, and gave up on a form it had
   not filled a single box of.

Also proves the slot is never guessed wrong: the inputs are anonymous, both
attachments are prepared, and the letter button must still get the letter.
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
from playwright.sync_api import sync_playwright  # noqa: E402

from src.apply import browser, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
RESUME = Path(TMP.name) / "Barracuda_Software_Engineer_II.pdf"
LETTER = Path(TMP.name) / "Barracuda_cover_letter.pdf"
RESUME.write_bytes(b"%PDF-1.4 resume\n")
LETTER.write_bytes(b"%PDF-1.4 letter\n")

FIXTURE = WORK / "fixture_jobvite.html"
FIXTURE.write_text("""<!doctype html><html><body>
<h2>Apply to this job</h2>
<div class="block"><h3>Resume*</h3>
  <button id="rbtn" type="button">Select</button>
  <span id="rname"></span></div>
<div class="block"><h3>Cover Letter</h3>
  <button id="lbtn" type="button">Add Cover Letter</button>
  <span id="lname"></span></div>
<script>
  // Jobvite builds the input on the click and throws it away again, so the
  // page never holds a file input for anyone to set files on directly.
  function picker(out) {
    const input = document.createElement('input');
    input.type = 'file';                      // no name, id, class or label
    input.style.display = 'none';
    document.body.appendChild(input);
    input.addEventListener('change', () => {
      out.textContent = input.files[0] ? input.files[0].name : '';
      input.remove();
    });
    input.click();
  }
  rbtn.onclick = () => picker(document.getElementById('rname'));
  lbtn.onclick = () => picker(document.getElementById('lname'));
</script></body></html>""", encoding="utf-8")


# What the dumps actually showed. "Select" opens a source menu - a role=dialog
# of Dropbox / File / Type or Paste Resume - and never a picker. There is one
# such menu PER upload button, each holding its own file input, and all of
# them are in the DOM from the start: Barracuda's form carried two before a
# single click. So the input is neither the page's only one nor a new one,
# and what marks it is the menu the click opened.
MENU_FIXTURE = WORK / "fixture_jobvite_menu.html"


def menu(slot: str, out: str) -> str:
    return f"""
<div id="dropdown-{slot}" role="dialog" aria-label="Attachment Options"
     style="display:none">
  <div><span role="button">Dropbox</span></div>
  <div><label for="file-input-{slot}"><span>File</span></label>
       <input id="file-input-{slot}" type="file"
              style="position:absolute;width:1px;height:1px;clip:rect(0,0,0,0)"></div>
  <div><span role="button">Type or Paste Resume</span></div>
</div>
<script>
  document.getElementById('file-input-{slot}').addEventListener('change', function () {{
    document.getElementById('{out}').textContent =
        this.files[0] ? this.files[0].name : '';
  }});
</script>"""


MENU_FIXTURE.write_text(f"""<!doctype html><html><body>
<h2>Apply to this job</h2>
<label for="fn">First Name*</label><input id="fn" type="text">
<label for="ln">Last Name*</label><input id="ln" type="text">
<div class="block"><h3>Resume*</h3>
  <button id="rbtn" type="button">Select</button>
  <span id="rname"></span></div>
{menu('0', 'rname')}
<div class="block"><h3>Additional Files (optional)</h3>
  <button id="lbtn" type="button">Add Cover Letter</button>
  <span id="lname"></span></div>
{menu('1', 'lname')}
<script>
  // Angular's ng-show: the menus are built up front and only shown on click.
  function open_(id) {{
    document.getElementById(id).style.display = 'block';
  }}
  rbtn.onclick = () => open_('dropdown-0');
  lbtn.onclick = () => open_('dropdown-1');
  document.addEventListener('keyup', e => {{
    if (e.key === 'Escape') {{
      document.querySelectorAll('[role=dialog]').forEach(
          el => el.style.display = 'none');
    }}
  }});
</script></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


class Attach:
    """Both files already vetted by the candidate, as they are by the time a
    tile is reached."""
    def __init__(self):
        self.resume_path = str(RESUME)
        self.letter_pdf = str(LETTER)
        self.resume_attached = False
        self.letter_attached = False

    def resume(self):
        return self.resume_path

    def cover_letter(self, for_upload=False):
        return self.letter_pdf


failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)

    check("the page fronts no file input at all",
          worker._sole_hidden_file_input(page) is None)

    sess, attach, handled, notes = Sess(), Attach(), set(), []
    print("\n== the resume tile ==")
    fields = browser.snapshot(page)
    tiles = [f for f in fields if worker._upload_tile_kind(f, browser.page_text(page, 1500))]
    check("both tiles are recognised", len(tiles) == 2, str([f.get("text") for f in tiles]))

    did = worker._handle_attachments(page, fields, handled, attach, sess, notes)
    check("it acted", did)
    check("the resume went in without the candidate touching anything",
          page.locator("#rname").inner_text() == RESUME.name,
          repr(page.locator("#rname").inner_text()))
    check("and it is recorded as attached", attach.resume_attached)
    check("nothing asked the candidate to click",
          not any("click" in s.lower() and "yourself" in s.lower() for s in sess.logs))

    print("\n== the cover-letter tile ==")
    fields = browser.snapshot(page)
    did = worker._handle_attachments(page, fields, handled, attach, sess, notes)
    check("it acted", did)
    check("the LETTER went into the letter slot, not the resume",
          page.locator("#lname").inner_text() == LETTER.name,
          repr(page.locator("#lname").inner_text()))
    check("the resume slot was not touched again",
          page.locator("#rname").inner_text() == RESUME.name)
    check("and it is recorded as attached", attach.letter_attached)

    print("\n== the menu Jobvite really opens ==")
    page2 = b.new_page(viewport={"width": 1280, "height": 900})
    page2.goto(MENU_FIXTURE.as_uri())
    page2.wait_for_timeout(200)
    sess2, attach2, handled2, notes2 = Sess(), Attach(), set(), []
    check("two file boxes are already there, so neither is 'the only one'",
          page2.locator("input[type=file]").count() == 2)
    check("and neither is new when the menu opens",
          worker._sole_hidden_file_input(page2) is None)
    fields = browser.snapshot(page2)
    check("the form is readable before the click",
          any(f.get("label") == "First Name*" for f in fields))

    did = worker._handle_attachments(page2, fields, handled2, attach2, sess2, notes2)
    check("it acted", did)
    check("the resume went into the box the RESUME menu holds",
          page2.locator("#rname").inner_text() == RESUME.name,
          repr(page2.locator("#rname").inner_text()))
    check("and nothing landed in the other slot",
          page2.locator("#lname").inner_text() == "")
    check("it is recorded as attached", attach2.resume_attached)
    check("the menu was closed again",
          page2.locator("#dropdown-0").is_hidden())
    check("so the rest of the form is still there for the next read",
          any(f.get("label") == "First Name*" for f in browser.snapshot(page2)))

    fields = browser.snapshot(page2)
    worker._handle_attachments(page2, fields, handled2, attach2, sess2, notes2)
    check("the letter button opens ITS menu and gets the letter",
          page2.locator("#lname").inner_text() == LETTER.name,
          repr(page2.locator("#lname").inner_text()))
    check("and the resume slot still holds the resume",
          page2.locator("#rname").inner_text() == RESUME.name)

    print("\n== a picker the CANDIDATE opens still gets read from the page ==")
    # The override must not leak: with no tile click in flight, the handler
    # falls back to reading the DOM around the input.
    check("no slot is pinned between clicks",
          not getattr(page, "_oea_chooser_want", ""))

    b.close()

print("\nFAILURES:" if failures else "\nJOBVITE UPLOAD CHECK PASSED")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
