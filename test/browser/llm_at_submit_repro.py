"""'llm: <question>' must work at the SUBMIT prompt, not only at "type next".

From a real Airblack application, the last two lines of a ten-minute session:

    513.2s  YOU: llm : Tell us something you have built or solved using AI...
    513.4s  AGENT ASKS: Everything I can fill is done. Review the form and
            click 'Submit' yourself in the browser, then type done...

No draft, no error - the same prompt back. The llm: branch was wired into the
"type next to click Continue" prompt only, and a one-page form never shows
that prompt: it goes straight to the submit prompt, where the reply was filed
as "guidance from the candidate" for a model call that never came.

Two things are checked here:
  - the request is answered at the submit prompt at all;
  - it is answered even though no box on the page matches the question.
    _field_for_question only offers EMPTY boxes, and a form with nothing left
    empty is exactly the form that reaches this prompt - so the box the
    candidate is reading is never one it can find. A request for help is not
    worth refusing because we cannot type the result in ourselves.

Fails before the fix, passes after.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = E2E = Path(__file__).resolve().parent
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_llmsub_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.invalid",
}), encoding="utf-8")

DRAFTS: list[str] = []
DRAFT_TEXT = "I built an agent that fills application forms and never submits them."


def fake_invoke(system, user, schema):
    if schema.__name__ == "DraftAnswer":
        DRAFTS.append(user)
        return schema(text=DRAFT_TEXT)
    return schema(actions=[])


worker.make_invoker = lambda cfg, env, purpose: fake_invoke


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260914T120000", "llmsub-1", "Airblack Lead Backend Engineer")
        self.script = list(script)
        self.asked: list[str] = []
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)
        super().log(text)
        print(f"  {self._events[-1]['at']:6.1f}s  {text[:96]}")

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        print(f"    ASK {question[:88]}")
        if suggestion:
            print(f"    (draft in the box: {suggestion[:60]})")
        if not self.script:
            raise AssertionError(f"unscripted question: {question[:90]}")
        reply = self.script.pop(0)
        print(f"    YOU {reply[:88]}")
        return reply

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


QUESTION = ("llm: Tell us something you have built or solved using AI that you are "
            "proud of. What problem does it solve?")
sess = ScriptedSession([
    "done",       # ready to start
    QUESTION,     # at the submit prompt: ask for a draft
    DRAFT_TEXT,   # the UI pre-fills the draft; pressing Enter sends it back
    "done",       # submitted by me
])
job = {"job_id": "llmsub:1", "company": "Airblack", "title": "Lead Backend Engineer",
       "description": "Build consumer AI.",
       "apply_url": (E2E / "fixture_submit_only.html").as_uri()}

worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)

print(f"\n  status  : {sess.status}")
print(f"  drafts  : {len(DRAFTS)}")

problems = []
if not DRAFTS:
    problems.append("the llm: request at the submit prompt produced no draft")
if not any(DRAFT_TEXT in line for line in sess.logs):
    problems.append("the draft never reached the transcript to be copied")
if not any("could not find that box" in line for line in sess.logs):
    problems.append("it did not say the box could not be found")
repeats = [q for q in sess.asked if "Everything I can fill is done" in q]
if len(repeats) > 2:
    problems.append(f"the submit prompt repeated {len(repeats)} times")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: the drafted answer came back at the submit prompt.")
print("scratch dir:", SCRATCH)
