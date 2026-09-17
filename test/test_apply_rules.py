from __future__ import annotations

import json
import types
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from src import answers, history
from src.apply import browser, profile, worker
from src.apply.worker import (
    ApplyAction,
    SubmitBlocked,
    _execute,
    _field_key,
    _find_advance,
    _is_affirmative,
    _is_submit,
    _needs_user,
    _option_agrees,
    _page_sig,
    _unresolved_fields,
)


class TempDbTestCase(unittest.TestCase):
    """Keep every test off the candidate's real data.

    The answer bank is consulted by _needs_user, and the profile by the
    sweep and the section opener. Both paths are redirected into a temporary
    directory here: a test that wrote to profile.PROFILE_PATH while it still
    pointed at localData/apply_profile.json destroyed the real profile.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"
        self._original_profile = profile.PROFILE_PATH
        profile.PROFILE_PATH = Path(self._tmp.name) / "apply_profile.json"

    def tearDown(self) -> None:
        history.DB_PATH = self._original_path
        profile.PROFILE_PATH = self._original_profile
        self._tmp.cleanup()


class OutcomeOrderTests(unittest.TestCase):
    def test_recording_runs_before_the_done_event(self) -> None:
        """The UI reloads the table the moment it hears 'done'; the history row
        must already be written by then, or a detected closed/applied outcome
        does not show up until a manual refresh."""
        from src.apply.session import ApplySession

        sess = ApplySession("20260101T000000", "test:1", "X")
        order: list[str] = []
        sess.on_outcome = lambda status: order.append(f"recorded:{status}")
        queue, _ = sess.subscribe()
        sess.finish("closed", "no longer accepting applications")
        self.assertEqual(order, ["recorded:closed"])
        event = queue.get_nowait()
        self.assertEqual(event["type"], "done")
        # Consumed once: a second finish never re-records.
        sess.finish("closed")
        self.assertEqual(order, ["recorded:closed"])


class ChoiceEventTests(unittest.TestCase):
    def test_ask_choice_emits_a_choice_event_with_kind_and_meta(self) -> None:
        """Regression: emit()'s first parameter was named 'kind', colliding with
        the kind= entry ask_choice passes through **extra -- every real resume/
        cover-letter modal crashed with 'multiple values for argument'. The
        attachment tests stub the session, so this must run the real one."""
        from src.apply.session import ApplySession

        sess = ApplySession("20260101T000000", "test:1", "X")
        sess.answer("tailored")  # queued before the blocking wait starts
        queue, _ = sess.subscribe()
        reply = sess.ask_choice("resume", "Pick a resume", {"changelog": "x"})
        self.assertEqual(reply, "tailored")
        event = queue.get_nowait()
        self.assertEqual(event["type"], "choice")
        self.assertEqual(event["kind"], "resume")
        self.assertEqual(event["meta"], {"changelog": "x"})

    def test_ask_with_suggestion_carries_it_in_the_event(self) -> None:
        """The UI pre-fills the chat box from the question event's suggestion."""
        from src.apply.session import ApplySession

        sess = ApplySession("20260101T000000", "test:1", "X")
        sess.answer("edited by hand")
        queue, _ = sess.subscribe()
        reply = sess.ask("Why this role?", suggestion="Because agents.")
        self.assertEqual(reply, "edited by hand")
        event = queue.get_nowait()
        self.assertEqual(event["type"], "question")
        self.assertEqual(event["suggestion"], "Because agents.")
        # A plain ask keeps its event lean - no empty suggestion key.
        sess.answer("ok")
        reply = sess.ask("Ready?")
        event = [e for e in sess.subscribe()[1] if e["type"] == "question"][-1]
        self.assertNotIn("suggestion", event)


class SubmittedPageTests(unittest.TestCase):
    def test_confirmation_pages_are_recognised(self) -> None:
        from src.apply.worker import _looks_submitted

        for text in (
            "Thank you for applying! Your application has been submitted.",
            "Application received. Our team will get back to you.",
            "Your application was sent to UST.",
            "You have successfully applied for this position",
            "We have received your application.",
        ):
            self.assertTrue(_looks_submitted(text), text)

    def test_ordinary_pages_are_not(self) -> None:
        from src.apply.worker import _looks_submitted

        for text in (
            "Lead II - Backend Dev. Apply now to join our team.",
            "Please complete the application form below.",
            "",
        ):
            self.assertFalse(_looks_submitted(text), text)

    def test_closed_pages_are_recognised(self) -> None:
        from src.apply.worker import _looks_closed

        for text in (
            "Sorry, this position has been filled.",
            "This job is no longer available.",
            "The vacancy has closed.",
            "This posting has expired and applications are closed.",
            "This job has expired",
            "No longer accepting applications",
            # A posting taken down does not use the word "closed" at all.
            "Unable to load the page. Job id provided may not be valid or the "
            "job posting has been removed.",
            "The job posting has been removed.",
        ):
            self.assertTrue(_looks_closed(text), text)
        for text in (
            "Apply for this job. Position: Senior Software Engineer.",
            "Job openings at Capgemini",
            # "removed" on its own is ordinary form wording.
            "Remove experience. Removed the attachment from your application.",
            "",
        ):
            self.assertFalse(_looks_closed(text), text)

    def test_submitted_words_exclude_done(self) -> None:
        # On the no-fields prompt "done" means "I opened the form", so it must
        # not be a submission word there.
        from src.apply.worker import FINISHED_WORDS, SUBMITTED_WORDS

        self.assertNotIn("done", SUBMITTED_WORDS)
        for word in SUBMITTED_WORDS:
            self.assertIn(word, FINISHED_WORDS)


class LlmChatTests(unittest.TestCase):
    def test_prefix_parsing(self) -> None:
        from src.apply.worker import _llm_instruction

        self.assertEqual(_llm_instruction("llm: make it shorter"), "make it shorter")
        self.assertEqual(_llm_instruction("AI: mention AWS"), "mention AWS")
        self.assertEqual(_llm_instruction("llm:"), "")  # bare prefix = fresh draft
        # Normal answers are never mistaken for instructions.
        self.assertIsNone(_llm_instruction("6 years of backend work"))
        self.assertIsNone(_llm_instruction("AI-ML engineer at Initech"))
        self.assertIsNone(_llm_instruction("skip"))

    def test_draft_answer_carries_context_and_instruction(self) -> None:
        from src.apply.worker import _draft_answer

        seen = {}

        def invoke(system, user, schema):
            seen["system"], seen["user"] = system, user
            return schema(text="  Drafted reply.  ")

        job = {"title": "Backend Engineer", "company": "Autter",
               "description": "Agentic pipelines."}
        got = _draft_answer(invoke, job, "resume body", "Why us?",
                            "old draft", "make it shorter")
        self.assertEqual(got, "Drafted reply.")
        for expected in ("Autter", "Agentic pipelines.", "resume body",
                         "Why us?", "old draft", "make it shorter"):
            self.assertIn(expected, seen["user"])
        self.assertIn("never invent", seen["system"])


class AffirmativeTests(unittest.TestCase):
    def test_plain_yes_no(self) -> None:
        self.assertTrue(_is_affirmative("yes"))
        self.assertTrue(_is_affirmative("Y"))
        self.assertTrue(_is_affirmative("agree"))
        self.assertFalse(_is_affirmative("no"))
        self.assertFalse(_is_affirmative(""))

    def test_negatives_win_over_substrings(self) -> None:
        # The old substring matcher ticked consent boxes on all of these.
        self.assertFalse(_is_affirmative("I don't agree"))
        self.assertFalse(_is_affirmative("disagree"))
        self.assertFalse(_is_affirmative("not really"))
        self.assertFalse(_is_affirmative("leave it unchecked"))

    def test_words_not_substrings(self) -> None:
        self.assertFalse(_is_affirmative("yearly"))  # contains "y"
        self.assertFalse(_is_affirmative("may"))


class SubmitDetectionTests(unittest.TestCase):
    def test_submit_input(self) -> None:
        self.assertTrue(_is_submit({"tag": "input", "type": "submit"}))

    def test_button_with_submit_text(self) -> None:
        self.assertTrue(_is_submit({"tag": "button", "type": "", "text": "Submit application"}))
        self.assertTrue(_is_submit({"tag": "div", "role": "button", "text": "Apply now"}))
        self.assertTrue(_is_submit({"tag": "a", "type": "", "text": "Submit"}))

    def test_non_clickable_never_submit(self) -> None:
        self.assertFalse(_is_submit({"tag": "select", "type": "", "label": "Year finished"}))
        self.assertFalse(_is_submit({"tag": "input", "type": "text", "label": "Submit date"}))

    def test_whole_word_match(self) -> None:
        self.assertFalse(_is_submit({"tag": "button", "type": "", "text": "Unsubmitted items"}))

    def test_advance_buttons_are_not_submit(self) -> None:
        for text in ("Next", "Continue", "Review", "Save and continue"):
            self.assertFalse(_is_submit({"tag": "button", "type": "", "text": text}), text)


class SubmitNeverClickedTests(unittest.TestCase):
    def test_execute_refuses_submit_clicks(self) -> None:
        """The hard backstop: even a direct request to click submit raises."""
        action = ApplyAction(action="click", field_id=1, confidence=1.0)
        submit = {"id": 1, "tag": "button", "type": "submit", "text": "Submit application"}
        with self.assertRaises(SubmitBlocked):
            # page=None proves the check fires before any browser access.
            _execute(None, action, submit, "", None)


class FieldKeyTests(unittest.TestCase):
    def test_label_wins(self) -> None:
        key = _field_key({"id": 3, "label": "Full Name", "path": "form:1>input:2"}, "Full Name")
        self.assertEqual(key, "full name")

    def test_unlabelled_uses_dom_path_not_id(self) -> None:
        a = _field_key({"id": 3, "label": "", "name": "", "path": "form:1>input:2"}, "")
        b = _field_key({"id": 9, "label": "", "name": "", "path": "form:1>input:2"}, "")
        self.assertEqual(a, b)
        self.assertEqual(a, "form:1>input:2")

    def test_none_field(self) -> None:
        self.assertEqual(_field_key(None, "x"), "")

    def test_same_option_text_under_different_questions_never_collides(self) -> None:
        # Regression: every "Yes" radio keyed to "yes", so a cached answer to
        # work-authorization was applied to sponsorship without asking.
        auth = {"id": 1, "type": "radio", "label": "Yes",
                "group": "Are you authorized to work in India?"}
        visa = {"id": 2, "type": "radio", "label": "Yes",
                "group": "Do you require visa sponsorship?"}
        self.assertNotEqual(_field_key(auth, "Yes"), _field_key(visa, "Yes"))

    def test_identical_file_inputs_differ_by_element_id(self) -> None:
        cv = {"id": 1, "tag": "input", "type": "file", "label": "Attach", "elid": "resume"}
        cl = {"id": 2, "tag": "input", "type": "file", "label": "Attach", "elid": "cover_letter"}
        self.assertNotEqual(_field_key(cv, "Attach"), _field_key(cl, "Attach"))

    def test_identical_tile_buttons_under_different_headings_differ(self) -> None:
        # Greenhouse: both tiles read "Attach"; only the heading differs.
        cv = {"id": 1, "tag": "button", "type": "", "label": "Attach", "group": "Resume/CV"}
        cl = {"id": 2, "tag": "button", "type": "", "label": "Attach", "group": "Cover Letter"}
        self.assertNotEqual(_field_key(cv, "Attach"), _field_key(cl, "Attach"))

    def test_radio_options_of_one_question_share_an_answer_key(self) -> None:
        from src.apply.worker import _answer_key

        yes = {"id": 1, "type": "radio", "label": "Yes", "group": "Need sponsorship?"}
        no = {"id": 2, "type": "radio", "label": "No", "group": "Need sponsorship?"}
        self.assertEqual(_answer_key(yes, _field_key(yes, "Yes")),
                         _answer_key(no, _field_key(no, "No")))
        # Checkboxes are independent controls, not one question.
        box = {"id": 3, "type": "checkbox", "label": "Python", "group": "Skills"}
        self.assertEqual(_answer_key(box, _field_key(box, "Python")), _field_key(box, "Python"))


class NeedsUserTests(TempDbTestCase):
    def test_resume_upload_never_asks(self) -> None:
        action = ApplyAction(action="upload", field_id=1, confidence=0.1)
        self.assertFalse(_needs_user(
            action, {"tag": "input", "type": "file", "label": "Resume"}, "Resume"))

    def test_other_file_inputs_always_ask(self) -> None:
        # The resume must never be uploaded into a portfolio/certificate input.
        action = ApplyAction(action="upload", field_id=1, confidence=0.95)
        self.assertTrue(_needs_user(
            action, {"tag": "input", "type": "file", "label": "Upload certificates"},
            "Upload certificates"))

    def test_low_confidence_click_asks(self) -> None:
        action = ApplyAction(action="click", field_id=1, confidence=0.3)
        self.assertTrue(_needs_user(action, {"tag": "button", "type": "", "text": "Next"}, "Next"))

    def test_low_confidence_goto_asks(self) -> None:
        action = ApplyAction(action="goto", field_id=-1, value="https://x.y", confidence=0.3)
        self.assertTrue(_needs_user(action, None, ""))

    def test_secret_and_legal_fields_ask(self) -> None:
        fill = ApplyAction(action="fill", field_id=1, value="123456", confidence=0.95)
        self.assertTrue(_needs_user(fill, {"tag": "input", "type": "text"}, "Enter OTP"))
        check = ApplyAction(action="check", field_id=1, confidence=0.95)
        self.assertTrue(_needs_user(check, {"tag": "input", "type": "checkbox"}, "I agree to the terms"))

    def test_legal_question_in_group_asks(self) -> None:
        # The question lives in the radio group's legend, not the option label.
        check = ApplyAction(action="check", field_id=1, confidence=0.95)
        field = {"tag": "input", "type": "radio", "label": "Yes",
                 "group": "Do you require visa sponsorship?"}
        self.assertTrue(_needs_user(check, field, "Yes"))

    def test_legal_fields_always_gate_even_with_saved_answer(self) -> None:
        # The gate must fire so the reuse is logged and the option-agreement
        # check runs; the bank satisfies it inside the gate, not by skipping it.
        answers.remember("Do you require visa sponsorship?", "No")
        check = ApplyAction(action="check", field_id=1, confidence=0.95)
        field = {"tag": "input", "type": "radio", "label": "No",
                 "group": "Do you require visa sponsorship?"}
        self.assertTrue(_needs_user(check, field, "No"))

    def test_confident_fill_does_not_ask(self) -> None:
        fill = ApplyAction(action="fill", field_id=1, value="Casey", confidence=0.95)
        self.assertFalse(_needs_user(fill, {"tag": "input", "type": "text"}, "First name"))


