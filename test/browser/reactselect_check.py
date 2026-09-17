"""A react-select dropdown that HAS been answered must not read as empty.

The Fospha application on Greenhouse, Sep 16 2026. react-select clears its
search box when you choose something and renders the choice in a sibling, so
the box the snapshot reads is empty on a dropdown that is already answered.
Three fields the agent had set correctly - Country, notice period, and the
Mumbai office question - all read as untouched:

  Selected 'Immediate / Available to join' for What is your notice period?*
  Could not fill What is your notice period?*: 'Immediate Joiner' matches
      none of the dropdown's options; pick one of:
  FILL THIS ONE YOURSELF: What is your notice period?* - required

Every one of those lines is about a box that was already right. The dump
agrees: value "" on all three, and the page's own live region saying "option
Immediate / Available to join, selected."

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
from src.apply import browser, profile, resolver, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "notice_period": "Immediate Joiner",
    "phone_country_code": "+91",
}), encoding="utf-8")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(250)

    fields = browser.snapshot(page)
    print(f"{len(fields)} fields:")
    for f in fields:
        print(f"  id={f['id']:>3} {f['tag']:6} role={str(f.get('role') or ''):9} "
              f"value={str(f.get('value') or '')[:34]!r:38} "
              f"{str(f.get('label') or '')[:44]!r}")

    print("\nthe page really is the one that failed")
    # If the search boxes carried their own value there would be nothing to
    # fix and this check would prove nothing.
    empty_boxes = page.eval_on_selector_all(
        "input[role=combobox]", "els => els.every(e => e.value === '')")
    check("every search box is empty, as react-select leaves them",
          empty_boxes is True)

    print("\nan answered dropdown reads as answered")
    by_label = {str(f.get("label") or "").split("*")[0].strip(): f for f in fields}
    for label, want in (("What is your notice period?", "Immediate / Available to join"),
                        ("Please confirm you are happy to work from our Andheri, "
                         "Mumbai office 4 days a week?", "Yes")):
        got = by_label.get(label.strip())
        check(f"{label[:34]!r} carries its choice",
              got is not None and got.get("value") == want,
              repr(got.get("value")) if got else "field missing")
    country = by_label.get("Country")
    check("and Country does too, flag and all",
          country is not None and "+91" in str(country.get("value") or ""),
          repr(country.get("value")) if country else "missing")

    print("\nso the profile leaves it alone")
    # This is what the fix is FOR. The profile says "Immediate Joiner", which
    # is not the option's wording, so a field wrongly read as empty gets
    # filled again and fails - twice on the real form, and then reported as
    # unanswered to the candidate.
    notice = by_label.get("What is your notice period?")
    check("no second attempt at a box already answered",
          notice is not None and resolver.resolve(notice) is None,
          repr(resolver.resolve(notice)) if notice else "missing")

    print("\nand an UNanswered one is still filled")
    # The other half: a placeholder is not a value. Reading one as an answer
    # would leave required dropdowns blank, which is worse than the bug.
    unanswered = by_label.get("How did you hear about us?")
    check("a placeholder does not count as a choice",
          unanswered is not None and not (unanswered.get("value") or ""),
          repr(unanswered.get("value")) if unanswered else "missing")

    print("\nand a plain text box is untouched by any of this")
    name = by_label.get("First Name")
    check("an ordinary input keeps its own empty value",
          name is not None and (name.get("value") or "") == "",
          repr(name.get("value")) if name else "missing")

    print("\nthe choice is read live, not once")
    # A snapshot that only worked on first load would be worse than useless:
    # the value arrives when the candidate or the model picks it.
    page.evaluate("() => { document.querySelector('#q4').closest('.select__value-container')"
                  ".querySelector('.select__placeholder').outerHTML ="
                  " '<div class=\"select__single-value remix-css-1dimb5e-singleValue\">LinkedIn</div>'; }")
    page.wait_for_timeout(100)
    again = {str(f.get("label") or "").split("*")[0].strip(): f for f in browser.snapshot(page)}
    picked = again.get("How did you hear about us?")
    check("a choice made after loading is seen",
          picked is not None and picked.get("value") == "LinkedIn",
          repr(picked.get("value")) if picked else "missing")

    print("\nand an unanswered one can actually be picked")
    # The other half of the Fospha session: "could not read this dropdown's
    # options, so 'Immediate Joiner' was not entered", twice, on a list that
    # was sitting right there. The menu does not exist until the box is
    # opened and mounts a beat later, so reading it the instant it is asked
    # for finds nothing.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    fresh = {str(f.get("label") or "").split("*")[0].strip(): f
             for f in browser.snapshot(page)}
    box = fresh.get("How did you hear about us?")

    class Sess:
        def __init__(self):
            self.lines = []

        def log(self, line):
            self.lines.append(line)

    sess = Sess()
    loc = browser.locate(page, box["id"], str(box.get("elid") or ""))
    try:
        worker._commit_combobox(page, loc, "LinkedIn", "How did you hear about us?",
                                "", sess, [])
        picked = True
    except Exception as exc:
        picked = False
        sess.log(f"refused: {exc}")
    check("the list is read and the option taken", picked is True, str(sess.lines))
    check("and the page received the choice",
          "LinkedIn" in page.locator("#log").inner_text(),
          repr(page.locator("#log").inner_text()))

    print("\nand a filtering box does not lose the list by being typed at")
    # The one the first version of this fixture missed, which is why it passed
    # while the real form failed three runs running: react-select FILTERS as
    # you type. "Immediate Joiner" matches no option, so the list empties, and
    # matching against an empty list can only refuse. The list worth matching
    # is the one from before the typing.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    page.evaluate("() => { const box = document.querySelector('#q2')"
                  ".closest('.select__value-container');"
                  " box.querySelector('.select__single-value').outerHTML ="
                  " '<div class=\"select__placeholder\">Select...</div>'; }")
    page.wait_for_timeout(100)
    notice_box = {str(f.get("label") or "").split("*")[0].strip(): f
                  for f in browser.snapshot(page)}["What is your notice period?"]
    check("it starts out unanswered", not (notice_box.get("value") or ""),
          repr(notice_box.get("value")))
    # Typing the profile's wording really does empty this list - if it did
    # not, the rest of this proves nothing. Shown on its own page, because
    # typing into the box and then clearing it leaves the widget in a state
    # the real flow never starts from, and the first version of this check
    # failed on its own setup rather than on the code.
    probe = b.new_page(viewport={"width": 1280, "height": 900})
    probe.goto((HERE / "fixture_reactselect.html").as_uri())
    probe.wait_for_timeout(200)
    probe.fill("#q2", "Immediate Joiner")
    probe.wait_for_timeout(500)
    filtered = probe.locator("[role=option]").count()
    check("typing the profile's wording empties the list", filtered == 0,
          f"{filtered} options still shown")
    probe.close()

    sess2 = Sess()
    loc2 = browser.locate(page, notice_box["id"], str(notice_box.get("elid") or ""))
    try:
        worker._commit_combobox(page, loc2, "Immediate Joiner",
                                "What is your notice period?", "", sess2, [])
        took = True
    except Exception as exc:
        took = False
        sess2.log(f"refused: {exc}")
    check("the profile's wording still reaches the option", took is True,
          str(sess2.lines)[:120])
    check("and the form received it",
          "Immediate / Available to join" in page.locator("#log").inner_text(),
          repr(page.locator("#log").inner_text()))

    print("\nand a dropdown that took nothing is never reported as filled")
    # Found by disabling the control-open above and watching what was left:
    # Enter at a widget whose list never opened does nothing, and the agent
    # logged "Selected 'Immediate Joiner'" - a value that is not one of the
    # four options - for a box the form had received nothing into. A wrong
    # answer is bad; a wrong answer the candidate is told is right, on a
    # required field, is worse.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    page.evaluate("() => { const box = document.querySelector('#q2')"
                  ".closest('.select__value-container');"
                  " box.querySelector('.select__single-value').outerHTML ="
                  " '<div class=\"select__placeholder\">Select...</div>'; }")
    page.wait_for_timeout(100)
    stuck = {str(f.get("label") or "").split("*")[0].strip(): f
             for f in browser.snapshot(page)}["What is your notice period?"]
    loc3 = browser.locate(page, stuck["id"], str(stuck.get("elid") or ""))
    real_open = worker._open_via_control
    worker._open_via_control = lambda _loc: False      # the widget will not open
    sess3 = Sess()
    try:
        worker._commit_combobox(page, loc3, "Immediate Joiner",
                                "What is your notice period?", "", sess3, [])
        claimed = True
    except Exception as exc:
        claimed = False
        sess3.log(f"refused: {exc}")
    finally:
        worker._open_via_control = real_open
    got = page.locator("#log").inner_text()
    check("it refuses rather than claiming a pick", claimed is False,
          str(sess3.lines)[:140])
    check("and nothing was chosen", "Immediate" not in got, repr(got))
    check("and no line says it selected anything",
          not any("Selected" in line for line in sess3.lines), str(sess3.lines)[:140])

    print("\nand a click that cannot land normally still opens the menu")
    # The bottom rung of browser.click is not a click. el.click() fires ONE
    # event - 'click'. A real press fires pointerdown, mousedown, focus,
    # mouseup, click, and react-select opens its menu on MOUSEDOWN. So when
    # the normal click could not land, the fallback drove nothing at all and
    # "clicked (direct)" went into the transcript anyway.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    fired = page.evaluate("""() => { const el = document.querySelector('#q4');
        window.SEEN = [];
        for (const e of ['pointerdown','mousedown','mouseup','click','focus'])
          el.addEventListener(e, () => window.SEEN.push(e));
        el.click(); return window.SEEN; }""")
    check("el.click() fires only 'click'", fired == ["click"], str(fired))
    check("so the menu it was meant to open stays shut",
          page.locator("[role=option]").count() == 0)

    # An element Playwright will not call stable: click() times out, which is
    # what sends a real session down the ladder, while a real press still
    # lands on it.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    page.evaluate("""() => { const s = document.createElement('style');
        s.textContent = '@keyframes jig{0%{transform:translateY(0)}'
          + '50%{transform:translateY(1px)}100%{transform:translateY(0)}}'
          + '#q4{animation:jig .12s infinite}';
        document.head.appendChild(s); }""")
    page.wait_for_timeout(200)
    rung = browser.click(page.locator("#q4"), timeout=2500, fallback_timeout=900)
    page.wait_for_timeout(600)
    opened = page.locator("[role=option]").count()
    check("the ladder takes the mouse rung", rung == "clicked (mouse)", repr(rung))
    check("and the menu actually opens", opened > 0, f"{opened} options")
    if opened:
        page.locator("[role=option]", has_text="Referral").first.click()
        page.wait_for_timeout(250)
        check("and a choice can be taken from it",
              "Referral" in page.locator("#log").inner_text(),
              repr(page.locator("#log").inner_text()))

    print("\nand the profile's own wording reaches the option")
    # "Immediate Joiner" is how the profile puts it; the form offers
    # "Immediate / Available to join". Neither is a prefix of the other, so
    # every match above fails and the field was handed back to the candidate.
    page.goto((HERE / "fixture_reactselect.html").as_uri())
    page.wait_for_timeout(200)
    check("'Immediate Joiner' finds 'Immediate / Available to join'",
          worker._choose_option(
              ["Immediate / Available to join", "30 days", "60 days", "90 days"],
              "Immediate Joiner") == 0)
    check("but a named subject never takes a different one",
          worker._choose_option(["Bachelor of Science", "Bachelor of Commerce"],
                                "Bachelor of Arts") == -1)
    check("and a one-word option is not a rewording of a phrase",
          worker._choose_option(["Mobile", "Home", "Work"], "Mobile phone") == -1)

    b.close()
TMP.cleanup()

print()
if failures:
    print("REACT-SELECT CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("REACT-SELECT CHECK PASSED")
