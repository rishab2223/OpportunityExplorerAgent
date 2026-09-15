"""End-to-end apply-flow check against local dummy forms. Everything isolated:
scratch Chrome profile, scratch profile JSON, scratch history DB. Nothing real
is opened, filled, stored or submitted."""

from __future__ import annotations

import json
import sys
import tempfile
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
        print(f"    LOG {text}")
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


def run(name, url, script, expect_calls, expect_status="applied"):
    print(f"\n== {name} ==")
    LLM_CALLS.clear()
    sess = ScriptedSession(script)
    job = {"job_id": f"test:{name}", "company": "DummyCo",
           "title": "Software Engineer", "description": "Build things."}
    job["apply_url"] = url
    worker.run_session(sess, job, "dummy resume text", AppConfig(), load_env(),
                       headless=True, resume_options=lambda: RESUME_OPTIONS,
                       out_dir=SCRATCH)
    assert sess.status == expect_status, f"status {sess.status}, wanted {expect_status}"
    assert len(LLM_CALLS) == expect_calls, f"{len(LLM_CALLS)} LLM calls, wanted {expect_calls}"
    assert not sess.queue, f"unused scripted answers: {sess.queue}"
    return sess


easy_url = (E2E / "fixture_easy.html").as_uri()
listing_url = (E2E / "fixture_listing.html").as_uri()

# Pass 1: cold bank. Resume choice + cover letter (one revision) + one plan call.
s1 = run("easy-pass1", easy_url, [
    "done",                          # ready
    "tailored",                      # resume choice modal
    "__revise__ make it shorter",    # cover letter: ask for changes
    "__use__\nMy own final wording.",  # cover letter: hand-edited, accepted
    "Python",                        # favourite language (model asks)
    "No",                            # sponsorship group question
    "yes",                           # remember the sensitive answer?
    "done",                          # hand-off: user submitted
], expect_calls=3)  # letter draft + revise + one batched plan
logs1 = "\n".join(s1.logs)
assert "[profile] Filled Full name = Test User" in logs1, logs1
assert "Compiling the tailored resume" in logs1
assert "Using the tailored resume" in logs1
assert "[resume] Uploaded dummy_resume.pdf" in logs1, logs1
# resume once; the letter modal reopens after the revision round.
assert s1.choices == ["resume", "cover_letter", "cover_letter"], s1.choices
assert "Everything I can fill is done" in s1.asked[-1]

# The hand-edited letter reached the textarea, and nothing letter-shaped was banked.
assert answers.recall("Favourite programming language")["answer"] == "Python"
assert answers.recall("Cover letter") is None
assert answers.recall("Do you need visa sponsorship?")["answer"] == "No"

# Pass 2: warm bank. Resume remembered? No - new session, so it asks again.
# Letter is redrafted (per job), accepted as-is with no revision.
s2 = run("easy-pass2", easy_url, [
    "done",
    "default",                    # this time pick the default resume
    "__use__\nDrafted letter body.",
    "done",
], expect_calls=2)  # letter draft + one plan call to nominate the radio option
                    # (the bank answers it inside the gate, without asking you)
logs2 = "\n".join(s2.logs)
assert "Using the default resume" in logs2, logs2
assert "[resume] Uploaded default_resume.pdf" in logs2, logs2
assert "[saved] Filled Favourite programming language = Python" in logs2, logs2
assert all("sponsorship" not in q.lower() for q in s2.asked), s2.asked

# The same form inside an iframe (ALTEN's talentrecruit page embeds the whole
# application from another origin): the top document has no fields, the
# frame does, and everything - fills, the upload, the letter - works there.
iframe_url = (E2E / "fixture_iframe.html").as_uri()
s2b = run("iframe", iframe_url, ["done", "default", "__use__\nDrafted letter body.", "done"], expect_calls=2)
logs2b = "\n".join(s2b.logs)
assert "[profile] Filled Full name = Test User" in logs2b, logs2b
assert "[resume] Uploaded default_resume.pdf" in logs2b, logs2b
assert "[saved] Filled Favourite programming language = Python" in logs2b, logs2b
assert not any("cannot see any form fields" in q for q in s2b.asked), s2b.asked

