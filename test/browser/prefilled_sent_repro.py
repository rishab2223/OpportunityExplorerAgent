"""A LinkedIn Easy Apply that LinkedIn had filled in entirely (Scitara,
Oct 2026): the agent typed nothing, the candidate pressed Submit, and the job
page came back with a search box and "Your application was sent to
Scitara!". With nothing typed, the session counted as untouched, the new
page's sentence went into the baseline as page furniture, and the agent said
it could see nothing confirming the application.

Expected: applied, from that sentence, with no model call.
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
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_prefilled_"))

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
    if schema.__name__ == "SalaryEstimate":
        # The background expected-pay estimate (started once a form is
        # reached) is not a read of the page: not counted, and none to give.
        raise RuntimeError("the stand-in model gives no salary estimate")
    CALLS.append(system[:20])
    raise AssertionError("a submitted application should never reach the model")


worker.make_invoker = lambda cfg, env, purpose: fake_invoke
worker.open_claude_session = lambda cfg, log=None: None   # no real Claude process


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260914T120000", "prefilled-1", "Scitara Platform Engineer")
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


sess = ScriptedSession(["__wait__"])
job = {"job_id": "prefilled:1", "company": "Scitara", "title": "Platform Engineer",
       "description": "Build things.",
       "apply_url": (E2E / "fixture_prefilled_form.html").as_uri()}

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
if not any("sent to scitara" in line.lower() for line in sess.logs):
    problems.append("the transcript does not name the sentence it acted on")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: the confirmation after a pre-filled form was recognised, with no model call.")
print("scratch dir:", SCRATCH)
