"""Reproduce the Katapult failure: an apply button replaced mid-click.

Real log from a real application:

    Error: Locator.click: Timeout 15000ms exceeded.
      - locator resolved to <a data-oea-id="8" aria-label="Easy Apply to this job" ...>
      - attempting click action
        - element is not stable
      - retrying click action
        - element was detached from the DOM, retrying

Run before the fix to see it fail, after to see it pass.
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
SCRATCH = Path(tempfile.mkdtemp(prefix="oea_rr_"))

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


def _no_model(system, user, schema):
    raise AssertionError("this check must not need the model")


worker.make_invoker = lambda cfg, env, purpose: _no_model
worker.sites.detect = lambda url: "linkedin"


class ScriptedSession(apply_session.ApplySession):
    def __init__(self, script):
        super().__init__("20260913T120000", "li-rr", "Katapult Junior Backend Developer")
        self.script = list(script)
        self.asked: list[str] = []
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)
        print(f"    LOG {text[:110]}")

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        print(f"    ASK {question[:80]}")
        if not self.script:
            raise AssertionError(f"unscripted question: {question}")
        reply = self.script.pop(0)
        print(f"    YOU {reply}")
        return reply

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text[:110]}")
        super().finish(status, text)


url = (E2E / "fixture_li_rerender.html").as_uri()
sess = ScriptedSession(["done"])
job = {"job_id": "li:rerender", "company": "Katapult", "title": "Junior Backend Developer",
       "description": "Build things.", "apply_url": url}

started = time.time()
worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                   headless=True, out_dir=SCRATCH)
elapsed = time.time() - started

print(f"\n  status  : {sess.status}")
print(f"  elapsed : {elapsed:.1f}s")
failed = sess.status == "failed"
stale = any("not stable" in line or "detached" in line for line in sess.logs)
print(f"  stale-click failure: {failed or stale}")

if failed or stale:
    print("\nREPRODUCED: the replaced apply button broke the session.")
    sys.exit(1)

print("  filled  :", [l for l in sess.logs if "[profile]" in l])
assert sess.status == "applied", sess.status
assert any("[profile] Filled Full name" in l for l in sess.logs), sess.logs
print("\nFIXED: the apply button was clicked despite being replaced twice.")
print("scratch dir:", SCRATCH)
