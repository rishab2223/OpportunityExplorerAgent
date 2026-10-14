"""The nearest option for an answer a dropdown does not list word for word,
and the one model call per page that picks the rest. Temp DB only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import history
from src.apply import matching, worker

PROFICIENCY = ["No Selection", "Advanced", "Basic", "Intermediate", "N/A"]
MAJORS = ["No Selection", "Accounting/Finance", "Agriculture", "Business", "Computer", "Engineering",
          "Human Resources", "Law", "Liberal Arts", "Other", "Research"]
DEGREES = ["No Selection", "Bachelors (approx 16 yrs)", "Doctorate (over 19 yrs)",
           "Masters (approx 18 yrs)", "Technical/Professional/Diploma (approx 12-13 yrs)"]


def pick(options, value):
    return matching.rule_pick(options, value, worker._choose_option)


class RuleTests(unittest.TestCase):
    """Cargill on SuccessFactors, Oct 2026: none of these were taken."""

    def test_a_level_is_never_overstated(self) -> None:
        self.assertEqual(PROFICIENCY[pick(PROFICIENCY, "Fluent")[0]], "Advanced")
        self.assertEqual(PROFICIENCY[pick(PROFICIENCY, "Native")[0]], "Advanced")
        # Nothing at or below the answer: no pick, never the level above.
        self.assertEqual(pick(["Advanced", "Expert"], "Basic")[0], -1)
        self.assertEqual(pick(["Basic", "Advanced"], "Intermediate"), (0, pick(["Basic", "Advanced"], "Intermediate")[1]))
        self.assertIn("not above", pick(PROFICIENCY, "Fluent")[1])

    def test_a_broad_heading_is_the_answers_first_words(self) -> None:
        index, why = pick(MAJORS, "Computer Science")
        self.assertEqual(MAJORS[index], "Computer")
        self.assertIn("broad", why)
        # Never "Other", never one of several.
        self.assertEqual(pick(["Computer Lab", "Computer Room"], "Computer Science")[0], -1)
        self.assertEqual(pick(["Other", "Law"], "Other Studies")[0], -1)

    def test_a_degree_short_form(self) -> None:
        self.assertEqual(DEGREES[pick(DEGREES, "B.Tech")[0]], "Bachelors (approx 16 yrs)")
        self.assertEqual(DEGREES[pick(DEGREES, "M.Tech")[0]], "Masters (approx 18 yrs)")

    def test_a_computing_field_on_a_list_without_computer(self) -> None:
        fields = ["No Selection", "Arts", "Information Technology", "Medicine"]
        self.assertEqual(fields[pick(fields, "Computer Science")[0]], "Information Technology")

    def test_linkedin_as_social_media(self) -> None:
        sources = ["No Selection", "Agency", "Company Website", "Social Media", "Newspaper"]
        self.assertEqual(sources[pick(sources, "LinkedIn")[0]], "Social Media")

    def test_yes_and_no_are_never_guessed(self) -> None:
        self.assertEqual(pick(["No Selection", "No", "Yes"], "No thanks")[0], -1)
        self.assertEqual(pick(["Yes", "No"], "Maybe")[0], -1)


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "h.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._saved
        self._tmp.cleanup()

    def test_a_pick_is_remembered_for_the_same_answer_and_options(self) -> None:
        options = ["No Selection", "Telecom", "Retail", "Energy"]
        self.assertEqual(matching.best_option(options, "Semiconductors", worker._choose_option)[0], -1)
        matching.remember(options, "Semiconductors", "Energy")
        index, why = matching.best_option(list(reversed(options)), "Semiconductors", worker._choose_option)
        self.assertEqual(list(reversed(options))[index], "Energy")
        self.assertIn("before", why)


class ModelPickTests(unittest.TestCase):
    ITEMS = [{"ref": 1, "question": "Read", "value": "Fluent", "options": PROFICIENCY},
             {"ref": 2, "question": "Major", "value": "Computer Science", "options": MAJORS},
             {"ref": 3, "question": "Industry", "value": "EDA", "options": ["Retail", "Energy"]},
             {"ref": 4, "question": "Source", "value": "Friend", "options": ["Agency", "Other"]}]

    def test_only_listed_exact_or_close_picks_survive(self) -> None:
        def invoke(system, user, schema):
            self.assertIn("Never claim more", system)
            self.assertIn("Candidate's answer: Fluent", user)
            return schema(picks=[
                schema.model_fields["picks"].annotation.__args__[0](ref=1, option="Advanced", match="close"),
                schema.model_fields["picks"].annotation.__args__[0](ref=2, option="Computers", match="close"),
                schema.model_fields["picks"].annotation.__args__[0](ref=3, option="Energy", match="loose"),
                schema.model_fields["picks"].annotation.__args__[0](ref=4, option="Other", match="exact"),
            ])
        picks = matching.pick_with_model(invoke, self.ITEMS)
        # Not on the list ("Computers"), a loose pick, and "Other" are all dropped.
        self.assertEqual({ref: p.option for ref, p in picks.items()}, {1: "Advanced"})
        self.assertEqual(matching.pick_with_model(invoke, []), {})


class PickRefusedTests(unittest.TestCase):
    """The page's one nearest-option call, end to end with fakes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = history.DB_PATH
        history.DB_PATH = Path(self._tmp.name) / "h.db"

    def tearDown(self) -> None:
        history.DB_PATH = self._saved
        self._tmp.cleanup()

    def test_refused_dropdowns_are_picked_in_one_call(self) -> None:
        read = {"id": 1, "tag": "input", "type": "text", "role": "combobox", "label": "* Read",
                "group": "", "value": "", "name": ""}
        gender = {"id": 2, "tag": "input", "type": "text", "role": "combobox", "label": "* Gender",
                  "group": "", "value": "", "name": ""}

        class Sess:
            def __init__(self):
                self.lines, self.refused = [], {}

            def log(self, t):
                self.lines.append(t)

        class Attach:
            calls = 0

        sess = Sess()
        for field, value, options in ((read, "Fluent", PROFICIENCY), (gender, "Male", ["Man", "Woman"])):
            exc = worker.NoOptionMatch("no", options)
            worker._remember_refusal(sess, worker._field_key(field, field["label"]), value, exc)
        calls, applied = [], []

        def invoke(system, user, schema):
            calls.append(user)
            item = schema.model_fields["picks"].annotation.__args__[0]
            return schema(picks=[item(ref=1, option="Advanced", match="close", reason="nearest level")])

        attach = Attach()
        attach.pick_invoke = invoke
        handled: set[str] = set()
        with mock.patch.object(worker, "_apply_value",
                               side_effect=lambda page, f, v, pdf, s, source="": applied.append((f["label"], v, source))):
            filled = worker._pick_refused(None, [read, gender], handled, sess, attach)
            again = worker._pick_refused(None, [read, gender], handled, sess, attach)
        self.assertEqual(filled, 1)
        self.assertEqual(again, 0)                     # asked once, never again
        self.assertEqual(len(calls), 1)
        self.assertNotIn("Gender", calls[0])           # a personal question never goes
        self.assertEqual(applied, [("* Read", "Advanced", "best match")])
        self.assertTrue(any("[best match]" in line and "Advanced" in line for line in sess.lines))
        # And remembered: the next form gets it from the rules' memory.
        self.assertEqual(matching.remembered(PROFICIENCY, "Fluent")[0], 1)


class ToggleTests(unittest.TestCase):
    def test_a_fill_on_the_arrow_button_goes_to_its_box(self) -> None:
        box = {"id": 31, "tag": "input", "type": "text", "role": "combobox", "label": "* Country:"}
        arrow = {"id": 32, "tag": "button", "type": "button", "role": "", "label": "Country:"}
        other = {"id": 40, "tag": "button", "type": "button", "role": "", "label": "Expand all"}
        fields = [box, arrow, other]
        self.assertIs(worker._combobox_for_toggle(fields, arrow), box)
        self.assertIsNone(worker._combobox_for_toggle(fields, other))
        self.assertIsNone(worker._combobox_for_toggle(fields, box))


if __name__ == "__main__":
    unittest.main()