class OptionAgreesTests(unittest.TestCase):
    def test_saved_yes_only_ticks_the_yes_option(self) -> None:
        yes = {"tag": "input", "type": "radio", "label": "Yes"}
        no = {"tag": "input", "type": "radio", "label": "No"}
        self.assertTrue(_option_agrees(yes, "Yes"))
        self.assertFalse(_option_agrees(no, "Yes"))
        self.assertTrue(_option_agrees(no, "No"))
        self.assertFalse(_option_agrees(yes, "No"))

    def test_named_option_matches_by_text(self) -> None:
        opt = {"tag": "input", "type": "radio", "label": "0-30 days"}
        self.assertTrue(_option_agrees(opt, "0-30 days"))
        self.assertFalse(_option_agrees(opt, "60 days"))

    def test_text_fields_always_agree(self) -> None:
        self.assertTrue(_option_agrees({"tag": "input", "type": "text", "label": "x"}, "anything"))
        self.assertTrue(_option_agrees(None, "anything"))


class UnresolvedFieldsTests(unittest.TestCase):
    def test_empty_inputs_and_unchecked_groups_count(self) -> None:
        fields = [
            {"id": 1, "tag": "input", "type": "text", "label": "Name", "value": ""},
            {"id": 2, "tag": "input", "type": "text", "label": "Email", "value": "x@y.z"},
            {"id": 3, "tag": "input", "type": "radio", "label": "Yes", "name": "auth",
             "checked": False},
            {"id": 4, "tag": "input", "type": "radio", "label": "No", "name": "auth",
             "checked": False},
            {"id": 5, "tag": "button", "type": "submit", "text": "Submit application"},
            {"id": 6, "tag": "button", "type": "", "text": "Next"},
        ]
        got = [f["id"] for f in _unresolved_fields(fields, set())]
        self.assertEqual(got, [1, 3, 4])

    def test_checked_group_is_resolved(self) -> None:
        fields = [
            {"id": 1, "tag": "input", "type": "radio", "label": "Yes", "name": "auth",
             "checked": True},
            {"id": 2, "tag": "input", "type": "radio", "label": "No", "name": "auth",
             "checked": False},
        ]
        self.assertEqual(_unresolved_fields(fields, set()), [])

    def test_handled_keys_are_skipped(self) -> None:
        fields = [{"id": 1, "tag": "input", "type": "text", "label": "Name", "value": ""}]
        self.assertEqual(_unresolved_fields(fields, {"name"}), [])


class FindAdvanceTests(unittest.TestCase):
    def test_finds_wizard_buttons_never_submit(self) -> None:
        fields = [
            {"id": 1, "tag": "button", "type": "submit", "text": "Submit application"},
            {"id": 2, "tag": "button", "type": "", "text": "Next"},
        ]
        got = _find_advance(fields, set())
        self.assertEqual(got["id"], 2)

    def test_no_advance_on_final_step(self) -> None:
        fields = [{"id": 1, "tag": "button", "type": "submit", "text": "Submit application"}]
        self.assertIsNone(_find_advance(fields, set()))

    def test_handled_advance_not_reclicked(self) -> None:
        fields = [{"id": 2, "tag": "button", "type": "", "text": "Next"}]
        self.assertIsNone(_find_advance(fields, {"next"}))

    def test_word_must_start_the_label(self) -> None:
        # Regression: a careers-page "Code Review" nav link matched \breview\b
        # and was clicked three times in a real session.
        self.assertIsNone(_find_advance(
            [{"id": 1, "tag": "a", "type": "", "text": "Code Review"}], set()))
        self.assertIsNone(_find_advance(
            [{"id": 1, "tag": "a", "type": "", "text": "Overview"}], set()))
        got = _find_advance(
            [{"id": 2, "tag": "button", "type": "", "text": "Review your application"}],
            set())
        self.assertEqual(got["id"], 2)


class PageSigTests(unittest.TestCase):
    def test_same_fields_same_sig_value_changes_it(self) -> None:
        fields = [{"tag": "input", "type": "text", "label": "Name", "value": "", "checked": None}]
        self.assertEqual(_page_sig(fields), _page_sig([dict(fields[0])]))
        changed = [dict(fields[0], value="Casey")]
        self.assertNotEqual(_page_sig(fields), _page_sig(changed))


class SalaryEstimateTests(TempDbTestCase):
    """Expected-salary fields get one model estimate per session, floored at
    current pay; the value is written in the field's own unit."""

    def setUp(self) -> None:
        super().setUp()
        from unittest import mock

        self.profile = {"full_name": "Test User", "total_experience_years": "6",
                        "current_ctc": "18 LPA", "expected_ctc": "22 LPA"}
        patcher = mock.patch("src.apply.profile.load_profile", side_effect=lambda: dict(self.profile))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = 0

    def _attach(self, low, high, fail=False):
        from src.apply import salary
        from src.apply.worker import Attachments

        logs = []

        class Sess:
            def log(self, text):
                logs.append(text)

        def invoke(system, user, schema):
            self.calls += 1
            if fail:
                raise RuntimeError("model down")
            self.assertIs(schema, salary.SalaryEstimate)
            self.assertIn("YEARS OF EXPERIENCE: 6", user)
            self.assertNotIn("18 LPA", user)   # current pay never reaches the model
            self.assertNotIn("Test User", user)
            return schema(low_lpa=low, high_lpa=high, basis="mid-size adtech, senior band")

        attach = Attachments(Sess(), {"title": "Sr. Backend Engineer", "company": "X Co"},
                             "resume", invoke, None, None)
        attach.logs = logs
        return attach

    def test_midpoint_once_per_session(self) -> None:
        attach = self._attach(40, 42)
        self.assertEqual(attach.expected_salary(), 4_100_000)
        self.assertEqual(attach.expected_salary(), 4_100_000)
        self.assertEqual(self.calls, 1)
        self.assertEqual(attach.calls, 1)
        # The log names the band, its midpoint, the model's basis and what is
        # quoted, so the candidate can judge the number before it is sent.
        joined = "\n".join(attach.logs)
        self.assertIn("model band 40 LPA-42 LPA, midpoint 41 LPA", joined)
        self.assertIn("mid-size adtech, senior band", joined)
        self.assertIn("Quoting 41 LPA (the band's midpoint)", joined)
        self.assertIn("Sr. Backend Engineer at X Co", joined)

    def test_never_below_current_pay(self) -> None:
        attach = self._attach(12, 16)
        # Band midpoint 14 < current 18: the saved expectation (22) is quoted.
        self.assertEqual(attach.expected_salary(), 2_200_000)
        joined = "\n".join(attach.logs)
        self.assertIn("model band 12 LPA-16 LPA, midpoint 14 LPA", joined)
        self.assertIn("below your current 18 LPA", joined)
        self.assertIn("Quoting 22 LPA", joined)
        self.assertIn("your saved expected pay", joined)

    def test_failed_call_falls_back_silently(self) -> None:
        attach = self._attach(0, 0, fail=True)
        self.assertIsNone(attach.expected_salary())
        self.assertIsNone(attach.expected_salary())  # not retried
        self.assertEqual(self.calls, 1)

    def test_only_empty_typeable_expected_fields_qualify(self) -> None:
        from src.apply.worker import _wants_salary_estimate

        base = {"tag": "input", "type": "text", "value": "", "group": ""}
        self.assertTrue(_wants_salary_estimate({**base, "label": "Expected CTC (in LPA)"}))
        self.assertTrue(_wants_salary_estimate({**base, "tag": "textarea", "type": "", "label": "Desired salary"}))
        self.assertFalse(_wants_salary_estimate({**base, "label": "Current CTC (in LPA)"}))
        self.assertFalse(_wants_salary_estimate({**base, "label": "Expected CTC", "value": "30"}))
        self.assertFalse(_wants_salary_estimate({**base, "tag": "select", "label": "Expected CTC", "options": ["20-22 LPA"]}))

    def test_sweep_writes_the_estimate_in_the_fields_unit(self) -> None:
        from unittest import mock

        from src.apply import worker

        attach = self._attach(40, 42)
        fields = [
            {"id": 1, "tag": "input", "type": "text", "label": "Expected CTC (in LPA)", "value": "", "group": "", "name": ""},
            {"id": 2, "tag": "input", "type": "number", "label": "Expected annual salary", "value": "", "group": "", "name": ""},
        ]
        written = []

        def fake_apply(page, field, value, pdf_path, sess, source=""):
            from src.apply import salary
            written.append((field["id"], salary.for_field(value, field), source))

        with mock.patch.object(worker, "_apply_value", side_effect=fake_apply):
            filled = worker._sweep(None, fields, set(), {}, {"company": "X Co"}, "", attach.sess, attach=attach)
        self.assertEqual(filled, 2)
        self.assertEqual(written, [(1, "41", "estimate"), (2, "4100000", "estimate")])
        self.assertEqual(self.calls, 1)


class PageWatchTests(unittest.TestCase):
    """While the worker waits at a hand-off prompt, the user may open the
    form themselves (an Easy Apply popup, a new tab). The idle tick raises
    PageChanged; the wait ends and the prompt is withdrawn."""

    def test_page_changed_ends_the_wait(self) -> None:
        import threading

        from src.apply.session import ApplySession, PageChanged

        sess = ApplySession("20260101T000000", "test:watch", "X")
        ticks = {"n": 0}

        def tick():
            ticks["n"] += 1
            if ticks["n"] >= 2:
                raise PageChanged()

        sess.idle_tick = tick
        result = {}

        def run():
            try:
                sess.ask("Everything I can fill is done...")
            except PageChanged:
                result["raised"] = True

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=8)
        self.assertTrue(result.get("raised"), "ask() did not end on PageChanged")
        self.assertEqual(sess.status, "running")
        self.assertEqual(sess.pending_question, "")
        types = [e["type"] for e in sess._events]
        self.assertEqual(types[-2:], ["question", "answer"])  # prompt withdrawn

    def test_page_grew_on_new_fields_url_or_tab(self) -> None:
        from unittest import mock

        from src.apply import browser, worker

        class Page:
            url = "https://x/apply"

        page = Page()
        base_fields = [{"id": 1, "tag": "input", "type": "submit", "label": "Apply Now", "value": ""}]
        popup_fields = base_fields + [
            {"id": 2, "tag": "input", "type": "text", "label": "First Name", "value": "", "name": ""}]
        watch = {"keys": set(), "handled": set(), "url": page.url, "ticks": 0}
        with mock.patch.object(browser, "current_page", return_value=page),                 mock.patch.object(browser, "page_text", return_value="Apply Now"):
            with mock.patch.object(browser, "snapshot", return_value=base_fields):
                self.assertEqual(worker._page_grew(None, page, watch), "")
            with mock.patch.object(browser, "snapshot", return_value=popup_fields):
                self.assertEqual(worker._page_grew(None, page, watch), "fields")
            page.url = "https://x/apply/step2"
            with mock.patch.object(browser, "snapshot", return_value=base_fields):
                self.assertEqual(worker._page_grew(None, page, watch), "url")
            page.url = watch["url"]
            # LinkedIn's confirmation, rendered after the user's own submit.
            with mock.patch.object(browser, "page_text", return_value="Your application was sent to X!"):
                self.assertEqual(worker._page_grew(None, page, watch), "submitted")
            # ...but not when the page already read that way at the prompt:
            # the baseline is the set of sentences seen, not a yes/no.
            seen = worker._submitted_marks("Your application was sent to X!")
            with mock.patch.object(browser, "page_text", return_value="Your application was sent to X!"),                     mock.patch.object(browser, "snapshot", return_value=base_fields):
                self.assertEqual(worker._page_grew(None, page, {**watch, "submitted_seen": seen}), "")
        other = Page()
        with mock.patch.object(browser, "current_page", return_value=other):
            self.assertEqual(worker._page_grew(None, page, watch), "tab")


class ListboxButtonTests(unittest.TestCase):
    """Workday dropdowns are BUTTONS that open a listbox."""

    def test_choose_option_prefers_exact_then_start_then_bounded_token(self) -> None:
        from src.apply.worker import _choose_option

        countries = ["Select One", "British Indian Ocean Territory (+246)", "India (+91)", "Indonesia (+62)"]
        self.assertEqual(_choose_option(countries, "India (+91)"), 2)
        self.assertEqual(_choose_option(countries, "india"), 2)
        self.assertEqual(_choose_option(countries, "+91"), 2)
        self.assertEqual(_choose_option(countries, "Ind"), -1)    # ambiguous
        self.assertEqual(_choose_option(countries, "+9"), -1)     # not a whole token
        self.assertEqual(_choose_option(["Yes", "No"], "no"), 1)
        self.assertEqual(_choose_option(["Mobile", "Home", "Work"], "Mobile phone"), -1)

    def test_listbox_buttons_count_as_fields(self) -> None:
        from src.apply.worker import _accepts_value

        button = {"tag": "button", "haspopup": "listbox", "label": "Country*", "text": "Select One", "value": ""}
        self.assertTrue(_accepts_value(button))
        self.assertFalse(_accepts_value({"tag": "button", "haspopup": "true", "label": "Menu", "text": ""}))
        fields = [button, {"tag": "button", "haspopup": "listbox", "label": "Phone Device Type*",
                           "text": "Mobile", "value": ""}]
        self.assertEqual([f["label"] for f in _unresolved_fields(fields, set())], ["Country*"])


class RadixControlTests(TempDbTestCase):
    """Radix/shadcn renders every control twice: a styled button with the real
    label, and a native mirror marked aria-hidden and tabindex=-1."""

    def test_a_combobox_button_is_a_dropdown_not_a_text_box(self) -> None:
        from src.apply import resolver

        # Radix sets role=combobox and aria-expanded, and no aria-haspopup at
        # all, so fill() was attempted on it: "Element is not an <input>".
        trigger = {"tag": "button", "type": "button", "role": "combobox",
                   "haspopup": "", "label": "Notice Period"}
        self.assertTrue(resolver.is_listbox_button(trigger))
        # Workday's own flavour still works.
        self.assertTrue(resolver.is_listbox_button(
            {"tag": "button", "haspopup": "listbox", "label": "Degree"}))
        # A plain button is not a dropdown.
        self.assertFalse(resolver.is_listbox_button(
            {"tag": "button", "type": "button", "label": "Submit"}))

    def test_a_radix_checkbox_is_clicked_not_checked(self) -> None:
        from src.apply import browser, worker

        clicks = []

        class Locator:
            def __init__(self):
                self.state = "false"

            def get_attribute(self, name, timeout=0):
                return self.state if name == "aria-checked" else None

            def check(self, timeout=0):
                raise AssertionError("check() must not be used on a button")

        locator = Locator()
        field = {"tag": "button", "type": "checkbox", "label": "I consent", "checked": False}
        page = type("P", (), {"wait_for_timeout": lambda self, ms: None})()
        with unittest.mock.patch.object(
                browser, "click",
                lambda loc, timeout=0: (clicks.append(1), setattr(loc, "state", "true"))):
            worker._set_checked(page, locator, field, True)
        self.assertEqual(len(clicks), 1)

    def test_one_already_ticked_is_left_alone(self) -> None:
        from src.apply import browser, worker

        clicks = []

        class Locator:
            def get_attribute(self, name, timeout=0):
                return "true" if name == "aria-checked" else None

        field = {"tag": "button", "type": "checkbox", "label": "I consent", "checked": True}
        page = type("P", (), {"wait_for_timeout": lambda self, ms: None})()
        with unittest.mock.patch.object(browser, "click",
                                        lambda loc, timeout=0: clicks.append(1)):
            worker._set_checked(page, locator=Locator(), field=field, on=True)
        self.assertEqual(clicks, [])