# Skipping both attachments leaves the fields untouched; then the "cover
# letter" command at the SUBMIT HAND-OFF prompt reopens the letter modal
# (typing it there used to loop the same prompt forever).
s3 = run("easy-skip", easy_url, [
    "done", "skip", "skip",
    "cover letter",                # at the hand-off: must open the modal
    "__use__\nLate letter.",       # accept -> saved to the run folder
    "done",
], expect_calls=2)  # letter drafted once + one plan call; the reopen is free
logs3 = "\n".join(s3.logs)
assert "Resume upload skipped" in logs3, logs3
assert "Cover letter skipped" in logs3, logs3
assert "Cover letter ready" in logs3, logs3
assert (SCRATCH / "cover_DummyCo_Software_Engineer.tex").exists(), "letter not saved"

# External path: listing -> new tab -> employer form (no attachments there).
s4 = run("external", listing_url, ["done", "done"], expect_calls=1)
logs4 = "\n".join(s4.logs)
assert "Clicked Apply for this job" in logs4
assert "Switched to" in logs4 and "fixture_form" in logs4

# Modal scoping: the form lives in a role=dialog while 70 background buttons
# bust the MAX_FIELDS budget (the LinkedIn Easy Apply failure shape). The
# snapshot must scope to the dialog: profile fills the field, no model call,
# and no background junk is ever touched.
modal_url = (E2E / "fixture_modal.html").as_uri()
s5 = run("modal", modal_url, ["done", "done"], expect_calls=0)
logs5 = "\n".join(s5.logs)
assert "[profile] Filled Full name = Test User" in logs5, logs5
assert "Junk" not in logs5, logs5

# Greenhouse-style tiles: an "Attach" button whose only clue is the "Resume/CV"
# heading above it. Detection must fire the resume modal (no model call), arm
# the file picker, and tell the user to click the tile; the letter tile gets a
# hint, never an auto-draft.
greenhouse_url = (E2E / "fixture_greenhouse.html").as_uri()
# Real Greenhouse: VISIBLE file inputs labelled "Attach", told apart only by
# id. The resume goes straight into its input (no tile clicking), the letter
# input drafts a letter (1 call) which is skipped here, and the tiles stay
# silent for the resume (already attached) but hint for the skipped letter.
s6 = run("greenhouse", greenhouse_url, ["done", "tailored", "skip", "done"], expect_calls=1)
logs6 = "\n".join(s6.logs)
assert s6.choices == ["resume", "cover_letter"], s6.choices
assert "[resume] Uploaded dummy_resume.pdf to Attach" in logs6, logs6
assert "Now click 'Attach'" not in logs6, logs6
# Skipped once is skipped for the form: the tile must not turn round
# and offer to draft the letter that was just turned down.
assert "Cover letter skipped" in logs6, logs6
assert logs6.count("Drafting a cover letter") == 1, logs6
assert s6.choices.count("cover_letter") == 1, s6.choices
assert "[profile] Filled Full name = Test User" in logs6, logs6

# Ceipal-style Easy Apply: the page shows only Apply Now / Easy Apply; the
# agent hands off, the USER opens the popup (timer here), and the watch ends
# the wait so the popup's fields get filled - no clicking by the agent.
popup_url = (E2E / "fixture_popup.html").as_uri()
s7 = run("popup", popup_url, ["done", "__wait__", "done"], expect_calls=0)
logs7 = "\n".join(s7.logs)
assert "The page changed" in logs7, logs7
assert "[profile] Filled Full name = Test User" in logs7, logs7
assert "[profile] Filled City = Bangalore" in logs7, logs7
assert "Clicked" not in logs7, logs7  # the agent clicked nothing
assert "Everything I can fill is done" in s7.asked[-1] and "Submit application" in s7.asked[-1], s7.asked

