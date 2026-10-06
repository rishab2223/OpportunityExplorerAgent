"""A page still drawing is not a reason to call the model.

15 of 58 model calls in the real logs were about zero fields - nothing to
fill and nothing the script could press - at a median 7.1 s each. Indeed's
contact step was one: its Continue button arrived while the model was being
asked to find it. fixture_late_continue.html is that step, with Continue
appearing 1.2 s after the boxes. The model is disabled here, so any call
fails the session; the agent must get to "type next to click 'Continue'"
on its own.

Scratch profile, temp DB. Nothing real is read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_late_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")

calls = []


def _no_model(system, user, schema):
    calls.append(user)
    raise RuntimeError("the model was called")


worker.make_invoker = lambda cfg, env, purpose: _no_model


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260918T120000", "late-continue", "Example Co Engineer")
        self.script = list(script)
        self.asked: list[str] = []
        self.lines: list[str] = []

    def log(self, text):
        self.lines.append(text)

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        if not self.script:
            raise apply_session.Aborted("unscripted question")
        return self.script.pop(0)


sess = ScriptedSession([])       # the first question ends the session
job = {"job_id": "late:1", "company": "Example Co", "title": "Engineer",
       "description": "Build things.", "apply_url": (HERE / "fixture_late_continue.html").as_uri()}
worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                   out_dir=SCRATCH)

reached = any("click 'Continue'" in q for q in sess.asked)
print("  asked :", [q[:60] for q in sess.asked])
print("  model calls:", len(calls))
ok = reached and not calls
print(("  PASS" if reached else "  FAIL") + "  the step reaches 'type next to click Continue'")
print(("  PASS" if not calls else "  FAIL") + "  without asking the model to find the button")
print()
if not ok:
    print("LATE CONTINUE CHECK FAILED")
    sys.exit(1)
print("LATE CONTINUE CHECK PASSED")