class AmountFormattingTests(TempDbTestCase):
    """A box that inserts its own commas turned "22,00,000" into three crore."""

    FIELD = {"tag": "input", "type": "text", "label": "Enter expected salary"}

    def test_grouping_separators_are_stripped_before_typing(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._plain_amount("22,00,000", self.FIELD), "2200000")
        self.assertEqual(worker._plain_amount("18, 00, 000", self.FIELD), "1800000")
        self.assertEqual(worker._plain_amount("2200000", self.FIELD), "2200000")

    def test_prose_is_left_for_the_amount_retry_to_handle(self) -> None:
        from src.apply import worker

        for text in ("25-22 LPA", "35 LPA", "negotiable"):
            self.assertEqual(worker._plain_amount(text, self.FIELD), text)

    def test_a_box_that_is_not_about_money_is_untouched(self) -> None:
        from src.apply import worker

        for label in ("Pincode", "Phone Number", "Employee ID"):
            self.assertEqual(
                worker._plain_amount("1,22,003", {"tag": "input", "label": label}),
                "1,22,003")


class RepairWrittenTests(TempDbTestCase):
    """A form still hydrating accepts a value, passes its read-back, and then
    renders itself empty again. Three boxes were logged as filled that the
    candidate saw blank in the browser."""

    FIELDS = [
        {"id": 1, "tag": "input", "type": "", "label": "First Name*", "value": "", "elid": "fn"},
        {"id": 2, "tag": "input", "type": "", "label": "Last Name*", "value": "", "elid": "ln"},
        {"id": 3, "tag": "button", "label": "Next", "value": ""},
    ]

    def _run(self, live, typed):
        from src.apply import worker

        logs = []
        sess = type("S", (), {"log": lambda self, t: logs.append(t)})()
        written = {worker._field_key(self.FIELDS[0], "First Name*"): "Casey",
                   worker._field_key(self.FIELDS[1], "Last Name*"): "Jordan"}
        with unittest.mock.patch.object(worker, "_live_value", lambda p, f: live.get(f["id"], "")),              unittest.mock.patch.object(worker, "_retype",
                                        lambda p, f, v: typed.append((f["id"], v)) or True):
            fixed = worker._repair_written(None, self.FIELDS, written, sess)
        return fixed, logs

    def test_what_the_page_emptied_is_put_back(self) -> None:
        typed = []
        fixed, logs = self._run(live={}, typed=typed)
        self.assertEqual(fixed, 2)
        self.assertEqual(typed, [(1, "Casey"), (2, "Jordan")])
        self.assertTrue(all("[again]" in line for line in logs), logs)

    def test_a_box_still_holding_its_value_is_left_alone(self) -> None:
        typed = []
        fixed, logs = self._run(live={1: "Casey", 2: "Jordan"}, typed=typed)
        self.assertEqual((fixed, typed, logs), (0, [], []))

    def test_only_the_emptied_one_is_retyped(self) -> None:
        typed = []
        fixed, _ = self._run(live={1: "Casey"}, typed=typed)
        self.assertEqual((fixed, typed), (1, [(2, "Jordan")]))

    def test_a_box_that_cannot_be_kept_tells_the_candidate(self) -> None:
        from src.apply import worker

        logs = []
        sess = type("S", (), {"log": lambda self, t: logs.append(t)})()
        written = {worker._field_key(self.FIELDS[0], "First Name*"): "Casey"}
        with unittest.mock.patch.object(worker, "_live_value", lambda p, f: ""),              unittest.mock.patch.object(worker, "_retype", lambda p, f, v: False):
            fixed = worker._repair_written(None, self.FIELDS, written, sess)
        self.assertEqual(fixed, 0)
        # Never a silent failure: the step must not be declared ready.
        self.assertTrue(any("CHECK" in line and "yourself" in line for line in logs), logs)

    def test_nothing_written_means_nothing_to_do(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._repair_written(None, self.FIELDS, {}, None), 0)


class SectionAddTests(unittest.TestCase):
    def test_add_buttons_in_repeating_sections(self) -> None:
        from src.apply.worker import _is_section_add

        self.assertTrue(_is_section_add({"tag": "button", "text": "Add", "section": "Work Experience"}))
        self.assertTrue(_is_section_add({"tag": "button", "text": "Add Another", "section": "Education"}))
        self.assertTrue(_is_section_add({"tag": "a", "text": "Add language", "section": "Languages"}))
        self.assertFalse(_is_section_add({"tag": "button", "text": "Add", "section": "Contact Information"}))
        self.assertFalse(_is_section_add({"tag": "button", "text": "Add to cart", "section": "Work Experience"}))
        self.assertFalse(_is_section_add({"tag": "input", "type": "text", "label": "Add", "section": "Work Experience"}))


class WorkSectionAddTests(TempDbTestCase):
    """The script clicks Add for Work Experience, as it already does for
    Languages and Websites, and stops when the page will not grow."""

    JOBS = [
        {"title": "Senior Engineer", "company": "Northwind", "start": "07/2020", "end": "01/2026"},
        {"title": "Engineer Intern", "company": "Northwind", "start": "06/2019", "end": "07/2020"},
    ]

    def _profile(self, **extra):
        from src.apply import profile
        import json as _json
        profile.PROFILE_PATH.write_text(
            json.dumps({"full_name": "T", **extra}), encoding="utf-8")

    def _page(self, clicks):
        class Locator:
            def click(self, timeout=0):
                clicks.append(1)

        class Frame:
            def evaluate(self, js):
                return "1:0:0"

        class Page:
            url = "https://example.invalid/f"

            def wait_for_timeout(self, ms):
                pass

            def locator(self, sel):
                return Locator()

        return Page()

    def _add_button(self):
        # Workday's own: the label says nothing about work at all.
        return {"id": 9, "tag": "button", "text": "Add Another", "label": "Add Another",
                "group": "Role Description", "section": "To*"}

    def _one_entry(self):
        return [
            {"id": 1, "tag": "input", "type": "text", "label": "Job Title*",
             "section": "Work History (Optional) 1", "value": ""},
            {"id": 2, "tag": "input", "type": "text", "label": "Company*",
             "section": "Work History (Optional) 1", "value": ""},
        ]

    def test_a_missing_entry_is_added(self) -> None:
        from src.apply import browser, worker

        self._profile(jobs=self.JOBS)
        clicks: list[int] = []
        fields = self._one_entry() + [self._add_button()]
        page = self._page(clicks)
        with unittest.mock.patch.object(browser, "locate", lambda p, i, e: p.locator("x")), \
             unittest.mock.patch.object(browser, "click", lambda loc, timeout=0: loc.click()), \
             unittest.mock.patch.object(browser, "settle", lambda *a, **k: True), \
             unittest.mock.patch.object(browser, "page_shape", lambda p: "x"):
            logs: list[str] = []
            sess = type("S", (), {"log": lambda self, t: logs.append(t)})()
            self.assertTrue(worker._open_profile_sections(page, fields, set(), sess))
        self.assertEqual(len(clicks), 1)
        self.assertTrue(any("Work Experience: entry 2 of 2" in line for line in logs), logs)

    def test_nothing_is_clicked_once_every_entry_is_there(self) -> None:
        from src.apply import browser, worker

        self._profile(jobs=self.JOBS)
        clicks: list[int] = []
        fields = self._one_entry() + [
            {"id": 3, "tag": "input", "type": "text", "label": "Job Title*",
             "section": "Work History (Optional) 2", "value": ""},
            self._add_button(),
        ]
        page = self._page(clicks)
        with unittest.mock.patch.object(browser, "locate", lambda p, i, e: p.locator("x")), \
             unittest.mock.patch.object(browser, "click", lambda loc, timeout=0: loc.click()):
            sess = type("S", (), {"log": lambda self, t: None})()
            self.assertFalse(worker._open_profile_sections(page, fields, set(), sess))
        self.assertEqual(clicks, [])

    def test_a_profile_with_no_jobs_leaves_the_section_to_the_model(self) -> None:
        from src.apply import browser, worker

        self._profile()
        clicks: list[int] = []
        page = self._page(clicks)
        with unittest.mock.patch.object(browser, "locate", lambda p, i, e: p.locator("x")), \
             unittest.mock.patch.object(browser, "click", lambda loc, timeout=0: loc.click()):
            sess = type("S", (), {"log": lambda self, t: None})()
            self.assertFalse(
                worker._open_profile_sections(page, self._one_entry() + [self._add_button()],
                                              set(), sess))
        self.assertEqual(clicks, [])

    def test_an_add_that_adds_nothing_is_not_clicked_forever(self) -> None:
        from src.apply import browser, worker

        self._profile(jobs=self.JOBS)
        clicks: list[int] = []
        page = self._page(clicks)
        handled: set[str] = set()
        with unittest.mock.patch.object(browser, "locate", lambda p, i, e: p.locator("x")), \
             unittest.mock.patch.object(browser, "click", lambda loc, timeout=0: loc.click()), \
             unittest.mock.patch.object(browser, "settle", lambda *a, **k: True), \
             unittest.mock.patch.object(browser, "page_shape", lambda p: "x"):
            sess = type("S", (), {"log": lambda self, t: None})()
            for _ in range(6):
                fields = self._one_entry() + [self._add_button()]   # page never grows
                worker._open_profile_sections(page, fields, handled, sess)
        # Never more clicks than there are entries to add.
        self.assertLessEqual(len(clicks), 2)


class ClipToLimitTests(TempDbTestCase):
    def test_a_long_value_is_cut_at_a_word_boundary(self) -> None:
        from src.apply import worker

        logs: list[str] = []
        sess = type("S", (), {"log": lambda self, t: logs.append(t)})()
        text = "Designed the core backend for collaborative interfaces and pipelines."
        cut = worker._clip_to_limit({"maxlength": 30}, text, "Role description", sess)
        self.assertLessEqual(len(cut), 30)
        self.assertFalse(cut.endswith(" "))
        self.assertTrue(text.startswith(cut))
        self.assertTrue(logs)

    def test_a_value_that_fits_is_untouched_and_silent(self) -> None:
        from src.apply import worker

        logs: list[str] = []
        sess = type("S", (), {"log": lambda self, t: logs.append(t)})()
        self.assertEqual(worker._clip_to_limit({"maxlength": 0}, "short", "X", sess), "short")
        self.assertEqual(worker._clip_to_limit({"maxlength": 99}, "short", "X", sess), "short")
        self.assertEqual(logs, [])


class WorkdayDeleteButtonTests(TempDbTestCase):
    def test_an_entry_gets_its_own_delete_not_the_next_ones(self) -> None:
        from src.apply import worker

        # Workday lists Delete BEFORE Job Title, so a slice starting at the
        # title holds the NEXT entry's Delete. Acting on that removed the
        # wrong job.
        fields = []
        for n in (1, 2):
            section = f"Work History (Optional) {n}"
            fields.append({"id": n * 10, "tag": "button", "text": "Delete", "label": "Delete",
                           "section": section, "group": section})
            fields.append({"id": n * 10 + 1, "tag": "input", "type": "text",
                           "label": "Job Title*", "section": section, "value": ""})
        entry2 = [f for f in fields if f["section"].endswith("2")]
        self.assertEqual(worker._entry_remove_button(fields, entry2)["id"], 20)
        entry1 = [f for f in fields if f["section"].endswith("1")]
        self.assertEqual(worker._entry_remove_button(fields, entry1)["id"], 10)


class SectionKeyTests(unittest.TestCase):
    def test_same_label_in_different_sections_are_different_controls(self) -> None:
        contact = {"tag": "input", "type": "text", "label": "Location", "section": "Contact"}
        job = {"tag": "input", "type": "text", "label": "Location", "section": "Work Experience"}
        self.assertNotEqual(_field_key(contact, "Location"), _field_key(job, "Location"))
        # Without sections the key is unchanged (bank-era snapshots).
        self.assertEqual(_field_key({"tag": "input", "type": "text", "label": "Location"}, "Location"),
                         _field_key({"tag": "input", "type": "text", "label": "Location", "section": ""}, "Location"))


class ApplyChoiceTests(unittest.TestCase):
    def test_apply_paths_are_recognised_in_order(self) -> None:
        from src.apply.worker import _apply_choices

        fields = [
            {"id": 1, "tag": "button", "text": "Accept Cookies"},
            {"id": 2, "tag": "a", "role": "button", "text": "Autofill with Resume"},
            {"id": 3, "tag": "a", "role": "button", "text": "Apply Manually"},
            {"id": 4, "tag": "a", "role": "button", "text": "Use My Last Application"},
            {"id": 5, "tag": "input", "type": "text", "label": "Apply"},
        ]
        self.assertEqual([c["id"] for c in _apply_choices(fields)], [2, 3, 4])
        # One apply button is not a choice.
        self.assertEqual(len(_apply_choices([{"id": 9, "tag": "a", "text": "Apply Now"}])), 1)


class AccentOptionTests(unittest.TestCase):
    def test_choose_option_ignores_accents(self) -> None:
        from src.apply.worker import _choose_option

        self.assertEqual(_choose_option(["Select One", "Odishā", "Karnātaka"], "Karnataka"), 2)
        self.assertEqual(_choose_option(["Bachelors", "Masters"], "Bachelor's Degree"), 0)   # degree family


class SectionAddFallbackTests(TempDbTestCase):
    def test_section_from_group_or_own_text(self) -> None:
        from src.apply.worker import _is_section_add, _needs_user

        self.assertTrue(_is_section_add({"tag": "button", "text": "Add", "group": "Work Experience", "section": ""}))
        self.assertTrue(_is_section_add({"tag": "button", "text": "Add Education", "group": "", "section": ""}))
        self.assertFalse(_is_section_add({"tag": "button", "text": "Add", "group": "", "section": ""}))
        # Adding an entry is what the candidate asked for: no yes/no gate.
        add = {"tag": "button", "text": "Add Another", "section": "Work Experience"}
        self.assertFalse(_needs_user(ApplyAction(action="click", field_id=1, confidence=0.3), add, "Add Another"))


