"""The Rippling ATS form, rebuilt from the dump of Sep 14 2026.

Nothing on that form was labelled in a way the snapshot could use:

- the Location box carries aria-label="textbox" over a real label that says
  "Location", and the junk won;
- twelve dropdowns are <div role=combobox> whose only text is "Select", so
  the transcript said "Selected 'Yes' for Select" twelve times and the
  candidate could not tell which question had been answered;
- two inputs have no label at all, only a generated name - "VIQ1zlI-W69" is
  what the transcript showed when it typed the expected pay in;
- the question itself sits in a <p> ABOVE the field wrapper, with its hint
  in a second block under it, tied to the control by nothing;
- both upload buttons read "Drop or select (.doc / .docx / .pdf)" and name
  neither the resume nor the letter. Only the hidden input behind each one
  does - data-testid="input-resume" and "input-cover_letter".
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

from src import answers  # noqa: E402
from src.apply import browser, salary, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()

FIXTURE = WORK / "fixture_rippling.html"
FIXTURE.write_text("""<!doctype html><html><body>
<form>
<h2>Application: Software Developer II</h2>

<div><label data-testid="resume" aria-labelledby="field-8-label">
  <span id="field-8-label">Resume</span>
  <input accept=".doc,.docx,.pdf" data-testid="input-resume" type="File"
         style="display:none">
  <button type="button"><span>Drop or select (.doc / .docx / .pdf)</span></button>
</label></div>

<div data-testid="field">
  <div><span id="field-46-label">Location</span><span>*</span></div>
  <div><input id="field-46" aria-label="textbox" autocomplete="off"></div>
</div>

<div>
  <div class="marginBottom--4"><p>Are you authorized to lawfully work in the
    country where the job is available?<div></div></p></div>
</div>
<div data-testid="field">
  <div id="field-67" role="combobox" aria-haspopup="listbox" tabindex="0">Select</div>
</div>

<div>
  <div class="marginBottom--4"><p>What are your compensation expectations for
    this role?<div></div></p></div>
  <div class="marginBottom--4"><div class="ATS_htmlPreview">Feel free to provide
    a range or share any factors influencing your expectations.</div></div>
</div>
<div data-testid="field">
  <div><input id="field-79" name="VIQ1zlI-W69" autocomplete="off" maxlength="50"></div>
</div>

<div>
  <div class="marginBottom--4"><p>Which university did you attend?<div></div></p></div>
</div>
<div data-testid="field">
  <div><input id="field-109" name="KXTFUcVOuOm" autocomplete="off"></div>
</div>

<div><label data-testid="cover_letter" aria-labelledby="field-63-label">
  <span id="field-63-label">Cover letter</span>
  <input accept=".doc,.docx,.pdf" data-testid="input-cover_letter" type="File"
         style="display:none">
  <button type="button"><span>Drop or select (.doc / .docx / .pdf)</span></button>
</label></div>

