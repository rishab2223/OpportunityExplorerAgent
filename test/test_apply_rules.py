from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from src import answers, history
from src.apply import profile
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
        ):
            self.assertTrue(_looks_closed(text), text)
        for text in (
            "Apply for this job. Position: Senior Software Engineer.",
            "Job openings at Capgemini",
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
        self.assertIsNone(_llm_instruction("AI-ML engineer at Cadence"))
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
        fill = ApplyAction(action="fill", field_id=1, value="Rishab", confidence=0.95)
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
        got = _find_advance(fields, set(), {})
        self.assertEqual(got["id"], 2)

    def test_no_advance_on_final_step(self) -> None:
        fields = [{"id": 1, "tag": "button", "type": "submit", "text": "Submit application"}]
        self.assertIsNone(_find_advance(fields, set(), {}))

    def test_handled_advance_not_reclicked(self) -> None:
        fields = [{"id": 2, "tag": "button", "type": "", "text": "Next"}]
        self.assertIsNone(_find_advance(fields, {"next"}, {}))

    def test_word_must_start_the_label(self) -> None:
        # Regression: a careers-page "Code Review" nav link matched \breview\b
        # and was clicked three times in a real session.
        self.assertIsNone(_find_advance(
            [{"id": 1, "tag": "a", "type": "", "text": "Code Review"}], set(), {}))
        self.assertIsNone(_find_advance(
            [{"id": 1, "tag": "a", "type": "", "text": "Overview"}], set(), {}))
        got = _find_advance(
            [{"id": 2, "tag": "button", "type": "", "text": "Review your application"}],
            set(), {})
        self.assertEqual(got["id"], 2)


class PageSigTests(unittest.TestCase):
    def test_same_fields_same_sig_value_changes_it(self) -> None:
        fields = [{"tag": "input", "type": "text", "label": "Name", "value": "", "checked": None}]
        self.assertEqual(_page_sig(fields), _page_sig([dict(fields[0])]))
        changed = [dict(fields[0], value="Rishab")]
        self.assertNotEqual(_page_sig(fields), _page_sig(changed))


class SalaryEstimateTests(TempDbTestCase):
    """Expected-salary fields get one model estimate per session, floored at
    current pay; the value is written in the field's own unit."""

    def setUp(self) -> None:
        super().setUp()
        from unittest import mock

        self.profile = {"full_name": "Test User", "total_experience_years": "6",
                        "current_ctc": "25 LPA", "expected_ctc": "30 LPA"}
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
            self.assertNotIn("25 LPA", user)   # current pay never reaches the model
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
        attach = self._attach(18, 22)
        # Band midpoint 20 < current 25: the saved expectation (30) is quoted.
        self.assertEqual(attach.expected_salary(), 3_000_000)
        joined = "\n".join(attach.logs)
        self.assertIn("model band 18 LPA-22 LPA, midpoint 20 LPA", joined)
        self.assertIn("below your current 25 LPA", joined)
        self.assertIn("Quoting 30 LPA", joined)
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
        self.assertFalse(_wants_salary_estimate({**base, "tag": "select", "label": "Expected CTC", "options": ["20-30 LPA"]}))

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
            # ...but not when the page already read that way at the prompt.
            with mock.patch.object(browser, "page_text", return_value="Your application was sent to X!"),                     mock.patch.object(browser, "snapshot", return_value=base_fields):
                self.assertEqual(worker._page_grew(None, page, {**watch, "submitted": True}), "")
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
            _json.dumps({"full_name": "T", **extra}), encoding="utf-8")

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

        self.assertEqual(_choose_option(["Select One", "Bihār", "Haryāna"], "Haryana"), 2)
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
                mock.patch("src.apply.resolver.resolve", return_value=("Gurgaon", "profile")):
            worker._sweep(None, [field], handled, attempts, {}, "", Sess(), written=written)
            # Still handled and still holding a value: nothing happens.
            worker._sweep(None, [dict(field, value="Gurgaon")], handled, attempts, {}, "", Sess(), written=written)
            # Blank again after a re-render: written once more, marked as such.
            worker._sweep(None, [field], handled, attempts, {}, "", Sess(), written=written)
        self.assertEqual(calls, [("City*", "Gurgaon", "profile"), ("City*", "Gurgaon", "again")])
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
        self.assertFalse(_holds(Loc("Gurgaon"), "Noida"))


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

        opts = ["Gurgaon, Bihar, India", "Gurgaon, Haryana, India", "Gurugram, Haryana, India"]
        self.assertEqual(_choose_option(opts, "Gurgaon"), 0)                       # no context: first
        self.assertEqual(_choose_option(opts, "Gurgaon", ["Haryana", "India"]), 1)
        self.assertEqual(_choose_option(opts, "Gurgaon", ["Kerala"]), 0)          # nothing to prefer: first
        # Containment with several hits stays ambiguous unless a preference decides.
        self.assertEqual(_choose_option(["A Haryana B", "C Haryana D"], "Haryana"), -1)
        self.assertEqual(_choose_option(["A Haryana B", "C Haryana D"], "Haryana", ["C"]), 1)


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

        self.assertEqual(resolver.city_aliases("Gurgaon, India"), ["Gurugram, India"])
        self.assertEqual(resolver.city_aliases("Bengaluru"), ["Bangalore"])
        self.assertEqual(resolver.city_aliases("Pune, India"), ["Poona, India"])
        self.assertEqual(resolver.city_aliases("Noida"), [])
        opts = ["Gurgaon, Bihar, India", "Gurugram, Haryana, India"]
        self.assertEqual(_choose_option(opts, "Gurgaon", ["Haryana", "India"]), 1)
        self.assertEqual(_choose_option(opts, "Gurgaon"), 0)   # no state to go by: as typed
        self.assertEqual(_choose_option(["Gurugram, Haryana, India"], "Gurgaon", ["Haryana"]), 0)


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
        self.assertEqual(worker._choose_option(["Gurgaon, Bihar", "Gurgaon, Haryana"], "Gurgaon,"), -1)


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

        q = ("What desired annual salary should I enter here (the field rejected '30 lpa' "
             "- should it be a plain number like 3000000 INR)?")
        self.assertEqual(worker._proposal_in_question(q), "3000000 INR")
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

        self.assertEqual(salary.parse_annual_inr("30 lpa"), 3000000)
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
                  + self._entry("Software Engineer", "Cadence Design Systems", 20))
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

        fields = (self._entry("Software Engineer", "Cadence Design Systems", 10)
                  + self._entry("Software Engineer Intern", "Cadence Design Systems", 20))
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
        data = {"email": "a_candidate@example.invalid", "phone": "9000000000", "location": "Gurgaon"}
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
        self.assertEqual(worker._excluded_experience(title, "Cadence Design Systems"), "")
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

        data = {"current_company": "Cadence Design Systems", "current_company_location": "Noida"}
        fields = [
            {"label": "Company*", "section": "Work History (Optional) 2", "value": "Cadence Design Systems"},
            {"label": "Location", "section": "Work History (Optional) 2", "value": ""},
            {"label": "Company*", "section": "Work History (Optional) 1", "value": "Other Corp"},
            {"label": "Location", "section": "Work History (Optional) 1", "value": ""},
            {"label": "Location", "section": "Education 1", "value": ""},
        ]
        self.assertEqual(worker._entry_location(fields[1], fields, data), "Noida")
        self.assertIsNone(worker._entry_location(fields[3], fields, data))   # another employer
        self.assertIsNone(worker._entry_location(fields[4], fields, data))   # not a job entry
        self.assertIsNone(worker._entry_location(fields[1], fields, {"current_company": "Cadence Design Systems"}))
