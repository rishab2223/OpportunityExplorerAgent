"""The Prachay application of Sep 15 2026 - a stock WordPress Contact Form 7.

Seven boxes, five filled from the profile, and the two that were left for the
candidate to type by hand were the two that mattered most:

  * The upload's entire label is the word "File". Nothing about it says
    resume, so none was prepared and the model was reduced to asking what the
    box wanted - on the one required field of the whole form. What identifies
    it is the company it keeps: required, accepts documents, names none, and
    the only such input on the page.
  * The box beside Qualification is labelled with the bare word "Field". The
    field-of-study rule wanted the phrase "field of study", so it matched
    nothing and a degree subject already in the profile went untyped.

Both fixes are narrow by construction, so most of what is checked here is what
must NOT happen: a photo input, a second document slot, an optional upload, a
label that merely contains the word "field".

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
from src.apply import browser, cover_letter, profile, resolver, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
PROFILE = {
    "full_name": "Test User",
    "email": "a_candidate@example.invalid",
    "phone": "00000 00000",
    "location": "Bangalore, India",
    "highest_education_level": "Bachelors",
    "field_of_study": "Computer and Information Science",
}
profile.PROFILE_PATH.write_text(json.dumps(PROFILE), encoding="utf-8")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def one(fields, wanted):
    return next((f for f in fields if str(f.get("label") or "").startswith(wanted)), None)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 1000})
    page.goto((HERE / "fixture_cf7.html").as_uri())
    page.wait_for_timeout(250)

    fields = browser.snapshot(page)
    print(f"{len(fields)} fields:")
    for f in fields:
        print(f"  id={f['id']:>3} {f['tag']:7} type={str(f.get('type') or ''):7} "
              f"req={str(f.get('required')):5} accept={str(f.get('accept') or ''):17} "
              f"{str(f.get('label') or '')!r}")

    print("\nthe upload nobody could name")
    upload = one(fields, "File")
    check("it is on the page and required",
          upload is not None and upload.get("required") is True)
    # It is genuinely nameless: no rule reads it as a resume, which is the
    # whole difficulty and the reason the sole-document rule has to exist.
    check("no rule reads it as a resume by its words",
          upload is not None and not resolver.wants_resume(upload)
          and not worker.is_resume_field(upload))
    check("and it is not mistaken for the cover letter's",
          upload is not None and not cover_letter.is_cover_letter(upload))
    check("but it is the form's only document upload",
          upload is not None and worker._sole_document_upload(fields, upload) is True)

    print("\nthe box beside Qualification")
    subject = one(fields, "Field")
    quali = one(fields, "Qualification")
    check("Qualification comes from the profile",
          quali is not None and resolver.resolve(quali) == (PROFILE["highest_education_level"], "profile"),
          repr(resolver.resolve(quali)) if quali else "missing")
    check("and the bare word 'Field' is the subject",
          subject is not None and resolver.resolve(subject) == (PROFILE["field_of_study"], "profile"),
          repr(resolver.resolve(subject)) if subject else "missing")

    print("\nwhat the word 'field' must NOT drag in")
    # The rule is anchored to the whole label. Every one of these contains the
    # word and none of them asks for a degree subject; matching any would put
    # "Computer and Information Science" into a box that wanted something else.
    for label in ("Required field", "This field is required", "Field of work",
                  "Playing field", "Fields marked with an asterisk", "Airfield"):
        got = resolver.resolve({"tag": "input", "type": "text", "label": label,
                                "name": "", "elid": "x", "group": "", "accept": "",
                                "value": "", "required": False, "autocomplete": ""})
        check(f"{label!r} is not a field of study", got is None, repr(got))

    b.close()

print("\nwhat the sole-document rule must NOT claim")
# Each of these is the ONLY file input in its list, so only the other
# conditions stand between the resume and the wrong box.
BASE = {"tag": "input", "type": "file", "id": 1, "label": "File * (required)",
        "name": "", "elid": "x", "group": "", "required": True,
        "accept": ".pdf,.doc,.docx", "value": "", "text": ""}


def sole(**kw):
    f = {**BASE, **kw}
    return worker._sole_document_upload([f], f)


check("a nameless required document upload is claimed", sole() is True)
check("an OPTIONAL one is not", sole(required=False) is False,
      "an optional upload is a portfolio as often as a resume")
check("a photo input is not", sole(accept="image/*") is False)
check("one that accepts anything at all is not", sole(accept="") is False,
      "no accept list is too vague to act on")
for named in ("Photograph * (required)", "Portfolio * (required)",
              "Upload your certificates *", "Cover letter *",
              "Government ID *", "Photo file *"):
    check(f"{named!r} names a document and is left alone",
          sole(label=named) is False)

print("\nand not when the form offers a choice of document")
# Two document slots and no words to tell them apart: picking either is a
# guess, and guessing puts the resume in the cover-letter box.
TWO = [{**BASE, "id": 1}, {**BASE, "id": 2}]
check("neither of two nameless uploads is claimed",
      worker._sole_document_upload(TWO, TWO[0]) is False
      and worker._sole_document_upload(TWO, TWO[1]) is False)
# A photo alongside it is not competition: it takes images, not documents.
WITH_PHOTO = [{**BASE, "id": 1},
              {**BASE, "id": 2, "accept": "image/*", "label": "Photograph *"}]
check("a photo input beside it does not make it ambiguous",
      worker._sole_document_upload(WITH_PHOTO, WITH_PHOTO[0]) is True)

TMP.cleanup()

print()
if failures:
    print("CF7 CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("CF7 CHECK PASSED")
