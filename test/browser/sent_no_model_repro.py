"""A confirmation page must cost no model call.

From a real queued application:

    The page changed (a form opened or a new page loaded); reading it.
    Asking the model about 0 field(s)...
    Model returned 1 action(s).
    Agent reports the application is complete: Page shows 'Application
    status: Application submitted'

Fifteen to forty-five seconds, at the end of every successful application,
spent asking a model to read a heading. The page said so in plain English and
_looks_submitted already matched that wording - but the check only ran in the
branch for a page with NO fields, and a confirmation page keeps a Done button.

Fails before the fix (one model call), passes after (none).
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
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_sent_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

CALLS: list[str] = []


def fake_invoke(system, user, schema):
    CALLS.append(system[:20])
    raise AssertionError("the confirmation page should never reach the model")


worker.make_invoker = lambda cfg, env, purpose: fake_invoke


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260913T120000", "sent-1", "Katapult Junior Backend Developer")
        self.script = list(script)
        self.asked: list[str] = []
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)
        print(f"    LOG {text[:100]}")

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        print(f"    ASK {question[:80]}")
        if not self.script:
            raise AssertionError(f"unscripted question: {question}")
        reply = self.script.pop(0)
        print(f"    YOU {reply}")
        return reply

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


sess = ScriptedSession(["done"])          # only the "ready to start" prompt
job = {"job_id": "sent:1", "company": "Katapult", "title": "Junior Backend Developer",
       "description": "Build things.",
       "apply_url": (E2E / "fixture_sent_with_button.html").as_uri()}

worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)

print(f"\n  status      : {sess.status}")
print(f"  model calls : {len(CALLS)}")
print(f"  questions   : {sess.asked}")

problems = []
if CALLS:
    problems.append(f"the model was called {len(CALLS)} time(s) to read a confirmation page")
if sess.status != "applied":
    problems.append(f"status was {sess.status!r}, wanted 'applied'")
if not any("confirmed by the page" in l or "applied" in l.lower() for l in sess.logs + [sess.status]):
    problems.append("nothing recorded the application")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: the confirmation page was recognised with no model call.")
print("scratch dir:", SCRATCH)
