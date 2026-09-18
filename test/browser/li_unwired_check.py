"""The LinkedIn apply button is on screen before it works.

From the logs of 26 real openings: the first click went through cleanly and
opened nothing in 25 of them, and the agent then waited four seconds for a
flow that was never coming before it tried again. External applies then sat
a further fixed three seconds after the employer's tab had opened. Together:
a median 8.8 s from "the job card has finished loading" to the form, with
the button visibly there the whole time.

Both variants run a whole session on fixture_li_unwired.html, whose button
appears at 0.3 s and does nothing until 1.8 s. Times are read off the
session's own log lines, so Chrome's start-up is not in them.

Scratch profile, temp DB, no model. Nothing real is read or written.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_unwired_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, worker  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({"full_name": "Test User"}), encoding="utf-8")


def _no_model(system, user, schema):
    raise AssertionError("this check must not need the model")


worker.make_invoker = lambda cfg, env, purpose: _no_model
worker.sites.detect = lambda url: "linkedin"
failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260918T120000", "li-unwired", "Example Co Backend Engineer")
        self.script = list(script)
        self.lines: list[tuple[float, str]] = []

    def log(self, text):
        self.lines.append((time.monotonic(), text))

    def ask(self, question, suggestion=""):
        if not self.script:
            raise AssertionError(f"unscripted question: {question}")
        return self.script.pop(0)

    def at(self, words):
        return next((t for t, line in self.lines if words in line), None)


for mode in ("easy", "external"):
    print(f"\n{mode}")
    sess = ScriptedSession(["done"])
    url = (HERE / "fixture_li_unwired.html").as_uri() + "#" + mode
    job = {"job_id": f"li:{mode}", "company": "Example Co", "title": "Backend Engineer",
           "description": "Build things.", "apply_url": url}
    worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                       headless=True, out_dir=SCRATCH)
    loaded, opened = sess.at("job card has finished loading"), sess.at("The apply flow opened")
    handed = sess.at("application form is open") or sess.at("External apply")
    check("the session completes", sess.status == "applied", sess.status)
    check("the name is filled", any("[profile] Filled Full name" in t for _, t in sess.lines))
    if loaded and opened and handed:
        # Old code: the first window alone was 4 s, so at least ~4.5 s here.
        check("the retry comes quickly once the first click does nothing",
              opened - loaded < 3.5, f"{opened - loaded:.1f}s card-loaded to flow-open")
        if mode == "external":
            # Old code: a fixed 3 s here, whatever the employer's page was doing.
            check("and the employer's tab is not waited on by the clock",
                  handed - opened < 1.5, f"{handed - opened:.1f}s flow-open to hand-over")
    else:
        check("the three milestones were logged", False, f"{loaded} {opened} {handed}")

print()
if failures:
    print("LINKEDIN UNWIRED CHECK FAILED:", ", ".join(failures))
    sys.exit(1)
print("LINKEDIN UNWIRED CHECK PASSED")