class RefillTests(TempDbTestCase):
    def test_sweep_refills_a_field_it_wrote_that_went_blank(self) -> None:
        from unittest import mock

        from src.apply import worker

        field = {"id": 1, "tag": "input", "type": "text", "label": "City*", "value": "", "group": "", "name": ""}
        handled, written, attempts = set(), {}, {}
        calls = []

        def fake_apply(page, f, value, pdf_path, sess, source=""):
            calls.append((f["label"], value, source))

        class Sess:
            def log(self, t):
                pass

        with mock.patch.object(worker, "_apply_value", side_effect=fake_apply), \
                mock.patch("src.apply.resolver.resolve", return_value=("Bangalore", "profile")):
            worker._sweep(None, [field], handled, attempts, {}, "", Sess(), written=written)
            # Still handled and still holding a value: nothing happens.
            worker._sweep(None, [dict(field, value="Bangalore")], handled, attempts, {}, "", Sess(), written=written)
            # Blank again after a re-render: written once more, marked as such.
            worker._sweep(None, [field], handled, attempts, {}, "", Sess(), written=written)
        self.assertEqual(calls, [("City*", "Bangalore", "profile"), ("City*", "Bangalore", "again")])
        # A skipped (never written) handled field is left alone.
        calls.clear()
        with mock.patch.object(worker, "_apply_value", side_effect=fake_apply):
            worker._sweep(None, [dict(field, label="Fax")], {worker._field_key(dict(field, label="Fax"), "Fax")},
                          attempts, {}, "", Sess(), written=written)
        self.assertEqual(calls, [])


class SuggestionRowTests(unittest.TestCase):
    def test_placeholder_rows_are_not_suggestions(self) -> None:
        from src.apply.worker import _real_suggestions

        self.assertFalse(_real_suggestions(["No Items.", "No Items."]))
        self.assertFalse(_real_suggestions(["Loading...", ""]))
        self.assertTrue(_real_suggestions(["No Items.", "JavaScript"]))
        self.assertTrue(_real_suggestions(["India (+91)"]))


class HoldsTests(unittest.TestCase):
    def test_reformatted_phone_counts_as_held(self) -> None:
        from src.apply.worker import _holds

        class Loc:
            def __init__(self, shown):
                self.shown = shown

            def input_value(self, timeout=0):
                return self.shown

        self.assertTrue(_holds(Loc("9000000000"), "9000000000"))
        self.assertTrue(_holds(Loc("090000 00000"), "9000000000"))   # national format, trunk 0
        self.assertTrue(_holds(Loc("+91 90000 00000"), "+91 9000000000"))
        self.assertFalse(_holds(Loc(""), "9000000000"))
        self.assertFalse(_holds(Loc("9000000001"), "9000000000"))
        self.assertFalse(_holds(Loc("Bangalore"), "Noida"))


class ModelNextRefusedTests(TempDbTestCase):
    """The model may not advance the wizard itself: with sections still empty
    it is told which, and otherwise the loop's own review prompt does it."""

    def _run(self, holder):
        from src.apply.worker import _run_action

        class Sess:
            def log(self, t):
                pass

        fields = [{"id": 7, "tag": "button", "text": "Next", "label": "Next", "section": ""}]
        notes = []
        result = _run_action(None, ApplyAction(action="click", field_id=7, confidence=0.99), fields,
                             {}, "", Sess(), [], notes, set(), {}, {}, set(), attach=None, holder=holder)
        return result, notes

    def test_next_refused_while_sections_are_empty(self) -> None:
        result, notes = self._run({"pending_sections": ["Education", "Languages"], "confirm_advance": False})
        self.assertEqual(result, "refused")
        self.assertIn("Education, Languages", notes[-1])

    def test_next_refused_in_review_mode(self) -> None:
        result, notes = self._run({"pending_sections": [], "confirm_advance": True})
        self.assertEqual(result, "refused")
        self.assertIn("reviewed", notes[-1])

    def test_advance_button_matcher(self) -> None:
        from src.apply.worker import _is_advance_button

        self.assertTrue(_is_advance_button({"tag": "button", "text": "Next"}))
        self.assertTrue(_is_advance_button({"tag": "button", "text": "Save and Continue"}))
        self.assertFalse(_is_advance_button({"tag": "a", "text": "Code Review"}))
        self.assertFalse(_is_advance_button({"tag": "button", "type": "submit", "text": "Submit application"}))


class DatePartTests(unittest.TestCase):
    def test_leading_zero_dropped_by_the_widget_still_counts(self) -> None:
        from src.apply.worker import _same_number

        self.assertTrue(_same_number("7", "07"))
        self.assertTrue(_same_number("2020", "2020"))
        self.assertFalse(_same_number("8", "07"))
        self.assertFalse(_same_number("", "07"))


class OrdinalKeyTests(unittest.TestCase):
    def test_repeated_entries_get_their_own_identity(self) -> None:
        first = {"tag": "select", "type": "", "label": "Language*", "section": "Languages", "ordinal": 0}
        second = {"tag": "select", "type": "", "label": "Language*", "section": "Languages", "ordinal": 1}
        self.assertNotEqual(_field_key(first, "Language*"), _field_key(second, "Language*"))
        # The first entry's key is unchanged by the numbering (bank-era keys).
        self.assertEqual(_field_key(first, "Language*"),
                         _field_key({"tag": "select", "type": "", "label": "Language*", "section": "Languages"}, "Language*"))
        # Radio options repeat by design (one per choice) and keep group keys.
        a = {"tag": "input", "type": "radio", "label": "Yes", "group": "Sponsorship", "ordinal": 0}
        b = {"tag": "input", "type": "radio", "label": "Yes", "group": "Sponsorship", "ordinal": 1}
        self.assertEqual(_field_key(a, "Yes"), _field_key(b, "Yes"))


class NumberedEntryTests(unittest.TestCase):
    def test_entry_number_in_the_title_is_the_position(self) -> None:
        from unittest import mock

        from src.apply import worker

        fields = [
            {"id": 1, "tag": "select", "type": "", "label": "Language*", "section": "Languages 1", "value": "", "options": ["Select One", "English", "Hindi"]},
            {"id": 2, "tag": "select", "type": "", "label": "Language*", "section": "Languages 2", "value": "", "options": ["Select One", "English", "Hindi"]},
        ]
        written = []
        with mock.patch.object(worker, "_apply_value", side_effect=lambda page, f, v, p, s, source="": written.append((f["section"], v))), \
                mock.patch("src.apply.profile.load_profile", return_value={"languages": "English - Intermediate; Hindi - Fluent"}):
            class Sess:
                def log(self, t):
                    pass
            worker._sweep(None, fields, set(), {}, {}, "", Sess())
        self.assertEqual(written, [("Languages 1", "English"), ("Languages 2", "Hindi")])

    def test_add_another_is_always_a_section_button(self) -> None:
        from src.apply.worker import _is_section_add

        self.assertTrue(_is_section_add({"tag": "button", "text": "Add Another", "section": "", "group": "Role Description"}))
        self.assertFalse(_is_section_add({"tag": "button", "text": "Add", "section": "", "group": ""}))


class TieBreakerTests(unittest.TestCase):
    def test_profile_state_picks_between_same_start_options(self) -> None:
        from src.apply.worker import _choose_option

        opts = ["Bangalore, Odisha, India", "Bangalore, Karnataka, India", "Bengaluru, Karnataka, India"]
        self.assertEqual(_choose_option(opts, "Bangalore"), 0)                       # no context: first
        self.assertEqual(_choose_option(opts, "Bangalore", ["Karnataka", "India"]), 1)
        self.assertEqual(_choose_option(opts, "Bangalore", ["Kerala"]), 0)          # nothing to prefer: first
        # Containment with several hits stays ambiguous unless a preference decides.
        self.assertEqual(_choose_option(["A Karnataka B", "C Karnataka D"], "Karnataka"), -1)
        self.assertEqual(_choose_option(["A Karnataka B", "C Karnataka D"], "Karnataka", ["C"]), 1)


class PickedNotRefilledTests(TempDbTestCase):
    def test_dropdown_picks_stay_out_of_the_refill_map(self) -> None:
        from unittest import mock

        from src.apply import worker

        combo = {"id": 1, "tag": "input", "type": "text", "role": "combobox", "label": "Country*",
                 "value": "", "group": "", "name": ""}
        written, handled = {}, set()

        class Sess:
            def log(self, t):
                pass

        with mock.patch.object(worker, "_apply_value", return_value="picked"), \
                mock.patch("src.apply.resolver.resolve", return_value=("India", "profile")):
            worker._sweep(None, [combo], handled, {}, {}, "", Sess(), written=written)
        self.assertEqual(written, {})   # the box is empty after a pick by design


class CityAliasTests(unittest.TestCase):
    def test_renamed_city_in_the_right_state_wins(self) -> None:
        from src.apply import resolver
        from src.apply.worker import _choose_option

        self.assertEqual(resolver.city_aliases("Bangalore, India"), ["Bengaluru, India"])
        self.assertEqual(resolver.city_aliases("Bengaluru"), ["Bangalore"])
        self.assertEqual(resolver.city_aliases("Pune, India"), ["Poona, India"])
        self.assertEqual(resolver.city_aliases("Noida"), [])
        opts = ["Bangalore, Odisha, India", "Bengaluru, Karnataka, India"]
        self.assertEqual(_choose_option(opts, "Bangalore", ["Karnataka", "India"]), 1)
        self.assertEqual(_choose_option(opts, "Bangalore"), 0)   # no state to go by: as typed
        self.assertEqual(_choose_option(["Bengaluru, Karnataka, India"], "Bangalore", ["Karnataka"]), 0)


class ApplyChoiceNoiseTests(unittest.TestCase):
    def test_privacy_notice_link_is_not_an_apply_option(self) -> None:
        from src.apply.worker import _apply_choices

        fields = [
            {"id": 1, "tag": "a", "text": "Apply"},
            {"id": 2, "tag": "a", "text": "Please read our Privacy Notice before you apply."},
        ]
        self.assertEqual([c["id"] for c in _apply_choices(fields)], [1])


class ClickHelperTests(unittest.TestCase):
    def test_blocked_click_falls_back_to_a_direct_one(self) -> None:
        from src.apply import browser

        class Loc:
            def __init__(self):
                self.direct = False

            def click(self, timeout=0):
                raise Exception("Locator.click: Timeout 5000ms exceeded.\n  - <div class=\"overlay\"> intercepts pointer events")

            def evaluate(self, js, timeout=0):
                self.direct = True

        loc = Loc()
        self.assertEqual(browser.click(loc), "clicked (direct)")
        self.assertTrue(loc.direct)

        class Missing(Loc):
            def click(self, timeout=0):
                raise Exception("Locator.click: Element is not attached to the DOM")

        with self.assertRaises(Exception):
            browser.click(Missing())


class DegreeStemTests(unittest.TestCase):
    def test_bachelors_degree_finds_the_bachelor_family(self) -> None:
        from src.apply.worker import _choose_option

        opts = ["Select One", "High School", "Bachelors", "Masters"]
        self.assertEqual(_choose_option(opts, "Bachelor's Degree"), 2)
        opts2 = ["Select One", "Bachelor of Technology", "Master of Science"]
        self.assertEqual(_choose_option(opts2, "Bachelors"), 1)
        self.assertEqual(_choose_option(["Select One", "Masters"], "Bachelor's Degree"), -1)


class LinksOnPageTests(TempDbTestCase):
    def test_named_link_boxes_take_their_link_out_of_the_pool(self) -> None:
        from unittest import mock

        from src.apply import resolver, worker

        data = {"linkedin": "https://linkedin.com/in/test", "github": "https://github.com/test"}
        fields = [
            {"tag": "input", "type": "text", "label": "Please enter your LinkedIn URL", "value": "", "section": "Social Network URLs"},
            {"tag": "input", "type": "text", "label": "URL*", "value": "", "section": "Portfolio (Optional) 1", "ordinal": 0},
        ]
        with mock.patch("src.apply.profile.load_profile", return_value=data):
            taken = worker._links_on_page(fields)
            self.assertIn("https://linkedin.com/in/test", taken)
            fields[0]["taken_links"] = fields[1]["taken_links"] = sorted(taken)
            self.assertEqual(resolver.entry_value(fields[1], data), "https://github.com/test")
            # The named box is not entry 0 of the list: it never gets GitHub.
            self.assertIsNone(resolver.entry_value(fields[0], data))
        self.assertTrue(resolver.generic_url_field({"label": "URL*"}))
        self.assertFalse(resolver.generic_url_field({"label": "Please enter your LinkedIn URL"}))


class DumpCommandTests(TempDbTestCase):
    def test_dump_saves_the_page_and_keeps_waiting(self) -> None:
        import threading

        from src.apply.session import ApplySession

        sess = ApplySession("stamp", "job", "label")
        dumps: list[int] = []
        sess.on_dump = dumps.append
        sess.answer("dump")
        sess.answer("dump 10")
        sess.answer("Bengaluru")
        result: list[str] = []
        worker = threading.Thread(target=lambda: result.append(sess.ask("City?")))
        worker.start()
        worker.join(timeout=5)
        self.assertEqual(result, ["Bengaluru"])   # the dumps were not answers
        self.assertEqual(dumps, [0, 10])

    def test_dump_is_plain_text_when_nothing_handles_it(self) -> None:
        import threading

        from src.apply.session import ApplySession

        sess = ApplySession("stamp", "job", "label")
        sess.answer("dump")
        result: list[str] = []
        worker = threading.Thread(target=lambda: result.append(sess.ask("City?")))
        worker.start()
        worker.join(timeout=5)
        self.assertEqual(result, ["dump"])


class DuplicateRowTests(TempDbTestCase):
    def test_a_row_read_twice_is_one_choice(self) -> None:
        # Workday: the row and its inner text node both read as options, so
        # "+91" saw two "India (+91)" candidates and refused as ambiguous.
        from src.apply import worker

        self.assertEqual(worker._choose_option(["India (+91)", "India (+91)"], "+91"), 0)
        self.assertEqual(worker._choose_option(["Afghanistan (+93)", "Afghanistan (+93)", "India (+91)", "India (+91)"], "+91"), 2)
        # Two DIFFERENT rows containing the value stay ambiguous.
        self.assertEqual(worker._choose_option(["Bangalore, Odisha", "Bangalore, Karnataka"], "Bangalore,"), -1)


