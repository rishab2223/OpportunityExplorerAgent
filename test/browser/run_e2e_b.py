"""End-to-end apply flow, scenarios 13-22: repeating sections, typeaheads,
the apply-path choice, review-before-Next, and the three prompts a
candidate can be sitting at when the form is not finished. Scenarios
1-12 are in run_e2e.py; the rig both share is e2e_common.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_common import *  # noqa: F401,F403 - the rig
from e2e_common import E2E, TIMES, answers, run, time, worker

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

# A stock Contact Form 7, whose required upload is labelled with the word
# "File" and nothing else. No rule reads that as a resume, so none was
# prepared and the model could only ask the candidate what the box wanted -
# the one required field on the form. Nameless, required, takes documents and
# the only such input on the page: that is the resume.
cf7_url = (E2E / "fixture_cf7.html").as_uri()
s21 = run("cf7-nameless-upload", cf7_url, ["done", "tailored", "done"], expect_calls=0)
logs21 = "\n".join(s21.logs)
assert "[resume] Uploaded dummy_resume.pdf" in logs21, logs21
assert "names no document and is this form's only upload" in logs21, logs21
# The bare words "Qualification" and "Field" both come from the profile now.
assert "[profile] Filled Qualification * (required) = Bachelors" in logs21, logs21
assert "[profile] Filled Field = Computer and Information Science" in logs21, logs21
# The point is that nobody is asked. A question here is the bug.
assert all("File" not in q or "Everything I can fill is done" in q
           for q in s21.asked), s21.asked
assert "Everything I can fill is done" in s21.asked[-1], s21.asked

# "llm: <the question>" at the STALL prompt. redo and llm: worked at the
# review prompt and at the hand-off prompt, but not at "I am not making
# progress on this form" - the only prompt reached when the form is NOT
# finished, and so the only one where there is a question left to draft. A
# real session typed it twice, once spelled "llm :", and got the same sentence
# back both times: the request was filed as a note for a model call that had
# nothing left to act on.
#
# Both boxes here are REQUIRED, which was the second half of it: the search
# for "the box that question names" looked at empty OPTIONAL boxes only, so a
# required question could never be found even at a prompt that handled llm:.
stall_url = (E2E / "fixture_stall_llm.html").as_uri()
s22 = run("stall-llm", stall_url, [
    "done",                                    # ready
    "llm : Have you ever been terminated, discharged, or asked to resign",
    "No, I have not.",                         # the draft, edited and sent
    "done",                                    # submitted by hand
], expect_calls=3, expect_status="applied")    # two plan rounds + the draft
logs22 = "\n".join(s22.logs)
assert any("not making progress" in q for q in s22.asked), s22.asked
assert "Drafting an answer for" in logs22, logs22
# The draft went INTO the box it named, not back as text to paste.
assert "could not find that box" not in logs22, logs22
assert "[llm] Filled Have you ever been terminated" in logs22, logs22
# And into the right one of the two: the other question is still empty.
assert "[llm] Filled Have you ever been employed by USP" not in logs22, logs22
assert all("Tell me which question" not in line for line in s22.logs), logs22


# Nothing here may ever click Submit. The rule is absolute, so it is
# asserted for every scenario rather than the ones that looked risky.
for name, sess in (("experience", s13),
                   ("apply-choice", s14),
                   ("rerender", s15),
                   ("typeahead", s16),
                   ("confirm-next", s17),
                   ("question-sent", s18),
                   ("stuck-required", s19),
                   ("optional-identifier", s20),
                   ("cf7-nameless-upload", s21),
                   ("stall-llm", s22)):
    joined = "\n".join(sess.logs)
    assert "Clicked Submit application" not in joined, f"{name} clicked submit!"

print("\nslowest scenarios:")
for name, took in sorted(TIMES, key=lambda t: -t[1])[:8]:
    print(f"  {took:6.1f}s  {name}")
print(f"  {sum(t for _, t in TIMES):6.1f}s  across {len(TIMES)} scenarios")
print("\nALL E2E CHECKS PASSED")
print("scratch dir:", SCRATCH)