# LinkedIn Easy Apply race: the modal shell is up, its fields come 2.5s later.
# The agent must wait for the form instead of reading the page behind the
# modal (where it used to ask the model, which clicked the blocked background
# "Easy Apply" button and scrolled the page under the popup).
late_url = (E2E / "fixture_latemodal.html").as_uri()
s8 = run("late-modal", late_url, ["done", "done"], expect_calls=0)
logs8 = "\n".join(s8.logs)
assert "still loading; waiting" in logs8, logs8
assert "[profile] Filled Full name = Test User" in logs8, logs8
assert "Easy Apply" not in logs8 and "search" not in logs8.lower(), logs8

# LinkedIn's new Easy Apply: the entire modal sits in an OPEN SHADOW ROOT.
# Plain DOM queries could not see it, so the scan read the page behind the
# modal; the snapshot must walk shadow roots and scope to the dialog inside.
shadow_url = (E2E / "fixture_shadow.html").as_uri()
s9 = run("shadow-modal", shadow_url, ["done", "done"], expect_calls=0)
logs9 = "\n".join(s9.logs)
assert "[profile] Filled Full name = Test User" in logs9, logs9
assert "[profile] Filled Location (city) = Bangalore" in logs9, logs9
assert "Selected 'India (+91)' for Phone country code" in logs9, logs9
assert "Easy Apply" not in logs9 and "search" not in logs9.lower(), logs9
assert "Submit application" in s9.asked[-1], s9.asked

# After the user clicks Submit, LinkedIn's "Your application was sent" lands
# in the shadow root. The hand-off watch must read it and END the session as
# applied - the user typed nothing, and nothing was left open.
sent_url = (E2E / "fixture_sent.html").as_uri()
s10 = run("sent", sent_url, ["done", "__wait__"], expect_calls=0)
logs10 = "\n".join(s10.logs)
assert "The page confirms the application was sent" in logs10, logs10
assert "Not now" not in logs10 and "Update profile" not in logs10, logs10

# Workday shape: "Select file" over a display:none input (upload goes straight
# in, no clicking asked of the user), then dropdown BUTTONS - Country and the
# phone code from the profile, the device type from the model - then submit.
workday_url = (E2E / "fixture_workday.html").as_uri()
s11 = run("workday", workday_url, ["done", "tailored", "done"], expect_calls=1)
logs11 = "\n".join(s11.logs)
assert "[resume] Uploaded dummy_resume.pdf via 'Select file'" in logs11, logs11
assert "Now click" not in logs11, logs11
assert "[resume] Uploaded dummy_resume.pdf via 'Select files'" in logs11, logs11   # step 2's own Resume/CV box
assert "via 'Add'" not in logs11 and "Clicked Add" not in logs11, logs11   # a bare Add is not a picker
assert "[profile] Selected 'India' for Country*" in logs11, logs11
assert "[profile] Selected 'India (+91)' for Country Phone Code*" in logs11, logs11
assert "Selected 'Mobile' for Phone Device Type*" in logs11, logs11
assert logs11.count("Clicked Next") == 2, logs11
assert "Submit application" in s11.asked[-1], s11.asked

# A Next that goes nowhere: three tries, then ask the user with the page's
# error text - never fifteen "Clicked Next" lines.
stuck_url = (E2E / "fixture_stuck.html").as_uri()
s12 = run("stuck-next", stuck_url, ["done", "done", "done"], expect_calls=0)
logs12 = "\n".join(s12.logs)
assert logs12.count("Clicked Next") == 3, logs12   # 2 stuck (page named the error) + 1 that moved on
assert any("not moving the form on" in q and "Work Authorization is required" in q for q in s12.asked), s12.asked
assert "Submit application" in s12.asked[-1], s12.asked