class SkillsBoxTests(TempDbTestCase):
    def test_skills_box_detection(self) -> None:
        from src.apply import worker

        self.assertTrue(worker._is_skills_box({"tag": "input", "type": "", "label": "Type to Add Skills", "section": "Skills (Optional)"}))
        self.assertTrue(worker._is_skills_box({"tag": "textarea", "type": "", "label": "Skills", "section": ""}))
        self.assertTrue(worker._is_skills_box({"tag": "input", "type": "text", "label": "Type to add", "section": "Skills"}))
        self.assertTrue(worker._is_skills_box(
            {"tag": "textarea", "type": "", "label": "Separate each skill with a comma.", "section": "Skills :"}))
        self.assertTrue(worker._is_skills_box({"tag": "input", "type": "text", "label": "Key skills", "section": ""}))
        self.assertFalse(worker._is_skills_box({"tag": "input", "type": "text", "label": "Job Title*", "section": "Skills"}))
        # An essay question that mentions the word got the comma-separated
        # list of skills written into it (Xplor).
        essay = ("What's a professional skill you've developed in the past year that wasn't on "
                 "your radar before, and how did the opportunity to learn it arise?")
        self.assertFalse(worker._is_skills_box({"tag": "textarea", "type": "", "label": essay, "section": ""}))
        self.assertFalse(worker._is_skills_box(
            {"tag": "textarea", "type": "", "label": "Describe your skills in detail", "section": ""}))
        self.assertFalse(worker._is_skills_box({"tag": "button", "type": "", "label": "Skills", "section": ""}))
        self.assertFalse(worker._is_skills_box({"tag": "input", "type": "file", "label": "Skills matrix", "section": ""}))

    def test_profile_skills_split(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._profile_skills({"skills": "JavaScript, Node.js; Python\nC++, javascript"}),
                         ["JavaScript", "Node.js", "Python", "C++"])
        self.assertEqual(worker._profile_skills({}), [])

    def test_a_stated_limit_is_respected(self) -> None:
        from src.apply import worker

        # Workday: "Add up to 10 skills that highlight your professional
        # abilities." A longer profile list would fail past the tenth.
        field = {"section": "Skills (Optional)", "label": "Type to Add Skills",
                 "text": "", "group": "Add up to 10 skills that highlight your professional abilities."}
        self.assertEqual(worker._skill_cap(field), 10)
        self.assertEqual(worker._skill_cap({"section": "Skills", "group": "maximum of 5 skills"}), 5)
        self.assertEqual(worker._skill_cap({"section": "Skills :", "label": "Separate each skill with a comma."}), 0)

    def test_the_widget_kind_is_read_from_the_wording_not_the_attributes(self) -> None:
        from src.apply import worker

        # Workday's chip typeahead reports NO dom hint at all (dump
        # outputs/dom/..._20260910-225356/fields.json, field 43): role,
        # aria-haspopup and aria-autocomplete are empty and autocomplete is
        # "off". Anything keyed on those attributes would call it a plain text
        # box and dump one nonsense comma chip into it.
        workday = {"tag": "input", "type": "", "role": "", "haspopup": "",
                   "autocomplete": "off", "label": "Type to Add Skills",
                   "section": "Skills (Optional)"}
        self.assertEqual(worker._skills_widget(workday), "typeahead")
        # Esko says what it wants, so the whole list goes in at once.
        esko = {"tag": "textarea", "type": "", "label": "Separate each skill with a comma.",
                "section": "Skills :"}
        self.assertEqual(worker._skills_widget(esko), "text")
        # A single-line box that states its separator is text as well.
        self.assertEqual(worker._skills_widget(
            {"tag": "input", "type": "text", "label": "Skills", "section": "",
             "group": "Comma-separated"}), "text")
        self.assertEqual(worker._skills_widget(
            {"tag": "select", "label": "Skills", "options": ["Python"]}), "select")
        self.assertEqual(worker._skills_widget(
            {"tag": "button", "haspopup": "listbox", "label": "Select skills"}), "select")

    def test_a_skills_dropdown_is_recognised_at_all(self) -> None:
        from src.apply import worker

        # Neither shape reached _fill_skills before: the box had to be an
        # input or a textarea, so a skills dropdown went to the model.
        self.assertTrue(worker._is_skills_box(
            {"tag": "select", "label": "Select skills", "section": "Skills",
             "options": ["Python", "AWS"]}))
        self.assertTrue(worker._is_skills_box(
            {"tag": "button", "haspopup": "listbox", "label": "Add skills", "section": "Skills"}))
        # A Skills section holds other controls too; the label still decides.
        self.assertFalse(worker._is_skills_box(
            {"tag": "select", "label": "Proficiency", "section": "Skills", "options": ["Expert"]}))
        self.assertFalse(worker._is_skills_box(
            {"tag": "select", "label": "Years of use", "section": "Skills", "options": ["1"]}))

    def test_a_multi_select_takes_every_skill_it_offers(self) -> None:
        from src.apply import worker

        picked: list[list[str]] = []
        logged: list[str] = []

        class Locator:
            def select_option(self, label=None, timeout=0):
                picked.append(list(label))

            def evaluate(self, js, timeout=0):
                return ["Python", "Amazon Web Services (AWS)", "Cobol"]

        class Sess:
            def log(self, text):
                logged.append(text)

        field = {"id": 1, "tag": "select", "label": "Select skills", "section": "Skills",
                 "multiple": True, "options": ["Python", "Amazon Web Services (AWS)", "Cobol"]}
        worker._pick_skills(None, Locator(), field, ["Python", "AWS", "Haskell"],
                            "Select skills", Sess())
        self.assertEqual(picked, [["Python", "Amazon Web Services (AWS)"]])
        # The skill the list does not carry is named, never guessed at.
        self.assertTrue(any("Haskell" in line for line in logged), logged)

    def test_a_single_choice_list_takes_only_the_first_that_fits(self) -> None:
        from src.apply import worker

        picked: list[list[str]] = []

        class Locator:
            def select_option(self, label=None, timeout=0):
                picked.append(list(label))

            def evaluate(self, js, timeout=0):
                return ["Python", "AWS"]

        class Sess:
            def log(self, text):
                pass

        field = {"id": 1, "tag": "select", "label": "Primary skill", "section": "Skills",
                 "options": ["Python", "AWS"]}
        worker._pick_skills(None, Locator(), field, ["Python", "AWS"], "Primary skill", Sess())
        self.assertEqual(picked, [["Python"]])

    def test_a_list_holding_none_of_the_skills_is_an_error_not_a_guess(self) -> None:
        from src.apply import worker

        class Locator:
            def select_option(self, label=None, timeout=0):
                raise AssertionError("nothing should be selected")

            def evaluate(self, js, timeout=0):
                return ["Cobol", "Fortran"]

        class Sess:
            def log(self, text):
                pass

        field = {"id": 1, "tag": "select", "label": "Skills", "section": "Skills",
                 "options": ["Cobol", "Fortran"]}
        with self.assertRaises(ValueError):
            worker._pick_skills(None, Locator(), field, ["Python", "AWS"], "Skills", Sess())


class SettleTests(TempDbTestCase):
    """A fixed sleep spent its whole budget however fast the page was."""

    class Page:
        """Counts controls; grows after `grows_at` polls and then holds."""

        def __init__(self, start=3, grows_at=None, grows_to=6):
            self.url = "https://example.invalid/form"
            self.count = start
            self.polls = 0
            self.slept = 0
            self.grows_at = grows_at
            self.grows_to = grows_to

        def wait_for_timeout(self, ms):
            self.slept += ms
            self.polls += 1
            if self.polls == self.grows_at:   # grows once, then holds
                self.count = self.grows_to

    def _patched(self, page):
        from src.apply import browser

        class Frame:
            def evaluate(self, js):
                return f"{page.count}:0:0"

        return unittest.mock.patch.object(browser, "target", lambda p: Frame())

    def test_a_page_that_settles_early_is_not_waited_out(self) -> None:
        from src.apply import browser

        page = self.Page(grows_at=1)
        with self._patched(page):
            before = browser.page_shape(page)
            self.assertTrue(browser.settle(page, before, 800))
        # Changed on the first poll, then two quiet polls to be sure the
        # framework had finished re-rendering: 300ms, not 800ms.
        self.assertEqual(page.slept, 300)

    def test_a_page_that_never_changes_waits_the_whole_budget(self) -> None:
        from src.apply import browser

        page = self.Page()
        with self._patched(page):
            before = browser.page_shape(page)
            self.assertFalse(browser.settle(page, before, 800))
        self.assertEqual(page.slept, 800)

    def test_a_slow_render_is_still_waited_for(self) -> None:
        from src.apply import browser

        page = self.Page(grows_at=5)
        with self._patched(page):
            before = browser.page_shape(page)
            self.assertTrue(browser.settle(page, before, 1500))
        self.assertEqual(page.slept, 700)

    def test_a_page_still_rendering_is_not_read_half_built(self) -> None:
        from src.apply import browser

        # Two renders in a row (a framework adding the entry, then filling
        # it in): settle must not return between them.
        page = self.Page(grows_at=1)
        with self._patched(page):
            before = browser.page_shape(page)
            original = page.wait_for_timeout

            def churn(ms):
                original(ms)
                if page.polls == 2:
                    page.count += 1   # a second render

            page.wait_for_timeout = churn
            self.assertTrue(browser.settle(page, before, 900))
        self.assertEqual(page.slept, 400)


class AliasOptionTests(TempDbTestCase):
    def test_an_options_own_alias_beats_a_prefix_match(self) -> None:
        from src.apply import worker

        rows = ["AWS VPN", "Amazon Web Services (AWS)", "AWS Cloud9", "AWS SDK"]
        self.assertEqual(worker._choose_option(rows, "AWS"), 1)
        self.assertEqual(worker._choose_option(rows, "AWS SDK"), 3)       # exact still first
        self.assertEqual(worker._choose_option(rows, "AWS Cloud9"), 2)
        self.assertEqual(worker._choose_option(["India (+91)", "Indonesia (+62)"], "India"), 0)


class ProposalTests(TempDbTestCase):
    def test_the_value_a_question_proposes(self) -> None:
        from src.apply import worker

        q = ("What desired annual salary should I enter here (the field rejected '22 lpa' "
             "- should it be a plain number like 2200000 INR)?")
        self.assertEqual(worker._proposal_in_question(q), "2200000 INR")
        self.assertEqual(worker._proposal_in_question("Should I set it to India (+91)?"), "India (+91)")
        self.assertEqual(worker._proposal_in_question("What should I enter for 'City'?"), "")

    def test_auto_pick_matches_the_new_pill(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._auto_pick(["LinkedIn corporate page"], "Linkedin", "Linkedin"), "LinkedIn corporate page")
        self.assertEqual(worker._auto_pick(["India (+91)"], "+91", "India"), "India (+91)")
        self.assertEqual(worker._auto_pick(["Job Fair"], "Linkedin", "Linkedin"), "")
        self.assertEqual(worker._auto_pick([], "Linkedin", "Linkedin"), "")

    def test_number_box_takes_a_salary_as_digits(self) -> None:
        from src.apply import salary

        self.assertEqual(salary.parse_annual_inr("22 lpa"), 2200000)
        self.assertIsNone(salary.parse_annual_inr("yes"))


class GrownOptionTests(TempDbTestCase):
    def test_one_word_matches_the_single_option_growing_out_of_it(self) -> None:
        from src.apply import worker

        # ALTEN's salary period: the profile says "Annual", the list says
        # "Annually", and no word-boundary rule can join them.
        self.assertEqual(worker._choose_option(["Monthly", "Annually", "Weekly"], "Annual"), 1)
        self.assertEqual(worker._choose_option(["Contractual", "Permanent"], "Contract"), 0)
        # Never when several options qualify, and never the other direction.
        self.assertEqual(worker._choose_option(["Annually", "Annualized"], "Annual"), -1)
        # A whole-word hit still wins over a grown one, as at every level.
        self.assertEqual(worker._choose_option(["Annually", "Annual Bonus"], "Annual"), 1)
        self.assertEqual(worker._choose_option(["Mobile"], "Mobile phone"), -1)
        self.assertEqual(worker._choose_option(["Javascript Coding", "JavaScript"], "JavaScript"), 1)


class DateBoxTests(TempDbTestCase):
    """Esko/Phenom keeps a whole date in one box showing MM/YYYY."""

    def test_which_boxes_are_dates(self) -> None:
        from src.apply import worker

        self.assertTrue(worker._is_date_box({"tag": "input", "type": "text", "label": "From*"}))
        self.assertTrue(worker._is_date_box({"tag": "input", "type": "text", "label": "To*"}))
        self.assertTrue(worker._is_date_box(
            {"tag": "input", "type": "text", "label": "", "elid": "experienceData[0].fromTo.startDate"}))
        self.assertFalse(worker._is_date_box({"tag": "input", "type": "text", "label": "Company*"}))
        self.assertFalse(worker._is_date_box({"tag": "select", "type": "", "label": "From*"}))
        # Names that merely contain the letters: a "candidate" box is not a date.
        for name in ("candidate", "candidateName", "update", "validate_email"):
            self.assertFalse(worker._is_date_box({"tag": "input", "type": "text", "label": "Name", "elid": name}), name)
        for name in ("start_date", "startDate", "date", "dob", "experienceData[1].fromTo.endDate"):
            self.assertTrue(worker._is_date_box({"tag": "input", "type": "text", "label": "", "elid": name}), name)

    def test_parsing_what_the_model_writes(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._parse_date("Jul 2020"), (7, None, 2020))
        self.assertEqual(worker._parse_date("July 2020"), (7, None, 2020))
        self.assertEqual(worker._parse_date("07/2020"), (7, None, 2020))
        self.assertEqual(worker._parse_date("2020-07"), (7, None, 2020))
        self.assertEqual(worker._parse_date("15 March 2019"), (3, 15, 2019))
        self.assertEqual(worker._parse_date("03/15/2019"), (3, 15, 2019))
        self.assertEqual(worker._parse_date("no date here"), (None, None, None))

    def test_the_box_keeps_its_own_format(self) -> None:
        from src.apply import worker

        # Learnt from what the box already shows, else from its placeholder.
        self.assertEqual(worker._date_mask({}, "07/2026"), "MM/YYYY")
        self.assertEqual(worker._date_mask({}, "2019-07-15"), "YYYY-MM-DD")
        self.assertEqual(worker._date_mask({"value": "03-2019"}, ""), "MM-YYYY")
        self.assertEqual(worker._date_mask({}, "MM/DD/YYYY"), "MM/DD/YYYY")
        self.assertEqual(worker._date_mask({}, ""), "")
        self.assertEqual(worker._format_date(7, None, 2020, "MM/YYYY"), "07/2020")
        self.assertEqual(worker._format_date(7, 15, 2019, "MM/DD/YYYY"), "07/15/2019")
        self.assertEqual(worker._format_date(7, 15, 2019, "YYYY-MM-DD"), "2019-07-15")
        # A date the mask cannot be filled from is refused, not half-written.
        self.assertEqual(worker._format_date(7, None, None, "MM/YYYY"), "")
        self.assertEqual(worker._format_date(7, None, 2020, "MM/DD/YYYY"), "")

    def test_what_the_box_shows_is_compared_by_digits(self) -> None:
        from src.apply import worker

        self.assertTrue(worker._same_digits("07/2020", "07/2020"))
        self.assertTrue(worker._same_digits("07-2020", "07/2020"))   # widget reformats
        self.assertFalse(worker._same_digits("", "07/2020"))         # wiped on close
        self.assertFalse(worker._same_digits("07/2026", "07/2020"))  # the year bug

    def test_the_calendar_header_names_its_month(self) -> None:
        from src.apply import worker

        self.assertEqual(worker._header_month("July 2020"), 7)
        self.assertEqual(worker._header_month("Jan 2026"), 1)
        self.assertIsNone(worker._header_month("2026"))   # month/year picker: year only


