"""A submitted application must not pay for the page behind its confirmation.

From a real UbiqEdge application, the last fourteen seconds of it:

    116.9s  Asking the model about 1 field(s)...
    122.9s  Model returned 0 action(s).
            A dialog is open but its form is still loading; waiting...
    130.4s  Model calls this session: 2

LinkedIn's "Your application was sent" card is a role=dialog with no form
controls, and the page under it has grown controls of its own (a follow-up
question, the search boxes). Both facts cost time: no controls in the dialog
looks like a form that has not loaded, so the loop waited 4 x 1.5s; and the
controls behind it meant "nothing left unresolved" was false, so a model call
was spent on a page that had already said the application was sent.

Modelled in the order it happens: the form is filled, the candidate presses
Submit while the agent waits at the hand-off (a timer stands in), and the card
appears. Expected: applied, no model call, no waiting for a form to load.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = E2E = Path(__file__).resolve().parent
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_sentdlg_"))

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

CALLS: list[str] = []


def fake_invoke(system, user, schema):
    CALLS.append(system[:20])
    raise AssertionError("a submitted application should never reach the model")


worker.make_invoker = lambda cfg, env, purpose: fake_invoke


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260914T120000", "sentdlg-1", "UbiqEdge Senior Software Engineer")
        self.script = list(script)
        self.asked: list[str] = []
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)
        super().log(text)
        print(f"  {self._events[-1]['at']:6.1f}s  {text[:96]}")

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        print(f"    ASK {question[:80]}")
        if not self.script:
            raise AssertionError(f"unscripted question: {question}")
        reply = self.script.pop(0)
        print(f"    YOU {reply}")
        if reply != "__wait__":
            self.answer(reply)
        # "__wait__": answer nothing; the card appearing ends the wait.
        return super().ask(question, suggestion)

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


sess = ScriptedSession(["done", "__wait__"])
job = {"job_id": "sentdlg:1", "company": "UbiqEdge", "title": "Senior Software Engineer",
       "description": "Build things.",
       "apply_url": (E2E / "fixture_sent_dialog.html").as_uri()}

started = time.time()
worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)
elapsed = time.time() - started

print(f"\n  status      : {sess.status}")
print(f"  model calls : {len(CALLS)}")
print(f"  elapsed     : {elapsed:.1f}s")

problems = []
if CALLS:
    problems.append(f"the model was called {len(CALLS)} time(s) after the application was sent")
if sess.status != "applied":
    problems.append(f"status was {sess.status!r}, wanted 'applied'")
if any("still loading" in line for line in sess.logs):
    problems.append("waited for a form to load inside the confirmation dialog")
if not any("sent to ubiqedge" in line.lower() for line in sess.logs):
    problems.append("the transcript does not name the sentence it acted on")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: the confirmation was recognised through the dialog, with no model call.")
print("scratch dir:", SCRATCH)