# Workday "My Experience": Add reveals a job entry the model fills from the
# resume. The profile's location goes to the Contact section only - never
# into the job's own Location - and the From Month/Year parts are told apart
# by their group.
exp_url = (E2E / "fixture_experience.html").as_uri()
s13 = run("experience", exp_url, ["done", "done"], expect_calls=3)  # WE Add, WE entry, one "any more jobs?" pass; Languages/Websites need no model
logs13 = "\n".join(s13.logs)
assert "Clicked Add" in logs13, logs13
assert "[profile] Filled Location = Bangalore, India" in logs13, logs13   # Contact section
assert "Filled Job Title* = Backend Engineer" in logs13, logs13
assert "Filled Location = Noida" in logs13, logs13                       # the job's own
assert logs13.count("Filled Location") == 2, logs13
assert "Filled Month = 07" in logs13 and "Filled Year = 2020" in logs13, logs13
assert "Checked I currently work here" in logs13, logs13
assert "[profile] Selected 'English' for Language*" in logs13 and "[profile] Selected 'Hindi' for Language*" in logs13, logs13
assert "[profile] Selected 'Intermediate' for Overall*" in logs13 and "[profile] Selected 'Fluent' for Overall*" in logs13, logs13
assert "Clicked Add (Languages: entry 1 of 2)" in logs13 and "Clicked Add Another (Languages: entry 2 of 2)" in logs13, logs13
assert "Clicked Add (Websites: entry 1 of 2)" in logs13 and "Clicked Add Another (Websites: entry 2 of 2)" in logs13, logs13
assert "[profile] Filled URL* = https://linkedin.com/in/test" in logs13 and "[profile] Filled URL* = https://github.com/test" in logs13, logs13
assert "Submit application" in s13.asked[-1], s13.asked

# Several ways to apply on offer: the model wants "Apply Manually"; the
# candidate is asked and picks Autofill; the resume then goes straight into
# the hidden input on the next step.
choice_url = (E2E / "fixture_applychoice.html").as_uri()
s14 = run("apply-choice", choice_url, ["done", "Autofill with Resume", "tailored", "done"], expect_calls=1)
logs14 = "\n".join(s14.logs)
assert any("more than one way to apply" in q and "Apply Manually" in q for q in s14.asked), s14.asked
assert "Clicked Autofill with Resume" in logs14, logs14
assert "Clicked Apply Manually" not in logs14, logs14
assert "[resume] Uploaded dummy_resume.pdf via 'Select file'" in logs14, logs14
assert "Submit application" in s14.asked[-1], s14.asked

# Workday's address block: selecting State wipes City/Postal Code. The sweep
# must fill them again (they were "handled"), and no "Filled" line may be a
# lie - the value is read back after every write.
answers.remember("Postal Code", "560001")
rerender_url = (E2E / "fixture_rerender.html").as_uri()
s15 = run("rerender", rerender_url, ["done", "done"], expect_calls=0)
logs15 = "\n".join(s15.logs)
assert logs15.count("Filled City* = Bangalore") == 2, logs15
assert logs15.count("Filled Postal Code* = 560001") == 2, logs15
assert "[again] Filled City* = Bangalore" in logs15, logs15
assert "Selected 'Karnātaka' for State*" in logs15, logs15
assert "Submit application" in s15.asked[-1], s15.asked

# Workday typeaheads and date segments: typed text alone is dropped; the
# suggestion must be clicked (profile's +91 -> "India (+91)"; each skill one
# by one; "Computer Science" refused with the real names, then picked); the
# Month/Year segments take digits by keystroke and move on by themselves.
typeahead_url = (E2E / "fixture_typeahead.html").as_uri()
s16 = run("typeahead", typeahead_url, ["done", "done"], expect_calls=2)
logs16 = "\n".join(s16.logs)
assert "[profile] Selected 'India (+91)' for Country Phone Code* (typeahead)" in logs16, logs16
for skill in ("JavaScript", "Node.js", "Python", "Amazon Web Services (AWS)", "AWS SDK"):
    assert f"Selected '{skill}' for Type to Add Skills (typeahead)" in logs16, logs16
