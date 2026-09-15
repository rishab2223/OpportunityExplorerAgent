"""The Oracle Taleo attachment tile, rebuilt from the Worldline and IGT dumps
of Sep 15 2026.

Neither form got a resume or a cover letter, and on both the reason was the
same: the tile is an ICON. Its own text is empty, and the label it points at is
the bare noun, because the one part of its aria-labelledby that carries a verb
- "Upload a Resume Opens a dialog" - is hiddenAriaContent and is dropped by the
screen-reader-only filter that keeps "File is uploaded successfully" out of
every label. So the gate looking for upload/attach/add/drop in the tile's own
words found nothing and returned before any noun was considered.

The verb survives one place: the visible attachmentLabel beside the icon, which
the snapshot already reads as `group`.

Widening the gate is only safe if the nouns still decide, so the decoys from the
same two pages are checked here too: Taleo's download link for a file already
attached, and its "Additional Attachment" slot, neither of which may be claimed.

Scratch profile, temp DB. Nothing real is read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent

from playwright.sync_api import sync_playwright  # noqa: E402

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

RESUME = Path(TMP.name) / "a_candidate_resume.pdf"
RESUME.write_bytes(b"%PDF-1.4\n% scratch\n")
LETTER = Path(TMP.name) / "a_candidate_letter.pdf"
LETTER.write_bytes(b"%PDF-1.4\n% scratch\n")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class FakeSess:
    def __init__(self):
        self.lines = []

    def log(self, line):
        self.lines.append(line)

    def aborted(self):
        return False


class FakeAttach:
    """Just enough of the real one for the tile path: the files exist, and
    nothing is drafted or written."""

    def __init__(self):
        self.resume_path = str(RESUME)
        self.letter_pdf = str(LETTER)
        self.resume_attached = False
        self.letter_attached = False
        self.letter_declined = False

    def resume(self):
        return self.resume_path

    def cover_letter(self, for_upload=False):
        return self.letter_pdf if for_upload else "Dear team,"


# Straight out of the dumps, with the real file name replaced. Each sits in a
# group whose words include a verb, and none is a slot to fill: the first is
# the download link Taleo shows for a resume already on file, the second is the
# spare "anything else" slot that must not quietly receive the cover letter,
# and the third is the resume tile while a file IS attached - no verb, no
# claim, which is the right answer when something is already there.
DECOYS = [
    {"tag": "div", "role": "button", "label": "*\n\xa0Resume / CV",
     "text": "a_candidate_resume.pdf\n(09/03/2026)", "group": "Uploading...",
     "section": "", "id": 6, "elid": "51:_attachDownloadLabel"},
    {"tag": "span", "role": "button", "label": "Additional Attachment",
     "text": "", "group": "Add a Document", "section": "", "id": 9,
     "elid": "55:_attachIcon"},
    {"tag": "span", "role": "button", "label": "*\n\xa0Resume / CV", "text": "",
     "group": "a_candidate_resume.pdf\n(09/03/2026)", "section": "", "id": 7,
     "elid": "51:_attachIcon"},
    # The one that caught the first attempt at this fix. `group` is proximity,
    # not ownership: a wizard's Next button one step below an upload heading
    # reads as group "Upload resume". Borrowing a verb from the group without
    # naming the noun turned Next into a resume tile, and the agent clicked it
    # and swallowed the rest of the form.
    {"tag": "button", "role": "", "label": "Next", "text": "Next",
     "group": "Upload resume", "section": "", "id": 12, "elid": "next"},
]

with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto((HERE / "fixture_taleo.html").as_uri())
    page.wait_for_timeout(250)

    fields = browser.snapshot(page)
    print(f"{len(fields)} fields:")
    for f in fields:
        print(f"  id={f['id']:>3} {f['tag']:6} role={str(f.get('role') or ''):7} "
              f"label={str(f.get('label') or '')[:28]!r} "
              f"group={str(f.get('group') or '')[:28]!r}")

    print("\nthe page really is the one that failed")
    # If a file input turns up here the fixture is not Taleo and the rest of
    # this check proves nothing: the whole difficulty is that there is nothing
    # to fill until the icon is clicked.
    check("there is no file input to find before the click",
          page.locator("input[type=file]").count() == 0)
    check("and no tile carries a verb of its own",
          all(not worker.UPLOAD_VERB_RE.search(
              str(f.get("text") or "") + str(f.get("label") or "")) for f in fields),
          str([f.get("label") for f in fields]))

    print("\nthe tiles are recognised for what they are")
    resume_tile = next((f for f in fields if "Resume" in str(f.get("label") or "")), None)
    letter_tile = next((f for f in fields if "Cover Letter" in str(f.get("label") or "")), None)
    check("the resume icon is a resume tile",
          resume_tile is not None and worker._upload_tile_kind(resume_tile) == "resume",
          repr(worker._upload_tile_kind(resume_tile)) if resume_tile else "no tile")
    check("the cover-letter icon is the letter's",
          letter_tile is not None and worker._upload_tile_kind(letter_tile) == "letter",
          repr(worker._upload_tile_kind(letter_tile)) if letter_tile else "no tile")

    print("\nand the neighbours that look like tiles are not")
    for decoy in DECOYS:
        got = worker._upload_tile_kind(decoy)
        check(f"{str(decoy['label'])[:20]!r} in {str(decoy['group'])[:18]!r} is left alone",
              got == "", repr(got))

    print("\nclicking one really lands the file")
    # Recognising the tile is not attaching it: Taleo builds the dialog, and
    # the input inside it, only when the icon is clicked. This is the part that
    # decides whether a resume is on the form.
    sess, attach = FakeSess(), FakeAttach()
    ok = worker._click_upload_tile(page, resume_tile, attach, sess, "resume",
                                   "Resume/CV", str(RESUME))
    check("the click attached the resume", ok is True, str(sess.lines))
    names = page.evaluate(
        "() => [...document.querySelectorAll('input[type=file]')]"
        ".map(e => e.files.length ? e.files[0].name : '')")
    check("the file box Taleo built holds the resume",
          names == [RESUME.name], str(names))

    print("\nand the dialog holding it is not thrown away")
    # Escape on a dialog that still holds the input just filled discards the
    # upload, so a dialog that keeps the file must be left standing.
    check("the resume is still there once the click has returned",
          page.evaluate(
              "() => { const e = document.querySelector('input[type=file]');"
              " return !!e && e.files.length === 1; }") is True)

    letter_sess, letter_attach = FakeSess(), FakeAttach()
    ok2 = worker._click_upload_tile(page, letter_tile, letter_attach, letter_sess,
                                    "letter", "Cover Letter", str(LETTER))
    check("the cover-letter tile attaches too", ok2 is True, str(letter_sess.lines))
    both = page.evaluate(
        "() => [...document.querySelectorAll('input[type=file]')]"
        ".map(e => e.files.length ? e.files[0].name : '')")
    check("both files are on the form, each in its own box",
          sorted(both) == sorted([RESUME.name, LETTER.name]), str(both))

    b.close()
TMP.cleanup()

print()
if failures:
    print("TALEO CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("TALEO CHECK PASSED")
