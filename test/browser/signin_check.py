"""The iCIMS sign-in wall, rebuilt from the dump of Sep 14 2026.

Applying to Shure never reached a form. iCIMS sends the candidate to a login
page first, and that page is nine controls: one username box, a Continue, and
seven "Continue with <provider>" buttons. Read as an application form it looks
finished the moment the e-mail goes in, so the transcript said

    Everything I can fill is done. Review the form and click 'action'

about a page with no application on it - and 'action' because the button's
NAME is action while the button in front of the candidate says Continue.

A sign-in page must be named for what it is, and a real form must not be
mistaken for one: a registration step that asks for a password alongside the
application is still the application.
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

PROVIDERS = ["Indeed", "Google", "Microsoft", "Linkedin", "Apple",
             "Corporate Login", "Facebook"]

LOGIN = HERE / "fixture_signin.html"
LOGIN.write_text("""<!doctype html><html><body>
<form>
<label for="u">Username or email address *</label>
<input id="u" type="text" name="username">
<button type="submit" name="action">Continue</button>
</form>
""" + "\n".join(
    f'<form><button type="submit">Continue with {p}</button></form>' for p in PROVIDERS
) + """
</body></html>""", encoding="utf-8")

# The same page AFTER signing in: this is the application, and it must not be
# read as a login just because it offers to create an account with a password.
FORM = HERE / "fixture_after_signin.html"
FORM.write_text("""<!doctype html><html><body>
<form>
<h2>Application</h2>
<label for="fn">First name</label><input id="fn" type="text">
<label for="ln">Last name</label><input id="ln" type="text">
<label for="e">Email</label><input id="e" type="text">
<label for="ph">Phone</label><input id="ph" type="text">
<label for="pw">Choose a password</label><input id="pw" type="password">
<label for="r">Resume</label><input id="r" type="file">
<button type="submit">Submit application</button>
</form></body></html>""", encoding="utf-8")

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1280, "height": 900})

    page.goto(LOGIN.as_uri())
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    print(f"== the sign-in page ({len(fields)} controls) ==")
    for f in fields:
        print(f"    {f['tag']:7} {(f.get('type') or ''):7} {(f.get('label') or '')[:46]!r}")

    check("it is recognised as a sign-in page", worker._is_sign_in_page(fields))
    go = next((f for f in fields if (f.get("name") or "") == "action"), None)
    check("the submit button is there", go is not None)
    check("it is called by the word on it, not by its name",
          worker._field_label(go or {}) == "Continue",
          repr(worker._field_label(go or {})))

    print("\n== the password step ==")
    # Step two of the same wall: one password box, and no submit the snapshot
    # can see. That misses the hand-off branch entirely, so the model was
    # asked what to do and came back with a question - and the agent typed
    # "What is the password for your iCIMS account?" into the chat.
    STEP2 = HERE / "fixture_signin_password.html"
    STEP2.write_text("""<!doctype html><html><body><form>
    <label for="p">Password</label><input id="p" type="password" name="password">
    </form></body></html>""", encoding="utf-8")
    page.goto(STEP2.as_uri())
    page.wait_for_timeout(200)
    step2 = browser.snapshot(page)
    check("a lone password box is a sign-in page too",
          worker._is_sign_in_page(step2), f"{len(step2)} field(s)")

    print("\n== and a secret is never asked for ==")
    ApplyAction = worker.ApplyAction

    class Sess:
        def __init__(self):
            self.logs = []

        def log(self, text):
            self.logs.append(text)

        def ask(self, *a, **k):
            raise AssertionError("it asked for the password")

        def ask_choice(self, *a, **k):
            raise AssertionError("it asked for the password")

    pw = next(f for f in step2 if (f.get("type") or "") == "password")
    sess, handled, notes = Sess(), set(), []
    outcome = worker._run_action(
        page, ApplyAction(action="ask", field_id=pw["id"], question="What is the password?",
                          reason="need it", confidence=0.9),
        step2, {"job_id": "t", "company": "X", "title": "Y"}, "", sess, [], notes,
        handled, {}, {}, set(),
    )
    check("the ask is refused, not put to the candidate", outcome == "skipped", str(outcome))
    check("and it says to type it in the browser",
          any("yourself" in s for s in sess.logs), str(sess.logs))

    print("\n== the application behind it ==")
    page.goto(FORM.as_uri())
    page.wait_for_timeout(200)
    real = browser.snapshot(page)
    check("a form that also sets a password is NOT a sign-in page",
          not worker._is_sign_in_page(real))

    print("\n== one provider button is not a wall ==")
    # A form offering a single "Continue with Google" shortcut alongside a
    # real application must not be waved through as a login.
    check("a lone provider button does not make a login page",
          not worker._is_sign_in_page(
              [f for f in fields if "Continue with Google" in (f.get("label") or "")]
              + [f for f in real if f.get("tag") in ("input", "textarea")]))
    b.close()

print("\nFAILURES:" if failures else "\nSIGN-IN CHECK PASSED")
for f in failures:
    print("  -", f)
sys.exit(1 if failures else 0)
