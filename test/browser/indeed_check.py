"""An Indeed job opens its application with no question and no model call.

What a real session did instead: "Ready to start? Type done" went unanswered
for three minutes with the Apply button on screen, and once answered the
model was paid 9 s to find the button, the page's only boxes being Indeed's
own What / Where search. Both kinds of job page are run here - Indeed's own
form, and "Apply on company site" in a new tab - on a replica whose buttons
do nothing for their first 0.8 s, and with the model disabled.

Scratch profile, temp DB. Nothing real is read or written.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_indeed_"))

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
worker.sites.detect = lambda url: "indeed"
failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class ScriptedSession(apply_session.ApplySession):
    def __init__(self):
        super().__init__("20260918T120000", "indeed", "Example Co Software Developer")
        self.asked: list[str] = []
        self.lines: list[str] = []

    def log(self, text):
        self.lines.append(text)

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        raise apply_session.Aborted("the first question ends the check")


for mode, how in (("indeed", "navigated"), ("company", "new tab")):
    print(f"\n{mode}")
    calls.clear()
    sess = ScriptedSession()
    job = {"job_id": f"indeed:{mode}", "company": "Example Co", "title": "Software Developer",
           "description": "Build things.",
           "apply_url": (HERE / "fixture_indeed_job.html").as_uri() + "#" + mode}
    worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(), headless=True,
                       out_dir=SCRATCH)
    check("no 'Ready to start?'", not any("Ready to start" in q for q in sess.asked))
    check(f"the application opened ({how})",
          any(f"[indeed] The application opened ({how})" in line for line in sess.lines),
          next((line for line in sess.lines if line.startswith("[indeed]")), ""))
    check("its first question is the filled-in step, with Continue",
          bool(sess.asked) and "click 'Continue'" in sess.asked[0],
          sess.asked[0][:60] if sess.asked else "nothing asked")
    check("and the model was never asked", not calls, f"{len(calls)} call(s)")

print()
if failures:
    print("INDEED CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("INDEED CHECK PASSED")
