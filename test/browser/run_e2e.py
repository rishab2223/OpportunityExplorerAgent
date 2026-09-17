"""End-to-end apply flow, scenarios 1-12: the basic wizard, the bank going
cold to warm, external and modal apply paths, Workday, and a Next that
does not move the form on. Scenarios 13-22 are in run_e2e_b.py; the rig
both share is e2e_common.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_common import *  # noqa: F401,F403 - the rig
from e2e_common import E2E, TIMES, answers, run, time, worker


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


# Nothing here may ever click Submit. The rule is absolute, so it is
# asserted for every scenario rather than the ones that looked risky.
for name, sess in (("pass1", s1),
                   ("pass2", s2),
                   ("skip", s3),
                   ("external", s4),
                   ("modal", s5),
                   ("greenhouse", s6),
                   ("popup", s7),
                   ("late-modal", s8),
                   ("shadow-modal", s9),
                   ("sent", s10),
                   ("workday", s11),
                   ("stuck-next", s12)):
    joined = "\n".join(sess.logs)
    assert "Clicked Submit application" not in joined, f"{name} clicked submit!"

print("\nslowest scenarios:")
for name, took in sorted(TIMES, key=lambda t: -t[1])[:8]:
    print(f"  {took:6.1f}s  {name}")
print(f"  {sum(t for _, t in TIMES):6.1f}s  across {len(TIMES)} scenarios")
print("\nALL E2E CHECKS PASSED")
print("scratch dir:", SCRATCH)
