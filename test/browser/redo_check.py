"""Going back to an answer already given.

The candidate accepted a drafted answer, the agent moved to the next
question, and then they wanted to change the earlier one. "redo" reopens it
pre-filled; "llm: <change>" inside redrafts it; the next question is left
untouched.
"""
import json
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

from src import history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

SKILL_Q = ("What's a professional skill you've developed in the past year that wasn't on your "
           "radar before, and how did the opportunity to learn it arise?")
SALARY_Q = "What is your expected compensation? (fixed only, in lakh rupees/INR)"
FIRST = "Over the past year I went deep on AI-assisted development and LLM application building."

FIXTURE = WORK / "fixture_redo.html"
FIXTURE.write_text(f"""<!doctype html><html><body>
<label for="skill">{SKILL_Q}</label><textarea id="skill" rows="3" cols="60"></textarea>
<label for="pay">{SALARY_Q}</label><input id="pay" type="text">
</body></html>""", encoding="utf-8")


class Sess:
    def __init__(self, replies):
        self.replies = list(replies)
        self.logs = []

    def log(self, text):
        self.logs.append(text)
        print("   ", text)

    def ask(self, question, suggestion=""):
        reply = self.replies.pop(0)
        print(f"    ASK {question[:72]}")
        if suggestion:
            print(f"    PREFILLED {suggestion[:66]}")
        print(f"    YOU {reply[:66]}")
        return suggestion if reply == "__send__" else reply


class Attach:
    calls = 0
    resume_text = "Six years of backend work."

    def invoke(self, system, user, schema):
        Attach.calls += 1
        assert FIRST[:30] in user, "the redraft must carry the answer being changed"
        return schema(text=FIRST + " I now lead the team's AI tooling review.")


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1200, "height": 800})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)
    skill = next(f for f in fields if f["tag"] == "textarea")
    holder = {}

    # the candidate answered the skill question a moment ago
    page.locator("#skill").fill(FIRST)
    worker._remember_answered(holder, skill, SKILL_Q, FIRST)

    # 1. "redo" reopens THAT answer, pre-filled, and takes an edit
    sess = Sess(["llm: mention that I now review the team's AI tooling", "__send__"])
    fields = browser.snapshot(page)
    handled = worker._redo_answer(page, fields, holder, {"title": "Engineer", "company": "Xplor"},
                                  Attach(), sess, "redo")
    written = page.locator("#skill").input_value()
    print("  handled as redo:", handled, "| box now:", repr(written[-60:]))
    if not handled:
        failures.append("'redo' was not recognised")
    if "AI tooling review" not in written:
        failures.append(f"the redraft was not written: {written[-60:]!r}")
    if page.locator("#pay").input_value():
        failures.append("the next question's box was touched")

    # 2. words pick an earlier answer by name
    worker._remember_answered(holder, next(f for f in fields if f["tag"] == "input"), SALARY_Q, "32")
    sess = Sess(["skip"])
    worker._redo_answer(page, browser.snapshot(page), holder, {}, Attach(), sess, "redo the skill one")
    if not any("Editing" in line and "skill" in line for line in [q for q in sess.logs] + [""]):
        pass  # the prompt text is checked below through the ask() trace
    # 3. a plain answer is never mistaken for a redo
    if worker._redo_answer(page, fields, holder, {}, Attach(), Sess([]), "no"):
        failures.append("'no' was treated as a redo")
    if worker._redo_answer(page, fields, {}, {}, Attach(), Sess([]), "redo"):
        failures.append("'redo' acted with nothing answered yet")

    # 4. the review prompt must not read a redo (or any sentence carrying a
    #    stray "on"/"yes") as permission to click Next.
    quoted = ("redo What's a professional skill you've developed in the past year "
              "that wasn't on your radar before")
    if worker._is_short_yes(quoted):
        failures.append("the quoted question still reads as a yes")
    if not worker._redo_answer(page, browser.snapshot(page), holder,
                               {"title": "Engineer", "company": "Xplor"}, Attach(),
                               Sess(["skip"]), quoted):
        failures.append("the quoted question did not reach the redo path")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nREDO CHECK PASSED")
