"""Shared rig for the end-to-end apply-flow scripts.

Split out because run_e2e was the sweep's critical path by a distance: 132s of
a 152s run, 23 scenarios one after another, ~1.3s of Chrome startup each. As
one script the pool could not overlap any of it. The scenarios live in
run_e2e.py and run_e2e_b.py, which import this and run side by side.

The seam is at scenario 12. Only three things cross a scenario boundary and
all stay whole: easy-pass1 -> easy-pass2 (cold bank then warm), the
answers.remember() that seeds "rerender", and the ASK_BEFORE_ADVANCE toggle
around "confirm-next". Nothing in 13-22 reads what 1-12 banked - checked
before splitting, and the suites prove it on every run.

Everything isolated: scratch Chrome profile, scratch profile JSON, scratch
history DB. Nothing real is opened, filled, stored or submitted.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

# Windows consoles default to cp1252: printing "Karnātaka" in a log line raised
# inside the worker and read as a failed fill.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = E2E = Path(__file__).resolve().parent
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

SCRATCH = Path(tempfile.mkdtemp(prefix="oea_e2e_"))

from src import answers, history  # noqa: E402
from src.apply import browser, profile, worker  # noqa: E402
from src.apply.worker import ApplyAction, ApplyPlan  # noqa: E402
from src.config import AppConfig, load_env  # noqa: E402

# ---- isolation ----
history.DB_PATH = SCRATCH / "history.db"
profile.PROFILE_PATH = SCRATCH / "apply_profile.json"
browser.CHROME_PROFILE_DIR = SCRATCH / "chrome-profile"
profile.PROFILE_PATH.write_text(json.dumps({
    "full_name": "Test User",
    "email": "test@example.invalid",
    "phone": "+91 00000 00000",
    "location": "Bangalore, India",
    "notice_period": "60 days",
    "expected_ctc": "30 LPA",
    "state": "Karnataka",
    "highest_education_level": "Bachelors",
    "field_of_study": "Computer and Information Science",
    "languages": "English - Intermediate; Hindi - Fluent",
    "linkedin": "https://linkedin.com/in/test",
    "github": "https://github.com/test",
    "skills": "JavaScript, Node.js, Python, AWS, AWS SDK",
}), encoding="utf-8")
PDF = SCRATCH / "dummy_resume.pdf"
PDF.write_bytes(b"%PDF-1.4 dummy resume for e2e\n%%EOF\n")
DEFAULT_PDF = SCRATCH / "default_resume.pdf"
DEFAULT_PDF.write_bytes(b"%PDF-1.4 default resume\n%%EOF\n")

LLM_CALLS: list[str] = []


def fake_invoke(system: str, user: str, schema):
    """Deterministic stand-in for the model."""
    LLM_CALLS.append(system[:24])
    if schema.__name__ == "CoverLetter":
        if "revise" in system.lower():
            return schema(text="Revised letter body.")
        return schema(text="Drafted letter body.")
    if schema.__name__ == "DraftAnswer":
        # "llm: <the question>" in the chat. The question is echoed back so a
        # scenario can prove the draft went to the box it named.
        question = user.split("FORM QUESTION:\n", 1)[1].split("\n\n", 1)[0]
        return schema(text=f"Drafted answer to: {question[:60]}")
    fields = json.loads(user.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0])
    actions = []
    # The worker's note after a refused typeahead pick names the suggestions;
    # a real model reads it and picks one of them next round.
    fos_suggested = "matches none of the suggestions for 'Field of Study'" in user
    for f in fields:
        label = (f.get("label") or "").lower()
        text = (f.get("text") or "").lower()
        if f.get("already_handled"):
            continue  # the prompt marks these; a real model leaves them alone
        if ("favourite programming language" in label or "favourite editor" in label
                or "permanent account number" in label) and not f.get("value"):
            actions.append(ApplyAction(action="ask", field_id=f["id"],
                                       question="", reason="unknown", confidence=0.9,
                                       reusable=True))
        elif f.get("type") == "radio" and f.get("label") == "No" \
                and "sponsor" in (f.get("group") or "").lower() and not f.get("checked"):
            actions.append(ApplyAction(action="check", field_id=f["id"], confidence=0.95))
        elif f.get("tag") == "a" and "apply for this job" in text:
            actions.append(ApplyAction(action="click", field_id=f["id"], confidence=0.95))
        elif text == "apply manually":
            # The real model's (unwanted) choice on Workday; must be gated.
            actions.append(ApplyAction(action="click", field_id=f["id"], confidence=0.95))
        elif f.get("tag") == "button" and f.get("haspopup") == "listbox" \
                and "phone device type" in label and (f.get("text") or "") == "Select One":
            actions.append(ApplyAction(action="select", field_id=f["id"], value="Mobile", confidence=0.95))
        # Repeating section (My Experience): the model sees section="Work
        # Experience" on the Add button and on the revealed entry's fields.
        elif f.get("tag") == "button" and text == "add" and f.get("section") == "Work Experience":
            # Like a real model: Add only while no entry is open (the resume
            # here has one job), never again once its fields are on the page.
            if not any(g.get("section") == "Work Experience" and g.get("tag") in ("input", "textarea")
                       for g in fields):
                actions.append(ApplyAction(action="click", field_id=f["id"], confidence=0.95))
        elif label == "type to add skills":
            for skill in ("JavaScript", "Node.js", "Python"):
                actions.append(ApplyAction(action="fill", field_id=f["id"], value=skill, confidence=0.95))
        elif label == "how did you hear about us?*" and not f.get("value"):
            actions.append(ApplyAction(action="fill", field_id=f["id"], value="Linkedin", confidence=0.95))
        elif label == "field of study" and f.get("section") == "Education":
            value = "Computer and Information Science" if fos_suggested else "Computer Science"
            actions.append(ApplyAction(action="fill", field_id=f["id"], value=value, confidence=0.95))
        elif label == "month" and f.get("group") == "From" and f.get("section") == "Work Experience" and not f.get("value"):
            actions.append(ApplyAction(action="fill", field_id=f["id"], value="07", confidence=0.95))
        elif label == "year" and f.get("group") == "From" and f.get("section") == "Work Experience" and not f.get("value"):
            actions.append(ApplyAction(action="fill", field_id=f["id"], value="2020", confidence=0.95))
        # Languages: one entry per profile language, Add Another between them,
        # nothing once both are in (an empty plan = the section is done).
        elif f.get("section") == "Languages" and f.get("tag") == "button" and text.startswith("add"):
            langs = [g for g in fields if g.get("section") == "Languages" and g.get("tag") == "select"
                     and "language" in (g.get("label") or "").lower()]
            if len(langs) < 2 and all(g.get("value") for g in langs):
                actions.append(ApplyAction(action="click", field_id=f["id"], confidence=0.95))
        elif f.get("section") == "Languages" and f.get("tag") == "select" and not f.get("value"):
            if "language" in label:
                filled = [g.get("value") for g in fields if g.get("section") == "Languages"
                          and g.get("tag") == "select" and "language" in (g.get("label") or "").lower()
                          and g.get("value")]
                nxt = "English" if "English" not in filled else "Hindi"
                actions.append(ApplyAction(action="select", field_id=f["id"], value=nxt, confidence=0.95))
            else:
                actions.append(ApplyAction(action="select", field_id=f["id"], value="Fluent", confidence=0.95))
        elif f.get("section") == "Work Experience" and f.get("type") == "checkbox":
            if not f.get("checked"):
                actions.append(ApplyAction(action="check", field_id=f["id"], confidence=0.95))
        elif f.get("section") == "Work Experience" and not f.get("value"):
            entry = {"job title": "Backend Engineer", "company": "Acme", "location": "Noida",
                     "role description": "Built APIs."}
            key = label.rstrip("*").strip()
            if key in entry:
                actions.append(ApplyAction(action="fill", field_id=f["id"], value=entry[key], confidence=0.95))
            elif label == "month" and f.get("group") == "From":
                actions.append(ApplyAction(action="fill", field_id=f["id"], value="03", confidence=0.95))
            elif label == "year" and f.get("group") == "From":
                actions.append(ApplyAction(action="fill", field_id=f["id"], value="2021", confidence=0.95))
    return ApplyPlan(actions=actions)


worker.make_invoker = lambda cfg, env, purpose: fake_invoke
# Most scenarios exercise the wizard without a human at each step.
worker.ASK_BEFORE_ADVANCE = False

RESUME_OPTIONS = {
    "tailored": {"path": str(PDF), "error": "", "changelog": "tightened the summary",
                 "source": "\\documentclass{article}\\begin{document}x\\end{document}",
                 "pages": 1},
    "default": {"path": str(DEFAULT_PDF), "error": ""},
}


from src.apply.session import ApplySession


class ScriptedSession(ApplySession):
    """The REAL session (emit/ask/ask_choice/finish all run for real â€” a pure
    stub hid the emit() 'kind' collision that crashed a live resume modal);
    scripted answers are enqueued just before each blocking wait."""

    def __init__(self, answers_queue):
        super().__init__("20260101T000000", "test:e2e", "DummyCo")
        self.queue = list(answers_queue)
        self.logs: list[str] = []
        self.asked: list[str] = []
        self.choices: list[str] = []

    def log(self, text):
        self.logs.append(text)
        # Elapsed, because this script is the sweep's critical path and "which
        # step is slow" is otherwise a guess. Printed only; `logs` is the
        # unchanged text every assertion reads.
        print(f"    {time.time() - getattr(self, 't0', time.time()):6.1f}s LOG {text}")
        super().log(text)

    def _feed(self, label):
        if not self.queue:
            raise AssertionError(f"unexpected {label}")
        answer = self.queue.pop(0)
        print(f"    {label}\n    YOU {answer[:60]}")
        self.answer(answer)

    def ask(self, question, suggestion=""):
        self.asked.append(question)
        # "__wait__": the user types nothing - they act on the page instead;
        # the real wait blocks until the worker's page watch ends it.
        if self.queue and self.queue[0] == "__wait__":
            self.queue.pop(0)
            print(f"    ASK {question[:70]}\n    YOU (silent - acting on the page)")
            return super().ask(question, suggestion)
        self._feed(f"ASK {question[:70]}")
        return super().ask(question, suggestion)

    def ask_choice(self, kind, text, meta=None):
        self.choices.append(kind)
        self._feed(f"CHOICE[{kind}] {text[:50]}")
        return super().ask_choice(kind, text, meta)

    def finish(self, status, text=""):
        print(f"    FINISH {status}: {text}")
        super().finish(status, text)


TIMES: list[tuple[str, float]] = []


def run(name, url, script, expect_calls, expect_status="applied"):
    print(f"\n== {name} ==")
    started = time.time()
    LLM_CALLS.clear()
    sess = ScriptedSession(script)
    sess.t0 = started
    job = {"job_id": f"test:{name}", "company": "DummyCo",
           "title": "Software Engineer", "description": "Build things."}
    job["apply_url"] = url
    worker.run_session(sess, job, "dummy resume text", AppConfig(), load_env(),
                       headless=True, resume_options=lambda: RESUME_OPTIONS,
                       out_dir=SCRATCH)
    assert sess.status == expect_status, f"status {sess.status}, wanted {expect_status}"
    assert len(LLM_CALLS) == expect_calls, f"{len(LLM_CALLS)} LLM calls, wanted {expect_calls}"
    assert not sess.queue, f"unused scripted answers: {sess.queue}"
    TIMES.append((name, time.time() - started))
    return sess


easy_url = (E2E / "fixture_easy.html").as_uri()
listing_url = (E2E / "fixture_listing.html").as_uri()
# Used by scenario 11 (run_e2e) and scenario 17 (run_e2e_b).
workday_url = (E2E / "fixture_workday.html").as_uri()
