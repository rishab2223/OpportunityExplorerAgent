"""The optional question LinkedIn asked and the agent retired quietly:
it must be named at the review prompt, and "llm: <question>" must draft an
answer for it, show it for editing, and write it in."""
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

QUESTION = "Is there anything else you\u2019d like us to know about you that is not captured in your application?"
FIXTURE = WORK / "fixture_optional.html"
FIXTURE.write_text(f"""<!doctype html><html><body>
<label for="ctc">What is your current CTC? (In lakh rupees/INR)</label>
<input id="ctc" type="text" value="25" required>
<label for="names">If yes, please list the names of the individual(s) and relationship.</label>
<textarea id="names" rows="2"></textarea>
<label for="more">{QUESTION}</label>
<textarea id="more" rows="3" cols="60"></textarea>
<button type="button">Continue to next step</button>
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
        print(f"    ASK {question[:70]}\n    SUGGESTED {suggestion[:70]}\n    YOU {reply[:60]}")
        return reply if reply != "__use__" else suggestion


class Attach:
    calls = 0
    resume_text = "Six years of backend work at Initech Systems."

    def invoke(self, system, user, schema):
        Attach.calls += 1
        assert QUESTION[:30] in user, "the draft call must carry the question"
        if "do we need to answer" in user:
            return schema(text="A shorter note about relocating to Pune.")
        return schema(text="I relocate to Pune at short notice and enjoy payments work.")


failures = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    page = b.new_page(viewport={"width": 1200, "height": 900})
    page.goto(FIXTURE.as_uri())
    page.wait_for_timeout(200)
    fields = browser.snapshot(page)

    spare = worker._unanswered_questions(fields)
    print("  reported as left empty:", [f["label"][:50] for f in spare])
    if [f["label"] for f in spare] != [QUESTION]:
        failures.append(f"reported {[f['label'][:40] for f in spare]}")

    # the candidate pastes the question back with the llm: prefix
    sess = Sess(["__use__"])
    attach = Attach()
    worker._answer_on_request(page, fields, set(), f"llm: {QUESTION}", {"title": "Engineer", "company": "Xplor"},
                              attach, sess, [])
    written = page.locator("#more").input_value()
    print("  box now holds:", repr(written[:60]))
    if "relocate to Pune" not in written:
        failures.append(f"the answer was not written: {written!r}")
    if Attach.calls != 1:
        failures.append(f"{Attach.calls} model calls, wanted 1")

    # an "llm: ..." reply is an instruction, never the answer: it reached the
    # form once, typed into the box verbatim
    page.locator("#more").fill("")
    sess = Sess(["llm: do we need to answer this at all?", "__use__"])
    before = Attach.calls
    worker._answer_on_request(page, browser.snapshot(page), set(), f"llm: {QUESTION}",
                              {"title": "Engineer", "company": "Xplor"}, attach, sess, [])
    written = page.locator("#more").input_value()
    print("  after a redraft request the box holds:", repr(written[:60]))
    if written.lower().startswith("llm:"):
        failures.append("the instruction was typed into the form")
    if Attach.calls != before + 2:
        failures.append(f"{Attach.calls - before} model calls for a draft plus a redraft")

    # an edited draft is what goes in, and "skip" leaves the box alone
    page.locator("#more").fill("")
    sess = Sess(["My own wording."])
    worker._answer_on_request(page, browser.snapshot(page), set(), f"llm: {QUESTION}",
                              {"title": "Engineer", "company": "Xplor"}, attach, sess, [])
    if page.locator("#more").input_value() != "My own wording.":
        failures.append(f"the edit was not used: {page.locator('#more').input_value()!r}")
    page.locator("#more").fill("")
    sess = Sess(["skip"])
    worker._answer_on_request(page, browser.snapshot(page), set(), f"llm: {QUESTION}",
                              {"title": "Engineer", "company": "Xplor"}, attach, sess, [])
    if page.locator("#more").input_value():
        failures.append("skip still wrote an answer")
    b.close()

TMP.cleanup()
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nOPTIONAL QUESTION CHECK PASSED")