class RemoveExcludedEntryTests(TempDbTestCase):
    """A site that parses the uploaded resume adds the flagged project as a
    job; the agent takes that entry out again."""

    def setUp(self) -> None:
        super().setUp()
        from unittest import mock

        patcher = mock.patch("src.apply.profile.load_profile",
                             return_value={"not_employment": "Applied AI & LLM Agents"})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _entry(title: str, company: str, first_id: int) -> list[dict]:
        section = "Work Experience :"
        return [
            {"id": first_id, "tag": "input", "type": "text", "label": "Job Title*",
             "section": section, "value": title},
            {"id": first_id + 1, "tag": "input", "type": "text", "label": "Company*",
             "section": section, "value": company},
            {"id": first_id + 2, "tag": "textarea", "label": "Role description",
             "section": section, "value": ""},
            {"id": first_id + 3, "tag": "button", "text": "Remove experience",
             "label": "Remove experience", "section": section},
        ]

    def test_the_flagged_entry_is_the_one_removed(self) -> None:
        from src.apply import worker

        fields = (self._entry("Applied AI & LLM Agents", "", 10)
                  + self._entry("Software Engineer", "Initech Systems", 20))
        removals = worker._excluded_entry_removals(fields)
        self.assertEqual(len(removals), 1, removals)
        project, button = removals[0]
        self.assertEqual(project, "Applied AI & LLM Agents")
        self.assertEqual(button["id"], 13)   # the flagged entry's own button

    def test_the_project_named_in_the_company_box_counts(self) -> None:
        from src.apply import worker

        fields = self._entry("Founder", "Applied AI and LLM Agents", 10)
        self.assertEqual(len(worker._excluded_entry_removals(fields)), 1)

    def test_real_jobs_are_never_removed(self) -> None:
        from src.apply import worker

        fields = (self._entry("Software Engineer", "Initech Systems", 10)
                  + self._entry("Software Engineer Intern", "Initech Systems", 20))
        self.assertEqual(worker._excluded_entry_removals(fields), [])
        # An entry with no remove button of its own is left alone.
        lonely = self._entry("Applied AI & LLM Agents", "", 10)[:-1]
        self.assertEqual(worker._excluded_entry_removals(lonely), [])

    def test_remove_button_wording(self) -> None:
        from src.apply.worker import REMOVE_ENTRY_RE

        for text in ("Remove experience", "- Remove", "Delete entry", "Remove this position"):
            self.assertTrue(REMOVE_ENTRY_RE.match(text), text)
        for text in ("Remove language", "Remove education", "Removed"):
            self.assertFalse(REMOVE_ENTRY_RE.match(text), text)


class ShortYesTests(TempDbTestCase):
    def test_a_sentence_is_never_a_yes(self) -> None:
        from src.apply.worker import _is_short_yes

        for reply in ("yes", "Yes please", "ok", "sure", "y", "yep"):
            self.assertTrue(_is_short_yes(reply), reply)
        # The exact reply that clicked Next: "on" (an affirmative, for
        # checkbox values) sat inside "on your radar".
        self.assertFalse(_is_short_yes(
            "redo What's a professional skill you've developed in the past year "
            "that wasn't on your radar before"))
        self.assertFalse(_is_short_yes("yes but change the salary to 30 first"))
        self.assertFalse(_is_short_yes("the location should be Pune on the second step"))
        self.assertFalse(_is_short_yes("no"))
        self.assertFalse(_is_short_yes(""))


class OptionalQuestionTests(TempDbTestCase):
    """An optional question the model skipped is retired quietly; the review
    prompt must still name it, and "llm: <question>" must find its box."""

    QUESTION = "Is there anything else you'd like us to know about you that is not captured in your application?"

    def _fields(self) -> list[dict]:
        return [
            {"id": 1, "tag": "textarea", "type": "", "label": self.QUESTION, "value": "", "required": False},
            {"id": 2, "tag": "textarea", "type": "", "label": "If yes, please list the names of the individual(s).",
             "value": "", "required": False},
            {"id": 3, "tag": "input", "type": "text", "label": "Middle name", "value": "", "required": False},
            {"id": 4, "tag": "input", "type": "text", "label": "What is your current CTC? (In lakh rupees/INR)",
             "value": "25", "required": True},
        ]

    def test_only_real_unanswered_questions_are_reported(self) -> None:
        from src.apply import worker

        spare = worker._unanswered_questions(self._fields())
        self.assertEqual([f["id"] for f in spare], [1])

    def test_the_quoted_question_finds_its_box(self) -> None:
        from src.apply import worker

        fields = self._fields()
        self.assertEqual(worker._field_for_question(fields, self.QUESTION)["id"], 1)
        # A paraphrase still lands on it.
        self.assertEqual(
            worker._field_for_question(fields, "anything else about you not captured")["id"], 1)
        # Nothing empty to answer: no guess.
        self.assertIsNone(worker._field_for_question([fields[3]], self.QUESTION))

    def test_two_questions_need_the_words_to_choose(self) -> None:
        from src.apply import worker

        fields = self._fields() + [
            {"id": 5, "tag": "textarea", "type": "", "required": False, "value": "",
             "label": "Why do you want to work at this company, and what draws you to the role?"},
        ]
        self.assertEqual(worker._field_for_question(fields, "why do you want to work here")["id"], 5)
        self.assertEqual(worker._field_for_question(fields, self.QUESTION)["id"], 1)
        # An instruction that names neither is not guessed at.
        self.assertIsNone(worker._field_for_question(fields, "make it shorter"))


class RadioAnswerTests(TempDbTestCase):
    """A yes/no answer selects the option that matches it. Applying "no" to
    the Yes radio asked for an impossible uncheck and failed the form."""

    @staticmethod
    def _group() -> list[dict]:
        question = "Have you applied to Xplor for another role in the past 12 months?"
        return [
            {"id": 1, "tag": "input", "type": "radio", "label": "Yes", "name": "applied_before",
             "group": question, "section": "Questions", "checked": False},
            {"id": 2, "tag": "input", "type": "radio", "label": "No", "name": "applied_before",
             "group": question, "section": "Questions", "checked": False},
        ]

    def test_the_matching_option_is_found(self) -> None:
        from src.apply import worker

        yes, no = self._group()
        self.assertEqual(worker._sibling_option([yes, no], yes, "no"), no)
        self.assertEqual(worker._sibling_option([yes, no], no, "yes"), yes)
        # An answer that names neither option leaves the choice to the model.
        self.assertIsNone(worker._sibling_option([yes, no], yes, "maybe next year"))

    def test_options_of_another_question_are_never_touched(self) -> None:
        from src.apply import worker

        yes, no = self._group()
        other = {"id": 3, "tag": "input", "type": "radio", "label": "No", "name": "sponsorship",
                 "group": "Do you need sponsorship?", "section": "Questions", "checked": False}
        self.assertEqual(worker._sibling_option([yes, no, other], yes, "no"), no)
        # Same section, different question: not a sibling.
        self.assertIsNone(worker._sibling_option([yes, other], yes, "no"))

    def test_an_agreeing_answer_stays_on_its_own_option(self) -> None:
        from src.apply.worker import _option_agrees

        yes, no = self._group()
        self.assertTrue(_option_agrees(no, "no"))
        self.assertFalse(_option_agrees(yes, "no"))
        self.assertTrue(_option_agrees(yes, "yes"))


class ContactGuardTests(TempDbTestCase):
    def test_a_contact_box_changed_under_us_is_put_back(self) -> None:
        from src.apply import worker

        email = {"tag": "input", "type": "text", "label": "Email address*",
                 "value": "acandidate@example.invalid"}
        self.assertTrue(worker._contact_went_wrong(email, "a_candidate@example.invalid"))
        self.assertFalse(worker._contact_went_wrong(
            {**email, "value": "a_candidate@example.invalid"}, "a_candidate@example.invalid"))
        # An empty box is the refill path's business, not this one.
        self.assertFalse(worker._contact_went_wrong({**email, "value": ""}, "a_candidate@example.invalid"))
        # Other fields may legitimately change (a widget reformats a date).
        self.assertFalse(worker._contact_went_wrong(
            {"tag": "input", "type": "text", "label": "From*", "value": "07/2020"}, "Jul 2020"))

    def test_the_warning_names_the_box_and_both_values(self) -> None:
        from unittest import mock

        from src.apply import worker

        fields = [
            {"tag": "input", "type": "text", "label": "Email address*", "value": "acandidate@example.invalid"},
            {"tag": "input", "type": "tel", "label": "Phone number*", "value": "+91 90000 00000"},
            {"tag": "input", "type": "text", "label": "City", "value": "Mumbai"},
        ]
        data = {"email": "a_candidate@example.invalid", "phone": "9000000000", "location": "Bangalore"}
        with mock.patch("src.apply.profile.load_profile", return_value=data):
            warnings = worker._contact_warnings(fields)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("Email address*", warnings[0])
        self.assertIn("acandidate@example.invalid", warnings[0])
        self.assertIn("a_candidate@example.invalid", warnings[0])


