"""The myKaarma/Rippling form, rebuilt from the dump of Sep 14 2026.

Four things went wrong on one page, and every one of them was a label the
snapshot never reached even though the text was sitting in the DOM:

  * Both uploads read "Drop or select (.doc / .docx / .pdf)" - the button's
    own words - so resume and cover letter were indistinguishable and neither
    was attached. Their real names are in the wrapping label's
    aria-labelledby: "Résumé" and "Cover letter".
  * Eleven dropdowns read "Select". The question sits six ancestors above the
    combobox and the walk climbed five, so the model was handed a page of
    boxes called Select and asked to fill them in.
  * "Which college tier does your institute fall under?" was invisible: the
    native radios are painted to zero size and the styled wrappers that
    replace them were skipped for containing an input.
  * "Website link" and "Share the name of your Institute/College:" matched no
    rule, because both patterns are anchored to the bare word.

Scratch profile, temp DB. Nothing real is read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "_work"   # generated, gitignored
WORK.mkdir(exist_ok=True)
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
    "university": "Example University",
    "github": "https://github.com/test",
    "portfolio": "",
    "college_tier": "Other/Not Listed",
    "gpa_10_point": "7.4",
    "gpa_5_point": "3.7",
    "gpa_scale": "10",
    "degree_recognized_by": "UGC",
}
profile.PROFILE_PATH.write_text(json.dumps(PROFILE), encoding="utf-8")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def labelled(fields, wanted):
    return [f for f in fields if wanted.lower() in str(f.get("label") or "").lower()]


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 1400})
    page.goto((HERE / "fixture_mykaarma.html").as_uri())
    page.wait_for_timeout(250)

    fields = browser.snapshot(page)
    print(f"{len(fields)} fields:")
    for f in fields:
        print(f"  id={f['id']:>3} {f['tag']:7} type={str(f.get('type') or ''):9} "
              f"req={str(f.get('required')):5} {str(f.get('label') or '')[:62]!r}")

    print("\nthe two uploads are told apart")
    drops = labelled(fields, "Drop or select")
    check("neither is named by the button's own words", len(drops) == 0,
          f"{len(drops)} still are")
    check("one is the resume", len(labelled(fields, "sum")) == 1,
          str([f.get("label") for f in labelled(fields, "sum")]))
    check("one is the cover letter", len(labelled(fields, "Cover letter")) == 1,
          str([f.get("label") for f in labelled(fields, "Cover letter")]))
    # Having the right NAME is not the same as being recognised by it. The
    # accents in "Résumé" meant the box was not a resume field at all, so no
    # resume was ever prepared for it and nothing logged a failure - the
    # upload simply never happened, quietly, on a required field.
    resume_box = labelled(fields, "sum")
    if resume_box:
        check("and the resume box is recognised through its accents",
              resolver.wants_resume(resume_box[0]),
              repr(resume_box[0].get("label")))
    letter_box = labelled(fields, "Cover letter")
    if letter_box:
        check("the letter box is still the letter's",
              cover_letter.is_cover_letter(letter_box[0])
              and not resolver.wants_resume(letter_box[0]))
    check("and the screen-reader filler is not in the name",
          not labelled(fields, "file selected"),
          str([f.get("label") for f in labelled(fields, "file selected")]))

    print("\nthe dropdowns are named by their question")
    selects = [f for f in fields if str(f.get("label") or "").strip().lower()
               in ("select", "select...")]
    check("none is left reading 'Select'", not selects, f"{len(selects)} are")
    check("the work-authorisation one found its question",
          len(labelled(fields, "authorized to lawfully work")) == 1)
    check("so did the degree one",
          len(labelled(fields, "degree was awarded")) == 1)

    print("\nthe college-tier radios exist at all")
    tiers = [f for f in fields
             if str(f.get("label") or "").strip() in ("Tier 1", "Tier 2", "Other/Not Listed")]
    check("all three options are in the snapshot", len(tiers) == 3,
          str([f.get("label") for f in tiers]))
    check("they read as radios",
          len(tiers) == 3 and all(f.get("type") == "radio" for f in tiers),
          str([f.get("type") for f in tiers]))
    check("and none is ticked yet",
          len(tiers) == 3 and all(not f.get("checked") for f in tiers))
    # Visible is not the same as answerable: three loose words with no
    # question and no shared name are three things the model cannot place.
    check("they share one radio group name",
          len({str(f.get("name") or "") for f in tiers}) == 1
          and all(f.get("name") for f in tiers),
          str([f.get("name") for f in tiers]))
    check("and the group carries the question",
          all("college tier" in str(f.get("group") or "").lower() for f in tiers),
          str([f.get("group") for f in tiers]))

    print("\nthe two boxes a rule should have filled")
    site = labelled(fields, "Website link")
    college = labelled(fields, "Institute/College")
    check("Website link is on the page", len(site) == 1)
    check("Institute/College is on the page", len(college) == 1)
    if site:
        got = resolver.resolve(site[0])
        check("website falls back to github when portfolio is empty",
              bool(got) and got[0] == PROFILE["github"], repr(got))
    if college:
        got = resolver.resolve(college[0])
        check("the college box gets the university",
              bool(got) and got[0] == PROFILE["university"], repr(got))

    print("\nthe upload tile is recognised as one, not just named right")
    # wants_resume() folding accents was not enough: _upload_tile_kind has its
    # own match, and a tile the sweep does not recognise is a resume that is
    # never prepared and never attached, silently, on a required field.
    tile = next((f for f in fields if "sum" in str(f.get("label") or "").lower()
                 and f.get("tag") in ("button", "div")), None)
    check("the Résumé tile is a resume tile",
          tile is not None and worker._upload_tile_kind(tile) == "resume",
          repr(worker._upload_tile_kind(tile)) if tile else "no tile")
    letter_tile = next((f for f in fields if str(f.get("label")) == "Cover letter"
                        and f.get("tag") in ("button", "div")), None)
    check("and the letter tile is still the letter's",
          letter_tile is not None and worker._upload_tile_kind(letter_tile) == "letter",
          repr(worker._upload_tile_kind(letter_tile)) if letter_tile else "no tile")

    print("\na GPA goes in only where the form insists")
    # The 5-point question does NOT get the converted 3.7: the candidate
    # studied on a 10-point scale, and the form offers an option that says so.
    # Converting is what makes a 7.4/10 look like a 3.7/5, which is not a
    # grade anyone was ever awarded.
    for label, want in (("On a 10-point GPA/Grade Point scale, what best represents "
                         "your academic performance?", "7.4"),
                        ("On a 5-point GPA/Grade Point scale, what best represents "
                         "your academic performance?", "10-point scale")):
        asked = {"tag": "div", "role": "combobox", "haspopup": "listbox", "type": "",
                 "label": label, "name": "", "elid": "x", "group": "", "accept": "",
                 "value": "", "required": True}
        got = resolver.resolve(asked)
        check(f"required {label[5:12]} is answered", bool(got) and got[0] == want, repr(got))
        spare = {**asked, "required": False}
        check(f"optional {label[5:12]} is left alone",
              resolver.resolve(spare) is None, repr(resolver.resolve(spare)))

    print("\nand a GPA meets the bands a form actually offers")
    TEN = ["7.0 or higher", "5.0–6.9", "Below 5.0",
           "I attended a university using a 5-point scale"]
    FIVE = ["4 or higher", "3", "2 or below",
            "I attended a university using a 10-point scale"]
    check("7.4 falls in the right band", worker._band_index(TEN, "7.4") == 0,
          str(worker._band_index(TEN, "7.4")))
    check("6.2 falls in the middle one", worker._band_index(TEN, "6.2") == 1,
          str(worker._band_index(TEN, "6.2")))
    check("4.1 falls below", worker._band_index(TEN, "4.1") == 2)
    check("a word is never a band", worker._band_index(TEN, "Computer Science") == -1)
    # The honest answer to the scale you did NOT study on is the option that
    # says so: a 7.4/10 is not really a 3.7/5, and none of 4/3/2 is true.
    asked5 = {"tag": "div", "role": "combobox", "haspopup": "listbox", "type": "",
              "label": "On a 5-point GPA/Grade Point scale, what best represents "
                       "your academic performance?",
              "name": "", "elid": "x", "group": "", "accept": "", "value": "",
              "required": True}
    got5 = resolver.resolve(asked5)
    check("the other scale is answered by saying which you used",
          bool(got5) and got5[0] == "10-point scale", repr(got5))
    check("and that lands on the option that says it",
          bool(got5) and worker._choose_option(FIVE, got5[0]) == 3,
          str(worker._choose_option(FIVE, got5[0]) if got5 else None))

    print("\nthe accrediting-body dropdown answers itself")
    degree = labelled(fields, "degree was awarded")
    if degree:
        got = resolver.resolve(degree[0])
        check("it comes from the profile, not a model guess",
              bool(got) and got[0] == PROFILE["degree_recognized_by"], repr(got))

    print("\nthe tier group answers itself, and only the right option")
    by_label = {str(f.get("label")): f for f in tiers}
    if len(tiers) == 3:
        chosen = resolver.resolve(by_label["Other/Not Listed"])
        check("the stored tier is ticked", chosen == ("yes", "profile"), repr(chosen))
        for wrong in ("Tier 1", "Tier 2"):
            got = resolver.resolve(by_label[wrong])
            check(f"and {wrong} is left alone", got is None, repr(got))
        # A group we know, with an answer naming none of its options, must
        # pick nothing rather than the nearest thing. Tested on the option
        # that WOULD have been ticked a moment ago, so the None means the
        # stored answer stopped matching - not that this option never matched.
        profile.PROFILE_PATH.write_text(
            json.dumps({**PROFILE, "college_tier": "Tier 9"}), encoding="utf-8")
        got = resolver.resolve(by_label["Other/Not Listed"])
        check("an answer matching no option picks nothing", got is None, repr(got))
        profile.PROFILE_PATH.write_text(json.dumps(PROFILE), encoding="utf-8")
        check("and it ticks again once the answer fits",
              resolver.resolve(by_label["Other/Not Listed"]) == ("yes", "profile"))

    print("\nticking a styled radio really ticks the native one")
    if len(tiers) == 3:
        other = next(f for f in tiers if f["label"] == "Other/Not Listed")
        browser.locate(page, other["id"], str(other.get("elid") or "")).click()
        page.wait_for_timeout(150)
        check("the form now holds the chosen value",
              page.evaluate("() => document.querySelector('input[value=\"Other/Not Listed\"]').checked") is True)
        again = browser.snapshot(page)
        picked = [f for f in again if str(f.get("label") or "") == "Other/Not Listed"]
        check("and the snapshot sees it as ticked",
              bool(picked) and picked[0].get("checked") is True,
              str([(f.get("label"), f.get("checked")) for f in again
                   if str(f.get("label") or "").startswith(("Tier", "Other"))]))

    b.close()
TMP.cleanup()

print()
if failures:
    print("MYKAARMA CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("MYKAARMA CHECK PASSED")
