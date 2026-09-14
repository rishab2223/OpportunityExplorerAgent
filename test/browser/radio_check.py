"""Xplor's conflict-of-interest questions: a page of Yes/No radios and one
essay box that mentions the word "skill". Answering "no" must tick No, and
the essay must not receive the profile's skills list."""
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
from src.apply.worker import ApplyAction  # noqa: E402

TMP = tempfile.TemporaryDirectory()
profile.PROFILE_PATH = Path(TMP.name) / "apply_profile.json"
history.DB_PATH = Path(TMP.name) / "history.db"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User", "skills": "JavaScript, Node.js, Python",
}), encoding="utf-8")

QUESTIONS = [
    "Have you applied to or been referred to Xplor for another role in the past 12 months?",
    "Do you own or hold a partial interest in any intellectual property relevant to this work?",
    "Do you serve on the board of directors or an advisory board for any vendor or competitor?",
]
ESSAY = ("What's a professional skill you've developed in the past year that wasn't on your "
         "radar before, and how did the opportunity to learn it arise?")
blocks = "".join(
    f"<fieldset><legend>{q}</legend>"
    f"<label><input type='radio' name='q{i}' value='yes'> Yes</label>"
    f"<label><input type='radio' name='q{i}' value='no'> No</label></fieldset>"
    for i, q in enumerate(QUESTIONS)
)
FIXTURE = WORK / "fixture_radios.html"
FIXTURE.write_text(f"""<!doctype html><html><body>
<h3>Questions</h3>{blocks}
<label for="essay">{ESSAY}</label><textarea id="essay" rows="3" cols="60"></textarea>
<h3>Skills :</h3>
<label for="sk">Separate each skill with a comma.</label><textarea id="sk" rows="2" cols="60"></textarea>
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
        print(f"    ASK {question[:60]}\n    YOU {reply}")
        return reply


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1200, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)

    # 1. the skills list goes to the skills box, never to the essay
    essay = next(f for f in fields if f["tag"] == "textarea" and "?" in f["label"])
    skills = next(f for f in fields if f["tag"] == "textarea" and "comma" in f["label"])
    if worker._is_skills_box(essay):
        failures.append("the essay question was taken for a skills box")
    if not worker._is_skills_box(skills):
        failures.append("the real skills box was not recognised")

    # 2. "no" to each question ticks that question's No radio
    sess = Sess(["no", "No", "no"])
    for index, question in enumerate(QUESTIONS):
        yes = next(f for f in fields
                   if f["type"] == "radio" and f["label"] == "Yes" and f["name"] == f"q{index}")
        action = ApplyAction(action="ask", field_id=yes["id"], question=question, confidence=0.9)
        result = worker._run_action(page, action, fields, {}, "", sess, [], [], set(), {}, {}, set())
        checked = page.locator(f"input[name=q{index}][value=no]").is_checked()
        wrong = page.locator(f"input[name=q{index}][value=yes]").is_checked()
        print(f"    -> {result}; No checked: {checked}; Yes checked: {wrong}")
        if result != "executed" or not checked or wrong:
            failures.append(f"q{index}: result={result}, no={checked}, yes={wrong}")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nRADIO CHECK PASSED")