<button type="submit">Apply</button>
</form></body></html>""", encoding="utf-8")


class Sess:
    def __init__(self):
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)


RESUME = Path(TMP.name) / "Tailored_Resume.pdf"
LETTER = Path(TMP.name) / "Cover_Letter.pdf"
RESUME.write_bytes(b"%PDF-1.4 resume\n")
LETTER.write_bytes(b"%PDF-1.4 letter\n")


class Attach:
    def __init__(self):
        self.resume_path = str(RESUME)
        self.letter_pdf = str(LETTER)
        self.resume_attached = False
        self.letter_attached = False
        self.letter_declined = False

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
    page = b.new_page(viewport={"width": 1280, "height": 1100})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    by_id = {f["path"].split("#")[-1]: f for f in fields if "#" in f["path"]}

    print("== labels ==")
    for f in fields:
        print(f"    {f['tag']:7} {f.get('path', '')[:22]:24} {(f.get('label') or '')[:66]!r}")

    loc = by_id.get("field-46", {})
    check("the real label beats aria-label='textbox'",
          (loc.get("label") or "").strip() == "Location", repr(loc.get("label")))

    auth = by_id.get("field-67", {})
    check("a 'Select' combobox takes the question above it",
          "authorized to lawfully work" in (auth.get("label") or ""),
          repr(auth.get("label")))

    pay = by_id.get("field-79", {})
    check("a generated name is not a label",
          "VIQ1zlI" not in (pay.get("label") or ""), repr(pay.get("label")))
    check("the pay question is read instead",
          "compensation expectations" in (pay.get("label") or ""),
          repr(pay.get("label")))
    check("...so it is recognised as an expected-pay question",
          salary.topic_of(pay) == "expected_ctc", salary.topic_of(pay))
    check("...and the estimate will run for it",
          worker._wants_salary_estimate(pay))

    uni = by_id.get("field-109", {})
    check("the other unlabelled box gets its question too",
          "university" in (uni.get("label") or "").lower(), repr(uni.get("label")))

    print("\n  a <select>'s options are never its label")
    # The guard for this existed - "a <select>'s innerText is every option it
    # has" - and was undone by a trailing `|| el.innerText` in its own
    # expression. Sixteen selects across the dumps came out labelled
    # "0 years\n1 year\n2 years\n3 years..." : unresolvable by any rule,
    # useless in the transcript, sixty characters of noise in every prompt.
    # Closing it exposed a second one - an unlabelled control was inheriting
    # the PREVIOUS field's <label for=...> - and a wrong name is worse than
    # none, because a rule can match it and fill the wrong box.
    # Its own page: the upload checks below still need this one, and writing
    # over it left them attaching files to a form that no longer existed.
    selects = b.new_page(viewport={"width": 1280, "height": 900})
    selects.set_content("""<form>
      <label for="ctry">Country</label>
      <select id="ctry"><option>India</option><option>United Kingdom</option></select>
      <select id="bare"><option>0 years</option><option>1 year</option><option>2 years</option></select>
      <p>How many years of Agile experience?</p>
      <select id="asked"><option>0 years</option><option>1 year</option></select>
      <label for="deg">Degree</label><select id="deg"><option>BSc</option></select>
    </form>""")
    selects.wait_for_timeout(200)
    labels = {f.get("elid"): (f.get("label") or "") for f in browser.snapshot(selects)}
    selects.close()
    check("a labelled select keeps its own label", labels.get("ctry") == "Country",
          repr(labels.get("ctry")))
    check("an unlabelled one is not labelled with its options",
          "0 years" not in labels.get("bare", ""), repr(labels.get("bare")))
    check("nor with the label of the field before it",
          labels.get("bare") == "", repr(labels.get("bare")))
    check("but a real question above it is still found",
          "Agile" in labels.get("asked", ""), repr(labels.get("asked")))
    check("and the field after is unaffected", labels.get("deg") == "Degree",
          repr(labels.get("deg")))

    print("\n== the two upload buttons ==")
    tiles = [f for f in fields if f.get("tag") == "button"
             and "Drop or select" in (f.get("text") or "")]
    check("the hidden file inputs are invisible to the snapshot, as on the real page",
          not [f for f in fields if (f.get("type") or "").lower() == "file"])
    check("both are seen at all", len(tiles) == 2, f"{len(tiles)} found")
    page_text = browser.page_text(page, 1500)
    kinds = [worker._upload_tile_kind(f, page_text) for f in tiles]
    check("one is the resume and the other the letter",
          sorted(k for k in kinds if k) == ["letter", "resume"], str(kinds))

    sess, attach, handled, notes = Sess(), Attach(), set(), []
    for _ in range(2):
        worker._handle_attachments(page, browser.snapshot(page), handled,
                                   attach, sess, notes)
    check("the resume was attached", attach.resume_attached)
    check("the cover letter was attached", attach.letter_attached)
    b.close()

print("\nFAILURES:" if failures else "\nRIPPLING CHECK PASSED")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
