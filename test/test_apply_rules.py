from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import answers, history
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
    """_needs_user consults the answer bank; keep every test off the real DB."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "job_history.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._original_path
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
        self.assertTrue(any("band 40 LPA-42 LPA -> quoting 41 LPA" in line for line in attach.logs), attach.logs)

    def test_never_below_current_pay(self) -> None:
        attach = self._attach(18, 22)
        # Band midpoint 20 < current 25: the saved expectation (30) is quoted.
        self.assertEqual(attach.expected_salary(), 3_000_000)
        self.assertTrue(any("below the current 25 LPA" in line for line in attach.logs), attach.logs)

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
        self.assertEqual(_choose_option(["Bachelors", "Masters"], "Bachelor's Degree"), -1)


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