assert "Selected 'AWS VPN'" not in logs16, logs16
# A one-hit search is taken by the widget itself: seen as the pick, not as "rows: none".
assert "Selected 'LinkedIn corporate page' for How Did You Hear About Us?* (typeahead, the search's only hit)" in logs16, logs16
assert "rows: none" not in logs16, logs16
assert "'Computer Science' matches none of the suggestions for 'Field of Study'; pick one of: Computer and Information Science" in logs16, logs16
assert "Selected 'Computer and Information Science' for Field of Study (typeahead)" in logs16, logs16
assert "Filled Month = 07" in logs16 and "Filled Year = 2020" in logs16, logs16
assert "Leaving 'Type to Add Skills' alone" not in logs16, logs16
assert "Submit application" in s16.asked[-1], s16.asked

# Ask before Next: every filled step waits for "next"; "auto next" stops it.
worker.ASK_BEFORE_ADVANCE = True
s17 = run("confirm-next", workday_url, ["done", "tailored", "next", "auto next", "done"], expect_calls=1)
worker.ASK_BEFORE_ADVANCE = False
logs17 = "\n".join(s17.logs)
assert sum("This step is filled in" in q for q in s17.asked) == 2, s17.asked
assert "Moving through the steps without asking" in logs17, logs17
assert logs17.count("Clicked Next") == 2, logs17

# A field question is open (the model asks about the favourite language) and
# the candidate submits by hand instead of answering: the wait ends on the
# confirmation and the job is applied - "check" typed an hour later used to
# be refused as "busy".
qsent_url = (E2E / "fixture_question_sent.html").as_uri()
s18 = run("question-sent", qsent_url, ["done", "__wait__"], expect_calls=1)
logs18 = "\n".join(s18.logs)
assert "The page confirms the application was sent" in logs18, logs18
assert any("Favourite editor" in q for q in s18.asked), s18.asked

# One required box neither a rule nor the model can answer. It must not hold
# the whole form open: the agent says which box it is, then hands off exactly
# as it would on a form it finished - "review it and click Submit" - rather
# than spinning to "I am not making progress" with nothing named.
stuck_url = (E2E / "fixture_stuck_required.html").as_uri()
s19 = run("stuck-required", stuck_url, ["done", "done"], expect_calls=1)
logs19 = "\n".join(s19.logs)
assert "FILL THIS ONE YOURSELF: Which of our four values speaks to you, and why?*" in logs19, logs19
assert "Everything I can fill is done" in s19.asked[-1], s19.asked
assert all("not making progress" not in q for q in s19.asked), s19.asked
assert "[profile] Filled Full name = Test User" in logs19, logs19

# An OPTIONAL government identifier. The model asks about it, exactly as it
# did on Worldline, and the answer is not to put that question to the
# candidate: nothing here holds a PAN, nothing may guess one, and the form
# does not want it. It is left blank and the rest of the form is finished.
optid_url = (E2E / "fixture_optional_id.html").as_uri()
s20 = run("optional-identifier", optid_url, ["done", "done"], expect_calls=1)
logs20 = "\n".join(s20.logs)
assert "Permanent account number" in logs20, logs20
assert "left blank" in logs20, logs20
assert all("Permanent account number" not in q for q in s20.asked), s20.asked
# The point of not asking is that the form is finished instead: filled, and
# handed over the same way a form with nothing missing is.
assert "[profile] Filled First Name: * = Test" in logs20, logs20
assert "Everything I can fill is done" in s20.asked[-1], s20.asked

for name, sess in (("pass1", s1), ("pass2", s2), ("skip", s3), ("external", s4), ("modal", s5),
                   ("greenhouse", s6), ("popup", s7), ("late-modal", s8), ("shadow-modal", s9),
                   ("sent", s10), ("workday", s11), ("stuck-next", s12), ("experience", s13),
                   ("apply-choice", s14), ("rerender", s15), ("typeahead", s16), ("confirm-next", s17), ("question-sent", s18),
                   ("stuck-required", s19), ("optional-identifier", s20)):
    joined = "\n".join(sess.logs)
    assert "Clicked Submit application" not in joined, f"{name} clicked submit!"

print("\nALL E2E CHECKS PASSED")
print(f"scratch dir: {SCRATCH}")
