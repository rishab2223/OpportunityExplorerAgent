"""Wording that means "submitted" but was there all along must not record
the job as applied - and the real confirmation, arriving later, must.

Two of today's changes compounded into a false positive: the submitted check
began scanning the WHOLE page (a confirmation below the fold had been missed)
and began running un-gated at the top of every loop iteration (a site reset
its form after submitting and the agent re-typed into it). Together, a job
description ending "thank you for your application" - which Indian postings
carry as a matter of course - would have recorded the job as applied on the
first read, before a box was filled, and closed the browser.

This page has that sentence in its description AND "once your application has
been submitted" in its form hint, from load. The real confirmation card
arrives 3.5s later. Expected: the fills happen first, the model is never
called, and the session ends on the card.
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
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_boiler_"))

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
    raise AssertionError("nothing on this page needs the model")


worker.make_invoker = lambda cfg, env, purpose: fake_invoke


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260914T120000", "boiler-1", "Acme Senior Software Engineer")
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
        if not self.script:
            raise AssertionError(f"unscripted question: {question[:90]}")
        reply = self.script.pop(0)
        print(f"    YOU {reply}")
        if reply != "__wait__":
            self.answer(reply)
        # "__wait__": answer nothing. The hand-off prompt is watched, and the
        # card appearing is what ends the wait (PageChanged from idle_tick).
        return super().ask(question, suggestion)

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


sess = ScriptedSession(["done", "__wait__"])
job = {"job_id": "boiler:1", "company": "Acme", "title": "Senior Software Engineer",
       "description": "Build things.",
       "apply_url": (E2E / "fixture_boilerplate.html").as_uri()}

worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)

filled = [l for l in sess.logs if "[profile] Filled" in l]
confirm = [l for l in sess.logs if "confirms the application was sent" in l]
print(f"\n  status      : {sess.status}")
print(f"  filled      : {len(filled)}")
print(f"  model calls : {len(CALLS)}")
print(f"  confirmed on: {confirm[-1][:90] if confirm else '-'}")

problems = []
if not filled:
    problems.append("nothing was filled: the boilerplate was taken as a confirmation on the first read")
if sess.status != "applied":
    problems.append(f"status was {sess.status!r}, wanted 'applied' from the card that arrived later")
if CALLS:
    problems.append(f"the model was called {len(CALLS)} time(s)")
if confirm and "sent to acme" not in confirm[-1].lower():
    problems.append(f"the wrong sentence was taken as the confirmation: {confirm[-1]!r}")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: boilerplate ignored, the real confirmation seen.")
print("scratch dir:", SCRATCH)
