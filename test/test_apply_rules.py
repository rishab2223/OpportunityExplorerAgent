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


class NeedsUserTests(TempDbTestCase):
    def test_upload_never_asks(self) -> None:
        action = ApplyAction(action="upload", field_id=1, confidence=0.1)
        self.assertFalse(_needs_user(action, {"tag": "input", "type": "file"}, "Resume"))

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
