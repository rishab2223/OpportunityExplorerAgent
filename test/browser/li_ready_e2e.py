"""A LinkedIn job page needs no "Ready to start?" keystroke.

Two things this proves that the unit tests cannot, because they stub the
browser: that a closed posting is recognised and recorded with the candidate
asked NOTHING at all, and that a card which renders late is still waited for
rather than missed.

sites.detect keys off the hostname, so a file:// fixture is forced down the
LinkedIn branch here. Everything else is real: real Chrome, real snapshot,
real timing. Isolated scratch profile and DB; nothing is submitted.
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

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_li_"))

from src import history  # noqa: E402
from src.apply import browser, profile, session as apply_session, sites, worker  # noqa: E402
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
# Force the LinkedIn branch for a file:// fixture.
worker.sites.detect = lambda url: "linkedin"


class ScriptedSession(apply_session.ApplySession):
    """Answers from a script, and records every question it was asked."""

    def __init__(self, script):
        super().__init__("20260913T120000", "li-1", "DummyCo Software Engineer")
        self.script = list(script)
        self.asked: list[str] = []
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)
        print(f"    LOG {text[:90]}")

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


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        raise AssertionError(f"{label} {detail}")


def run(name, url, script):
    print(f"\n== {name} ==")
    sess = ScriptedSession(script)
    job = {"job_id": f"li:{name}", "company": "DummyCo", "title": "Software Engineer",
           "description": "Build things.", "apply_url": url}
    worker.run_session(sess, job, "dummy resume", AppConfig(), load_env(),
                       headless=True, out_dir=SCRATCH)
    return sess


# ---- a closed posting: recognised late-rendering, recorded, nothing asked ----
closed = run("closed-posting", (E2E / "fixture_li_closed.html").as_uri(), [])
check("the candidate was asked nothing at all", closed.asked == [], str(closed.asked))
check("it was recorded as closed", closed.status == "closed", closed.status)
check("the banner was found, late render and all",
      any("no longer accepting" in line.lower() for line in closed.logs), str(closed.logs))
check("no 'Ready to start' anywhere",
      not any("ready to start" in q.lower() for q in closed.asked), str(closed.asked))

# ---- a live posting: the apply button is clicked with no keystroke first ----
live = run("live-posting", (E2E / "fixture_listing.html").as_uri(), ["done"])
check("no 'Ready to start' was asked",
      not any("ready to start" in q.lower() for q in live.asked), str(live.asked))
check("the apply control was followed without being asked first",
      any("external apply" in line.lower() or "switched to" in line.lower()
          for line in live.logs), str(live.logs)[:400])
check("it reached the form and handed back once", live.status == "applied", live.status)

print("\nLINKEDIN READY-STEP CHECK PASSED")
print("scratch dir:", SCRATCH)