class NotEmploymentTests(TempDbTestCase):
    """Self-directed AI project work belongs on the resume, never in a Work
    Experience entry."""

    def setUp(self) -> None:
        super().setUp()
        from unittest import mock

        patcher = mock.patch("src.apply.profile.load_profile",
                             return_value={"not_employment": "Applied AI & LLM Agents; Side Project X"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_flagged_entries_are_read_from_the_profile(self) -> None:
        from src.apply import worker

        self.assertEqual(worker.not_employment(), ["Applied AI & LLM Agents", "Side Project X"])
        note = worker._not_employment_note()
        self.assertIn("Applied AI & LLM Agents", note)
        self.assertIn("never enter them as a work experience", note.lower())

    def test_which_boxes_record_employment(self) -> None:
        from src.apply import worker

        self.assertTrue(worker._is_employment_field(
            {"label": "Job Title*", "section": "Work Experience :", "group": ""}))
        self.assertTrue(worker._is_employment_field(
            {"label": "Company*", "section": "Employment History", "group": ""}))
        self.assertTrue(worker._is_employment_field(
            {"label": "Role description", "section": "", "elid": "experienceData[0].description"}))
        # Education and the rest of the form are untouched by this rule.
        self.assertFalse(worker._is_employment_field(
            {"label": "School or University*", "section": "Education :", "group": ""}))
        self.assertFalse(worker._is_employment_field(
            {"label": "Job Title*", "section": "", "group": ""}))

    def test_a_flagged_project_is_refused_in_an_employment_box(self) -> None:
        from src.apply import worker

        title = {"label": "Job Title*", "section": "Work Experience :", "group": ""}
        self.assertEqual(worker._excluded_experience(title, "Applied AI & LLM Agents"),
                         "Applied AI & LLM Agents")
        # The same words in another order or case still count.
        self.assertEqual(worker._excluded_experience(title, "applied ai and llm agents (self)"),
                         "Applied AI & LLM Agents")
        # A real employer passes, and so does the project name elsewhere on
        # the form (a portfolio or a "tell us about a project" answer).
        self.assertEqual(worker._excluded_experience(title, "Initech Systems"), "")
        self.assertEqual(worker._excluded_experience(
            {"label": "Describe a project", "section": "Questions"}, "Applied AI & LLM Agents"), "")


class SectionAddTextTests(TempDbTestCase):
    def test_a_plus_prefixed_button_is_still_a_section_add(self) -> None:
        from src.apply import worker

        # Phenom renders "+ Add Language" and labels it "Add language"; the
        # "+" kept the Languages section from ever being opened.
        field = {"tag": "button", "text": "+ Add Language", "label": "Add language",
                 "section": "Languages :", "group": "Languages :"}
        self.assertTrue(worker._is_section_add(field))
        self.assertTrue(worker._is_section_add(
            {"tag": "button", "text": "+ Add Experience", "label": "Add experience",
             "section": "Work Experience :", "group": ""}))
        self.assertFalse(worker._is_section_add(
            {"tag": "button", "text": "+ Add to favourites", "label": "", "section": "Languages :"}))


class NearOptionTests(TempDbTestCase):
    def test_the_refusal_names_options_worth_looking_at(self) -> None:
        from src.apply import worker

        options = ["Please Select", "Accounting", "Actuarial Science", "Advertising",
                   "Computer Engineering", "Computer and Information Science", "Nursing"]
        near = worker._near_options("Computer Science", options)
        self.assertIn("Computer and Information Science", near)
        self.assertIn("Computer Engineering", near)
        self.assertNotIn("Accounting", near)
        # Nothing shares a word: fall back to the first few.
        self.assertEqual(worker._near_options("Zzz", options, limit=2), options[:2])


class DropZoneTests(TempDbTestCase):
    def test_drop_zone_wording(self) -> None:
        from src.apply.worker import DROPZONE_RE

        for text in ("Drag and Drop Your Resume OR Browse File",
                     "Drop your file here", "drag & drop a file", "Browse File"):
            self.assertTrue(DROPZONE_RE.search(text), text)
        for text in ("Upload your resume", "Select files", "Attach a cover letter"):
            self.assertFalse(DROPZONE_RE.search(text), text)


class EntryLocationTests(TempDbTestCase):
    def test_current_employers_entries_get_the_profile_location(self) -> None:
        from src.apply import worker

        data = {"current_company": "Initech Systems", "current_company_location": "Noida"}
        fields = [
            {"label": "Company*", "section": "Work History (Optional) 2", "value": "Initech Systems"},
            {"label": "Location", "section": "Work History (Optional) 2", "value": ""},
            {"label": "Company*", "section": "Work History (Optional) 1", "value": "Other Corp"},
            {"label": "Location", "section": "Work History (Optional) 1", "value": ""},
            {"label": "Location", "section": "Education 1", "value": ""},
        ]
        self.assertEqual(worker._entry_location(fields[1], fields, data), "Noida")
        self.assertIsNone(worker._entry_location(fields[3], fields, data))   # another employer
        self.assertIsNone(worker._entry_location(fields[4], fields, data))   # not a job entry
        self.assertIsNone(worker._entry_location(fields[1], fields, {"current_company": "Initech Systems"}))


class PromptFieldTrimTests(TempDbTestCase):
    """The form-field JSON is the biggest part of an apply prompt, and a
    quarter of it was empty keys. Dropping them is only safe while every
    meaningful zero survives: ordinal 0 is the FIRST entry of a repeating
    section, and checked false is a box that is not ticked."""

    def _fields(self):
        return [
            {"id": 0, "tag": "input", "type": "text", "role": "", "haspopup": "",
             "autocomplete": "", "path": "html>body>input", "section": "", "ordinal": 0,
             "group": "", "label": "Full name", "name": "", "accept": "", "elid": "",
             "required": True, "value": "", "text": "", "maxlength": 0},
            {"id": 1, "tag": "input", "type": "checkbox", "role": "", "haspopup": "",
             "autocomplete": "", "path": "html>body>input", "section": "Work Experience",
             "ordinal": 0, "group": "Consent", "label": "I currently work here",
             "name": "", "accept": "", "elid": "", "required": False, "value": "on",
             "text": "", "maxlength": 0, "checked": False},
        ]

    def _sent(self, handled=None):
        from src.apply import worker

        page = type("P", (), {"url": "https://x/apply"})()
        with unittest.mock.patch.object(worker.browser, "page_text", lambda *a, **k: "page"):
            prompt = worker._build_prompt(
                {"title": "SWE", "company": "X", "description": "d"}, "resume",
                self._fields(), page, [], [], handled or set())
        blob = prompt.split("FORM FIELDS:\n", 1)[1].split("\n\nALREADY DONE:")[0]
        return json.loads(blob)

    def test_empty_keys_are_not_sent(self) -> None:
        sent = self._sent()
        for gone in ("role", "haspopup", "autocomplete", "name", "accept", "elid", "text"):
            self.assertNotIn(gone, sent[0], gone)
        self.assertNotIn("path", sent[0])          # never sent, empty or not

    def test_meaningful_zeroes_survive(self) -> None:
        sent = self._sent()
        self.assertEqual(sent[0]["id"], 0)         # the model addresses fields by id
        self.assertEqual(sent[0]["ordinal"], 0)    # the first entry, not "no entry"
        self.assertIs(sent[1]["checked"], False)   # an unticked box, not a missing one
        self.assertIs(sent[1]["required"], False)

    def test_what_the_model_needs_is_still_there(self) -> None:
        sent = self._sent()
        self.assertEqual(sent[0]["label"], "Full name")
        self.assertEqual(sent[0]["type"], "text")
        self.assertEqual(sent[1]["section"], "Work Experience")
        self.assertEqual(sent[1]["group"], "Consent")
        self.assertEqual(sent[1]["value"], "on")

    def test_a_handled_field_is_still_marked(self) -> None:
        key = _field_key(self._fields()[0], "Full name")
        sent = self._sent(handled={key})
        self.assertTrue(sent[0].get("already_handled"))

    def test_the_json_actually_got_smaller(self) -> None:
        fat = json.dumps([{k: v for k, v in f.items() if k != "path"} for f in self._fields()])
        self.assertLess(len(json.dumps(self._sent())), len(fat) * 0.75)


class SubmittedBaselineTests(unittest.TestCase):
    """Only wording that was NOT on the page at the first read may record an
    application as applied.

    Scanning the whole page for "thank you for your application" and acting
    on a match would have recorded a job as applied on the first read of a
    form whose DESCRIPTION ends with that sentence, and closed the browser on
    a form the candidate had not touched.
    """

    DESCRIPTION = ("Senior Engineer. Thank you for your application, shortlisted "
                   "candidates will be contacted within five days.")
    CONFIRMED = DESCRIPTION + " Your application was sent to Acme! Track it in My Jobs."
    FORM = [{"tag": "input", "type": "text", "label": "Full name", "id": 1}]
    BUTTONS_ONLY = [{"tag": "button", "type": "button", "text": "Done", "id": 1}]

    def _page_saying(self, text: str):
        from src.apply import browser

        self.addCleanup(setattr, browser, "full_page_text", browser.full_page_text)
        browser.full_page_text = lambda page: text
        return object()

    def test_boilerplate_at_the_first_read_is_not_a_confirmation(self) -> None:
        holder = {}
        page = self._page_saying(self.DESCRIPTION)
        worker._take_submitted_baseline(page, self.FORM, holder)
        self.assertEqual(worker._newly_submitted(page, holder), "")

    def test_a_confirmation_that_arrives_later_is(self) -> None:
        holder = {}
        worker._take_submitted_baseline(self._page_saying(self.DESCRIPTION), self.FORM, holder)
        found = worker._newly_submitted(self._page_saying(self.CONFIRMED), holder)
        self.assertIn("application was sent to acme", found)

    def test_the_same_sentence_again_is_not_new(self) -> None:
        # The description repeated verbatim (a re-render) must not count.
        holder = {}
        worker._take_submitted_baseline(self._page_saying(self.DESCRIPTION), self.FORM, holder)
        self.assertEqual(worker._newly_submitted(
            self._page_saying(self.DESCRIPTION + " " + self.DESCRIPTION), holder), "")

    def test_a_page_with_nothing_fillable_is_taken_at_its_word(self) -> None:
        # An apply link that bounced to "you already applied": buttons and
        # links only, so the wording IS the page's meaning.
        holder = {}
        page = self._page_saying("Your application has been submitted. Back to listings")
        worker._take_submitted_baseline(page, self.BUTTONS_ONLY, holder)
        self.assertTrue(worker._newly_submitted(page, holder))

    def test_no_baseline_means_no_verdict(self) -> None:
        self.assertEqual(worker._newly_submitted(self._page_saying(self.CONFIRMED), {}), "")

    def test_marks_tell_sentences_apart_by_what_follows(self) -> None:
        marks = worker._submitted_marks(self.CONFIRMED)
        self.assertEqual(len(marks), 2, marks)


class FilePickerChoiceTests(unittest.TestCase):
    """Which of the two attachments a file picker gets.

    Reading only the input's own attributes sent the resume to a Jobvite
    "Add Cover Letter" button three times in a row: the input behind that
    button has no name, id or label, so the cover-letter branch could not
    match and the resume branch - which only required NOT matching cover
    letter - always did. The candidate parked the job.
    """

    def test_the_inputs_own_attributes_win(self) -> None:
        self.assertEqual(worker._picker_wants({"own": "coverLetterFile", "around": []}), "letter")
        self.assertEqual(worker._picker_wants({"own": "resume_upload", "around": []}), "resume")

    def test_an_anonymous_input_reads_what_is_around_it(self) -> None:
        self.assertEqual(
            worker._picker_wants({"own": " ", "around": ["Add Cover Letter", "Apply to this job"]}),
            "letter")
        self.assertEqual(
            worker._picker_wants({"own": "", "around": ["Attach Resume", "Apply to this job"]}),
            "resume")

    def test_the_nearest_container_that_names_one_decides(self) -> None:
        # Far enough up, everything holds the whole form and names both.
        context = {"own": "", "around": ["Add Cover Letter",
                                         "Attach Resume Add Cover Letter Send Application"]}
        self.assertEqual(worker._picker_wants(context), "letter")

    def test_a_container_naming_both_settles_nothing(self) -> None:
        self.assertEqual(
            worker._picker_wants({"own": "", "around": ["Attach Resume Add Cover Letter"]}), "")

    def test_nothing_at_all_is_not_a_guess(self) -> None:
        # The caller asks rather than picking: the wrong file in a
        # cover-letter slot is an application sent with two resumes.
        self.assertEqual(worker._picker_wants({"own": "", "around": ["Upload", "Apply"]}), "")
        self.assertEqual(worker._picker_wants({}), "")


class _PickerPage:
    """Enough of a Page for the file-chooser handler: it remembers what was
    registered so the test can fire a chooser at it."""

    def __init__(self) -> None:
        self.handler = None

    def on(self, event, fn):
        self.handler = fn


class _PickerChooser:
    def __init__(self, own: str = "", around=()) -> None:
        payload = json.dumps({"own": own, "around": list(around)})
        self.element = types.SimpleNamespace(evaluate=lambda js: payload)
        self.files = ""

    def set_files(self, path):
        self.files = path


class _PickerAttach:
    def __init__(self, resume="C:/tmp/Resume.pdf", letter="C:/tmp/Letter.pdf") -> None:
        self.resume_path = resume
        self.letter_pdf = letter


class _PickerSess:
    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, text):
        self.logs.append(text)


class TileClickPickerTests(unittest.TestCase):
    """A picker the agent itself opened knows which slot it is for.

    Jobvite's buttons front no file input at all - each one builds its input
    on the click and throws it away - so nothing can be set directly and the
    candidate was told to click the button by hand on a form the agent was
    otherwise filling. The agent clicks it now, and because it clicked a tile
    it had already classified, the slot is known rather than inferred from an
    anonymous input.
    """

    def _fire(self, page, chooser):
        worker._arm_file_chooser(page, self.attach, self.sess)
        page.handler(chooser)

    def setUp(self) -> None:
        self.attach = _PickerAttach()
        self.sess = _PickerSess()

    def test_a_tile_we_clicked_pins_the_slot(self) -> None:
        page = _PickerPage()
        page._oea_chooser_want = "letter"
        # An input with nothing on it and nothing around it: inference would
        # refuse, because both attachments are prepared.
        self._fire(page, chooser := _PickerChooser())
        self.assertEqual(chooser.files, self.attach.letter_pdf)
        self.assertEqual(page._oea_chooser_filled, "letter")

    def test_without_a_pin_the_page_still_decides(self) -> None:
        page = _PickerPage()
        self._fire(page, chooser := _PickerChooser(own="resume_upload"))
        self.assertEqual(chooser.files, self.attach.resume_path)

    def test_a_pin_never_outlives_the_click(self) -> None:
        # The candidate opening a picker later must not inherit the last
        # tile's slot - that is how a cover-letter box gets a resume.
        page = _PickerPage()
        page._oea_chooser_want = ""
        self._fire(page, chooser := _PickerChooser(own="", around=["Upload", "Apply"]))
        self.assertEqual(chooser.files, "")
        self.assertTrue(any("nothing on it says" in s for s in self.sess.logs))


class CloseOpenedTests(unittest.TestCase):
    """Escape closes what the agent's own click put up - and nothing else.

    Jobvite's upload menu is a role=dialog, and leaving it open hides the
    whole form from the next read. But Escape at a LinkedIn Easy Apply modal
    discards the application, so it is pressed only when the click itself
    added a dialog.
    """

    def _page(self, pressed):
        return types.SimpleNamespace(
            keyboard=types.SimpleNamespace(press=pressed.append),
            wait_for_timeout=lambda ms: None,
        )

    def test_a_dialog_the_click_opened_is_closed(self) -> None:
        pressed: list[str] = []
        with unittest.mock.patch.object(worker, "_visible_dialogs", return_value=2):
            worker._close_opened(self._page(pressed), 1)
        self.assertEqual(pressed, ["Escape"])

    def test_a_dialog_that_was_already_there_is_left_alone(self) -> None:
        pressed: list[str] = []
        with unittest.mock.patch.object(worker, "_visible_dialogs", return_value=1):
            worker._close_opened(self._page(pressed), 1)
        self.assertEqual(pressed, [])

    def test_a_dialog_that_closed_itself_needs_nothing(self) -> None:
        pressed: list[str] = []
        with unittest.mock.patch.object(worker, "_visible_dialogs", return_value=0):
            worker._close_opened(self._page(pressed), 1)
        self.assertEqual(pressed, [])


class SiteSearchGuardTests(unittest.TestCase):
    """The site's own job search is not part of any application.

    On an iCIMS login flow with no form in front of it, the model typed the
    job title into "Start your job search here", which navigates - losing
    whatever the candidate had open. The candidate parked the job.
    """

    def _field(self, **kw) -> dict:
        base = {"label": "", "name": "", "elid": "", "placeholder": "", "text": "",
                "tag": "input", "type": "text", "id": 1}
        base.update(kw)
        return base

    def test_a_job_search_box_is_recognised(self) -> None:
        for label in ("Start your job search here",
                      "Search jobs",
                      "Search for a job",
                      "Job search",
                      "Search open positions",
                      "Search careers",
                      "Search by keyword"):
            self.assertTrue(worker._is_site_search(self._field(label=label)), label)

    def test_a_form_field_that_merely_says_search_is_not(self) -> None:
        # A Workday skills picker is <input type=search>, and a "Research"
        # label contains the word. The wording is read, never the input type.
        for label in ("Type to Add Skills", "Research interests", "Search Committee",
                      "Where did you search for this role?"):
            self.assertFalse(worker._is_site_search(self._field(label=label)), label)

    def test_the_type_alone_never_decides(self) -> None:
        self.assertFalse(worker._is_site_search(
            self._field(label="Skills", type="search")))

    def test_the_name_counts_too(self) -> None:
        self.assertTrue(worker._is_site_search(
            self._field(label="", name="job_search_keyword")))


class GovernmentIdentifierTests(unittest.TestCase):
    """PAN, Aadhaar and their kin are never stored and never guessed, so the
    only question is whether the form insists. A Worldline application parked
    in the chat on an OPTIONAL "Permanent account number" and went no further:
    no resume on the form, nothing submitted, waiting on a number the site had
    not asked for."""

    def test_the_ones_a_form_asks_for_are_recognised(self) -> None:
        for label in ("Permanent account number", "PAN Number", "PAN Card",
                      "Aadhaar Number", "Passport Number", "Social Security Number",
                      "SSN", "National Insurance Number", "Driving Licence"):
            self.assertTrue(profile.is_identifier(label), label)

    def test_an_ordinary_field_is_not_one(self) -> None:
        # "Company Name" contains "pan", which is why every hint is a whole
        # phrase: a field wrongly read as an identifier is silently skipped.
        for label in ("Company Name", "Position Title", "Panel interview",
                      "Expected CTC", "First Name", "Japan"):
            self.assertFalse(profile.is_identifier(label), label)

    def test_a_secret_is_still_a_secret(self) -> None:
        # The two lists are separate: a password is never asked for at all,
        # required or not, while a required identifier is a fair question.
        self.assertTrue(profile.is_secret("Choose Password:"))
        self.assertFalse(profile.is_identifier("Choose Password:"))


class ApplyChoiceTests(unittest.TestCase):
    """Two or more ways to apply is a question for the candidate. One way is
    not, and neither is a page telling you that you already applied."""

    def _link(self, text, tag="a"):
        return {"tag": tag, "text": text, "label": text, "id": 1}

    def test_a_real_choice_is_offered(self) -> None:
        got = worker._apply_choices([
            self._link("Easy Apply"), self._link("Apply on company website")])
        self.assertEqual(len(got), 2)

    def test_linkedins_status_chip_is_not_a_way_to_apply(self) -> None:
        # The IGT session: LinkedIn stamps the card "Clicked apply" the moment
        # it hands you to the employer, so the chip carries the word and was
        # offered alongside the real button. The candidate was asked to choose
        # between them on a card whose form was already open in another tab.
        fields = [{"tag": "a", "text": "Clicked apply", "label": "Clicked apply", "id": 1},
                  {"tag": "a", "text": "Apply", "label": "Apply on company website", "id": 15}]
        got = worker._apply_choices(fields)
        self.assertEqual([f["id"] for f in got], [15])

    def test_the_other_things_a_finished_card_says(self) -> None:
        for text in ("Clicked apply", "Applied", "Already applied",
                     "Did you finish applying?", "Application sent",
                     "Application submitted", "View application"):
            self.assertEqual(worker._apply_choices([self._link(text)]), [], text)

    def test_a_single_path_asks_nothing(self) -> None:
        # One choice is not a choice: the agent clicks it. This is what makes
        # the chip's removal fix the session rather than merely tidy the list.
        self.assertEqual(
            len(worker._apply_choices([self._link("Apply on company website")])), 1)


class WaitQuietTests(unittest.TestCase):
    """wait_quiet waits for change to STOP, without caring whether any
    happened - the question after an upload, where a site that ignores the
    resume must not pay the whole cap.

    These use a fake page rather than a browser on purpose. The browser check
    tried to prove the cap with a page rewriting itself on a 100ms timer, and
    it failed twice inside a parallel sweep: a starved Chromium throttles that
    timer, the DOM stops changing, and "never settles" quietly stops being
    true. A fake page cannot be throttled.
    """

    class Page:
        """Yields a different shape every poll unless `settles_at` is reached."""

        def __init__(self, settles_at=None):
            self.url = "https://example.invalid/form"
            self.polls = 0
            self.slept = 0
            self.settles_at = settles_at

        def wait_for_timeout(self, ms):
            self.slept += ms
            self.polls += 1

        def shape(self):
            if self.settles_at is not None and self.polls >= self.settles_at:
                return "settled"
            return f"moving-{self.polls}"

    def _patched(self, page):
        from src.apply import browser

        class Frame:
            def evaluate(self, js):
                return page.shape()

        return unittest.mock.patch.object(browser, "target", lambda p: Frame())

    def test_a_page_that_never_settles_gives_up_at_the_cap(self) -> None:
        from src.apply import browser

        page = self.Page()                      # never the same shape twice
        with self._patched(page):
            self.assertFalse(browser.wait_quiet(page, timeout=2000, step=250))
        self.assertEqual(page.slept, 2000)      # the cap, and not a step more

    def test_a_still_page_costs_only_the_quiet_window(self) -> None:
        from src.apply import browser

        page = self.Page(settles_at=0)          # quiet from the first poll
        with self._patched(page):
            self.assertTrue(browser.wait_quiet(page, timeout=12000, step=250,
                                               quiet=1000))
        # Four identical polls to make up the quiet window, not the 12s cap:
        # this is what keeps the wait off every upload on every other site.
        self.assertEqual(page.slept, 1000)

    def test_a_page_that_settles_late_is_waited_out_then_returns(self) -> None:
        from src.apply import browser

        page = self.Page(settles_at=8)
        with self._patched(page):
            self.assertTrue(browser.wait_quiet(page, timeout=12000, step=250,
                                               quiet=1000))
        # Eight polls of movement, then four of quiet.
        self.assertEqual(page.slept, 250 * 12)


class UkgEntryNumberingTests(unittest.TestCase):
    """UKG numbers the ENTRY, not the section.

    Its section heading says only "Work Experience"; the boxes inside an entry
    carry no section at all, and the only thing naming the entry they belong
    to is the "Delete Work Experience 3" button above them. Reading the number
    off the section alone collapsed all four entries onto position 0, so the
    agent counted one entry where the site had already built four from the
    resume, clicked Add Experience twice more, and wrote the same profile job
    into both - three identical "Software Engineer at Acme Systems"
    rows on an application about to be submitted.
    """

    JOBS = [{"title": "Software Engineer", "company": "Acme Systems"},
            {"title": "Software Engineer Intern", "company": "Acme Systems"}]

    def _page(self, entries):
        """The shape of the real dump: a sectioned Delete button naming the
        entry, then that entry's boxes carrying no section whatsoever."""
        fields = [{"label": "Add Experience", "section": "Work Experience",
                   "tag": "button", "group": "", "value": ""}]
        for n, (title, company) in enumerate(entries, start=1):
            fields.append({"label": f"Delete Work Experience {n}",
                           "section": "Work Experience", "tag": "button",
                           "group": "", "value": ""})
            for label, value in (("Job Title", title),
                                 ("Company / Organization", company),
                                 ("Location", ""), ("Year (YYYY)", "")):
                fields.append({"label": label, "section": "", "tag": "input",
                               "group": "", "value": value})
        return fields

    def _positions(self, fields):
        return {f["work_pos"] for f in fields if f.get("work_pos") is not None}

    def test_each_numbered_entry_is_its_own_position(self) -> None:
        fields = self._page([("Software Engineer", "Acme Systems"),
                             ("Software Engineer", "Acme Systems"),
                             ("Software Engineer", "Acme Systems"),
                             ("Software Engineer Intern", "Acme Systems")])
        from src.apply import resolver
        resolver.tag_work_entries(fields, self.JOBS)
        self.assertEqual(self._positions(fields), {0, 1, 2, 3})

    def test_a_page_the_site_already_filled_needs_no_more_entries(self) -> None:
        # The count _open_profile_sections compares against the profile: two
        # jobs, two entries already there, so Add Experience is done. Counting
        # one is what made it click twice more.
        from src.apply import resolver

        fields = self._page([("Software Engineer", "Acme Systems"),
                             ("Software Engineer Intern", "Acme Systems")])
        resolver.tag_work_entries(fields, self.JOBS)
        self.assertEqual(len(self._positions(fields)), len(self.JOBS))

    def test_each_entry_gets_its_own_job(self) -> None:
        from src.apply import resolver

        fields = self._page([("", ""), ("", "")])
        resolver.tag_work_entries(fields, self.JOBS)
        first = {f["work_entry"] for f in fields if f.get("work_pos") == 0}
        second = {f["work_entry"] for f in fields if f.get("work_pos") == 1}
        self.assertEqual(first, {0})
        self.assertEqual(second, {1})

    def test_a_heading_that_merely_ends_in_a_digit_renumbers_nothing(self) -> None:
        # The rule reads a DELETE control, not any label with a number on the
        # end, or "Employment history 2020" would start a fifth entry.
        from src.apply import resolver

        fields = self._page([("Software Engineer", "Acme Systems")])
        fields.insert(1, {"label": "Employment history 2020", "tag": "button",
                          "section": "Work Experience", "group": "", "value": ""})
        resolver.tag_work_entries(fields, self.JOBS)
        self.assertEqual(self._positions(fields), {0})


class LongRadioGroupTests(unittest.TestCase):
    """One question may not spend the whole field budget.

    USP's application on UKG asks "What is your country of origin?" with 46
    radios sharing a name. MAX_FIELDS is 60 and they took every slot from 46
    on, so the six REQUIRED questions below them - and the form's own Submit
    button - were invisible to the model, to the profile and to the llm: flow.
    The candidate's report was "nothing was filled by system on this page".

    A group longer than MAX_GROUP_OPTIONS is carried as ONE field listing its
    options, the way a <select> already is. Shorter ones are untouched, so
    every Yes/No on every form keeps the shape the rest of the code expects.
    """

    @staticmethod
    def _group(name, labels, question="What is your country of origin?", checked=None):
        return [
            {"id": 10 + n, "tag": "input", "type": "radio", "name": name,
             "label": label, "group": question, "section": "Questions",
             "required": True, "value": str(n), "checked": label == checked}
            for n, label in enumerate(labels)
        ]

    def test_a_long_group_becomes_one_field(self):
        countries = [f"Country {n}" for n in range(46)]
        fields = self._group("MCR0", countries)
        out = browser.collapse_long_groups(fields)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["options"], countries)
        self.assertEqual(len(out[0]["group_ids"]), 46)

    def test_it_is_labelled_with_the_question_not_an_option(self):
        fields = self._group("MCR0", [f"Country {n}" for n in range(46)])
        out = browser.collapse_long_groups(fields)
        self.assertEqual(out[0]["label"], "What is your country of origin?")

    def test_the_ids_stay_lined_up_with_the_options(self):
        # The pick clicks group_ids[i] for options[i]. A shuffle here answers
        # a 46-country question with the wrong country.
        labels = [f"Country {n}" for n in range(46)]
        out = browser.collapse_long_groups(self._group("MCR0", labels))
        self.assertEqual(out[0]["group_ids"], list(range(10, 56)))
        self.assertEqual(out[0]["options"][13], labels[13])

    def test_an_answered_group_carries_its_answer(self):
        labels = [f"Country {n}" for n in range(46)]
        out = browser.collapse_long_groups(self._group("MCR0", labels, checked="Country 13"))
        self.assertEqual(out[0]["value"], "Country 13")
        self.assertIs(out[0]["checked"], True)
        # The ticked option represents the group; reading the first one would
        # report an answered question as empty and fill it again.
        self.assertEqual(out[0]["id"], 23)

    def test_an_unanswered_group_reads_as_empty(self):
        out = browser.collapse_long_groups(
            self._group("MCR0", [f"Country {n}" for n in range(46)]))
        self.assertEqual(out[0]["value"], "")
        self.assertIs(out[0]["checked"], False)

    def test_a_short_group_is_untouched(self):
        fields = self._group("sponsor", ["Yes", "No"],
                             question="Do you require sponsorship?")
        self.assertEqual(browser.collapse_long_groups(fields), fields)

    def test_a_group_exactly_at_the_limit_is_untouched(self):
        fields = self._group("x", [f"Option {n}" for n in range(browser.MAX_GROUP_OPTIONS)])
        self.assertEqual(browser.collapse_long_groups(fields), fields)

    def test_everything_that_is_not_the_group_is_kept_in_order(self):
        before = [{"id": 1, "tag": "input", "type": "text", "label": "First Name"}]
        after = [{"id": 90, "tag": "textarea", "type": "", "required": True,
                  "label": "What are your salary expectations for this position?"},
                 {"id": 91, "tag": "button", "type": "button", "label": "Submit"}]
        out = browser.collapse_long_groups(
            before + self._group("MCR0", [f"C{n}" for n in range(46)]) + after)
        self.assertEqual([f["label"] for f in out],
                         ["First Name", "What is your country of origin?",
                          "What are your salary expectations for this position?",
                          "Submit"])

    def test_a_nameless_radio_is_left_alone(self):
        # Without a shared name there is nothing to say these belong together,
        # and guessing from the label would merge unrelated questions.
        fields = self._group("", [f"C{n}" for n in range(46)])
        for field in fields:
            field.pop("name")
        self.assertEqual(browser.collapse_long_groups(fields), fields)

    def test_two_long_groups_stay_separate(self):
        fields = (self._group("MCR0", [f"C{n}" for n in range(46)])
                  + self._group("MCR1", [f"L{n}" for n in range(20)],
                                question="Which languages do you speak?"))
        out = browser.collapse_long_groups(fields)
        self.assertEqual(len(out), 2)
        self.assertEqual([f["label"] for f in out],
                         ["What is your country of origin?",
                          "Which languages do you speak?"])
        self.assertEqual([len(f["options"]) for f in out], [46, 20])


