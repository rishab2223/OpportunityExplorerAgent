"""Saying "I submitted it" must record the application, at ANY prompt.

From a real session, after the candidate had submitted the form themselves:

    1322.9s  AGENT ASKS: Please enter the 6-digit confirmation code sent to
             your email/phone to continue the application.
    1351.9s  YOU: dump
             ... still waiting for your answer.

There was no way out that recorded the application. At a question about one
box every reply is typed into that box, so "done" would have gone into the
code field; abort - the only other exit - records nothing, so the candidate
had to abort and then press Mark applied on the row.

Drives a form whose last box the model asks about, and answers it the way the
candidate needed to.
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
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_isub_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.apply.worker import ApplyAction  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.invalid",
}), encoding="utf-8")


def fake_invoke(system, user, schema):
    if schema.__name__ == "DraftAnswer":
        return schema(text="a draft")
    fields = json.loads(user.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0])
    for f in fields:
        if "confirmation code" in (f.get("label") or "").lower():
            return schema(actions=[ApplyAction(
                action="ask", field_id=f["id"],
                question="Please enter the 6-digit confirmation code sent to your email.",
                reason="only the candidate has it", confidence=0.95, reusable=False)])
    return schema(actions=[])


worker.make_invoker = lambda cfg, env, purpose: fake_invoke


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260914T120000", "isub-1", "Airblack Lead Backend Engineer")
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
        print(f"    YOU {reply[:88]}")
        self.answer(reply)
        return super().ask(question, suggestion)

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


recorded: list[str] = []
sess = ScriptedSession(["done", "i submitted"])
sess.on_outcome = recorded.append
job = {"job_id": "isub:1", "company": "Airblack", "title": "Lead Backend Engineer",
       "description": "Build consumer AI.",
       "apply_url": (E2E / "fixture_otp.html").as_uri()}

worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)

print(f"\n  status   : {sess.status}")
print(f"  recorded : {recorded}")

problems = []
if sess.status != "applied":
    problems.append(f"status was {sess.status!r}, wanted 'applied'")
if recorded != ["applied"]:
    problems.append(f"the outcome callback got {recorded}, wanted ['applied']")
if not any("confirmation code" in q for q in sess.asked):
    problems.append("the confirmation-code prompt never appeared, so nothing was proven")

if problems:
    print("\nREPRODUCED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("\nFIXED: 'i submitted' recorded the application from the code prompt.")
print("scratch dir:", SCRATCH)