class SkillsBoxTests(unittest.TestCase):
    """"Skill level" is not a skills box.

    A six-option proficiency dropdown was offered the profile's eleven skills,
    which logged "none of your skills match the 6 options of 'Skill level'"
    five times in one session while the real question went unanswered.
    """

    @staticmethod
    def _box(label, tag="input", section=""):
        return {"tag": tag, "type": "text", "label": label, "section": section}

    def test_a_list_of_skills_is_still_recognised(self):
        for label in ("Skills", "Skills*", "Skills (required)", "Key Skills",
                      "Type to Add Skills", "Add skills", "Your skills",
                      "Skills (comma separated)"):
            with self.subTest(label=label):
                self.assertTrue(worker._is_skills_box(self._box(label)), label)

    def test_a_question_about_skills_is_not(self):
        for label in ("Skill level", "Skill rating", "Skill proficiency",
                      "Years of skill use"):
            with self.subTest(label=label):
                self.assertFalse(worker._is_skills_box(self._box(label)), label)

    def test_a_proficiency_dropdown_in_the_skills_section_is_not(self):
        # The section-based branch needs the label to invite a list; "Skill
        # level" under Skills is the dropdown beside the box, not the box.
        self.assertFalse(
            worker._is_skills_box(self._box("Skill level", tag="select", section="Skills")))


class StatedLimitTests(unittest.TestCase):
    """A length the form states in prose, not in maxlength.

    _clip_to_limit read maxlength and nothing else, so a box saying "Max 250
    words" with no attribute got the whole answer - and the form either
    refused it or truncated it mid-sentence. The pattern mirrors _skill_cap,
    which already reads "up to 10 skills" the same way.

    The maxlength path is here too: it had no direct test of its own.
    """

    class Sess:
        def __init__(self):
            self.lines = []

        def log(self, line):
            self.lines.append(line)

    LONG = " ".join(f"w{n}" for n in range(400))

    def clip(self, field, value):
        sess = self.Sess()
        return worker._clip_to_limit(field, value, "Essay", sess), sess.lines

    def test_a_ceiling_in_words_is_read(self):
        for label, want in (
                ("Tell us about a time you handled conflict. Max 250 words.", (250, "words")),
                ("Cover note - no more than 100 words", (100, "words")),
                ("Summary (200 words max)", (200, "words")),
                ("Why this role? (500 characters maximum)", (500, "characters")),
                ("Describe your experience, limited to 300 characters", (300, "characters"))):
            with self.subTest(label=label):
                self.assertEqual(worker._stated_limit({"label": label}), want)

    def test_a_floor_is_never_read_as_a_ceiling(self):
        # "minimum of 250 words" is the opposite instruction; clipping to it
        # would cut an answer the form wanted longer.
        for label in ("Your answer must be a minimum of 250 words",
                      "At least 100 words please"):
            with self.subTest(label=label):
                self.assertEqual(worker._stated_limit({"label": label}), (0, ""))

    def test_an_ordinary_label_states_nothing(self):
        for label in ("What is your notice period", "Salary expectation in LPA",
                      "Tell us about yourself"):
            with self.subTest(label=label):
                self.assertEqual(worker._stated_limit({"label": label}), (0, ""))

    def test_the_section_is_not_searched(self):
        # A limit written about one field must not travel to its neighbours,
        # so only the field's own words count.
        self.assertEqual(
            worker._stated_limit({"label": "Your answer", "section": "Max 50 words"}),
            (0, ""))

    def test_a_word_ceiling_clips_to_words(self):
        out, lines = self.clip({"label": "Essay. Max 250 words."}, self.LONG)
        self.assertEqual(len(out.split()), 250)
        self.assertTrue(any("250 words or fewer" in line for line in lines), lines)

    def test_a_character_ceiling_clips_to_characters(self):
        out, _ = self.clip({"label": "Note (100 characters maximum)"}, "x" * 400)
        self.assertEqual(len(out), 100)

    def test_maxlength_still_works_on_its_own(self):
        out, lines = self.clip({"label": "Essay", "maxlength": 50}, self.LONG)
        self.assertLessEqual(len(out), 50)
        self.assertTrue(any("50 characters" in line for line in lines), lines)

    def test_the_stricter_of_the_two_wins(self):
        # A form may set maxlength AND say something tighter beside the box.
        out, _ = self.clip({"label": "Note (40 characters max)", "maxlength": 200}, "x" * 400)
        self.assertEqual(len(out), 40)
        out, _ = self.clip({"label": "Note (400 characters max)", "maxlength": 30}, "x" * 400)
        self.assertLessEqual(len(out), 30)

    def test_an_answer_that_already_fits_is_untouched(self):
        out, lines = self.clip({"label": "Essay. Max 250 words."}, "A short answer.")
        self.assertEqual(out, "A short answer.")
        self.assertEqual(lines, [])
